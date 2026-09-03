"""The runtime projection a console round runs before it starts work (B-11).

The endpoint tests stub this whole step, so what it actually asks the
controller for is only observable here. Three things have to hold, and none of
them is visible from the outside:

* every agent in the topology is registered — the organization leader as a
  Manager, every repository leader and worker as a Worker — because a Team
  naming a resource the controller has never heard of is not a Team with rooms;
* a resource that *already* exists is read and validated rather than re-asked
  for, because its runtime, model and skills were chosen per agent when the
  repository was staffed and this pass has only global defaults to offer;
* the projections match ``scripts/run_pipeline.py``'s field for field for the
  resources this pass does create, because the controller compares an existing
  resource against the one being asked for and answers 409 on a mismatch: a
  repository first staffed by the script and later reached by the console must
  converge, not conflict;
* a reconcile that produced teams without rooms is a refusal, not a success —
  that state *is* defect B-11.

Nothing reaches the network: the control plane is a recording double.
"""

from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from repomesh.integrations.agentteams.control_plane import (
    AgentTeamsConflict,
    AgentTeamsResponseError,
)
from repomesh.integrations.agentteams.runtime_projection import (
    AgentTeamsIdentitiesPending,
    AgentTeamsResourceMismatch,
    AgentTeamsRoomsPending,
    ExternalWorkerProjection,
    ProjectRuntimeProjection,
)
from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    ProvisionRepositoryAgentTeam,
)
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_directory.infrastructure import InMemoryAgentDirectory
from repomesh.modules.agent_runtime.application.external_worker import (
    ProvisionExternalMember,
    ProvisionExternalWorker,
    ResolveExternalWorkerBinding,
)
from repomesh.modules.agent_runtime.contracts import (
    ExternalMemberRefused,
    ExternalWorkerBindingQuery,
    ExternalWorkerRefused,
    ProvisionExternalMemberCommand,
    ProvisionExternalWorkerCommand,
    UnknownExternalWorker,
)
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
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import (
    InMemoryRepositoryCatalog,
)
from repomesh.settings import Settings

from .fakes import StubDirectory

MODEL = "deepseek-chat"

