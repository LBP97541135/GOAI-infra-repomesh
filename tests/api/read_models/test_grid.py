"""Contract v0.2 §4: repository grid, team list and agent roster.

The load-bearing rules are in §4.4 and both are hard contract constraints, not
implementation advice: a runtime probe failure degrades **its own row only**, and
uptime_seconds / awake stay null because the controller exposes neither.
"""

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from repomesh.api.read_models import service as service_module
from repomesh.api.read_models.sources import RepositoryProfileData, RuntimeSnapshot
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.delivery.contracts import ChangeSetStatus
from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatus, TaskStatus

from .test_issues import StubTopology, _snapshot, _topology
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

T0 = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)


class StubProfiles:
    def __init__(self, *profiles: RepositoryProfileData) -> None:
        # Not named `profiles`: that would shadow the protocol method below.
        self.rows = profiles

    async def list(self):
        from repomesh.api.read_models.sources import RepositoryData

        return tuple(
            RepositoryData(id=item.id, name=item.name, description=item.description)
            for item in self.rows
        )

    async def profiles(self):
        return self.rows


def _profile(name: str, repository_id: UUID) -> RepositoryProfileData:
    return RepositoryProfileData(
        id=repository_id,
        name=name,
        url=f"https://github.example/{name}.git",
        description=f"{name} service",
        topics=("pricing",),
        languages=("python",),
        profiled_at=T0,
    )


class StubRoster:
    def __init__(self, *principals: AgentPrincipalView) -> None:
        self.principals = principals

    async def name(self, agent_id: UUID):
        return next(
            (item.agentteams_resource_name for item in self.principals if item.id == agent_id),
            None,
        )

    async def organization_id(self, agent_id: UUID):
        return None

    async def list_all(self):
        return self.principals


def _principal(
    role: AgentRole,
    name: str,
    *,
    agent_id: UUID | None = None,
    repository_id: UUID | None = None,
    leader_agent_id: UUID | None = None,
) -> AgentPrincipalView:
    return AgentPrincipalView(
        id=agent_id or uuid4(),
        organization_id=uuid4(),
        role=role,
        leader_agent_id=leader_agent_id,
        repository_id=repository_id,
        responsibility_paths=("src/**",) if repository_id else (),
        agentteams_resource_name=name,
        status=AgentPrincipalStatus.ACTIVE,
    )


