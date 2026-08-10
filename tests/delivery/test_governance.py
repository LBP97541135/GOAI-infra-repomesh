from uuid import uuid4

import pytest

from repomesh.modules.delivery import DeliveryService, InMemoryChangeSetStore
from repomesh.modules.delivery.contracts import (
    CIObservationCommand,
    GovernanceDecisionKind,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecordGovernanceDecisionCommand,
    RepositoryCandidateInput,
)


async def governed_delivery():
    repository_id = uuid4()
    service = DeliveryService(InMemoryChangeSetStore(), require_governance=True)
    change_set = await service.prepare(
        PrepareChangeSetCommand(
            uuid4(),
            uuid4(),
            uuid4(),
            "Govern delivery",
            uuid4(),
            (
                RepositoryCandidateInput(
                    repository_id,
                    uuid4(),
                    "a" * 40,
                    "b" * 40,
                    "repomesh/governed",
                ),
            ),
        ),
        idempotency_key="governed-delivery",
    )
    await service.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id, repository_id, 12, "https://example.test/pr/12", "a" * 40
        )
    )
    await service.observe_ci(
        CIObservationCommand(change_set.id, repository_id, True, "ci-12", "passed")
    )
    return service, change_set.id, repository_id


@pytest.mark.asyncio
async def test_merge_requires_ready_decision_bound_to_candidate_head() -> None:
    service, change_set_id, repository_id = await governed_delivery()

    missing = await service.evaluate_merge_gate(change_set_id, repository_id)
    assert "head-bound governance decision is missing" in missing.reasons

    await service.record_governance_decision(
        RecordGovernanceDecisionCommand(
            change_set_id,
            repository_id,
            "a" * 40,
            GovernanceDecisionKind.READY,
            uuid4(),
            "release approved",
        )
    )
    assert (await service.evaluate_merge_gate(change_set_id, repository_id)).allowed


@pytest.mark.asyncio
async def test_blocked_decision_closes_merge_gate_and_wrong_head_is_rejected() -> None:
    service, change_set_id, repository_id = await governed_delivery()

    with pytest.raises(Exception, match="candidate head"):
        await service.record_governance_decision(
            RecordGovernanceDecisionCommand(
                change_set_id,
                repository_id,
                "c" * 40,
                GovernanceDecisionKind.READY,
                uuid4(),
                "wrong revision",
            )
        )

    await service.record_governance_decision(
        RecordGovernanceDecisionCommand(
            change_set_id,
            repository_id,
            "a" * 40,
            GovernanceDecisionKind.BLOCKED,
            uuid4(),
            "security review failed",
        )
    )
    gate = await service.evaluate_merge_gate(change_set_id, repository_id)
    assert "governance blocked delivery: security review failed" in gate.reasons