#: What the composition root injects, read from the same defaults production
#: reads. Not literals: the point of A-6's fix is that the runtime has exactly
#: one source, and a test that spelled it out here would be a second one.
_DEFAULTS = Settings()
_RUNTIMES = {
    "manager_runtime": _DEFAULTS.agentteams_manager_runtime,
    "worker_runtime": _DEFAULTS.agentteams_worker_runtime,
    "manager_image": _DEFAULTS.agentteams_manager_image,
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
        self.manager_reads: list[str] = []
        self._rooms = rooms
        self._identities = identities
        self._memberships = dict(memberships or {})
        self._teamed: set[str] = set()
        #: Resources this double has been made to create. A controller does not
        #: forget them between passes, and ``_register`` reads before it
        #: ensures, so a replay has to find them present.
        self._created: set[str] = set()

    async def get_manager(self, name: str) -> ManagerRuntimeRef | None:
        self.manager_reads.append(name)
        if name not in self._created:
            return None
        return ManagerRuntimeRef(name, "Ready")

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        # Two different questions arrive through this one method. ``_register``
        # reads each resource once, before it decides whether to create it; the
        # reconcile then reads *one* of them a second time, while no Team of
        # its own exists yet, to ask which Team already holds it (A-8). The
        # second read is the membership question, and this is how the double
        # tells them apart.
        if name in self.worker_reads and name not in self._teamed:
            self.membership_reads.append(name)
        self.worker_reads.append(name)
        if name not in self._memberships and name not in self._created:
            return None
        # ``matrixUserID`` is absent until the controller's worker reconciler
        # reaches this Worker, which is minutes after the create returns 201
        # on a busy host (A-9). ``identities=False`` is that window.
        return WorkerRuntimeRef(
            name,
            "Ready",
            matrix_user_id=f"@{name}:matrix.local" if self._identities else None,
            team=self._memberships.get(name),
        )

    async def ensure_manager(
        self, projection: ManagerProjection, *, idempotency_key: str
    ) -> ManagerRuntimeRef:
        self.managers.append(projection)
        self.keys.append(idempotency_key)
        self._created.add(projection.name)
        return ManagerRuntimeRef(projection.name, "Ready")

    async def ensure_worker(
        self, projection: WorkerProjection, *, idempotency_key: str
    ) -> WorkerRuntimeRef:
        self.workers.append(projection)
        self.keys.append(idempotency_key)
        self._created.add(projection.name)
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
    # The script asks for ``image=runtimes.agentteams_manager_image`` from the
    # same settings object, so whatever the deployment names — or None — has
    # to be the value this pass asks for too, or the two paths conflict.
    assert manager.image == _RUNTIMES["manager_image"]
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
async def test_the_injected_manager_image_reaches_the_manager_projection() -> None:
    """The durable fix for the crash-loop: an imageless manager CR is handed
    the *worker* image by the controller's role-blind fallback and exits for
    want of ``AGENTTEAMS_WORKER_NAME`` — so a copaw deployment names its
    manager image, and this is the injection that has to carry it through.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    project_id = await _console_project(directory, store, repositories=1)

    control_plane = RecordingControlPlane()
    image = "registry/agentteams-manager-copaw:v1.2.0"
    await ProjectRuntimeProjection(
        directory,
        store,
        control_plane,  # type: ignore[arg-type]
        model=MODEL,
        **{**_RUNTIMES, "manager_image": image},
    ).project(project_id)

    assert control_plane.managers[0].image == image


@pytest.mark.asyncio
async def test_a_profiled_repository_s_team_is_created_with_its_own_skills() -> None:
    """The cross-repo test team's controller-side story, at creation time.

    The repository carries ``cross-repo-test-team`` in the catalog, so the
    *fresh* resources this pass creates present the test team's skills instead
    of the coding defaults — a test Worker that answers "coding" is the wrong
    story even with ``integration-run`` appended after it. The organization
    leader has no repository and keeps the default tuple; existing resources
    keep the read-first rule (the tests below cover that nothing is re-ensured).
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    project_id = await _console_project(directory, store, repositories=1)
    topology = await store.get(project_id)
    repository_id = topology.repository_teams[0].repository_id

    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(
            id=repository_id,
            name="test-assets",
            url="https://github.com/example/test-assets",
            capability_profile="cross-repo-test-team",
        )
    )

    control_plane = RecordingControlPlane()
    await ProjectRuntimeProjection(
        directory,
        store,
        control_plane,  # type: ignore[arg-type]
        model=MODEL,
        **_RUNTIMES,
        repository_catalog=catalog,
    ).project(project_id)

    assert control_plane.managers[0].skills == ("planning", "coordination")
    by_skills = {worker.skills for worker in control_plane.workers}
    assert by_skills == {
        ("cross-repo-test", "worker-management", "reporting"),
        ("integration-run", "task-execution"),
    }


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

    expected = f"repomesh-team-{repository_id.hex}"
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
        agentteams_team_name="repomesh-team-b0e9b2eee4074dfd9cf767a46b2d2575",
    )
    await store.save(replace(topology, repository_teams=(stale,)))
    incumbent = "repomesh-team-6c503f0227a44e9280b3ab29775c0b76"
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
        memberships={worker.agentteams_resource_name: "repomesh-team-somebody-else"}
    )

    with pytest.raises(AgentTeamsResponseError, match="already a member of Team") as raised:
        await ProjectRuntimeProjection(
            directory, store, control_plane, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
        ).project(project_id)
    assert raised.value.status_code == 400


# ---------------------------------------------------------------------------
# A repository that is already staffed is read, not re-asked for (track P, P2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExistingWorker:
    """A Worker onboarding created, as the controller holds it today.

    ``runtime``, ``model`` and ``skills`` are per-agent facts somebody chose
    when the repository was staffed — the console offers ``repomesh-runner``
    per worker while this projection has one global default — so none of them
    is derivable from RepoMesh settings. That is the whole reason materialize
    may not replay a global spec over them.
    """

    runtime: str
    model: str
    skills: tuple[str, ...]
    team: str
    matrix_user_id: str


