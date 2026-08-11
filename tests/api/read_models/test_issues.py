"""Contract v0.2 §2/§3: issue-grained read model.

The six-rule state ladder and the phase selection rule are pure functions, so
they are asserted directly; the aggregation is driven through the same source
stubs the v0.1 service tests use.
"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from repomesh.api.read_models import DeliveryPhase, derive_issue_state, select_issue_phase
from repomesh.api.read_models.sources import PlanSnapshotData
from repomesh.modules.delivery.contracts import ChangeSetStatus
from repomesh.modules.project.contracts import (
    CodeAccessLevel,
    HumanProjectGrantView,
    HumanProjectRole,
    ProjectAgentTopologyView,
    ProjectCheckpoint,
    ProjectExecutionMode,
    ProjectOperationalStatus,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
)
from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatus

from .test_service_stubs import (
    StubArchives,
    StubChangeSets,
    StubPlans,
    StubSnapshots,
    StubTasks,
    _manual_intervention_change_set,
    _plan,
    _service,
    _worker,
)

T0 = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)


# ------------------------------------------------------------------ §2.1/§2.2


def _state(**overrides) -> str:
    kwargs = {
        "operational_status": ProjectOperationalStatus.ACTIVE,
        "has_active_round": False,
        "has_open_change_set": False,
        "has_draft": False,
        "has_rounds": False,
    }
    return derive_issue_state(**{**kwargs, **overrides}).value


def test_state_follows_the_six_rules_in_order() -> None:
    # 1. cancelled wins over every open signal below it.
    assert (
        _state(
            operational_status=ProjectOperationalStatus.CANCELLED,
            has_active_round=True,
            has_open_change_set=True,
            has_draft=True,
        )
        == "closed"
    )
    assert _state(has_active_round=True, has_rounds=True) == "open"  # 2
    assert _state(has_open_change_set=True, has_rounds=True) == "open"  # 3
    assert _state(has_draft=True) == "open"  # 4
    assert _state(has_rounds=True) == "closed"  # 5
    assert _state() == "open"  # 6: empty issue is a todo


def test_paused_does_not_close_an_issue() -> None:
    assert (
        _state(operational_status=ProjectOperationalStatus.PAUSED, has_rounds=True)
        == "closed"  # closed because all rounds are terminal, not because paused
    )
    assert (
        _state(
            operational_status=ProjectOperationalStatus.PAUSED,
            has_active_round=True,
            has_rounds=True,
        )
        == "open"
    )


def test_missing_topology_is_not_treated_as_cancelled() -> None:
    assert _state(operational_status=None, has_rounds=True) == "closed"
    assert _state(operational_status=None, has_draft=True) == "open"


def test_phase_selection_prefers_active_then_latest_then_draft() -> None:
    assert (
        select_issue_phase(
            active_round_phase=DeliveryPhase.EXECUTE,
            latest_round_phase=DeliveryPhase.DELIVERED,
            draft_phase=DeliveryPhase.PLAN,
        )
        is DeliveryPhase.EXECUTE
    )
    assert (
        select_issue_phase(
            active_round_phase=None,
            latest_round_phase=DeliveryPhase.DELIVERED,
            draft_phase=DeliveryPhase.PLAN,
        )
        is DeliveryPhase.DELIVERED
    )
    assert (
        select_issue_phase(
            active_round_phase=None, latest_round_phase=None, draft_phase=DeliveryPhase.PLAN
        )
        is DeliveryPhase.PLAN
    )
    # No round and no draft: requirement exists but was never planned.
    assert (
        select_issue_phase(
            active_round_phase=None, latest_round_phase=None, draft_phase=None
        )
        is DeliveryPhase.PLAN
    )


# ---------------------------------------------------------------- aggregation


def _snapshot(
    project_id: UUID,
    plan_id: UUID | None,
    *,
    version: int = 1,
    created_at: datetime = T0,
    agent_id: UUID | None = None,
) -> PlanSnapshotData:
    return PlanSnapshotData(
        id=uuid4(),
        project_id=project_id,
        plan_version=version,
        created_at=created_at,
        engineering_spec="spec",
        requirement_text="给 API 加上 discount_amount",
        execution_batches=(("repo",),),
        task_dag=(),
        execution_plan_id=plan_id,
        created_by_agent_id=agent_id,
    )


class StubTopology:
    def __init__(self, mapping: dict[UUID, ProjectAgentTopologyView] | None = None) -> None:
        self.mapping = mapping or {}

    async def get_view(self, project_id: UUID):
        return self.mapping.get(project_id)

    async def matrix_room_id(self, project_id: UUID):
        return None


def _topology(
    project_id: UUID,
    repository_id: UUID,
    *,
    operational_status=ProjectOperationalStatus.ACTIVE,
) -> ProjectAgentTopologyView:
    return ProjectAgentTopologyView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=project_id,
        organization_leader_id=uuid4(),
        repository_teams=(
            RepositoryTeamView(
                id=uuid4(),
                project_id=project_id,
                repository_id=repository_id,
                leader_agent_id=uuid4(),
                worker_agent_ids=(uuid4(),),
                agentteams_team_name="rm-team-1",
                runtime_status=ProjectTeamRuntimeStatus.READY,
                room_id="!team:local",
                leader_room_id="!leader:local",
            ),
        ),
        execution_mode=ProjectExecutionMode.SUPERVISED,
        required_checkpoints=frozenset(
            {ProjectCheckpoint.DELIVERY, ProjectCheckpoint.SPECIFICATION}
        ),
        human_grants=(
            HumanProjectGrantView(
                human_principal_id=uuid4(),
                role=HumanProjectRole.PROJECT_SUPERVISOR,
                code_access=CodeAccessLevel.READ,
                control_actions=frozenset(),
            ),
        ),
        operational_status=operational_status,
    )


class StubAgents:
    def __init__(self, organization_id: UUID | None = None) -> None:
        self._organization_id = organization_id

    async def name(self, agent_id: UUID):
        return "leader-01"

    async def organization_id(self, agent_id: UUID):
        return self._organization_id


def _issue_service(
    *, plans, snapshots, change_sets, topology=None, tasks=None, agents=None
):
    return _service(
        StubPlans(*plans),
        StubSnapshots(*snapshots),
        StubTasks(*(tasks or ())),
        StubChangeSets(change_sets),
        StubArchives(),
        topology=topology,
        agents=agents,
    )


@pytest.mark.asyncio
async def test_list_issues_derives_state_phase_and_default_open_filter() -> None:
    """A delivered round closes an issue; an in-flight change set keeps it open."""

    closed_project, open_project, draft_project = uuid4(), uuid4(), uuid4()
    repository_id = uuid4()
    delivered_plan = _plan(
        closed_project, repository_id, uuid4(), ExecutionPlanStatus.COMPLETED
    )
    delivering_plan = _plan(
        open_project, repository_id, uuid4(), ExecutionPlanStatus.COMPLETED
    )
    worker = _worker(open_project, repository_id, uuid4())
    delivered = replace(
        _manual_intervention_change_set(delivered_plan, repository_id, worker.id),
        status=ChangeSetStatus.DELIVERED,
        recovery_plans=(),
        updated_at=T0,
    )
    delivering = replace(
        _manual_intervention_change_set(delivering_plan, repository_id, worker.id),
        status=ChangeSetStatus.DELIVERING,
        recovery_plans=(),
        updated_at=T0.replace(hour=12),
    )
    opened_by = uuid4()
    service = _issue_service(
        plans=(delivered_plan, delivering_plan),
        snapshots=(
            _snapshot(closed_project, delivered_plan.id, agent_id=opened_by),
            _snapshot(open_project, delivering_plan.id),
            _snapshot(draft_project, None, created_at=T0.replace(hour=8)),
        ),
        change_sets={delivered_plan.id: delivered, delivering_plan.id: delivering},
    )

    default_listing = await service.list_issues()
    states = {item["issue_id"]: item["state"] for item in default_listing["issues"]}
    assert states == {open_project: "open", draft_project: "open"}
    assert default_listing["next_cursor"] is None
    # §2.3: newest activity first.
    assert [item["issue_id"] for item in default_listing["issues"]] == [
        open_project,
        draft_project,
    ]

    draft = next(i for i in default_listing["issues"] if i["issue_id"] == draft_project)
    assert draft["phase"] == "plan"
    assert draft["phase_note"] == "计划 v1 待物化"
    assert draft["round_count"] == 0
    assert draft["latest_round_id"] is None
    assert draft["active_round_id"] is None

    closed_listing = await service.list_issues(state="closed")
    closed = closed_listing["issues"][0]
    assert closed["issue_id"] == closed_project
    assert closed["phase"] == "delivered"
    assert closed["issue_key"] is None
    assert closed["opened_by_agent_id"] == opened_by
    assert closed["opened_at"] == T0
    assert closed["organization_id"] == delivered_plan.organization_id
    assert closed["repository_count"] == 1
    assert closed["title"] == "给 API 加上 discount_amount"

    assert len((await service.list_issues(state="all"))["issues"]) == 3


@pytest.mark.asyncio
async def test_active_round_keeps_issue_open_and_drives_phase() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    running = _plan(project_id, repository_id, uuid4(), ExecutionPlanStatus.IN_PROGRESS)
    service = _issue_service(
        plans=(running,),
        snapshots=(_snapshot(project_id, running.id),),
        change_sets={},
    )

    issue = (await service.list_issues())["issues"][0]

    assert issue["state"] == "open"
    assert issue["phase"] == "execute"
    assert issue["active_round_id"] == running.id
    assert issue["latest_round_id"] == running.id
    assert issue["updated_at"] == T0


@pytest.mark.asyncio
async def test_cancelled_topology_closes_an_otherwise_open_issue() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    running = _plan(project_id, repository_id, uuid4(), ExecutionPlanStatus.IN_PROGRESS)
    topology = StubTopology(
        {
            project_id: _topology(
                project_id,
                repository_id,
                operational_status=ProjectOperationalStatus.CANCELLED,
            )
        }
    )
    service = _issue_service(
        plans=(running,),
        snapshots=(_snapshot(project_id, running.id),),
        change_sets={},
        topology=topology,
    )

    issue = (await service.list_issues(state="all"))["issues"][0]

    assert issue["state"] == "closed"
    assert issue["operational_status"] == "cancelled"
    assert issue["execution_mode"] == "supervised"
    assert issue["team_count"] == 1


@pytest.mark.asyncio
async def test_opened_by_name_resolves_the_agent_resource_name() -> None:
    """§2 (追认): same source as v0.1 sender_name — a resource name, not a
    person's name — and null when there is nothing to resolve."""

    named_project, anonymous_project = uuid4(), uuid4()
    named_plan = _plan(named_project, uuid4(), uuid4(), ExecutionPlanStatus.IN_PROGRESS)
    anonymous_plan = _plan(
        anonymous_project, uuid4(), uuid4(), ExecutionPlanStatus.IN_PROGRESS
    )
    service = _issue_service(
        plans=(named_plan, anonymous_plan),
        snapshots=(
            _snapshot(named_project, named_plan.id, agent_id=uuid4()),
            # No created_by_agent_id: nothing to resolve, so no name.
            _snapshot(anonymous_project, anonymous_plan.id, agent_id=None),
        ),
        change_sets={},
        agents=StubAgents(),
    )

    by_issue = {
        item["issue_id"]: item for item in (await service.list_issues())["issues"]
    }

    assert by_issue[named_project]["opened_by_name"] == "leader-01"
    assert by_issue[anonymous_project]["opened_by_name"] is None
    assert by_issue[anonymous_project]["opened_by_agent_id"] is None
    # The overview spreads the same summary, so the field is on both endpoints.
    assert (await service.get_issue(named_project))["opened_by_name"] == "leader-01"


