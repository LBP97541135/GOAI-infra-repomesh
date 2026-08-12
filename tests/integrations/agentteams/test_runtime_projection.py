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

from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from repomesh.integrations.agentteams.control_plane import AgentTeamsResponseError
from repomesh.integrations.agentteams.runtime_projection import (
    AgentTeamsIdentitiesPending,
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
    """Answers every ensure, and remembers what it was asked for.

    ``memberships`` is the controller's answer to "which Team does this worker
    already belong to" — the read the reconcile makes before it names a Team
    (A-8). Empty by default, which is a controller that has never seen these
    principals.
    """

    def __init__(
        self,
        *,
        rooms: bool = True,
        identities: bool = True,
        memberships: dict[str, str] | None = None,
    ) -> None:
        self.managers: list[ManagerProjection] = []
        self.workers: list[WorkerProjection] = []
        self.teams: list[TeamProjection] = []
        self.keys: list[str] = []
        #: Reads of a worker the caller has not yet named a Team over — the
        #: membership question (A-8), as opposed to the identity question the
        #: projection asks about the same worker once its Team exists (A-9).
        self.membership_reads: list[str] = []
        self.worker_reads: list[str] = []
        self._rooms = rooms
        self._identities = identities
        self._memberships = dict(memberships or {})
        self._teamed: set[str] = set()

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        self.worker_reads.append(name)
        if name not in self._teamed:
            self.membership_reads.append(name)
        if name not in self._memberships:
            return None
        # ``matrixUserID`` is absent until the controller's worker reconciler
        # reaches this Worker, which is minutes after the create returns 201
        # on a busy host (A-9). ``identities=False`` is that window.
        return WorkerRuntimeRef(
            name,
            "Ready",
            matrix_user_id=f"@{name}:matrix.local" if self._identities else None,
            team=self._memberships[name],
        )

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
        # The rule that makes A-8 a defect rather than a tidiness question:
        # Team membership is exclusive, and asking for a second Team over a
        # worker some other Team already holds is a 400 no retry can clear.
        # Copied from the deployed controller's own sentence.
        for member in projection.members:
            held = self._memberships.get(member.name)
            if held is not None and held != projection.name:
                raise AgentTeamsResponseError(
                    400,
                    f"Worker {member.name} is already a member of Team {held}",
                )
        for member in projection.members:
            self._memberships[member.name] = projection.name
            self._teamed.add(member.name)
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


async def _shared_repository(
    directory: InMemoryAgentDirectory,
    store: InMemoryProjectTopologyStore,
) -> tuple[UUID, UUID, UUID, UUID]:
    """One repository, one organization, one project — the first issue.

    Returns ``(organization_id, organization_leader_id, repository_id,
    project_id)`` so a second issue can be laid over the same repository.
    """

    organization_id = uuid4()
    repository_id = uuid4()
    leader = await CreateAgent(directory).execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="agt-org-shared",
        ),
        idempotency_key="shared-org-leader",
    )
    project_id = await _second_project_on(
        directory,
        store,
        organization_id=organization_id,
        organization_leader_id=leader.principal.id,
        repository_id=repository_id,
        label="first-issue",
    )
    return organization_id, leader.principal.id, repository_id, project_id


