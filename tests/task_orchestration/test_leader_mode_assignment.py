"""Batch assignment forking on a team's decomposition mode (adjudication D-2).

The behaviour this pins is a *negative* one, and it is the whole point of the
slice: in ``leader`` mode the platform assigns the repository task, tells the
leader where to plan, and then **stops**. No worker task, no decomposer call,
nothing dispatched. The server has stopped guessing what the work breaks down
into, which is what makes the leader's Engineering Spec the leader's.

Server mode is checked here too, in the same file and against the same
harness, because "unchanged" is a claim about a comparison — the existing
suite next door proves server mode still works, and these prove the fork did
not quietly move it.

The harness comes from ``test_plan_execution``: reproducing a topology, a
directory, an assigner and a task store here would be a second harness that
could drift from the one every other batch-assignment test uses.
"""

from uuid import uuid4

import pytest

from repomesh.modules.collaboration.contracts import CollaborationMessageKind
from repomesh.modules.task_orchestration.application import derive_allowed_paths
from repomesh.modules.task_orchestration.contracts import LeaderAssignmentPhase
from repomesh.modules.task_orchestration.domain import TaskDenied
from task_orchestration.test_plan_execution import Environment

TESTS = ("uv run pytest -q tests/pricing",)
TEST_PATHS = ("tests/pricing/",)


async def start_leader_mode_round(
    *, worker_paths: tuple[str, ...] = ("src/pricing/",)
) -> Environment:
    environment = Environment(worker_paths=worker_paths, leader_mode_repositories=(0,))
    plan = environment.plan(((0,),), tests={0: TESTS}, test_paths={0: TEST_PATHS})
    await environment.advancer.start(plan, idempotency_key="round-1")
    return environment


async def test_leader_mode_assigns_the_leader_task_and_stops() -> None:
    environment = await start_leader_mode_round()

    leader_task_id = await environment.leader_task_id(
        (await environment.plans.list_all())[0].id, 0, 0
    )
    leader_task = await environment.tasks.get(leader_task_id)
    assert leader_task is not None
    assert leader_task.assignee_agent_id == environment.leader_ids[0]
    # The negative half: nothing under the leader task, and no permit either.
    assert await environment.tasks.list_by_parent(leader_task_id) == ()
    assert environment.recorded_specifications == []
    # Exactly one assignment was made, and it is the leader's.
    assert [command.assignee_agent_id for command, _ in environment.assigner.commands] == [
        environment.leader_ids[0]
    ]


async def test_leader_mode_records_a_planning_assignment() -> None:
    environment = await start_leader_mode_round()
    leader_task_id = await environment.leader_task_id(
        (await environment.plans.list_all())[0].id, 0, 0
    )

    assignment = await environment.leader_assignments.get(leader_task_id)

    assert assignment is not None
    assert assignment.phase is LeaderAssignmentPhase.PLANNING
    assert assignment.project_id == environment.project_id
    assert assignment.repository_id == environment.repository_ids[0]
    assert assignment.leader_agent_id == environment.leader_ids[0]
    assert [entry.worker_agent_id for entry in assignment.worker_roster] == [
        environment.worker_ids[0]
    ]
    assert assignment.safety_envelope.test_commands == TESTS
    assert assignment.safety_envelope.test_paths == TEST_PATHS


async def test_the_recorded_envelope_is_the_permit_derivation_not_a_second_one() -> None:
    """The bounds handed out are the bounds the clamp will use.

    Pinned as an equality against ``derive_allowed_paths`` rather than against
    a literal, because a literal would pass just as happily if leader mode grew
    a copy of the expression: what must hold is that the two callers are one
    function, and the failure this guards against is them drifting apart.
    """

    worker_paths = ("src/pricing/", "src/quotes/")
    environment = await start_leader_mode_round(worker_paths=worker_paths)
    leader_task_id = await environment.leader_task_id(
        (await environment.plans.list_all())[0].id, 0, 0
    )

    assignment = await environment.leader_assignments.get(leader_task_id)

    assert assignment is not None
    assert assignment.safety_envelope.allowed_path_roots == derive_allowed_paths(
        worker_paths, TEST_PATHS
    )
    # And the same derivation is what a server-mode permit is written from, so
    # the envelope cannot bound work the permit would refuse.
    server_side = Environment(worker_paths=worker_paths)
    server_plan = server_side.plan(((0,),), tests={0: TESTS}, test_paths={0: TEST_PATHS})
    await server_side.advancer.start(server_plan, idempotency_key="round-1")
    assert server_side.recorded_specifications[0].allowed_paths == (
        assignment.safety_envelope.allowed_path_roots
    )