class ExistingResourcesControlPlane:
    """A controller that already holds every resource this project names.

    Strict where ``RecordingControlPlane`` is permissive, and deliberately so:
    it answers ``ensure_worker`` about an existing resource the way the real
    client does, by comparing the projection against what it holds and raising
    ``AgentTeamsConflict`` on a mismatch
    (``AgentTeamsControlPlaneClient._assert_worker_matches`` compares
    ``runtime`` unconditionally). A pass that re-ensures anything here does not
    quietly pass — it reproduces the 409 that made materialize unreachable for
    every already-staffed repository.

    ``ensure_team`` stays ensure-shaped because a Team genuinely is: the
    reconcile is allowed to ask for it on every pass, and adoption (A-8)
    depends on it.
    """

    def __init__(
        self,
        *,
        managers: dict[str, tuple[str, str]],
        workers: dict[str, ExistingWorker],
        rooms: bool = True,
    ) -> None:
        #: name -> (model, runtime)
        self._managers = dict(managers)
        self._workers = dict(workers)
        self._rooms = rooms
        #: Every ``ensure_manager``/``ensure_worker`` this double was asked for,
        #: by resource name. The assertion is that it stays empty: an existing
        #: resource is somebody else's to define.
        self.ensure_calls: list[str] = []
        self.teams: list[TeamProjection] = []

    def snapshot(self) -> tuple[dict[str, tuple[str, str]], dict[str, ExistingWorker]]:
        """What the controller holds, for a before/after comparison."""

        return dict(self._managers), dict(self._workers)

    async def get_manager(self, name: str) -> ManagerRuntimeRef | None:
        if name not in self._managers:
            return None
        return ManagerRuntimeRef(name, "Ready", matrix_user_id=f"@{name}:matrix.local")

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        existing = self._workers.get(name)
        if existing is None:
            return None
        return WorkerRuntimeRef(
            name,
            "Ready",
            runtime=existing.runtime,
            room_id=f"!{name}:matrix.local",
            matrix_user_id=existing.matrix_user_id,
            team=existing.team,
            container_managed=True,
        )

    async def ensure_manager(
        self, projection: ManagerProjection, *, idempotency_key: str
    ) -> ManagerRuntimeRef:
        self.ensure_calls.append(projection.name)
        asked = (projection.model, projection.runtime.value)
        existing = self._managers.get(projection.name)
        if existing is not None and existing != asked:
            raise AgentTeamsConflict(
                "existing AgentTeams manager differs in: "
                + ", ".join(
                    sorted(
                        key
                        for key, held, want in (
                            ("model", existing[0], asked[0]),
                            ("runtime", existing[1], asked[1]),
                        )
                        if held != want
                    )
                )
            )
        self._managers[projection.name] = asked
        return ManagerRuntimeRef(projection.name, "Ready")

    async def ensure_worker(
        self, projection: WorkerProjection, *, idempotency_key: str
    ) -> WorkerRuntimeRef:
        self.ensure_calls.append(projection.name)
        existing = self._workers.get(projection.name)
        if existing is not None:
            mismatches = sorted(
                key
                for key, held, want in (
                    ("model", existing.model, projection.model),
                    ("runtime", existing.runtime, projection.runtime.value),
                    ("skills", existing.skills, tuple(projection.skills)),
                )
                if held != want
            )
            if mismatches:
                raise AgentTeamsConflict(
                    f"existing AgentTeams worker differs in: {', '.join(mismatches)}"
                )
            return await self.get_worker(projection.name)
        self._workers[projection.name] = ExistingWorker(
            runtime=projection.runtime.value,
            model=projection.model,
            skills=tuple(projection.skills),
            team="",
            matrix_user_id=f"@{projection.name}:matrix.local",
        )
        return WorkerRuntimeRef(projection.name, "Ready")

    async def ensure_team(
        self, projection: TeamProjection, *, idempotency_key: str
    ) -> TeamRuntimeRef:
        self.teams.append(projection)
        return TeamRuntimeRef(
            name=projection.name,
            phase="Ready",
            team_room_id=f"!{projection.name}:matrix.local" if self._rooms else None,
            leader_room_id=f"!lead-{projection.name}:matrix.local" if self._rooms else None,
            leader_name=projection.members[0].name,
            ready_workers=len(projection.members),
            total_workers=len(projection.members),
        )


async def _staffed_repository(
    directory: InMemoryAgentDirectory,
    store: InMemoryProjectTopologyStore,
    *,
    runtimes: tuple[str, str] = ("copaw", "repomesh-runner"),
) -> tuple[UUID, ExistingResourcesControlPlane, str]:
    """A project whose principals the controller already holds, with per-agent runtimes.

    The state onboarding leaves and materialize then walks into: the Manager
    and both Workers exist, the repository leader runs one runtime and the
    worker another, and neither is this projection's global default for every
    field.
    """

    project_id = await _console_project(directory, store, repositories=1)
    topology = await store.get(project_id)
    team = topology.repository_teams[0]
    organization_leader = await directory.get_view(topology.organization_leader_id)
    leader = await directory.get_view(team.leader_agent_id)
    worker = await directory.get_view(team.worker_agent_ids[0])
    team_name = f"repomesh-team-{team.repository_id.hex}"

    def held(runtime: str, model: str, skills: tuple[str, ...]) -> ExistingWorker:
        return ExistingWorker(
            runtime=runtime,
            model=model,
            skills=skills,
            team=team_name,
            matrix_user_id="@held:matrix.local",
        )

    control_plane = ExistingResourcesControlPlane(
        managers={organization_leader.agentteams_resource_name: ("qwen3.6-plus", "openclaw")},
        workers={
            leader.agentteams_resource_name: held(
                runtimes[0], "qwen3.6-plus", ("code-review",)
            ),
            worker.agentteams_resource_name: held(
                runtimes[1], "qwen3.6-plus", ("coding", "testing")
            ),
        },
    )
    return project_id, control_plane, team_name


