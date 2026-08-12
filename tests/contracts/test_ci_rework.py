from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from repomesh.integrations.scm.delivery import ChangeSetSCMCoordinator
from repomesh.integrations.scm.observation_processor import GitHubObservationProcessor
from repomesh.integrations.scm.rework import CIReworkTaskCreator
from repomesh.modules.delivery import (
    DeliveryService,
    InMemoryChangeSetStore,
    InMemorySCMObservationStore,
    SCMObservationService,
)
from repomesh.modules.delivery.contracts import (
    CIObservationCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecordSCMObservationCommand,
    RepositoryCandidateInput,
    SCMObservationSource,
)
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
)
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import InMemoryRepositoryCatalog
from repomesh.modules.task_orchestration.contracts import (
    CreateCIReworkTaskCommand,
    TaskOrigin,
)

CANDIDATE_SHA = "a" * 40


class Tasks:
    def __init__(self) -> None:
        self.calls = []

    async def assign(self, command, *, idempotency_key, origin=TaskOrigin.PLANNED):
        self.calls.append((command, idempotency_key, origin))
        return SimpleNamespace(id=uuid4(), command=command)


@pytest.mark.asyncio
async def test_ci_rework_uses_stable_idempotency_and_worker_assignment() -> None:
    tasks = Tasks()
    creator = CIReworkTaskCreator(tasks)
    change_set_id = uuid4()
    repository_id = uuid4()
    manager_id = uuid4()
    worker_id = uuid4()

    await creator.create(
        CreateCIReworkTaskCommand(
            uuid4(),
            uuid4(),
            change_set_id,
            repository_id,
            manager_id,
            worker_id,
            uuid4(),
            "a" * 40,
            "unit test failed",
            ("unit passes",),
        )
    )

    assigned, key, origin = tasks.calls[0]
    assert assigned.assigned_by_agent_id == manager_id
    assert assigned.assignee_agent_id == worker_id
    assert assigned.parent_task_id is not None
    assert key == f"ci-rework:{change_set_id}:{repository_id}:{'a' * 40}"
    # The rework fact is declared, not left to be inferred from the title.
    assert origin is TaskOrigin.REWORK


class Topology:
    """Project topology with one repository team, or none at all."""

    def __init__(self, project_id, repository_id, *, staffed: bool = True) -> None:
        self.leader_agent_id = uuid4()
        self.worker_agent_id = uuid4()
        self._view = ProjectAgentTopologyView(
            id=uuid4(),
            organization_id=uuid4(),
            project_id=project_id,
            organization_leader_id=uuid4(),
            repository_teams=(
                (
                    RepositoryTeamView(
                        id=uuid4(),
                        project_id=project_id,
                        repository_id=repository_id,
                        leader_agent_id=self.leader_agent_id,
                        worker_agent_ids=(self.worker_agent_id,),
                        agentteams_team_name="pricing",
                        runtime_status=ProjectTeamRuntimeStatus.READY,
                        room_id=None,
                        leader_room_id=None,
                    ),
                )
                if staffed
                else ()
            ),
        )

    async def get_view(self, project_id):
        return self._view


async def change_set_with_ci(*, passed: bool):
    """One PR-open candidate whose required check has reported."""

    repository_id = uuid4()
    service = DeliveryService(InMemoryChangeSetStore())
    change_set = await service.prepare(
        PrepareChangeSetCommand(
            uuid4(),
            uuid4(),
            uuid4(),
            "Raise prices",
            uuid4(),
            (
                RepositoryCandidateInput(
                    repository_id,
                    uuid4(),
                    CANDIDATE_SHA,
                    "b" * 40,
                    "repomesh/pricing",
                    required_checks=("ci",),
                ),
            ),
        ),
        idempotency_key="ci-rework-delivery",
    )
    await service.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id, repository_id, 42, "https://example.test/pr/42", CANDIDATE_SHA
        )
    )
    result = await service.observe_ci(
        CIObservationCommand(
            change_set.id, repository_id, passed, "run-1", "unit suite", check_name="ci"
        )
    )
    return result, repository_id


@pytest.mark.asyncio
async def test_a_failed_candidate_is_handed_to_the_repository_worker() -> None:
    change_set, repository_id = await change_set_with_ci(passed=False)
    tasks = Tasks()
    topology = Topology(change_set.project_id, repository_id)

    await CIReworkTaskCreator(tasks, topology).create_for_failed_candidate(
        change_set, repository_id
    )

    assigned, key, origin = tasks.calls[0]
    assert assigned.assigned_by_agent_id == topology.leader_agent_id
    assert assigned.assignee_agent_id == topology.worker_agent_id
    assert assigned.repository_id == repository_id
    # Keyed on the failed candidate, so a replay reuses this one task.
    assert key == f"ci-rework:{change_set.id}:{repository_id}:{CANDIDATE_SHA}"
    assert origin is TaskOrigin.REWORK
    # The Worker is told which check failed, not merely that something did.
    assert "unit suite" in assigned.instruction


