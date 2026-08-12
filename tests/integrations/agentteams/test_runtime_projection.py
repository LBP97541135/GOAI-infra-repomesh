"""The runtime projection a console round runs before it starts work (B-11).

The endpoint tests stub this whole step, so what it actually asks the
controller for is only observable here. Three things have to hold, and none of
them is visible from the outside:

* every agent in the topology is registered — the organization leader as a
  Manager, every repository leader and worker as a Worker — because a Team
  naming a resource the controller has never heard of is not a Team with rooms;
* the projections match ``scripts/run_pipeline.py``'s field for field, because
  the controller compares an existing resource against the one being asked for
  and answers 409 on a mismatch: a repository first staffed by the script and
  later reached by the console must converge, not conflict;
* a reconcile that produced teams without rooms is a refusal, not a success —
  that state *is* defect B-11.

Nothing reaches the network: the control plane is a recording double.
"""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from repomesh.integrations.agentteams.runtime_projection import (
    AgentTeamsRoomsPending,
    ProjectRuntimeProjection,
)
from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    ProvisionRepositoryAgentTeam,
)
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.infrastructure import InMemoryAgentDirectory
from repomesh.modules.agent_runtime.ports.agent_team import (
    ManagerProjection,
    ManagerRuntimeRef,
    TeamProjection,
    TeamRuntimeRef,
    WorkerProjection,
    WorkerRuntimeRef,
)
from repomesh.modules.project import (
    CreateProjectAgentTopology,
    CreateProjectAgentTopologyRequest,
    RepositoryTeamAssignment,
)
from repomesh.modules.project.domain import ProjectTopologyViolation
from repomesh.modules.project.infrastructure import InMemoryProjectTopologyStore
from repomesh.settings import Settings

MODEL = "deepseek-chat"

#: What the composition root injects, read from the same defaults production
#: reads. Not literals: the point of A-6's fix is that the runtime has exactly
#: one source, and a test that spelled it out here would be a second one.
_DEFAULTS = Settings()
_RUNTIMES = {
    "manager_runtime": _DEFAULTS.agentteams_manager_runtime,
    "worker_runtime": _DEFAULTS.agentteams_worker_runtime,
}


class RecordingControlPlane:
    """Answers every ensure, and remembers what it was asked for."""

    def __init__(self, *, rooms: bool = True) -> None:
        self.managers: list[ManagerProjection] = []
        self.workers: list[WorkerProjection] = []
        self.teams: list[TeamProjection] = []
        self.keys: list[str] = []
        self._rooms = rooms

    async def ensure_manager(
        self, projection: ManagerProjection, *, idempotency_key: str
    ) -> ManagerRuntimeRef:
        self.managers.append(projection)
        self.keys.append(idempotency_key)
        return ManagerRuntimeRef(projection.name, "Ready")

    async def ensure_worker(
        self, projection: WorkerProjection, *, idempotency_key: str
    ) -> WorkerRuntimeRef:
        self.workers.append(projection)
        self.keys.append(idempotency_key)
        return WorkerRuntimeRef(projection.name, "Ready")

    async def ensure_team(
        self, projection: TeamProjection, *, idempotency_key: str
    ) -> TeamRuntimeRef:
        self.teams.append(projection)
        self.keys.append(idempotency_key)
        return TeamRuntimeRef(
            name=projection.name,
            phase="Ready",
            team_room_id=f"!{projection.name}:matrix.local" if self._rooms else None,
            leader_room_id=f"!lead-{projection.name}:matrix.local" if self._rooms else None,
            leader_name=projection.members[0].name,
            ready_workers=len(projection.members),
            total_workers=len(projection.members),
        )


async def _console_project(
    directory: InMemoryAgentDirectory,
    store: InMemoryProjectTopologyStore,
    *,
    repositories: int = 2,
) -> UUID:
    """A project in exactly the state materialize's ``ensure topology`` leaves.

    Built through the production path — ``ProvisionRepositoryAgentTeam`` then
    ``CreateProjectAgentTopology`` — rather than by hand, so "the principals
    exist but the controller has never heard of them" is the real starting
    state and not an assumption about it.
    """

    organization_id = uuid4()
    project_id = uuid4()
    leader = await CreateAgent(directory).execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="agt-org-console",
        ),
        idempotency_key="console-org-leader",
    )
    provisioner = ProvisionRepositoryAgentTeam(directory)
    assignments = []
    for index in range(repositories):
        repository_id = uuid4()
        team = await provisioner.provision(
            organization_id=organization_id,
            organization_leader_id=leader.principal.id,
            repository_id=repository_id,
            idempotency_key=f"console-team-{index}",
        )
        assignments.append(
            RepositoryTeamAssignment(
                repository_id=repository_id,
                leader_agent_id=team.leader.id,
                worker_agent_ids=tuple(worker.id for worker in team.workers),
            )
        )
    await CreateProjectAgentTopology(directory, store).execute(
        CreateProjectAgentTopologyRequest(
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=leader.principal.id,
            repository_teams=tuple(assignments),
        ),
        idempotency_key="console-topology",
    )
    return project_id


@pytest.mark.asyncio
async def test_every_agent_is_registered_and_every_team_gets_its_rooms() -> None:
    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    project_id = await _console_project(directory, store)

    control_plane = RecordingControlPlane()
    view = await ProjectRuntimeProjection(
        directory, store, control_plane, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
    ).project(project_id)

    # One Manager (the organization leader) and four Workers (a leader and a
    # worker per repository). Registering fewer would leave `ensure_team`
    # naming a resource the controller does not have.
    assert [m.name for m in control_plane.managers] == ["agt-org-console"]
    assert len(control_plane.workers) == 4
    assert len(control_plane.teams) == 2

    # This is the state that was missing before: rooms on the persisted row,
    # which is what `collaboration._route` reads.
    assert all(team.room_id for team in view.repository_teams)
    assert all(team.leader_room_id for team in view.repository_teams)
    persisted = await store.get(project_id)
    assert all(team.room_id for team in persisted.to_view().repository_teams)