@pytest.mark.asyncio
async def test_existing_mixed_runtime_workers_are_read_without_reensuring_or_overwriting_them() -> (
    None
):
    """The hard 409 every already-staffed repository hit, in one assertion.

    Onboarding creates each Worker with the runtime chosen for it — the console
    offers ``repomesh-runner`` while this projection's global default is
    ``copaw`` — and binds it to a principal. ``_register`` used to re-``ensure``
    every one of them with that *global* spec, and the controller compares an
    existing resource against the one being asked for, so it answered
    ``409 ... differs in: runtime`` and no retry could clear it.

    Reading the resource that already exists is the fix, and it has to be the
    fix: a different default would only move the 409 to ``model``, then to
    ``skills``, then to the MCP servers (arch-team T2 §6). Which is why this
    fixture disagrees on all three at once.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    project_id, control_plane, team_name = await _staffed_repository(directory, store)
    before = control_plane.snapshot()

    view = await ProjectRuntimeProjection(
        directory, store, control_plane, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
    ).project(project_id)

    # Materialize gets its rooms — this is the call that used to raise.
    assert all(team.room_id for team in view.repository_teams)
    assert view.repository_teams[0].agentteams_team_name == team_name
    # Nothing existing was re-asked for, and nothing existing changed: the
    # per-agent runtime, model and skills survive the pass untouched.
    assert control_plane.ensure_calls == []
    assert control_plane.snapshot() == before

    # Materialize is re-entrant, so this is the same pass a retry makes.
    again = await ProjectRuntimeProjection(
        directory, store, control_plane, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
    ).project(project_id)

    assert again.repository_teams[0].agentteams_team_name == team_name
    assert all(team.room_id for team in again.repository_teams)
    assert control_plane.ensure_calls == []
    assert control_plane.snapshot() == before


@pytest.mark.asyncio
async def test_a_repository_the_controller_has_never_seen_is_still_created() -> None:
    """The other half: read-before-ensure must not turn creation off.

    A repository nobody has staffed has no resources to read, so every one of
    them is still created from this projection's own spec — the path the
    console has always taken for a fresh project.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    project_id = await _console_project(directory, store, repositories=1)

    control_plane = ExistingResourcesControlPlane(managers={}, workers={})
    view = await ProjectRuntimeProjection(
        directory, store, control_plane, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
    ).project(project_id)

    assert all(team.room_id for team in view.repository_teams)
    # One Manager and two Workers, each created exactly once.
    assert len(control_plane.ensure_calls) == 3
    assert len(set(control_plane.ensure_calls)) == 3