@pytest.mark.asyncio
async def test_a_repository_with_no_worker_cannot_be_asked_to_repair() -> None:
    change_set, repository_id = await change_set_with_ci(passed=False)
    tasks = Tasks()
    topology = Topology(change_set.project_id, repository_id, staffed=False)

    with pytest.raises(ValueError, match="no Worker"):
        await CIReworkTaskCreator(tasks, topology).create_for_failed_candidate(
            change_set, repository_id
        )
    assert tasks.calls == []


class Rework:
    def __init__(self, *, explode: bool = False) -> None:
        self.calls = []
        self._explode = explode

    async def create_for_failed_candidate(self, change_set, repository_id):
        self.calls.append(repository_id)
        if self._explode:
            raise RuntimeError("AgentTeams is unreachable")
        return uuid4()


def processor_for(delivery, rework) -> GitHubObservationProcessor:
    return GitHubObservationProcessor(
        observations=None,
        delivery=delivery,
        catalog=None,
        coordinator=None,
        rework_tasks=rework,
    )


@pytest.mark.asyncio
async def test_ci_failure_asks_for_rework_and_a_pass_does_not() -> None:
    failed, repository_id = await change_set_with_ci(passed=False)
    rework = Rework()
    await processor_for(None, rework)._request_rework(failed, repository_id)
    assert rework.calls == [repository_id]

    green, green_repository_id = await change_set_with_ci(passed=True)
    quiet = Rework()
    await processor_for(None, quiet)._request_rework(green, green_repository_id)
    assert quiet.calls == []


@pytest.mark.asyncio
async def test_a_rework_that_cannot_be_created_does_not_lose_the_ci_fact() -> None:
    """The CI observation is already recorded; it must not be rolled back."""

    failed, repository_id = await change_set_with_ci(passed=False)
    rework = Rework(explode=True)

    await processor_for(None, rework)._request_rework(failed, repository_id)

    assert rework.calls == [repository_id]


async def process_check_run(delivery, change_set_id, repository_id, rework, *, conclusion: str):
    """Drive one real check_run observation end to end through the processor."""

    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(id=repository_id, name="pricing", url="https://github.com/acme/pricing")
    )
    observations = SCMObservationService(InMemorySCMObservationStore())
    recorded = await observations.record(
        RecordSCMObservationCommand(
            provider="github",
            source=SCMObservationSource.WEBHOOK,
            external_id=f"check-{conclusion}",
            event_type="check_run",
            payload={
                "repository": {"name": "pricing", "owner": {"login": "acme"}},
                "check_run": {
                    "id": 91,
                    "name": "ci",
                    "head_sha": CANDIDATE_SHA,
                    "status": "completed",
                    "conclusion": conclusion,
                    "output": {"summary": "unit suite"},
                },
            },
            payload_hash="0" * 64,
            observed_at=datetime.now(UTC),
            change_set_id=change_set_id,
            repository_id=repository_id,
        )
    )
    processor = GitHubObservationProcessor(
        observations,
        delivery,
        catalog,
        ChangeSetSCMCoordinator(delivery, catalog, None),
        rework_tasks=rework,
    )
    return await processor.process(recorded.observation.id)


@pytest.mark.asyncio
async def test_a_real_failing_check_run_reaches_the_rework_creator() -> None:
    """Covers the call site, not just the helper: a webhook drives the repair."""

    repository_id = uuid4()
    delivery = DeliveryService(InMemoryChangeSetStore())
    change_set = await delivery.prepare(
        PrepareChangeSetCommand(
            uuid4(),
            uuid4(),
            uuid4(),
            "Raise prices",
            uuid4(),
            (
                RepositoryCandidateInput(
                    repository_id,
                    uuid4(),
                    CANDIDATE_SHA,
                    "b" * 40,
                    "repomesh/pricing",
                    required_checks=("ci",),
                ),
            ),
        ),
        idempotency_key="ci-rework-webhook",
    )
    await delivery.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id, repository_id, 42, "https://example.test/pr/42", CANDIDATE_SHA
        )
    )
    rework = Rework()

    await process_check_run(
        delivery, change_set.id, repository_id, rework, conclusion="failure"
    )

    assert rework.calls == [repository_id]