async def test_a_worker_with_no_responsibility_paths_widens_the_envelope_to_everything() -> None:
    environment = await start_leader_mode_round(worker_paths=())
    leader_task_id = await environment.leader_task_id(
        (await environment.plans.list_all())[0].id, 0, 0
    )

    assignment = await environment.leader_assignments.get(leader_task_id)

    assert assignment is not None
    # ``minItems: 1`` on the wire, and honest: an unbounded worker is not a
    # worker with no ground.
    assert assignment.safety_envelope.allowed_path_roots == ("**",)


async def test_leader_mode_tells_the_leader_where_to_plan() -> None:
    environment = await start_leader_mode_round()
    leader_task_id = await environment.leader_task_id(
        (await environment.plans.list_all())[0].id, 0, 0
    )

    assert len(environment.collaboration.sent) == 1
    command, idempotency_key = environment.collaboration.sent[0]
    # Sent by the plan's author to the repository leader, which is the pair the
    # collaboration router resolves to the team's leader DM room.
    assert command.sender_agent_id == environment.organization_leader_id
    assert command.recipient_agent_id == environment.leader_ids[0]
    assert command.kind is CollaborationMessageKind.DECISION
    assert command.task_id == leader_task_id
    # The body carries the route, and nothing else: no workspace, no disk path
    # (adjudication D-8), no token.
    assert f"/api/v1/agent-actions/leader/assignments/{leader_task_id}" in command.body
    assert "\\" not in command.body
    assert "Bearer" not in command.body
    # Keyed off the batch's own prefix, so a replay finds the delivered message.
    assert idempotency_key.startswith("round-1:b0:")
    assert len(idempotency_key) <= 200


async def test_replaying_the_round_neither_duplicates_the_notice_nor_moves_the_envelope() -> None:
    environment = await start_leader_mode_round()
    plan_id = (await environment.plans.list_all())[0].id
    leader_task_id = await environment.leader_task_id(plan_id, 0, 0)
    before = await environment.leader_assignments.get(leader_task_id)

    plan = await environment.plans.get(plan_id)
    assert plan is not None
    await environment.advancer.start(plan, idempotency_key="round-1")

    assert await environment.leader_assignments.get(leader_task_id) == before
    assert await environment.tasks.list_by_parent(leader_task_id) == ()
    # The gateway is asked twice; one delivered message and one replay is the
    # send path's own idempotency, not this caller's business to prevent.
    keys = {key for _, key in environment.collaboration.sent}
    assert len(keys) == 1


async def test_server_mode_still_decomposes_and_records_no_assignment() -> None:
    """The unchanged half, checked through the same harness as the changed one."""

    environment = Environment(leader_mode_repositories=())
    plan = environment.plan(((0,),), tests={0: TESTS}, test_paths={0: TEST_PATHS})
    await environment.advancer.start(plan, idempotency_key="round-1")

    leader_task_id = await environment.leader_task_id(
        (await environment.plans.list_all())[0].id, 0, 0
    )
    children = await environment.tasks.list_by_parent(leader_task_id)
    assert [child.assignee_agent_id for child in children] == [environment.worker_ids[0]]
    assert len(environment.recorded_specifications) == 1
    assert await environment.leader_assignments.get(leader_task_id) is None
    assert environment.collaboration.sent == []


async def test_one_batch_may_hold_both_modes_at_once() -> None:
    """Two repositories, two modes, one batch: the fork is per team, not per round."""

    environment = Environment(repository_count=2, leader_mode_repositories=(1,))
    plan = environment.plan(((0, 1),), tests={0: TESTS, 1: TESTS})
    await environment.advancer.start(plan, idempotency_key="round-1")

    plan_id = (await environment.plans.list_all())[0].id
    server_side = await environment.leader_task_id(plan_id, 0, 0)
    leader_side = await environment.leader_task_id(plan_id, 0, 1)

    assert len(await environment.tasks.list_by_parent(server_side)) == 1
    assert await environment.tasks.list_by_parent(leader_side) == ()
    assert await environment.leader_assignments.get(server_side) is None
    assert await environment.leader_assignments.get(leader_side) is not None
    assert [command.task_id for command, _ in environment.collaboration.sent] == [leader_side]


async def test_a_leader_mode_team_with_no_active_worker_is_refused_not_parked() -> None:
    """A roster the frozen contract cannot carry is a refusal, not a thin package."""

    environment = Environment(leader_mode_repositories=(0,))
    # The worker principal disappears between topology and directory — the
    # shape a de-provisioned member leaves behind.
    environment.directory._principals.pop(environment.worker_ids[0])
    plan = environment.plan(((0,),))

    with pytest.raises(TaskDenied):
        await environment.advancer.start(plan, idempotency_key="round-1")

    assert environment.collaboration.sent == []


async def test_an_unknown_leader_task_has_no_assignment() -> None:
    environment = await start_leader_mode_round()
    assert await environment.leader_assignments.get(uuid4()) is None