@pytest.mark.asyncio
async def test_a_controller_answering_about_another_resource_is_a_conflict() -> None:
    """The binding is confirmed, not echoed.

    A read that comes back naming a different resource means RepoMesh and the
    controller disagree about which Worker this principal *is*. Registering
    the topology on that answer would put somebody else's identity in this
    repository's Team, so it is a refusal — and a conflict rather than a wait,
    because no amount of retrying makes two names agree.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    project_id, control_plane, _ = await _staffed_repository(directory, store)

    async def answers_about_somebody_else(name: str) -> WorkerRuntimeRef | None:
        return WorkerRuntimeRef("repomesh-worker-somebody-else", "Ready")

    control_plane.get_worker = answers_about_somebody_else  # type: ignore[method-assign]

    with pytest.raises(AgentTeamsResourceMismatch, match="somebody-else"):
        await ProjectRuntimeProjection(
            directory, store, control_plane, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
        ).project(project_id)

    assert control_plane.ensure_calls == []


# ---------------------------------------------------------------------------
# External workers are explicit, and the default path stays managed
# (ADR 0004 decisions 2, 4, 5)
# ---------------------------------------------------------------------------

TASK_CONTROL = "http://task-control.internal/mcp"


class ExternalControlPlane:
    """A controller that answers about one worker, and remembers what it was asked.

    ``RecordingControlPlane`` above serves the *default project* path, where no
    worker is external and nobody ever reads a Team back. The external path
    needs both, so it gets a double of its own rather than a second set of
    flags on the first one.

    ``confirms_external`` is the controller that took the request and answered
    with a managed worker anyway — an older build that ignores the field. The
    provisioning use case must not report success on that answer.
    """

    def __init__(self, *, confirms_external: bool = True) -> None:
        self.workers: list[WorkerProjection] = []
        self.keys: list[str] = []
        self.refs: dict[str, WorkerRuntimeRef] = {}
        self.teams: dict[str, TeamRuntimeRef] = {}
        self._confirms_external = confirms_external

    async def ensure_worker(
        self, projection: WorkerProjection, *, idempotency_key: str
    ) -> WorkerRuntimeRef:
        self.workers.append(projection)
        self.keys.append(idempotency_key)
        return WorkerRuntimeRef(
            projection.name,
            "Pending",
            container_managed=projection.container_managed if self._confirms_external else True,
        )

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        return self.refs.get(name)

    async def get_team(self, name: str) -> TeamRuntimeRef | None:
        return self.teams.get(name)


def _external_projection(
    control_plane: ExternalControlPlane, *, task_control: str | None = None
) -> ExternalWorkerProjection:
    return ExternalWorkerProjection(
        control_plane,  # type: ignore[arg-type]
        model=MODEL,
        worker_runtime=_RUNTIMES["worker_runtime"],
        worker_task_control_url=task_control,
    )


async def _repository_principals(
    directory: InMemoryAgentDirectory, store: InMemoryProjectTopologyStore
) -> tuple[AgentPrincipalView, AgentPrincipalView, UUID]:
    """The leader and worker of one repository, plus its project id."""

    _, _, _, project_id = await _shared_repository(directory, store)
    topology = await store.get(project_id)
    team = topology.repository_teams[0]
    leader = await directory.get_view(team.leader_agent_id)
    worker = await directory.get_view(team.worker_agent_ids[0])
    return leader, worker, project_id


def _bound_worker(
    name: str,
    *,
    container_managed: bool | None = False,
    matrix_user_id: str | None = "@worker:matrix.local",
    room_id: str | None = "!worker:matrix.local",
    team: str | None = "repomesh-team-pricing",
) -> WorkerRuntimeRef:
    return WorkerRuntimeRef(
        name,
        "Ready",
        room_id=room_id,
        matrix_user_id=matrix_user_id,
        team=team,
        container_managed=container_managed,
    )


def _bound_team(
    name: str = "repomesh-team-pricing", *, room: str | None = "!team-pricing:matrix.local"
) -> TeamRuntimeRef:
    return TeamRuntimeRef(
        name=name,
        phase="Ready",
        team_room_id=room,
        leader_room_id="!lead-pricing:matrix.local",
        leader_name="repomesh-worker-lead",
        ready_workers=2,
        total_workers=2,
    )


@pytest.mark.asyncio
async def test_the_default_project_path_still_projects_managed_workers() -> None:
    """Decision 2's other half: nothing about the ordinary path changes.

    ``container_managed`` defaults to True on the projection, so every worker
    the console and ``run_pipeline.py`` provision keeps its controller-managed
    container without either of them naming the field.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    project_id = await _console_project(directory, store)

    control_plane = RecordingControlPlane()
    await ProjectRuntimeProjection(
        directory, store, control_plane, model=MODEL, **_RUNTIMES  # type: ignore[arg-type]
    ).project(project_id)

    assert control_plane.workers
    assert all(worker.container_managed is True for worker in control_plane.workers)