@pytest.mark.asyncio
async def test_topology_only_fields_degrade_to_null_without_a_team() -> None:
    """Honest data: no topology row means no fact, not a fabricated default."""

    project_id = uuid4()
    plan = _plan(project_id, uuid4(), uuid4(), ExecutionPlanStatus.COMPLETED)
    service = _issue_service(
        plans=(plan,), snapshots=(_snapshot(project_id, plan.id),), change_sets={}
    )

    issue = (await service.list_issues(state="all"))["issues"][0]

    assert issue["operational_status"] is None
    assert issue["execution_mode"] is None
    assert issue["team_count"] == 0


@pytest.mark.asyncio
async def test_tab_counts_ignore_state_and_paging_but_honour_the_workspace() -> None:
    """§2 (追认): the tab the caller is not looking at still shows a true total."""

    other_workspace_project = uuid4()
    open_projects = [uuid4() for _ in range(3)]
    closed_project = uuid4()
    plans, snapshots, change_sets = [], [], {}
    organization_id = None
    for index, project_id in enumerate(open_projects):
        plan = _plan(project_id, uuid4(), uuid4(), ExecutionPlanStatus.IN_PROGRESS)
        organization_id = organization_id or plan.organization_id
        plan = replace(plan, organization_id=organization_id)
        plans.append(plan)
        snapshots.append(
            _snapshot(project_id, plan.id, created_at=T0.replace(hour=10 + index))
        )
    closed_plan = replace(
        _plan(closed_project, uuid4(), uuid4(), ExecutionPlanStatus.COMPLETED),
        organization_id=organization_id,
    )
    plans.append(closed_plan)
    snapshots.append(_snapshot(closed_project, closed_plan.id))
    # A second workspace must not bleed into the first workspace's counts.
    foreign_plan = _plan(
        other_workspace_project, uuid4(), uuid4(), ExecutionPlanStatus.IN_PROGRESS
    )
    plans.append(foreign_plan)
    snapshots.append(_snapshot(other_workspace_project, foreign_plan.id))

    service = _issue_service(
        plans=tuple(plans), snapshots=tuple(snapshots), change_sets=change_sets
    )

    first = await service.list_issues(organization_id=organization_id, limit=2)

    assert len(first["issues"]) == 2  # page window
    assert first["open_count"] == 3  # unaffected by limit
    assert first["closed_count"] == 1  # unaffected by state=open
    assert first["next_cursor"] == "2"

    second = await service.list_issues(
        organization_id=organization_id, limit=2, offset=2
    )
    assert len(second["issues"]) == 1
    assert second["next_cursor"] is None
    assert (second["open_count"], second["closed_count"]) == (3, 1)
    # The two pages together are the whole open tab, in order, no repeats.
    paged = [item["issue_id"] for item in first["issues"] + second["issues"]]
    whole = [
        item["issue_id"]
        for item in (await service.list_issues(organization_id=organization_id))["issues"]
    ]
    assert paged == whole

    closed_tab = await service.list_issues(
        state="closed", organization_id=organization_id
    )
    assert [item["issue_id"] for item in closed_tab["issues"]] == [closed_project]
    assert (closed_tab["open_count"], closed_tab["closed_count"]) == (3, 1)


