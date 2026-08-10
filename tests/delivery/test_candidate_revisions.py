from uuid import uuid4

import pytest

from repomesh.modules.delivery import DeliveryService, InMemoryChangeSetStore
from repomesh.modules.delivery.contracts import (
    CIObservationCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecordCandidateRevisionCommand,
    RepositoryCandidateInput,
)


@pytest.mark.asyncio
async def test_rework_appends_revision_and_invalidates_head_bound_evidence() -> None:
    repository_id = uuid4()
    first_task = uuid4()
    service = DeliveryService(InMemoryChangeSetStore())
    change_set = await service.prepare(
        PrepareChangeSetCommand(
            uuid4(),
            uuid4(),
            uuid4(),
            "Pricing rework",
            uuid4(),
            (
                RepositoryCandidateInput(
                    repository_id,
                    first_task,
                    "a" * 40,
                    "b" * 40,
                    "repomesh/stable-branch",
                ),
            ),
        ),
        idempotency_key="candidate-rework",
    )
    await service.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id, repository_id, 42, "https://example.test/pr/42", "a" * 40
        )
    )
    await service.observe_ci(
        CIObservationCommand(change_set.id, repository_id, False, "ci-1", "failed")
    )

    rework_task = uuid4()
    revised = await service.record_candidate_revision(
        RecordCandidateRevisionCommand(
            change_set.id,
            repository_id,
            rework_task,
            "a" * 40,
            "c" * 40,
            "repair failed unit test",
        )
    )

    candidate = revised.repositories[0]
    assert candidate.branch_name == "repomesh/stable-branch"
    assert candidate.pull_request_number == 42
    assert candidate.commit_sha == "c" * 40
    assert candidate.base_sha == "a" * 40
    assert candidate.task_id == rework_task
    assert candidate.status.value == "pr_open"
    assert candidate.ci_checks == ()
    assert [item.sequence for item in revised.candidate_revisions] == [0, 1]


@pytest.mark.asyncio
async def test_rework_rejects_stale_previous_head() -> None:
    repository_id = uuid4()
    service = DeliveryService(InMemoryChangeSetStore())
    change_set = await service.prepare(
        PrepareChangeSetCommand(
            uuid4(),
            uuid4(),
            uuid4(),
            "Stale rework",
            uuid4(),
            (
                RepositoryCandidateInput(
                    repository_id,
                    uuid4(),
                    "a" * 40,
                    "b" * 40,
                    "repomesh/stable-branch",
                ),
            ),
        ),
        idempotency_key="stale-rework",
    )

    with pytest.raises(Exception, match="stale head"):
        await service.record_candidate_revision(
            RecordCandidateRevisionCommand(
                change_set.id,
                repository_id,
                uuid4(),
                "d" * 40,
                "c" * 40,
                "stale",
            )
        )