@pytest.mark.asyncio
async def test_an_external_worker_is_projected_with_container_managed_false() -> None:
    """The explicit command, end to end through the adapter.

    The idempotency key is keyed on the agent and nothing else: an external
    worker is not provisioned by a round, so re-running the command must be the
    same controller side effect rather than a second one.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, worker, _ = await _repository_principals(directory, store)

    control_plane = ExternalControlPlane()
    view = await ProvisionExternalWorker(
        directory, _external_projection(control_plane, task_control=TASK_CONTROL)
    ).execute(ProvisionExternalWorkerCommand(worker_agent_id=worker.id))

    assert [projection.container_managed for projection in control_plane.workers] == [False]
    assert view.worker_agent_id == worker.id
    assert view.worker_name == worker.agentteams_resource_name
    assert view.container_managed is False
    assert control_plane.keys == [f"external-worker:{worker.id}:agentteams"]


@pytest.mark.asyncio
async def test_the_external_projection_differs_in_exactly_one_field() -> None:
    """Field-for-field parity, or the conflict lands on the wrong field.

    The controller compares an existing worker against the one being asked for,
    so an external projection that also drifted on skills or on the task-control
    MCP server would answer 409 about *that* — and the operator would read a
    spurious mismatch instead of "this worker is already managed".
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, worker, project_id = await _repository_principals(directory, store)

    managed_plane = RecordingControlPlane()
    await ProjectRuntimeProjection(
        directory,
        store,
        managed_plane,  # type: ignore[arg-type]
        model=MODEL,
        **_RUNTIMES,
        worker_task_control_url=TASK_CONTROL,
    ).project(project_id)
    managed = next(
        projection
        for projection in managed_plane.workers
        if projection.name == worker.agentteams_resource_name
    )

    external_plane = ExternalControlPlane()
    await ProvisionExternalWorker(
        directory, _external_projection(external_plane, task_control=TASK_CONTROL)
    ).execute(ProvisionExternalWorkerCommand(worker_agent_id=worker.id))

    assert external_plane.workers == [replace(managed, container_managed=False)]


@pytest.mark.asyncio
async def test_the_external_leader_projection_also_differs_in_exactly_one_field() -> None:
    """The same parity argument for a Repository Leader (PR 5.5A).

    This is the one that would have been silently wrong. A leader carries
    ``("code-review", "planning")`` wherever the ordinary path registered it,
    so an external provisioning that sent a worker's ``("coding",)`` would make
    the controller answer 409 about *skills* — a Repository Leader that parses
    v2 fine and still cannot be provisioned, which is the R0 risk one field
    over. Asserting equality against the managed projection is what makes the
    role argument load-bearing rather than decorative.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    leader, _, project_id = await _repository_principals(directory, store)

    managed_plane = RecordingControlPlane()
    await ProjectRuntimeProjection(
        directory,
        store,
        managed_plane,  # type: ignore[arg-type]
        model=MODEL,
        **_RUNTIMES,
        worker_task_control_url=TASK_CONTROL,
    ).project(project_id)
    managed = next(
        projection
        for projection in managed_plane.workers
        if projection.name == leader.agentteams_resource_name
    )
    assert managed.skills == ("code-review", "planning")

    external_plane = ExternalControlPlane()
    view = await ProvisionExternalMember(
        directory, _external_projection(external_plane, task_control=TASK_CONTROL)
    ).execute(ProvisionExternalMemberCommand(member_agent_id=leader.id))

    assert external_plane.workers == [replace(managed, container_managed=False)]
    assert view.role.value == "repository_leader"
    assert external_plane.keys == [f"external-worker:{leader.id}:agentteams"]


@pytest.mark.asyncio
async def test_an_organization_leader_cannot_be_made_an_external_member() -> None:
    """The one role D-11 keeps refusing, refused before the controller is touched."""

    organization_leader = AgentPrincipalView(
        id=uuid4(),
        organization_id=uuid4(),
        role=AgentRole.ORGANIZATION_LEADER,
        leader_agent_id=None,
        repository_id=None,
        responsibility_paths=(),
        agentteams_resource_name="repomesh-manager-acme",
        status=AgentPrincipalStatus.ACTIVE,
    )

    control_plane = ExternalControlPlane()
    with pytest.raises(ExternalMemberRefused, match="organization_leader"):
        await ProvisionExternalMember(
            StubDirectory(organization_leader), _external_projection(control_plane)
        ).execute(ProvisionExternalMemberCommand(member_agent_id=organization_leader.id))

    assert control_plane.workers == []


@pytest.mark.asyncio
async def test_a_non_worker_identity_cannot_be_made_external() -> None:
    """A repository leader is a Worker *resource*, not a worker *identity*.

    Both are ``ensure_worker`` on the controller, so the refusal has to come
    from RepoMesh's own role — and it has to come before the request, or a
    leader ends up with no container and no local process serving it.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    leader, _, _ = await _repository_principals(directory, store)

    control_plane = ExternalControlPlane()
    with pytest.raises(ExternalWorkerRefused, match="repository_leader"):
        await ProvisionExternalWorker(
            directory, _external_projection(control_plane)
        ).execute(ProvisionExternalWorkerCommand(worker_agent_id=leader.id))

    assert control_plane.workers == []


