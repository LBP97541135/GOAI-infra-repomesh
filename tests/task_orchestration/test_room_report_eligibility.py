"""Adjudication D-7's producing side: which tasks a room may still report on.

``DispatchedWorkerTaskReader`` answers one bit — "may a chat message still move
this task?" — and the whole of the ruling rests on it, so each of its four
answers is pinned here rather than only through the collaboration path that
consumes it. Real ``InMemoryTaskStore`` and real ``InMemoryAgentDirectory``:
the reader's answer is derived from a task row and a principal's role, and a
double for either would make the derivation untestable in the one way that
matters (that it reads the *assignee's* role, not the reporter's).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.infrastructure import InMemoryAgentDirectory
from repomesh.modules.task_orchestration import DispatchedWorkerTaskReader, InMemoryTaskStore
from repomesh.modules.task_orchestration.domain import Task


async def _team(directory: InMemoryAgentDirectory):
    """One organization leader, one repository leader, one worker.

    Built through the real ``CreateRepositoryAgentTeam`` rather than by
    inserting principals: a worker without a leader is refused by the
    directory, and the hierarchy that refusal enforces is the same hierarchy
    the reader's answer depends on.
    """

    organization_id = uuid4()
    organization_leader = await CreateAgent(directory).execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="org-leader",
        ),
        idempotency_key="org-leader",
    )
    return await CreateRepositoryAgentTeam(directory).execute(
        CreateRepositoryAgentTeamRequest(
            organization_id=organization_id,
            organization_leader_id=organization_leader.principal.id,
            repository_id=uuid4(),
            leader_agentteams_resource_name="pricing-leader",
            worker_agentteams_resource_names=("pricing-worker",),
            worker_responsibility_paths=("src/pricing/**",),
        ),
        idempotency_key="repository-team",
    )


def _task(assignee_id) -> Task:
    return Task(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=assignee_id,
        title="Implement pricing",
        instruction="Do it.",
        acceptance=("Tests pass",),
    )


@pytest.mark.asyncio
async def test_a_dispatched_worker_task_refuses_room_reports() -> None:
    """The closed case. Its truth is the Runner's event stream, full stop."""

    directory = InMemoryAgentDirectory()
    team = await _team(directory)
    tasks = InMemoryTaskStore()
    task = _task(team.workers[0].id)
    await tasks.add(task, idempotency_key="dispatch-1", request_fingerprint="sha256:x")

    reader = DispatchedWorkerTaskReader(tasks, directory)

    assert await reader.accepts_room_report(task.id) is False


@pytest.mark.asyncio
async def test_a_leader_task_still_accepts_room_reports() -> None:
    """The open case D-7 deliberately leaves alone.

    A repository leader's task carries no published package — publication is
    the WORKER branch of the dispatch — so nothing about it is a coding task,
    and the path PR 7 will eventually replace stays working until it does.
    """

    directory = InMemoryAgentDirectory()
    team = await _team(directory)
    tasks = InMemoryTaskStore()
    task = _task(team.leader.id)
    await tasks.add(task, idempotency_key="dispatch-2", request_fingerprint="sha256:x")

    reader = DispatchedWorkerTaskReader(tasks, directory)

    assert await reader.accepts_room_report(task.id) is True


@pytest.mark.asyncio
async def test_an_unknown_task_is_left_to_the_report_paths_own_refusal() -> None:
    """A wrong task id must produce the report path's error, not silence.

    Answering False here would turn "you named a task that does not exist"
    into an ignored message with an audit row blaming a coding task — an
    operator reading that ledger would be told the wrong thing about the wrong
    task.
    """

    reader = DispatchedWorkerTaskReader(InMemoryTaskStore(), InMemoryAgentDirectory())

    assert await reader.accepts_room_report(uuid4()) is True


@pytest.mark.asyncio
async def test_a_worker_task_that_was_never_dispatched_is_not_a_coding_task_yet() -> None:
    """No assignment key means nothing was published and nothing was announced.

    The reader answers from the two facts that make publication true; a row
    written straight into the store has neither, and claiming otherwise would
    have the ledger record refusals for tasks that were never in a room.
    """

    directory = InMemoryAgentDirectory()
    team = await _team(directory)
    tasks = InMemoryTaskStore()
    task = _task(team.workers[0].id)
    tasks.tasks[task.id] = task  # written without an assignment key, on purpose

    reader = DispatchedWorkerTaskReader(tasks, directory)

    assert await reader.accepts_room_report(task.id) is True
