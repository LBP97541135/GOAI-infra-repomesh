"""Adjudication D-7 at the seam: which room reports still move a task.

The producing side's rule is pinned in
``tests/task_orchestration/test_room_report_eligibility.py``. This module pins
what ``ProcessMatrixTaskReport`` does with the answer, over the real
orchestrator and the real directory so that "the task did not move" is a
statement about the task store rather than about a mock's call count.

Both sides are here on purpose. A test that only showed the closed case would
pass just as well if the gate were wired shut for everything, which would
silently take the leader path down with it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from test_orchestration_flow import StaticIdentityVerifier, build_flow

from repomesh.modules.collaboration import (
    ROOM_REPORT_IGNORED_EVENT,
    ROOM_REPORT_IGNORED_REASON,
    InboundMatrixMessage,
    InMemoryCollaborationAuditLedger,
    InMemoryProcessedMatrixEventStore,
    MatrixInboundResult,
    ProcessMatrixTaskReport,
)
from repomesh.modules.task_orchestration import (
    AssignTaskCommand,
    DispatchedWorkerTaskReader,
    TaskStatus,
)

T0 = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)


def _report_body(*, sender_agent_id, project_id, task_id, summary: str) -> str:
    return json.dumps(
        {
            "schema": "repomesh.agent-report.v1",
            "sender_agent_id": str(sender_agent_id),
            "project_id": str(project_id),
            "task_id": str(task_id),
            "status": "succeeded",
            "summary": summary,
        }
    )


async def _scenario():
    """A dispatched leader task and, under it, a dispatched worker task."""

    (
        organization_id,
        repository_id,
        project_id,
        organization_leader,
        repository_team,
        _,
        _,
        orchestrator,
        directory,
        topologies,
    ) = await build_flow()
    leader_task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            assigned_by_agent_id=organization_leader.id,
            assignee_agent_id=repository_team.leader.id,
            title="Repository task",
            instruction="Coordinate the pricing change.",
            acceptance=("Worker result is collected",),
        ),
        idempotency_key="d7-repository-task",
    )
    worker = repository_team.workers[0]
    worker_task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            parent_task_id=leader_task.id,
            assigned_by_agent_id=repository_team.leader.id,
            assignee_agent_id=worker.id,
            title="Worker task",
            instruction="Implement pricing.",
            acceptance=("Tests pass",),
        ),
        idempotency_key="d7-worker-task",
    )
    topology = await topologies.get_view(project_id)
    assert topology is not None
    team = topology.repository_teams[0]
    return {
        "project_id": project_id,
        "directory": directory,
        "topologies": topologies,
        "orchestrator": orchestrator,
        "tasks": orchestrator._tasks,  # noqa: SLF001 - the store under assertion
        "leader": repository_team.leader,
        "leader_task": leader_task,
        "worker": worker,
        "worker_task": worker_task,
        "team_room": team.room_id,
        "leader_room": team.leader_room_id,
    }


def _processor(scenario, *, audit: InMemoryCollaborationAuditLedger, verifier_for, matrix_id):
    return ProcessMatrixTaskReport(
        scenario["directory"],
        scenario["topologies"],
        StaticIdentityVerifier(verifier_for.id, matrix_id),
        InMemoryProcessedMatrixEventStore(),
        scenario["orchestrator"],
        DispatchedWorkerTaskReader(scenario["tasks"], scenario["directory"]),
        audit,
    )


@pytest.mark.asyncio
async def test_a_workers_room_report_is_ignored_and_written_down() -> None:
    """The closed half: a coding task's truth is the Runner's, not the room's.

    The message is refused *and* the refusal is recorded. Ignoring silently
    would be indistinguishable from a message that never arrived, and the
    difference is the whole answer to "why did my Worker's done not finish the
    task".
    """

    scenario = await _scenario()
    audit = InMemoryCollaborationAuditLedger()
    processor = _processor(
        scenario,
        audit=audit,
        verifier_for=scenario["worker"],
        matrix_id="@worker:matrix.local",
    )
    worker_task = scenario["worker_task"]
    message = InboundMatrixMessage(
        event_id="$worker-claims-success",
        room_id=scenario["team_room"],
        sender="@worker:matrix.local",
        body=_report_body(
            sender_agent_id=scenario["worker"].id,
            project_id=scenario["project_id"],
            task_id=worker_task.id,
            summary="Pricing implementation and tests completed.",
        ),
        occurred_at=T0,
    )

    assert await processor.execute(message) is MatrixInboundResult.IGNORED

    # The task did not move. Read from the store, not from a spy.
    stored = await scenario["tasks"].get(worker_task.id)
    assert stored is not None
    assert stored.status is TaskStatus.ASSIGNED
    assert stored.result_summary is None
    progress = await scenario["orchestrator"].progress(scenario["project_id"])
    assert progress.succeeded == 0

    # ...and the refusal is on the ledger, attributed to the raw Matrix sender
    # rather than to the agent id the unverified body claims to be.
    (entry,) = audit.events
    assert entry.event_type == ROOM_REPORT_IGNORED_EVENT
    assert entry.payload["reason"] == ROOM_REPORT_IGNORED_REASON
    assert entry.actor_id == "@worker:matrix.local"
    assert entry.task_id == worker_task.id
    assert entry.project_id == scenario["project_id"]
    assert entry.payload["matrix_event_id"] == "$worker-claims-success"
    assert entry.payload["room_id"] == scenario["team_room"]
    assert entry.payload["claimed_status"] == "succeeded"


@pytest.mark.asyncio
async def test_replaying_an_ignored_report_does_not_double_the_ledger() -> None:
    """A replayed batch must not write the same refusal twice.

    The event is marked consumed when it is refused, so the second pass reads
    as DUPLICATE — an audit trail with one row per refusal, not one row per
    retry of the batch that carried it.
    """

    scenario = await _scenario()
    audit = InMemoryCollaborationAuditLedger()
    processor = _processor(
        scenario,
        audit=audit,
        verifier_for=scenario["worker"],
        matrix_id="@worker:matrix.local",
    )
    message = InboundMatrixMessage(
        event_id="$worker-claims-success",
        room_id=scenario["team_room"],
        sender="@worker:matrix.local",
        body=_report_body(
            sender_agent_id=scenario["worker"].id,
            project_id=scenario["project_id"],
            task_id=scenario["worker_task"].id,
            summary="Done.",
        ),
        occurred_at=T0,
    )

    assert await processor.execute(message) is MatrixInboundResult.IGNORED
    assert await processor.execute(message) is MatrixInboundResult.DUPLICATE
    assert len(audit.events) == 1


@pytest.mark.asyncio
async def test_a_spoofed_worker_report_is_ignored_without_consulting_identity() -> None:
    """The closed path is closed whoever is knocking.

    Verifying first would spend a control-plane round trip to reach the same
    answer, and worse: the refusal is raised, which makes the poller retry the
    whole batch forever rather than move past a message it will never accept.
    """

    scenario = await _scenario()
    audit = InMemoryCollaborationAuditLedger()
    processor = _processor(
        scenario,
        audit=audit,
        verifier_for=scenario["worker"],
        matrix_id="@worker:matrix.local",
    )
    spoofed = InboundMatrixMessage(
        event_id="$attacker-claims-success",
        room_id=scenario["team_room"],
        sender="@attacker:matrix.local",
        body=_report_body(
            sender_agent_id=scenario["worker"].id,
            project_id=scenario["project_id"],
            task_id=scenario["worker_task"].id,
            summary="Done.",
        ),
        occurred_at=T0,
    )

    assert await processor.execute(spoofed) is MatrixInboundResult.IGNORED
    assert audit.events[0].actor_id == "@attacker:matrix.local"
    stored = await scenario["tasks"].get(scenario["worker_task"].id)
    assert stored is not None and stored.status is TaskStatus.ASSIGNED


@pytest.mark.asyncio
async def test_a_leader_task_report_is_processed_as_before() -> None:
    """The open half, over the same wiring, so the gate cannot be shut wholesale.

    A leader task carries no published package, so D-7 does not touch it: the
    report is authenticated, applied, and the ledger stays empty because
    nothing was refused. This is the path PR 7's review submission replaces;
    until it lands it must keep working.
    """

    scenario = await _scenario()
    audit = InMemoryCollaborationAuditLedger()
    processor = _processor(
        scenario,
        audit=audit,
        verifier_for=scenario["leader"],
        matrix_id="@leader:matrix.local",
    )
    leader_task = scenario["leader_task"]
    message = InboundMatrixMessage(
        event_id="$leader-report",
        room_id=scenario["leader_room"],
        sender="@leader:matrix.local",
        body=_report_body(
            sender_agent_id=scenario["leader"].id,
            project_id=scenario["project_id"],
            task_id=leader_task.id,
            summary="Repository work collected and verified.",
        ),
        occurred_at=T0,
    )

    assert await processor.execute(message) is MatrixInboundResult.PROCESSED

    stored = await scenario["tasks"].get(leader_task.id)
    assert stored is not None and stored.status is TaskStatus.SUCCEEDED
    assert audit.events == []


@pytest.mark.asyncio
async def test_an_unparseable_room_message_is_still_just_ignored() -> None:
    """Chat is chat: ignored, and with no ledger row, because nothing was refused.

    The ledger is for refusals of a *report*. A person saying good morning
    named no task and claimed nothing, so writing it down as a D-7 refusal
    would fill the audit trail with the room's ordinary conversation.
    """

    scenario = await _scenario()
    audit = InMemoryCollaborationAuditLedger()
    processor = _processor(
        scenario,
        audit=audit,
        verifier_for=scenario["worker"],
        matrix_id="@worker:matrix.local",
    )

    result = await processor.execute(
        InboundMatrixMessage(
            event_id="$chatter",
            room_id=scenario["team_room"],
            sender="@bohan:matrix.local",
            body="morning — how is the pricing change going?",
            occurred_at=T0,
        )
    )

    assert result is MatrixInboundResult.IGNORED
    assert audit.events == []