@pytest.mark.asyncio
async def test_draft_only_issue_inherits_its_opening_agent_organization() -> None:
    """Without this fallback the workspace filter would drop draft issues."""

    project_id = uuid4()
    organization_id = uuid4()
    opened_by = uuid4()
    service = _issue_service(
        plans=(),
        snapshots=(_snapshot(project_id, None, agent_id=opened_by),),
        change_sets={},
        agents=StubAgents(organization_id),
    )

    listing = await service.list_issues(organization_id=organization_id)

    assert [item["issue_id"] for item in listing["issues"]] == [project_id]
    assert listing["issues"][0]["organization_id"] == organization_id
    assert (await service.list_issues(organization_id=uuid4()))["issues"] == []


@pytest.mark.asyncio
async def test_organization_filter_and_unknown_issue() -> None:
    mine, theirs = uuid4(), uuid4()
    my_plan = _plan(mine, uuid4(), uuid4(), ExecutionPlanStatus.IN_PROGRESS)
    their_plan = _plan(theirs, uuid4(), uuid4(), ExecutionPlanStatus.IN_PROGRESS)
    service = _issue_service(
        plans=(my_plan, their_plan),
        snapshots=(_snapshot(mine, my_plan.id), _snapshot(theirs, their_plan.id)),
        change_sets={},
    )

    filtered = await service.list_issues(
        state="all", organization_id=my_plan.organization_id
    )

    assert [item["issue_id"] for item in filtered["issues"]] == [mine]
    assert await service.get_issue(uuid4()) is None