class StubRuntime:
    """Controller stub: a name maps to a snapshot, an exception, or None (404)."""

    def __init__(self, outcomes: dict[str, object]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def _resolve(self, name: str):
        self.calls.append(name)
        outcome = self.outcomes.get(name, RuntimeSnapshot(phase="Running"))
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == "hang":
            await asyncio.sleep(10)
        return outcome or None

    async def worker(self, name: str):
        return await self._resolve(name)

    async def manager(self, name: str):
        return await self._resolve(name)

    async def team(self, name: str):
        return await self._resolve(name)


# ------------------------------------------------------------------------ §4.1


@pytest.mark.asyncio
async def test_repository_grid_counts_only_open_issues_and_active_tasks() -> None:
    open_project, closed_project = uuid4(), uuid4()
    api, idle = uuid4(), uuid4()
    open_plan = _plan(open_project, api, uuid4(), ExecutionPlanStatus.IN_PROGRESS)
    closed_plan = _plan(closed_project, api, uuid4(), ExecutionPlanStatus.COMPLETED)
    worker = replace(_worker(open_project, api, uuid4()), status=TaskStatus.IN_PROGRESS)
    done = replace(_worker(closed_project, api, uuid4()), status=TaskStatus.SUCCEEDED)
    delivered = replace(
        _manual_intervention_change_set(closed_plan, api, done.id),
        status=ChangeSetStatus.DELIVERED,
        recovery_plans=(),
        updated_at=T0.replace(hour=15),
    )
    service = _service(
        StubPlans(open_plan, closed_plan),
        StubSnapshots(
            _snapshot(open_project, open_plan.id),
            _snapshot(closed_project, closed_plan.id),
        ),
        StubTasks(worker, done),
        StubChangeSets({closed_plan.id: delivered}),
        StubArchives(),
        repositories=StubProfiles(_profile("repomesh-e2e-api", api), _profile("idle", idle)),
        topology=StubTopology({open_project: _topology(open_project, api)}),
    )

    payload = await service.list_repositories()

    by_name = {item["name"]: item for item in payload["repositories"]}
    assert list(by_name) == ["idle", "repomesh-e2e-api"]  # sorted by name
    grid = by_name["repomesh-e2e-api"]
    assert grid["url"].endswith("repomesh-e2e-api.git")
    assert grid["topics"] == ["pricing"] and grid["languages"] == ["python"]
    # Two issues carry this repository, but only one of them is open.
    assert grid["open_issue_count"] == 1
    assert grid["active_task_count"] == 1  # the succeeded task does not count
    assert grid["last_delivery_at"] == T0.replace(hour=15)
    assert grid["resident_team_count"] == 1
    assert grid["teams"][0]["issue_id"] == open_project
    assert grid["teams"][0]["runtime_status"] == "ready"

    # A repository nobody works on reports zeros and null, not absent keys.
    assert by_name["idle"]["open_issue_count"] == 0
    assert by_name["idle"]["resident_team_count"] == 0
    assert by_name["idle"]["last_delivery_at"] is None
    assert by_name["idle"]["teams"] == []


# ------------------------------------------------------------------------ §4.2


@pytest.mark.asyncio
async def test_team_list_keeps_formation_status_and_live_phase_apart() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    plan = _plan(project_id, repository_id, uuid4(), ExecutionPlanStatus.COMPLETED)
    topology = StubTopology({project_id: _topology(project_id, repository_id)})
    team = topology.mapping[project_id].repository_teams[0]
    runtime = StubRuntime(
        {
            team.agentteams_team_name: RuntimeSnapshot(
                phase="Degraded", ready_workers=1, total_workers=2
            )
        }
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        repositories=StubProfiles(_profile("repomesh-e2e-api", repository_id)),
        topology=topology,
        runtime=runtime,
    )

    payload = await service.list_teams()

    item = payload["teams"][0]
    # Formation history says ready; the controller says Degraded right now. Both
    # are facts and neither overwrites the other.
    assert item["runtime_status"] == "ready"
    assert item["runtime"] == {
        "reachable": True,
        "phase": "Degraded",
        "ready_workers": 1,
        "total_workers": 2,
    }
    assert item["team_room_id"] == team.room_id
    assert item["leader_room_id"] == team.leader_room_id
    assert item["leader"]["role"] == "repository_leader"
    assert [worker["role"] for worker in item["workers"]] == ["worker"]
    assert item["repository_name"] == "repomesh-e2e-api"


@pytest.mark.asyncio
async def test_with_runtime_false_makes_no_controller_call() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    runtime = StubRuntime({})
    service = _service(
        StubPlans(_plan(project_id, repository_id, uuid4(), ExecutionPlanStatus.COMPLETED)),
        StubSnapshots(),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        topology=StubTopology({project_id: _topology(project_id, repository_id)}),
        runtime=runtime,
    )

    payload = await service.list_teams(with_runtime=False)

    assert payload["teams"][0]["runtime"] is None
    assert runtime.calls == []  # opting out must not cost a request


# ------------------------------------------------------------------------ §4.3


@pytest.mark.asyncio
async def test_agent_roster_resolves_membership_and_never_invents_uptime() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    topology_view = _topology(project_id, repository_id)
    team = topology_view.repository_teams[0]
    manager = _principal(
        AgentRole.ORGANIZATION_LEADER,
        "console-demo-org-leader",
        agent_id=topology_view.organization_leader_id,
    )
    leader = _principal(
        AgentRole.REPOSITORY_LEADER,
        "rm-leader-a-api",
        agent_id=team.leader_agent_id,
        repository_id=repository_id,
        leader_agent_id=manager.id,
    )
    worker_principal = _principal(
        AgentRole.WORKER,
        "rm-worker-a-api",
        agent_id=team.worker_agent_ids[0],
        repository_id=repository_id,
        leader_agent_id=leader.id,
    )
    busy = replace(
        _worker(project_id, repository_id, uuid4()),
        assignee_agent_id=worker_principal.id,
        status=TaskStatus.IN_PROGRESS,
    )
    service = _service(
        StubPlans(_plan(project_id, repository_id, uuid4(), ExecutionPlanStatus.IN_PROGRESS)),
        StubSnapshots(),
        StubTasks(busy),
        StubChangeSets({}),
        StubArchives(),
        repositories=StubProfiles(_profile("repomesh-e2e-api", repository_id)),
        agents=StubRoster(manager, leader, worker_principal),
        topology=StubTopology({project_id: topology_view}),
        runtime=StubRuntime(
            {
                "rm-worker-a-api": RuntimeSnapshot(
                    phase="Running",
                    runtime_kind="openclaw",
                    matrix_user_id="@rm-worker-a-api:local",
                    room_id="!team:local",
                    message="ready",
                )
            }
        ),
    )

    payload = await service.list_agents()

    by_name = {item["agentteams_resource_name"]: item for item in payload["agents"]}
    worker_row = by_name["rm-worker-a-api"]
    assert worker_row["role"] == "worker"
    assert worker_row["status"] == "active"
    assert worker_row["team_id"] == team.id
    assert worker_row["issue_id"] == project_id
    assert worker_row["repository_name"] == "repomesh-e2e-api"
    assert worker_row["active_task_count"] == 1
    assert worker_row["runtime"]["runtime_kind"] == "openclaw"
    assert worker_row["runtime"]["matrix_user_id"] == "@rm-worker-a-api:local"
    # §4.4: no start timestamp and no observed sleep state exist upstream.
    assert worker_row["runtime"]["uptime_seconds"] is None
    assert worker_row["runtime"]["awake"] is None
    # The organization leader belongs to no repository team.
    assert by_name["console-demo-org-leader"]["team_id"] is None
    assert by_name["console-demo-org-leader"]["repository_id"] is None
    assert by_name["rm-leader-a-api"]["team_id"] == team.id


# ------------------------------------------------------------------------ §4.4


@pytest.mark.asyncio
async def test_runtime_failures_are_isolated_to_their_own_row(monkeypatch) -> None:
    """The hard constraint: a hang, an error or a 404 on one agent must not stop
    the page — the persisted roster is readable regardless of the controller."""

    monkeypatch.setattr(service_module, "_RUNTIME_PROBE_TIMEOUT", 0.05)
    healthy = _principal(AgentRole.WORKER, "healthy", repository_id=uuid4())
    hanging = _principal(AgentRole.WORKER, "hanging", repository_id=uuid4())
    broken = _principal(AgentRole.WORKER, "broken", repository_id=uuid4())
    missing = _principal(AgentRole.WORKER, "missing", repository_id=uuid4())
    service = _service(
        StubPlans(),
        StubSnapshots(),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        agents=StubRoster(healthy, hanging, broken, missing),
        runtime=StubRuntime(
            {
                "healthy": RuntimeSnapshot(phase="Running"),
                "hanging": "hang",
                "broken": RuntimeError("controller exploded"),
                "missing": None,
            }
        ),
    )

    payload = await service.list_agents()

    runtimes = {item["agentteams_resource_name"]: item["runtime"] for item in payload["agents"]}
    assert runtimes["healthy"]["reachable"] is True
    assert runtimes["healthy"]["phase"] == "Running"
    # A hang is bounded per item and degrades only itself.
    assert runtimes["hanging"] == {"reachable": False}
    assert runtimes["broken"] == {"reachable": False}
    # 404 is not the same as unreachable: the resource simply does not exist.
    assert runtimes["missing"] is None
    assert len(payload["agents"]) == 4  # nothing was dropped


@pytest.mark.asyncio
async def test_unreachable_probes_run_concurrently(monkeypatch) -> None:
    """Isolation alone is not enough: probing serially makes an offline
    controller cost rows x timeout, which reads as an outage even though every
    row degrades correctly. The page must cost about one timeout, not N."""

    timeout = 0.2
    monkeypatch.setattr(service_module, "_RUNTIME_PROBE_TIMEOUT", timeout)
    principals = [
        _principal(AgentRole.WORKER, f"hanging-{index}", repository_id=uuid4())
        for index in range(6)
    ]
    service = _service(
        StubPlans(),
        StubSnapshots(),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        agents=StubRoster(*principals),
        runtime=StubRuntime({item.agentteams_resource_name: "hang" for item in principals}),
    )

    started = time.monotonic()
    payload = await service.list_agents()
    elapsed = time.monotonic() - started

    assert len(payload["agents"]) == 6
    assert all(item["runtime"] == {"reachable": False} for item in payload["agents"])
    # Serial would be 6 x 0.2 = 1.2s; concurrent is ~0.2s.
    assert elapsed < timeout * 3


@pytest.mark.asyncio
async def test_runtime_is_null_when_agentteams_is_not_configured() -> None:
    """Not configured is not the same as unreachable: there is no fact at all."""

    principal = _principal(AgentRole.WORKER, "rm-worker-a-api", repository_id=uuid4())
    service = _service(
        StubPlans(),
        StubSnapshots(),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        agents=StubRoster(principal),
    )

    payload = await service.list_agents()

    assert payload["agents"][0]["runtime"] is None


@pytest.mark.asyncio
async def test_a_row_whose_snapshot_breaks_the_shape_degrades_alone() -> None:
    """Isolation has to cover the whole block, not just the await.

    The probe call sat inside a try while the attribute lookup before it and
    the shape() call after it did not, so a snapshot the shape function cannot
    read took the entire page down — exactly the wholesale failure §4.4
    forbids, and the kind of thing an adapter change introduces quietly.
    """

    healthy = _principal(AgentRole.WORKER, "healthy", repository_id=uuid4())
    malformed = _principal(AgentRole.WORKER, "malformed", repository_id=uuid4())
    service = _service(
        StubPlans(),
        StubSnapshots(),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        agents=StubRoster(healthy, malformed),
        runtime=StubRuntime(
            {
                "healthy": RuntimeSnapshot(phase="Running"),
                # Not None, not an exception: an object the shape cannot read.
                "malformed": object(),
            }
        ),
    )

    payload = await service.list_agents()

    runtimes = {item["agentteams_resource_name"]: item["runtime"] for item in payload["agents"]}
    assert runtimes["healthy"]["reachable"] is True
    assert runtimes["malformed"] == {"reachable": False}
    assert len(payload["agents"]) == 2  # the page survived


class _ConcurrencyWatchingRuntime:
    """Records the high-water mark of simultaneously in-flight probes."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0

    async def _resolve(self, name: str):
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            return RuntimeSnapshot(phase="Running")
        finally:
            self.in_flight -= 1

    async def worker(self, name: str):
        return await self._resolve(name)

    async def manager(self, name: str):
        return await self._resolve(name)

    async def team(self, name: str):
        return await self._resolve(name)


@pytest.mark.asyncio
async def test_probe_fan_out_stays_under_the_ceiling() -> None:
    """Unbounded concurrency is not the opposite of the serial bug, it is the
    other end of it.

    httpx caps a client at 100 connections; past that the requests queue in
    the pool, and that queue time counts against each probe's own timeout. The
    rows then report reachable:false while the controller is healthy — a
    fabricated outage, which is worse than the slow page §4.4 removed.
    """

    ceiling = 4
    principals = [
        _principal(AgentRole.WORKER, f"agent-{index}", repository_id=uuid4()) for index in range(20)
    ]
    watcher = _ConcurrencyWatchingRuntime()
    service = _service(
        StubPlans(),
        StubSnapshots(),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        agents=StubRoster(*principals),
        runtime=watcher,
        probe_concurrency=ceiling,
    )

    payload = await service.list_agents()

    assert len(payload["agents"]) == 20
    assert all(row["runtime"]["reachable"] is True for row in payload["agents"])
    assert watcher.peak <= ceiling, f"{watcher.peak} probes were in flight at once"

    # Positive control. Stashing the fix cannot prove this one — the parameter
    # would not exist, so the failure would be a TypeError rather than a
    # demonstration of unbounded fan-out. Raising the ceiling instead shows the
    # watcher does observe every probe running at once, which is what the
    # bounded assertion above rules out.
    unbounded = _ConcurrencyWatchingRuntime()
    service = _service(
        StubPlans(),
        StubSnapshots(),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        agents=StubRoster(*principals),
        runtime=unbounded,
        probe_concurrency=len(principals),
    )

    await service.list_agents()

    assert unbounded.peak == len(principals)