@pytest.mark.asyncio
async def test_an_unknown_agent_is_refused_before_the_controller_is_touched() -> None:
    directory = InMemoryAgentDirectory()
    control_plane = ExternalControlPlane()

    with pytest.raises(UnknownExternalWorker):
        await ProvisionExternalWorker(
            directory, _external_projection(control_plane)
        ).execute(ProvisionExternalWorkerCommand(worker_agent_id=uuid4()))

    assert control_plane.workers == []


@pytest.mark.asyncio
async def test_a_controller_that_will_not_confirm_external_is_a_refusal() -> None:
    """Asking is not the same as being answered.

    The worker document the controller returns carries ``containerManaged``, so
    a build that ignored the request says so in its answer. Reporting success
    there would hand PR 2 a worker whose container is about to start.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, worker, _ = await _repository_principals(directory, store)

    control_plane = ExternalControlPlane(confirms_external=False)
    with pytest.raises(ExternalWorkerRefused, match="containerManaged"):
        await ProvisionExternalWorker(
            directory, _external_projection(control_plane)
        ).execute(ProvisionExternalWorkerCommand(worker_agent_id=worker.id))


# ---------------------------------------------------------------------------
# Preflight is fail-closed: a partial binding is never an answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_binds_the_agent_to_its_worker_team_and_rooms() -> None:
    """What the Bridge gets to trust, and where each field comes from.

    The team is the controller's answer to "which Team holds this worker", not
    a name from the enrollment file; the rooms are the Team's room and the
    worker's own, because those are the two RepoMesh routes work through for a
    worker identity (``SendCollaborationMessage._route``).
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, worker, _ = await _repository_principals(directory, store)

    control_plane = ExternalControlPlane()
    control_plane.refs[worker.agentteams_resource_name] = _bound_worker(
        worker.agentteams_resource_name
    )
    control_plane.teams["repomesh-team-pricing"] = _bound_team()

    binding = await ResolveExternalWorkerBinding(directory, control_plane).execute(  # type: ignore[arg-type]
        ExternalWorkerBindingQuery(worker_agent_id=worker.id)
    )

    assert binding.organization_id == worker.organization_id
    assert binding.worker_agent_id == worker.id
    assert binding.worker_name == worker.agentteams_resource_name
    assert binding.team_name == "repomesh-team-pricing"
    assert binding.matrix_user_id == "@worker:matrix.local"
    assert binding.allowed_room_ids == (
        "!team-pricing:matrix.local",
        "!worker:matrix.local",
    )
    assert binding.container_managed is False


@pytest.mark.asyncio
async def test_preflight_refuses_a_managed_worker() -> None:
    """The check the whole document exists for (decision 5)."""

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, worker, _ = await _repository_principals(directory, store)

    control_plane = ExternalControlPlane()
    control_plane.refs[worker.agentteams_resource_name] = _bound_worker(
        worker.agentteams_resource_name, container_managed=True
    )
    control_plane.teams["repomesh-team-pricing"] = _bound_team()

    with pytest.raises(ExternalWorkerRefused, match="containerManaged"):
        await ResolveExternalWorkerBinding(directory, control_plane).execute(  # type: ignore[arg-type]
            ExternalWorkerBindingQuery(worker_agent_id=worker.id)
        )


@pytest.mark.asyncio
async def test_preflight_refuses_a_worker_whose_document_is_silent_about_containers() -> None:
    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, worker, _ = await _repository_principals(directory, store)

    control_plane = ExternalControlPlane()
    control_plane.refs[worker.agentteams_resource_name] = _bound_worker(
        worker.agentteams_resource_name, container_managed=None
    )
    control_plane.teams["repomesh-team-pricing"] = _bound_team()

    with pytest.raises(ExternalWorkerRefused, match="containerManaged"):
        await ResolveExternalWorkerBinding(directory, control_plane).execute(  # type: ignore[arg-type]
            ExternalWorkerBindingQuery(worker_agent_id=worker.id)
        )


@pytest.mark.asyncio
async def test_preflight_refuses_an_agent_repomesh_does_not_know() -> None:
    directory = InMemoryAgentDirectory()

    with pytest.raises(UnknownExternalWorker):
        await ResolveExternalWorkerBinding(directory, ExternalControlPlane()).execute(  # type: ignore[arg-type]
            ExternalWorkerBindingQuery(worker_agent_id=uuid4())
        )