async def _second_project_on(
    directory: InMemoryAgentDirectory,
    store: InMemoryProjectTopologyStore,
    *,
    organization_id: UUID,
    organization_leader_id: UUID,
    repository_id: UUID,
    label: str,
) -> UUID:
    """Another issue over a repository that already has a project.

    Through ``ProvisionRepositoryAgentTeam``, which converges rather than
    creates, so the second project genuinely reuses the *same* leader and
    worker principals — repository-scoped directory singletons. That sharing is
    what makes the AgentTeams Team unshareable-by-row, and building it by hand
    would assume away the only interesting fact.
    """

    team = await ProvisionRepositoryAgentTeam(directory).provision(
        organization_id=organization_id,
        organization_leader_id=organization_leader_id,
        repository_id=repository_id,
        idempotency_key=f"{label}-team",
    )
    project_id = uuid4()
    await CreateProjectAgentTopology(directory, store).execute(
        CreateProjectAgentTopologyRequest(
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=organization_leader_id,
            repository_teams=(
                RepositoryTeamAssignment(
                    repository_id=repository_id,
                    leader_agent_id=team.leader.id,
                    worker_agent_ids=tuple(worker.id for worker in team.workers),
                ),
            ),
        ),
        idempotency_key=f"{label}-topology",
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
async def test_workers_without_a_matrix_identity_are_refused_before_the_round() -> None:
    """Defect A-9 in one assertion.

    The rooms are there and the Workers exist, so B-11's check passes — but
    the controller's worker reconciler has not reached them, so none of them
    has the ``matrixUserID`` that ``AgentTeamsMatrixClient.send_task`` resolves
    the recipient from. Returning normally here is what let a round start and
    die on its first dispatch with ``AgentTeamsUnavailable``, which no console
    button retries.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    project_id = await _console_project(directory, store, repositories=1)

    control_plane = RecordingControlPlane(identities=False)
    with pytest.raises(
        AgentTeamsIdentitiesPending, match="has not provisioned Matrix identities"
    ):
        await ProjectRuntimeProjection(
            directory,
            store,
            control_plane,  # type: ignore[arg-type]
            model=MODEL,
            **_RUNTIMES,
        ).project(project_id)

    # Refused *after* the teams were asked for, not instead of: the round that
    # retries has to find the reconcile further along than it left it, or the
    # refusal is a deadlock rather than a wait.
    assert len(control_plane.teams) == 1


@pytest.mark.asyncio
async def test_a_worker_identity_that_arrives_late_makes_the_retry_succeed() -> None:
    """The other half of A-9: the refusal has to clear on its own.

    Same control plane, same project — only the controller has caught up in
    between. A retry of materialize is the operator's button, so this is the
    path that has to end in a view rather than in a second refusal.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    project_id = await _console_project(directory, store, repositories=1)

    control_plane = RecordingControlPlane(identities=False)
    projection = ProjectRuntimeProjection(
        directory,
        store,
        control_plane,  # type: ignore[arg-type]
        model=MODEL,
        **_RUNTIMES,
    )
    with pytest.raises(AgentTeamsIdentitiesPending):
        await projection.project(project_id)

    control_plane._identities = True  # the reconciler got there
    view = await projection.project(project_id)

    assert all(team.room_id for team in view.repository_teams)


@pytest.mark.asyncio
async def test_a_project_with_no_topology_is_a_violation_not_a_silent_success() -> None:
    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()

    with pytest.raises(ProjectTopologyViolation):
        await ProjectRuntimeProjection(
            directory, store, RecordingControlPlane(), model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
        ).project(uuid4())


# ---------------------------------------------------------------------------
# A Team belongs to a repository, not to a topology row (defect A-8, §8.7.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_fresh_team_is_named_after_its_repository_not_its_row() -> None:
    """The name has to be derivable from the repository by anyone who asks.

    A row-derived name is unguessable from outside the row, so a second project
    over the same repository has no way to arrive at it — which is how three
    issues ended up asking for three Teams over one set of principals.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, _, repository_id, project_id = await _shared_repository(directory, store)

    control_plane = RecordingControlPlane()
    view = await ProjectRuntimeProjection(
        directory, store, control_plane, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
    ).project(project_id)

    expected = f"rm-team-{repository_id.hex}"
    assert [team.name for team in control_plane.teams] == [expected]
    assert view.repository_teams[0].agentteams_team_name == expected


@pytest.mark.asyncio
async def test_a_second_issue_on_one_repository_shares_the_first_issue_s_team() -> None:
    """Defect A-8 in one assertion.

    Two issues, one repository, one controller. The second must land in the
    Team the first created — same name, same two rooms — because the leader it
    would put in a Team of its own is already in that one, and the controller
    holds membership exclusively.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    organization_id, leader_id, repository_id, first = await _shared_repository(
        directory, store
    )
    control_plane = RecordingControlPlane()
    first_view = await ProjectRuntimeProjection(
        directory, store, control_plane, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
    ).project(first)

    second = await _second_project_on(
        directory,
        store,
        organization_id=organization_id,
        organization_leader_id=leader_id,
        repository_id=repository_id,
        label="second-issue",
    )
    second_view = await ProjectRuntimeProjection(
        directory, store, control_plane, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
    ).project(second)

    shared = first_view.repository_teams[0]
    joined = second_view.repository_teams[0]
    assert joined.agentteams_team_name == shared.agentteams_team_name
    assert (joined.room_id, joined.leader_room_id) == (
        shared.room_id,
        shared.leader_room_id,
    )
    # Two projections, but only ever one Team asked for.
    assert len({team.name for team in control_plane.teams}) == 1


@pytest.mark.asyncio
async def test_a_row_minted_before_the_fix_adopts_the_team_that_already_exists() -> None:
    """The two stuck specimens, on replay, without touching the controller.

    A row whose stored name predates A-8 points at a Team nobody ever created,
    while the repository's real Team sits under some unrelated name holding the
    leader. Replay must converge on the *existing* Team — adoption — and write
    that name back, because a row still pointing at the phantom asks the same
    question again next time.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, _, repository_id, project_id = await _shared_repository(directory, store)

    # The state 35e66beb / 5c1b3567 are actually in: a per-row name, and a
    # controller whose Team for this repository is called something else
    # entirely (96896557's row id, in the live case).
    topology = await store.get(project_id)
    stale = replace(
        topology.repository_teams[0],
        agentteams_team_name="rm-team-b0e9b2eee4074dfd9cf767a46b2d2575",
    )
    await store.save(replace(topology, repository_teams=(stale,)))
    incumbent = "rm-team-6c503f0227a44e9280b3ab29775c0b76"
    leader = await directory.get_view(stale.leader_agent_id)
    worker = await directory.get_view(stale.worker_agent_ids[0])
    control_plane = RecordingControlPlane(
        memberships={
            leader.agentteams_resource_name: incumbent,
            worker.agentteams_resource_name: incumbent,
        }
    )

    view = await ProjectRuntimeProjection(
        directory, store, control_plane, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
    ).project(project_id)

    assert [team.name for team in control_plane.teams] == [incumbent]
    assert view.repository_teams[0].agentteams_team_name == incumbent
    # Written back, not just returned: the next replay must not re-ask.
    persisted = (await store.get(project_id)).to_view()
    assert persisted.repository_teams[0].agentteams_team_name == incumbent
    assert persisted.repository_teams[0].room_id


@pytest.mark.asyncio
async def test_the_leader_is_the_anchor_the_membership_is_read_from() -> None:
    """One read, and it is the repository leader's.

    The leader is the one principal a repository's Team must contain and it is
    a directory singleton, so whatever Team holds it *is* this repository's
    Team. Asking the workers instead would be asking a question the leader
    already answers.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, _, _, project_id = await _shared_repository(directory, store)
    topology = await store.get(project_id)
    leader = await directory.get_view(topology.repository_teams[0].leader_agent_id)

    control_plane = RecordingControlPlane()
    await ProjectRuntimeProjection(
        directory, store, control_plane, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
    ).project(project_id)

    assert control_plane.membership_reads == [leader.agentteams_resource_name]


@pytest.mark.asyncio
async def test_a_team_whose_members_disagree_is_a_conflict_not_a_wait() -> None:
    """What is left of the 400 once adoption exists.

    Adoption removes the already-a-member case, not every case: a controller
    holding a Team whose membership genuinely differs from what this topology
    asks for still refuses, and that refusal is a 4xx the composition root now
    turns into a 409 rather than a retryable 503.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, _, _, project_id = await _shared_repository(directory, store)
    topology = await store.get(project_id)
    worker = await directory.get_view(topology.repository_teams[0].worker_agent_ids[0])

    # The worker is spoken for by an unrelated Team; the leader is not, so
    # there is nothing to adopt and the ensure walks into the refusal.
    control_plane = RecordingControlPlane(
        memberships={worker.agentteams_resource_name: "rm-team-somebody-else"}
    )

    with pytest.raises(AgentTeamsResponseError, match="already a member of Team") as raised:
        await ProjectRuntimeProjection(
            directory, store, control_plane, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
        ).project(project_id)
    assert raised.value.status_code == 400


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
