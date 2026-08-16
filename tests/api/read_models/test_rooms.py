"""Contract v0.2 §5: room list, room stream and the per-repository plan sheet.

The load-bearing rule here is §5.2: only `source == "message"` happened inside
the room. Everything else is a console projection and must be renderable as a
system entry, so those items carry no `message` payload at all.
"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from repomesh.api.read_models.sources import PlanSnapshotData, RepositorySpecData
from repomesh.modules.collaboration.contracts import (
    CollaborationDeliveryStatus,
    CollaborationMessageKind,
    CollaborationMessageView,
)
from repomesh.modules.delivery.contracts import (
    ChangeSetStatus,
    GovernanceDecisionKind,
    GovernanceDecisionView,
    SCMObservationSource,
    SCMObservationStatus,
    SCMObservationView,
)
from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatus, TaskStatus

from .test_issues import StubTopology, _topology
from .test_service_stubs import (
    RunnerEventData,
    StubArchives,
    StubChangeSets,
    StubMessages,
    StubObservations,
    StubPlans,
    StubRunnerEvents,
    StubSnapshots,
    StubTasks,
    _manual_intervention_change_set,
    _plan,
    _service,
    _worker,
)

T0 = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)


class StubRepositories:
    def __init__(self, mapping: dict[UUID, str]) -> None:
        self.mapping = mapping

    async def list(self):
        from repomesh.api.read_models.sources import RepositoryData

        return tuple(
            RepositoryData(id=key, name=name, description="") for key, name in self.mapping.items()
        )


class StubSpecifications:
    def __init__(self, spec: RepositorySpecData | None = None) -> None:
        self.spec = spec

    async def engineering_contract(self, project_id: UUID):
        return None

    async def repository_spec(self, project_id: UUID, repository_id: UUID):
        return self.spec


def _message(
    *,
    project_id: UUID,
    repository_id: UUID,
    room_id: str,
    task_id: UUID | None,
    at: datetime,
    subject: str = "任务指派",
) -> CollaborationMessageView:
    return CollaborationMessageView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=project_id,
        repository_id=repository_id,
        task_id=task_id,
        sender_agent_id=uuid4(),
        recipient_agent_id=uuid4(),
        kind=CollaborationMessageKind.TASK_ASSIGNMENT,
        subject=subject,
        body="body",
        room_id=room_id,
        status=CollaborationDeliveryStatus.DELIVERED,
        event_id="$evt",
        correlation_id=uuid4(),
        created_at=at,
    )


def _snapshot(project_id: UUID, plan_id: UUID | None, *, batches, dag) -> PlanSnapshotData:
    return PlanSnapshotData(
        id=uuid4(),
        project_id=project_id,
        plan_version=2,
        created_at=T0,
        engineering_spec="spec",
        requirement_text="req",
        execution_batches=batches,
        task_dag=dag,
        execution_plan_id=plan_id,
        created_by_agent_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_rooms_are_two_per_team_and_empty_rooms_stay_empty() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    plan = _plan(project_id, repository_id, uuid4(), ExecutionPlanStatus.IN_PROGRESS)
    worker = replace(_worker(project_id, repository_id, uuid4()), status=TaskStatus.IN_PROGRESS)
    topology = StubTopology({project_id: _topology(project_id, repository_id)})
    team = topology.mapping[project_id].repository_teams[0]
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({}),
        StubArchives(),
        repositories=StubRepositories({repository_id: "repomesh-e2e-api"}),
        messages=StubMessages(
            _message(
                project_id=project_id,
                repository_id=repository_id,
                room_id=team.room_id,
                task_id=worker.id,
                at=T0,
            ),
            _message(
                project_id=project_id,
                repository_id=repository_id,
                room_id=team.room_id,
                task_id=worker.id,
                at=T0.replace(hour=11),
                subject="返工指派",
            ),
        ),
        topology=topology,
    )

    payload = await service.list_rooms(project_id)

    assert [room["kind"] for room in payload["rooms"]] == ["team_room", "leader_dm"]
    team_room, leader_dm = payload["rooms"]
    assert team_room["room_id"] == team.room_id
    assert leader_dm["room_id"] == team.leader_room_id
    assert team_room["repository_name"] == "repomesh-e2e-api"
    assert team_room["message_count"] == 2
    assert team_room["last_message"]["subject"] == "返工指派"  # newest, not first
    # §5.1: an empty room is reported empty, never padded with a placeholder.
    assert leader_dm["message_count"] == 0
    assert leader_dm["last_message"] is None
    # §5.3: live comes from an in-flight task, not from presence.
    assert team_room["live"] is True
    # Membership is per room kind: workers are in the team room, and the DM is
    # the repository leader talking to the organization leader.
    assert [member["role"] for member in team_room["members"]] == [
        "repository_leader",
        "worker",
    ]
    assert [member["role"] for member in leader_dm["members"]] == [
        "repository_leader",
        "organization_leader",
    ]
    assert leader_dm["members"][1]["agent_id"] == (
        topology.mapping[project_id].organization_leader_id
    )
    assert all(member["name"] == "worker-01" for member in team_room["members"])


@pytest.mark.asyncio
async def test_issue_without_a_team_has_no_rooms_but_is_not_a_404() -> None:
    project_id = uuid4()
    plan = _plan(project_id, uuid4(), uuid4(), ExecutionPlanStatus.COMPLETED)
    service = _service(
        StubPlans(plan), StubSnapshots(), StubTasks(), StubChangeSets({}), StubArchives()
    )

    assert await service.list_rooms(project_id) == {"rooms": []}
    assert await service.list_rooms(uuid4()) is None


@pytest.mark.asyncio
async def test_live_is_false_without_in_flight_work() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    plan = _plan(project_id, repository_id, uuid4(), ExecutionPlanStatus.COMPLETED)
    finished = replace(_worker(project_id, repository_id, uuid4()), status=TaskStatus.SUCCEEDED)
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(finished),
        StubChangeSets({}),
        StubArchives(),
        topology=StubTopology({project_id: _topology(project_id, repository_id)}),
    )

    assert all(not room["live"] for room in (await service.list_rooms(project_id))["rooms"])


def _governance_change_set(plan, repository_id: UUID, task_id: UUID):
    change_set = replace(
        _manual_intervention_change_set(plan, repository_id, task_id),
        status=ChangeSetStatus.DELIVERING,
        recovery_plans=(),
        governance_decisions=(
            GovernanceDecisionView(
                id=uuid4(),
                repository_id=repository_id,
                head_sha="a" * 40,
                decision=GovernanceDecisionKind.READY,
                decided_by_agent_id=uuid4(),
                reason="人工放行",
                decided_at=T0.replace(hour=12),
            ),
        ),
    )
    return change_set


@pytest.mark.asyncio
async def test_stream_separates_real_messages_from_console_projections() -> None:
    """§5.2: governance lands in the leaderDM, runner/gate in the teamRoom, and
    no projection may carry a message payload."""

    project_id = uuid4()
    repository_id = uuid4()
    plan = _plan(project_id, repository_id, uuid4(), ExecutionPlanStatus.COMPLETED)
    worker = _worker(project_id, repository_id, uuid4())
    change_set = _governance_change_set(plan, repository_id, worker.id)
    topology = StubTopology({project_id: _topology(project_id, repository_id)})
    team = topology.mapping[project_id].repository_teams[0]
    observation = SCMObservationView(
        id=uuid4(),
        provider="github",
        source=SCMObservationSource.WEBHOOK,
        external_id="obs-1",
        event_type="check_run.completed",
        payload={},
        payload_hash="0" * 64,
        status=SCMObservationStatus.PROCESSED,
        change_set_id=change_set.id,
        repository_id=repository_id,
        attempts=1,
        version=1,
        last_error=None,
        observed_at=T0.replace(hour=10),
        received_at=T0.replace(hour=10),
        claimed_at=None,
        processed_at=None,
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({plan.id: change_set}),
        StubArchives(),
        repositories=StubRepositories({repository_id: "repomesh-e2e-api"}),
        runner_events=StubRunnerEvents(
            RunnerEventData(
                event_id=uuid4(),
                run_id=uuid4(),
                sequence=1,
                event_type="runner.completed",
                occurred_at=T0.replace(hour=9, minute=30),
                task_id=worker.id,
                repository_id=repository_id,
            )
        ),
        messages=StubMessages(
            _message(
                project_id=project_id,
                repository_id=repository_id,
                room_id=team.room_id,
                task_id=worker.id,
                at=T0,
            )
        ),
        observations=StubObservations(observation),
        topology=topology,
    )

    team_stream = await service.room_stream(team.room_id)
    leader_stream = await service.room_stream(team.leader_room_id)

    assert [item["source"] for item in team_stream["items"]] == [
        "message",
        "runner",
        "gate",
    ]
    # Governance is a leader-layer fact: it must not appear in the team room.
    assert [item["source"] for item in leader_stream["items"]] == ["governance"]
    assert leader_stream["items"][0]["text"].startswith("治理决策 ready")
    assert leader_stream["items"][0]["payload_ref"].startswith("governance-decision:")

    for stream in (team_stream, leader_stream):
        for item in stream["items"]:
            if item["source"] == "message":
                assert item["message"]["room_id"] == item["room_id"]
                assert item["message"]["direction"] == "leader_to_worker"
            else:
                # The hard constraint: nothing to render as a chat bubble.
                assert item["message"] is None
                assert item["payload_ref"]

    assert await service.room_stream("!nobody:matrix.local") is None


@pytest.mark.asyncio
async def test_stream_pages_with_a_stable_offset_cursor() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    plan = _plan(project_id, repository_id, uuid4(), ExecutionPlanStatus.COMPLETED)
    worker = _worker(project_id, repository_id, uuid4())
    topology = StubTopology({project_id: _topology(project_id, repository_id)})
    team = topology.mapping[project_id].repository_teams[0]
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({}),
        StubArchives(),
        messages=StubMessages(
            *(
                _message(
                    project_id=project_id,
                    repository_id=repository_id,
                    room_id=team.room_id,
                    task_id=worker.id,
                    at=T0.replace(minute=minute),
                    subject=f"消息 {minute}",
                )
                for minute in (0, 10, 20)
            )
        ),
        topology=topology,
    )

    whole = await service.room_stream(team.room_id)
    first = await service.room_stream(team.room_id, limit=2)
    second = await service.room_stream(team.room_id, offset=2, limit=2)

    assert first["next_cursor"] == "2"
    assert second["next_cursor"] is None
    assert [item["payload_ref"] for item in first["items"] + second["items"]] == [
        item["payload_ref"] for item in whole["items"]
    ]


@pytest.mark.asyncio
async def test_repository_plan_projects_a_repository_grained_dag(caplog) -> None:
    project_id = uuid4()
    api, client = uuid4(), uuid4()
    plan = _plan(project_id, api, uuid4(), ExecutionPlanStatus.COMPLETED)
    spec = RepositorySpecData(
        specification_id=uuid4(),
        kind="repository",
        status="frozen",
        revision=3,
        goal="Expose discount_amount",
        acceptance=("Old clients keep working",),
        allowed_paths=("src/pricing/**",),
        forbidden_paths=("src/pricing/legacy/**",),
        tests=("pytest",),
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(
            _snapshot(
                project_id,
                plan.id,
                batches=(("repomesh-e2e-api",), ("repomesh-e2e-client",)),
                dag=(
                    {"repository": "repomesh-e2e-api", "depends_on": []},
                    {
                        "repository": "repomesh-e2e-client",
                        "depends_on": ["repomesh-e2e-api", "unknown-repo"],
                    },
                ),
            )
        ),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        repositories=StubRepositories({api: "repomesh-e2e-api", client: "repomesh-e2e-client"}),
        specifications=StubSpecifications(spec),
    )

    with caplog.at_level("WARNING"):
        payload = await service.repository_plan(project_id, client)

    assert payload["plan_version"] == 2
    assert payload["dag"]["granularity"] == "repository"
    assert payload["dag"]["edge_source"] == "task_dag.depends_on"
    assert payload["dag"]["nodes"] == [
        {
            "repository_id": api,
            "name": "repomesh-e2e-api",
            "batch_index": 0,
            "is_focus": False,
        },
        {
            "repository_id": client,
            "name": "repomesh-e2e-client",
            "batch_index": 1,
            "is_focus": True,
        },
    ]
    # The unresolvable dependency name is dropped instead of becoming a null edge.
    assert payload["dag"]["edges"] == [{"from_repository_id": api, "to_repository_id": client}]
    # ...and the drop is reported, because a DAG quietly missing an edge reads
    # as a complete one.
    assert "dropped 1 unresolvable DAG edge" in caplog.text
    assert "unknown-repo" in caplog.text
    # The log is for operators; the counts are for the person looking at the
    # picture, who cannot see the log (v0.2 §7.2's reserved self-report
    # fields, taken up now that a DAG panel actually renders this).
    assert payload["dag"]["unresolved_node_count"] == 0
    assert payload["dag"]["dropped_edge_unresolved_count"] == 1
    assert payload["dag"]["dropped_edge_off_batch_count"] == 0
    assert payload["execution_batches"] == [["repomesh-e2e-api"], ["repomesh-e2e-client"]]
    assert payload["spec"]["status"] == "frozen"
    assert payload["spec"]["revision"] == 3
    assert payload["engineering_contract"] is None
    assert await service.repository_plan(uuid4(), client) is None
    # A repository that belongs to a different issue is a 404, not this
    # issue's sheet with is_focus false everywhere: §5.4 tells the frontend to
    # read spec:null as "no spec of its own", so the wrong page rendered as a
    # perfectly normal one.
    assert await service.repository_plan(project_id, uuid4()) is None


@pytest.mark.asyncio
async def test_the_dag_counts_separate_the_two_reasons_an_edge_is_dropped() -> None:
    """Two causes, two counts — they need different responses.

    An endpoint the catalog cannot resolve means a missing catalog row. An
    endpoint that is in no batch means the planning output disagrees with
    itself: nodes come from ``execution_batches``, edges from ``task_dag``,
    and nothing makes them agree. Collapsing both into one number leaves "why
    is an edge missing" unanswerable, which is the question the count exists
    to answer.

    An unresolved *node* is also counted, and is a third thing again: unlike a
    dropped edge it stays in the picture, drawn with a null id.
    """

    project_id = uuid4()
    api, client = uuid4(), uuid4()
    plan = _plan(project_id, api, uuid4(), ExecutionPlanStatus.COMPLETED)
    service = _service(
        StubPlans(plan),
        StubSnapshots(
            _snapshot(
                project_id,
                plan.id,
                # "ghost-repo" is batched but has no catalog row; "off-batch"
                # is in the catalog but in no batch.
                batches=(("repomesh-e2e-api", "ghost-repo"), ("repomesh-e2e-client",)),
                dag=(
                    {"repository": "repomesh-e2e-api", "depends_on": []},
                    {
                        "repository": "repomesh-e2e-client",
                        "depends_on": [
                            "repomesh-e2e-api",
                            "unknown-repo",
                            "off-batch",
                        ],
                    },
                ),
            )
        ),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        repositories=StubRepositories(
            {
                api: "repomesh-e2e-api",
                client: "repomesh-e2e-client",
                uuid4(): "off-batch",
            }
        ),
    )

    payload = await service.repository_plan(project_id, client)

    assert payload["dag"]["edges"] == [
        {"from_repository_id": api, "to_repository_id": client}
    ]
    assert payload["dag"]["unresolved_node_count"] == 1
    assert payload["dag"]["dropped_edge_unresolved_count"] == 1
    assert payload["dag"]["dropped_edge_off_batch_count"] == 1
    # The unresolved node is still drawn — dropping it would leave a hole in
    # the batch — so the count is not derivable from the node list length.
    assert [node["name"] for node in payload["dag"]["nodes"]] == [
        "repomesh-e2e-api",
        "ghost-repo",
        "repomesh-e2e-client",
    ]


@pytest.mark.asyncio
async def test_a_name_shared_with_another_issue_does_not_steal_the_node(caplog) -> None:
    """repositories.name has no unique constraint and holds the short name.

    Two owners' `api` are both legitimate rows, and a plain name->id map
    resolved to whichever came last. The issue's own repository has to win, or
    nodes, edges and is_focus all point at somebody else's repository while
    the page looks perfectly ordinary.
    """

    project_id = uuid4()
    mine, stranger = uuid4(), uuid4()
    plan = _plan(project_id, mine, uuid4(), ExecutionPlanStatus.COMPLETED)
    service = _service(
        StubPlans(plan),
        StubSnapshots(
            _snapshot(
                project_id,
                plan.id,
                batches=(("api",),),
                dag=({"repository": "api", "depends_on": []},),
            )
        ),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        # The stranger is listed after mine, so last-write-wins picked it.
        repositories=StubRepositories({mine: "api", stranger: "api"}),
    )

    payload = await service.repository_plan(project_id, mine)

    assert payload["dag"]["nodes"][0]["repository_id"] == mine
    assert payload["dag"]["nodes"][0]["is_focus"] is True


@pytest.mark.asyncio
async def test_edges_never_point_at_a_node_the_layout_did_not_draw(caplog) -> None:
    """Nodes come from execution_batches, edges from task_dag.

    §5.5 reads them as one graph but nothing made them agree, so a repository
    dropped from the batches while its dependency survived produced an edge
    into empty canvas. Dropping it is the same treatment §7.2 already gives an
    unresolvable name, and it is logged for the same reason.
    """

    project_id = uuid4()
    api, client = uuid4(), uuid4()
    plan = _plan(project_id, api, uuid4(), ExecutionPlanStatus.COMPLETED)
    service = _service(
        StubPlans(plan),
        StubSnapshots(
            _snapshot(
                project_id,
                plan.id,
                # client is known to the catalog but was left out of the batches
                batches=(("repomesh-e2e-api",),),
                dag=(
                    {
                        "repository": "repomesh-e2e-client",
                        "depends_on": ["repomesh-e2e-api"],
                    },
                ),
            )
        ),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        repositories=StubRepositories({api: "repomesh-e2e-api", client: "repomesh-e2e-client"}),
    )

    with caplog.at_level("WARNING"):
        payload = await service.repository_plan(project_id, api)

    assert [node["name"] for node in payload["dag"]["nodes"]] == ["repomesh-e2e-api"]
    assert payload["dag"]["edges"] == []
    assert "not in any batch" in caplog.text


@pytest.mark.asyncio
async def test_a_node_with_no_catalog_match_is_reported_not_just_nulled(caplog) -> None:
    """The node stays — dropping it would leave a hole in its batch — but
    §7.2's "never truncate silently" covers nodes as much as edges, and only
    the edges were reporting."""

    project_id = uuid4()
    api = uuid4()
    plan = _plan(project_id, api, uuid4(), ExecutionPlanStatus.COMPLETED)
    service = _service(
        StubPlans(plan),
        StubSnapshots(
            _snapshot(
                project_id,
                plan.id,
                batches=(("repomesh-e2e-api",), ("retired-repo",)),
                dag=({"repository": "repomesh-e2e-api", "depends_on": []},),
            )
        ),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        repositories=StubRepositories({api: "repomesh-e2e-api"}),
    )

    with caplog.at_level("WARNING"):
        payload = await service.repository_plan(project_id, api)

    retired = [n for n in payload["dag"]["nodes"] if n["name"] == "retired-repo"][0]
    assert retired["repository_id"] is None
    assert retired["is_focus"] is False
    assert "no catalog match" in caplog.text
    assert "retired-repo" in caplog.text