@pytest.mark.asyncio
async def test_the_projections_match_the_pipeline_script() -> None:
    """A mismatch here is a 409 from the controller, not a cosmetic difference."""

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    project_id = await _console_project(directory, store, repositories=1)

    control_plane = RecordingControlPlane()
    await ProjectRuntimeProjection(
        directory,
        store,
        control_plane,  # type: ignore[arg-type]
        model=MODEL,
        **_RUNTIMES,
        worker_task_control_url="http://task-control.internal/mcp",
    ).project(project_id)

    manager = control_plane.managers[0]
    assert manager.model == MODEL
    assert manager.runtime is _RUNTIMES["manager_runtime"]
    assert manager.skills == ("planning", "coordination")

    by_skills = {worker.skills: worker for worker in control_plane.workers}
    assert set(by_skills) == {("code-review", "planning"), ("coding",)}
    for worker in control_plane.workers:
        assert worker.model == MODEL
        assert worker.runtime is _RUNTIMES["worker_runtime"]
        assert worker.state.value == "Running"
        # The task-control MCP server, injected the one way `RegisterNativeAgent`
        # injects it — a second spelling would make the two paths conflict.
        assert [(s.name, s.url) for s in worker.mcp_servers] == [
            ("repomesh-task-control", "http://task-control.internal/mcp")
        ]


@pytest.mark.asyncio
async def test_projecting_twice_asks_for_the_same_side_effects() -> None:
    """Materialize is re-entrant, so this runs again on every retry.

    The keys are the assertion: they are derived from the project, the agent
    and the repository, never from the round's idempotency key, so a retry
    under a *fresh* key is the same side effect rather than a second one.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    project_id = await _console_project(directory, store)
    projection = ProjectRuntimeProjection(
        directory, store, RecordingControlPlane(), model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
    )
    await projection.project(project_id)

    second = RecordingControlPlane()
    view = await ProjectRuntimeProjection(
        directory, store, second, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
    ).project(project_id)

    assert len(second.keys) == len(set(second.keys))
    assert all(str(project_id) in key for key in second.keys)
    assert all(team.room_id for team in view.repository_teams)


@pytest.mark.asyncio
async def test_teams_without_rooms_are_refused_rather_than_reported_ready() -> None:
    """Defect B-11 in one assertion.

    The controller took the Teams and answered without rooms. Returning
    normally here is what let a round start and die on its first dispatch.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    project_id = await _console_project(directory, store, repositories=1)

    with pytest.raises(AgentTeamsRoomsPending, match="has not created rooms"):
        await ProjectRuntimeProjection(
            directory,
            store,
            RecordingControlPlane(rooms=False),  # type: ignore[arg-type]
            model=MODEL,
            **_RUNTIMES,
        ).project(project_id)


@pytest.mark.asyncio
async def test_a_project_with_no_topology_is_a_violation_not_a_silent_success() -> None:
    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()

    with pytest.raises(ProjectTopologyViolation):
        await ProjectRuntimeProjection(
            directory, store, RecordingControlPlane(), model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
        ).project(uuid4())


# ---------------------------------------------------------------------------
# The runtime is one value, not two (defect A-6)
# ---------------------------------------------------------------------------


def test_the_runtime_default_is_this_deployment_s_pairing() -> None:
    """``copaw``, and it is a decision about the *controller*, not a taste.

    The controller pairs each runtime with its own image env. Asking for
    ``openclaw`` where only the copaw image is configured spawns workers that
    exit(1) on boot, so they never obtain a Matrix identity and every dispatch
    fails — the root cause under A-6, seen live 2026-08-12.
    """

    assert Settings().agentteams_manager_runtime.value == "copaw"
    assert Settings().agentteams_worker_runtime.value == "copaw"


def test_an_unknown_runtime_is_refused_at_startup_not_at_first_dispatch() -> None:
    """Typing the setting as the wire enum is what buys this."""

    with pytest.raises(ValidationError):
        Settings(agentteams_worker_runtime="clawpo")


def test_neither_projection_path_writes_a_runtime_of_its_own() -> None:
    """§8.7's field-for-field rule, held structurally instead of by copying.

    The console and ``scripts/run_pipeline.py`` must ask the controller for
    field-identical resources. For ``runtime`` that used to mean the literal
    ``OPENCLAW`` written out in both files — two places to keep in step, and
    the pair silently drifts into a 409 the moment one is edited. Now there is
    one setting and no second value, and this is the test that keeps it that
    way.
    """

    roots = Path(__file__).parents[3]
    sources = {
        "runtime_projection.py": (
            roots / "src/repomesh/integrations/agentteams/runtime_projection.py"
        ),
        "run_pipeline.py": roots / "scripts/run_pipeline.py",
    }
    for label, path in sources.items():
        body = path.read_text(encoding="utf-8")
        # Only the prose may name it; no projection may construct one.
        assert "runtime=ManagerRuntime." not in body, label
        assert "runtime=WorkerRuntime." not in body, label
        assert "agentteams_manager_runtime" in body or "_manager_runtime" in body, label
        assert "agentteams_worker_runtime" in body or "_worker_runtime" in body, label