@pytest.mark.asyncio
async def test_preflight_refuses_a_worker_the_controller_has_never_heard_of() -> None:
    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, worker, _ = await _repository_principals(directory, store)

    with pytest.raises(ExternalWorkerRefused, match="not provisioned"):
        await ResolveExternalWorkerBinding(directory, ExternalControlPlane()).execute(  # type: ignore[arg-type]
            ExternalWorkerBindingQuery(worker_agent_id=worker.id)
        )


@pytest.mark.asyncio
async def test_preflight_refuses_a_worker_without_a_matrix_identity() -> None:
    """A-9 again, in the shape the Bridge meets it.

    Without ``matrixUserID`` there is no identity to sync as, and the Bridge
    would come up bound to a worker nobody can mention.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, worker, _ = await _repository_principals(directory, store)

    control_plane = ExternalControlPlane()
    control_plane.refs[worker.agentteams_resource_name] = _bound_worker(
        worker.agentteams_resource_name, matrix_user_id=None
    )
    control_plane.teams["repomesh-team-pricing"] = _bound_team()

    with pytest.raises(ExternalWorkerRefused, match="Matrix identity"):
        await ResolveExternalWorkerBinding(directory, control_plane).execute(  # type: ignore[arg-type]
            ExternalWorkerBindingQuery(worker_agent_id=worker.id)
        )


@pytest.mark.asyncio
async def test_preflight_refuses_a_worker_that_belongs_to_no_team() -> None:
    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, worker, _ = await _repository_principals(directory, store)

    control_plane = ExternalControlPlane()
    control_plane.refs[worker.agentteams_resource_name] = _bound_worker(
        worker.agentteams_resource_name, team=None
    )

    with pytest.raises(ExternalWorkerRefused, match="Team"):
        await ResolveExternalWorkerBinding(directory, control_plane).execute(  # type: ignore[arg-type]
            ExternalWorkerBindingQuery(worker_agent_id=worker.id)
        )


@pytest.mark.asyncio
async def test_preflight_refuses_a_team_whose_room_is_not_ready() -> None:
    """Room ownership is the other half of the binding, so an empty allowlist
    is a refusal rather than a binding with nothing in it."""

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, worker, _ = await _repository_principals(directory, store)

    control_plane = ExternalControlPlane()
    control_plane.refs[worker.agentteams_resource_name] = _bound_worker(
        worker.agentteams_resource_name, room_id=None
    )
    control_plane.teams["repomesh-team-pricing"] = _bound_team(room=None)

    with pytest.raises(ExternalWorkerRefused, match="room"):
        await ResolveExternalWorkerBinding(directory, control_plane).execute(  # type: ignore[arg-type]
            ExternalWorkerBindingQuery(worker_agent_id=worker.id)
        )


@pytest.mark.asyncio
async def test_preflight_refuses_a_worker_resource_that_answers_to_another_name() -> None:
    """The name is confirmed, not echoed.

    A controller answering about ``repomesh-worker-other`` for a read of this
    worker's name means the two sides disagree about which resource this
    principal is; binding to it would point the Bridge at somebody else's
    identity.
    """

    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, worker, _ = await _repository_principals(directory, store)

    control_plane = ExternalControlPlane()
    control_plane.refs[worker.agentteams_resource_name] = _bound_worker("repomesh-worker-other")
    control_plane.teams["repomesh-team-pricing"] = _bound_team()

    with pytest.raises(ExternalWorkerRefused, match="name"):
        await ResolveExternalWorkerBinding(directory, control_plane).execute(  # type: ignore[arg-type]
            ExternalWorkerBindingQuery(worker_agent_id=worker.id)
        )


@pytest.mark.asyncio
async def test_preflight_refuses_a_disabled_principal() -> None:
    directory = InMemoryAgentDirectory()
    store = InMemoryProjectTopologyStore()
    _, worker, _ = await _repository_principals(directory, store)
    retired = StubDirectory(replace(worker, status=AgentPrincipalStatus.DISABLED))

    control_plane = ExternalControlPlane()
    control_plane.refs[worker.agentteams_resource_name] = _bound_worker(
        worker.agentteams_resource_name
    )
    control_plane.teams["repomesh-team-pricing"] = _bound_team()

    with pytest.raises(ExternalWorkerRefused, match="not active"):
        await ResolveExternalWorkerBinding(retired, control_plane).execute(  # type: ignore[arg-type]
            ExternalWorkerBindingQuery(worker_agent_id=worker.id)
        )


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