@pytest.mark.asyncio
async def test_issue_overview_adds_rounds_repositories_teams_and_grants() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    first = _plan(project_id, repository_id, uuid4(), ExecutionPlanStatus.FAILED)
    second = _plan(project_id, repository_id, uuid4(), ExecutionPlanStatus.IN_PROGRESS)
    topology = StubTopology({project_id: _topology(project_id, repository_id)})
    service = _issue_service(
        plans=(first, second),
        snapshots=(
            _snapshot(project_id, second.id, version=2, created_at=T0.replace(hour=14)),
            _snapshot(project_id, first.id, version=1, created_at=T0),
        ),
        change_sets={},
        topology=topology,
    )

    overview = await service.get_issue(project_id)

    assert overview["issue_id"] == project_id
    assert overview["round_count"] == 2
    # Chronological index: round 1 first.
    assert [r["plan_version"] for r in overview["rounds"]] == [1, 2]
    assert [r["round_id"] for r in overview["rounds"]] == [first.id, second.id]
    assert overview["rounds"][0]["status"] == "failed"
    assert overview["rounds"][1]["phase"] == "execute"
    assert overview["phase"] == "execute"  # active round wins over the failed one
    assert overview["opened_at"] == T0  # earliest snapshot, not the newest
    assert overview["repositories"] == [
        {
            "repository_id": repository_id,
            "name": str(repository_id)[:8],
            "team_id": topology.mapping[project_id].repository_teams[0].id,
            "role_in_issue": None,
        }
    ]
    assert overview["teams"][0]["agentteams_team_name"] == "rm-team-1"
    assert overview["teams"][0]["runtime_status"] == "ready"
    assert overview["human_grants"][0]["role"] == "project_supervisor"
    assert overview["human_grants"][0]["code_access"] == "read"
    assert overview["required_checkpoints"] == ["delivery", "specification"]
    assert overview["contract"] is None  # no engineering spec in this stub
