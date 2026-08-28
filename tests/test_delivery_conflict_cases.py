from uuid import uuid4

import pytest

from repomesh.integrations.scm.contracts import (
    PullRequestObservation,
    PullRequestState,
    RepositoryRef,
    SCMProvider,
)
from repomesh.integrations.scm.delivery import ChangeSetSCMCoordinator
from repomesh.modules.delivery import (
    DeliveryService,
    PostgresChangeSetStore,
    PostgresDeliveryConflictCaseStore,
)
from repomesh.modules.delivery.conflicts import DeliveryConflictKind
from repomesh.modules.delivery.contracts import (
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecordCandidateRevisionCommand,
    RepositoryCandidateInput,
)
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import InMemoryRepositoryCatalog
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


class _DriftAdapter:
    def __init__(self, observation): self.observation = observation
    async def get_pull_request(self, repository, number): return self.observation
    async def list_check_runs(self, repository, head_sha): return ()
    async def list_pull_request_reviews(self, repository, number): return ()
    async def ready_for_review(self, repository, number, *, idempotency_key):
        return self.observation


@pytest.mark.asyncio
async def test_base_drift_opens_gate_and_revision_resolves_case(tmp_path) -> None:
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'delivery-conflicts.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    cases = PostgresDeliveryConflictCaseStore(database)
    delivery = DeliveryService(PostgresChangeSetStore(database), conflict_cases=cases)
    repository_id, task_id = uuid4(), uuid4()
    old_head, new_head, old_base, current_base = (
        "a" * 40, "c" * 40, "b" * 40, "d" * 40
    )
    change_set = await delivery.prepare(
        PrepareChangeSetCommand(
            uuid4(), uuid4(), uuid4(), "Drifted delivery", uuid4(),
            (RepositoryCandidateInput(
                repository_id, task_id, old_head, old_base, "repomesh/drift",
            ),),
        ),
        idempotency_key="drifted-delivery",
    )
    await delivery.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id, repository_id, 42,
            "https://github.com/acme/drift/pull/42", old_head,
        )
    )
    catalog = InMemoryRepositoryCatalog()
    await catalog.add(RepositoryProfile(
        id=repository_id, name="drift", url="https://github.com/acme/drift"
    ))
    observation = PullRequestObservation(
        SCMProvider.GITHUB,
        RepositoryRef.from_github("acme", "drift"),
        42, "https://github.com/acme/drift/pull/42", PullRequestState.OPEN,
        False, "repomesh/drift", old_head, "main", current_base, True,
    )
    coordinator = ChangeSetSCMCoordinator(
        delivery, catalog, _DriftAdapter(observation), conflict_cases=cases
    )

    await coordinator.reconcile_and_merge(change_set.id)

    conflict = await cases.active_for(change_set.id, repository_id)
    assert conflict is not None and conflict.kind is DeliveryConflictKind.BASE_DRIFT
    assert conflict.expected_base_sha == old_base
    assert conflict.observed_base_sha == current_base
    gate = await delivery.evaluate_merge_gate(change_set.id, repository_id)
    assert any("delivery conflict is unresolved" in reason for reason in gate.reasons)

    await delivery.record_candidate_revision(
        RecordCandidateRevisionCommand(
            change_set.id, repository_id, task_id, old_head, new_head,
            "rebased on current target and reverified",
        )
    )
    assert await cases.active_for(change_set.id, repository_id) is None
    revised_gate = await delivery.evaluate_merge_gate(change_set.id, repository_id)
    assert "required CI checks have not passed" in revised_gate.reasons
    assert not any("delivery conflict is unresolved" in reason for reason in revised_gate.reasons)
    await database.dispose()


@pytest.mark.asyncio
async def test_unmergeable_case_is_idempotent_and_has_one_repair_task(tmp_path) -> None:
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'case-idempotency.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    cases = PostgresDeliveryConflictCaseStore(database)
    values = dict(
        change_set_id=uuid4(), project_id=uuid4(), repository_id=uuid4(),
        organization_id=uuid4(),
        candidate_head_sha="a" * 40, kind=DeliveryConflictKind.CONTENT_CONFLICT,
        expected_base_sha="b" * 40, observed_base_sha="c" * 40,
        detail="SCM reports the pull request is not mergeable",
    )
    first = await cases.ensure(**values)
    second = await cases.ensure(**values)
    assert second.id == first.id
    task_id = uuid4()
    bound = await cases.set_repair_task(first.id, task_id)
    replay = await cases.set_repair_task(first.id, task_id)
    assert bound.repair_task_id == replay.repair_task_id == task_id
    await database.dispose()
