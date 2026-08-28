"""Adopting an external Repository Leader turns its team into a leader team (PR 5.5B).

Adjudication D-2 in one sentence: ``leader`` mode is a *consequence of adoption*,
not a switch. So there are only two ways for a team to end up in it, and this
file walks both — the domain latch that can raise a team but never lower it, and
the one place that pulls the latch: the reconcile pass, from the same worker
document it already reads to find out which AgentTeams Team this repository's
leader belongs to (A-8). No script, no console action and no admin route sets
this, and there is deliberately nothing here to test that would.

The last two sections are the two ends of the wire. The composition root has to
answer with the *persisted* mode rather than the placeholder that answered
``SERVER`` for everyone (A-3), and the consumer that reads it — B track's
``AdvanceExecutionPlan`` — has to actually park a batch when it does. A reader
that is right and a lane that never sees it would be two green halves of a
broken feature, so the last test drives the real adapter into the real lane.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from project.test_agent_topology import RecordingControlPlane, build_agents
from task_orchestration.test_plan_execution import Environment

from repomesh.bootstrap.container import ApplicationContainer
from repomesh.integrations.agentteams.project_topology import ReconcileProjectAgentTopology
from repomesh.integrations.agentteams.runtime_projection import ProjectRuntimeProjection
from repomesh.modules.agent_runtime.ports.agent_team import ManagerRuntimeRef, WorkerRuntimeRef
from repomesh.modules.project import (
    CreateProjectAgentTopology,
    CreateProjectAgentTopologyRequest,
    RepositoryTeamAssignment,
)
from repomesh.modules.project.contracts import ProjectTeamRuntimeStatus, TeamDecompositionMode
from repomesh.modules.project.domain import ProjectAgentTopology, RepositoryTeam
from repomesh.modules.project.infrastructure import (
    InMemoryProjectTopologyStore,
    PersistedTeamDecompositionModeReader,
)
from repomesh.settings import Settings

_DEFAULTS = Settings()
_RUNTIMES = {
    "manager_runtime": _DEFAULTS.agentteams_manager_runtime,
    "worker_runtime": _DEFAULTS.agentteams_worker_runtime,
}


class LeaderAwareControlPlane(RecordingControlPlane):
    """A controller that answers about the repository leaders it was told about.

    ``RecordingControlPlane.get_worker`` answers ``None`` for everything — a
    controller that has seen nobody, which is what every existing reconcile test
    wants. Here the answer is the whole subject: the worker document for a
    leader carries ``containerManaged``, and that field is the only input the
    adoption pass has.
    """

    def __init__(self, workers: dict[str, WorkerRuntimeRef | None] | None = None) -> None:
        super().__init__()
        self._workers = workers or {}
        self.worker_reads: list[str] = []

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        self.worker_reads.append(name)
        return self._workers.get(name)


def _document(name: str, team: str | None, container_managed: bool | None) -> WorkerRuntimeRef:
    return WorkerRuntimeRef(
        name=name,
        phase="Ready",
        # Set on every document: ``ProjectRuntimeProjection`` refuses a worker
        # with no Matrix identity, and an absent one here would fail the
        # projection test for a reason that is not its subject.
        matrix_user_id=f"@{name}:matrix.local",
        team=team,
        container_managed=container_managed,
    )


def external(name: str, *, team: str | None = None) -> WorkerRuntimeRef:
    """A leader the controller does not containerize: a Bridge is serving it."""

    return _document(name, team, False)


def managed(name: str, *, team: str | None = None) -> WorkerRuntimeRef:
    return _document(name, team, True)


def unknown(name: str, *, team: str | None = None) -> WorkerRuntimeRef:
    """A worker document that did not carry ``containerManaged`` at all.

    Never "external": the field is ``None`` when the answer did not come from a
    controller that knows it, and a guess here would provision a leader lane for
    a team whose leader has a container starting under it.
    """

    return _document(name, team, None)


class ProvisionedControlPlane(LeaderAwareControlPlane):
    """A controller that already holds every resource this project needs.

    What E1 leaves behind: the principals were pre-built and the leader was
    provisioned external through PR 5.5A's route, so materialize has nothing to
    create and everything to adopt. Every name it is asked about answers with a
    document, which is what lets ``ProjectRuntimeProjection`` get past its own
    identity check and reach the question this file is about.
    """

    def __init__(self, leader_name: str) -> None:
        super().__init__({leader_name: external(leader_name)})

    async def get_manager(self, name: str) -> ManagerRuntimeRef | None:
        return ManagerRuntimeRef(name, "Ready", matrix_user_id=f"@{name}:matrix.local")

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        return await super().get_worker(name) or managed(name)


def a_team(mode: TeamDecompositionMode = TeamDecompositionMode.SERVER) -> RepositoryTeam:
    return RepositoryTeam(
        project_id=uuid4(),
        repository_id=uuid4(),
        leader_agent_id=uuid4(),
        worker_agent_ids=(uuid4(),),
        decomposition_mode=mode,
    )


# ---------------------------------------------------------------------------
# The domain latch: raise only, and only on an observation
# ---------------------------------------------------------------------------


def test_a_team_is_server_side_until_something_says_otherwise() -> None:
    assert a_team().decomposition_mode is TeamDecompositionMode.SERVER


def test_an_external_leader_raises_the_team_into_leader_mode() -> None:
    adopted = a_team().with_adopted_leader(external=True)
    assert adopted.decomposition_mode is TeamDecompositionMode.LEADER


def test_a_leader_nobody_observed_leaves_a_fresh_team_alone() -> None:
    team = a_team()
    assert team.with_adopted_leader(external=False) is team


def test_adopting_an_adopted_team_is_the_same_object() -> None:
    """Idempotent by identity, so a re-run of materialize rewrites nothing."""

    adopted = a_team(TeamDecompositionMode.LEADER)
    assert adopted.with_adopted_leader(external=True) is adopted


def test_an_unobserved_leader_never_demotes_an_adopted_team() -> None:
    """The no-downgrade invariant, stated where it is enforced.

    ``external=False`` means "this pass saw no external leader" — a controller
    that did not answer, a document without the field. Reading it as "this is
    not a leader team" would decompose and dispatch work the leader was in the
    middle of planning, from a plan nobody submitted.
    """

    adopted = a_team(TeamDecompositionMode.LEADER)
    assert adopted.with_adopted_leader(external=False) is adopted
    assert adopted.decomposition_mode is TeamDecompositionMode.LEADER


def test_the_mode_reaches_the_view_the_contract_freezes() -> None:
    view = a_team(TeamDecompositionMode.LEADER).to_view()
    assert view.decomposition_mode is TeamDecompositionMode.LEADER
    assert a_team().to_view().decomposition_mode is TeamDecompositionMode.SERVER


# ---------------------------------------------------------------------------
# The adoption pass: one read, two facts
# ---------------------------------------------------------------------------


async def _topology_with_one_team(store: InMemoryProjectTopologyStore):
    directory, organization_id, organization_leader, teams = await build_agents(1)
    project_id = uuid4()
    await CreateProjectAgentTopology(directory, store).execute(
        CreateProjectAgentTopologyRequest(
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=organization_leader.id,
            repository_teams=(
                RepositoryTeamAssignment(
                    repository_id=teams[0].repository_id,
                    leader_agent_id=teams[0].leader.id,
                    worker_agent_ids=tuple(worker.id for worker in teams[0].workers),
                ),
            ),
        ),
        idempotency_key=f"adoption-{project_id}",
    )
    return directory, project_id, teams[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (external, TeamDecompositionMode.LEADER),
        (managed, TeamDecompositionMode.SERVER),
        (unknown, TeamDecompositionMode.SERVER),
    ],
    ids=["external-leader-is-adopted", "managed-leader-is-not", "unknown-is-not"],
)
async def test_the_reconcile_sets_the_mode_from_the_leaders_worker_document(
    document, expected: TeamDecompositionMode
) -> None:
    store = InMemoryProjectTopologyStore()
    directory, project_id, team = await _topology_with_one_team(store)
    principal = await directory.get_view(team.leader.id)
    control_plane = LeaderAwareControlPlane(
        {principal.agentteams_resource_name: document(principal.agentteams_resource_name)}
    )

    view = await ReconcileProjectAgentTopology(directory, store, control_plane).execute(project_id)

    assert view.repository_teams[0].decomposition_mode is expected
    stored = await store.get(project_id)
    assert stored.repository_teams[0].decomposition_mode is expected


@pytest.mark.asyncio
async def test_a_leader_the_controller_has_never_seen_leaves_the_team_server_side() -> None:
    """No worker document at all: nothing has been provisioned, nothing is adopted."""

    store = InMemoryProjectTopologyStore()
    directory, project_id, _ = await _topology_with_one_team(store)

    view = await ReconcileProjectAgentTopology(
        directory, store, LeaderAwareControlPlane()
    ).execute(project_id)

    assert view.repository_teams[0].decomposition_mode is TeamDecompositionMode.SERVER


@pytest.mark.asyncio
async def test_the_mode_and_the_team_name_come_from_one_read() -> None:
    """Adoption of the identity and activation of the mode are one decision.

    Two reads could answer differently — ``ensure_team`` runs between them — and
    a team would then be adopted under a Team name observed at one instant and a
    mode observed at another.
    """

    store = InMemoryProjectTopologyStore()
    directory, project_id, team = await _topology_with_one_team(store)
    principal = await directory.get_view(team.leader.id)
    control_plane = LeaderAwareControlPlane(
        {
            principal.agentteams_resource_name: external(
                principal.agentteams_resource_name, team="adopted-team-elsewhere"
            )
        }
    )

    view = await ReconcileProjectAgentTopology(directory, store, control_plane).execute(project_id)

    assert control_plane.worker_reads == [principal.agentteams_resource_name]
    assert view.repository_teams[0].agentteams_team_name == "adopted-team-elsewhere"
    assert view.repository_teams[0].decomposition_mode is TeamDecompositionMode.LEADER


@pytest.mark.asyncio
async def test_reconciling_again_keeps_the_team_adopted() -> None:
    """Acceptance: the re-run is idempotent, not a second adoption."""

    store = InMemoryProjectTopologyStore()
    directory, project_id, team = await _topology_with_one_team(store)
    principal = await directory.get_view(team.leader.id)
    control_plane = LeaderAwareControlPlane(
        {principal.agentteams_resource_name: external(principal.agentteams_resource_name)}
    )
    reconcile = ReconcileProjectAgentTopology(directory, store, control_plane)

    first = await reconcile.execute(project_id)
    second = await reconcile.execute(project_id)

    assert first.repository_teams[0].decomposition_mode is TeamDecompositionMode.LEADER
    assert second.repository_teams[0].decomposition_mode is TeamDecompositionMode.LEADER
    assert second.repository_teams[0].id == first.repository_teams[0].id


@pytest.mark.asyncio
@pytest.mark.parametrize("later", [managed, unknown, None], ids=["managed", "unknown", "absent"])
async def test_a_later_pass_that_sees_no_external_leader_does_not_demote(later) -> None:
    """The invariant that matters most, at the layer an operator would hit it.

    The controller going quiet, or answering a document without
    ``containerManaged``, is exactly what happens during an outage — and a team
    demoted mid-round would have its parked batch decomposed server-side from a
    plan its leader never submitted.
    """

    store = InMemoryProjectTopologyStore()
    directory, project_id, team = await _topology_with_one_team(store)
    principal = await directory.get_view(team.leader.id)
    name = principal.agentteams_resource_name

    adopted = LeaderAwareControlPlane({name: external(name)})
    await ReconcileProjectAgentTopology(directory, store, adopted).execute(project_id)

    degraded = LeaderAwareControlPlane({name: later(name)} if later is not None else {})
    view = await ReconcileProjectAgentTopology(directory, store, degraded).execute(project_id)

    assert view.repository_teams[0].decomposition_mode is TeamDecompositionMode.LEADER
    stored = await store.get(project_id)
    assert stored.repository_teams[0].decomposition_mode is TeamDecompositionMode.LEADER


@pytest.mark.asyncio
async def test_an_already_provisioned_external_leader_is_not_rebuilt_as_managed() -> None:
    """Acceptance: adoption, not a managed stand-in.

    The pre-provisioning E1 does is what makes a leader external in the first
    place, and a projection that registered it again would ask the controller
    for a worker with ``containerManaged`` defaulted back to true.
    """

    store = InMemoryProjectTopologyStore()
    directory, project_id, team = await _topology_with_one_team(store)
    principal = await directory.get_view(team.leader.id)
    name = principal.agentteams_resource_name
    control_plane = ProvisionedControlPlane(name)

    view = await ProjectRuntimeProjection(
        directory,
        store,
        control_plane,
        model="deepseek-chat",
        **_RUNTIMES,
    ).project(project_id)

    # ``workers`` is what ``ensure_worker`` recorded — the create path. Nothing
    # was created, the leader least of all, and the team came out adopted.
    assert control_plane.workers == []
    assert control_plane.managers == []
    assert name in control_plane.worker_reads
    assert view.repository_teams[0].decomposition_mode is TeamDecompositionMode.LEADER


# ---------------------------------------------------------------------------
# The reader: the persisted answer, and every absence is SERVER
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_reader_answers_the_adopted_mode() -> None:
    store = InMemoryProjectTopologyStore()
    directory, project_id, team = await _topology_with_one_team(store)
    principal = await directory.get_view(team.leader.id)
    name = principal.agentteams_resource_name
    await ReconcileProjectAgentTopology(
        directory, store, LeaderAwareControlPlane({name: external(name)})
    ).execute(project_id)

    reader = PersistedTeamDecompositionModeReader(store)

    assert (
        await reader.decomposition_mode(project_id, team.repository_id)
        is TeamDecompositionMode.LEADER
    )


@pytest.mark.asyncio
async def test_every_absence_reads_as_server() -> None:
    """A project with no topology, a repository with no team: same answer.

    The protocol has no error channel on purpose — absence of an adopted
    external leader is exactly what ``SERVER`` means.
    """

    store = InMemoryProjectTopologyStore()
    _, project_id, team = await _topology_with_one_team(store)
    reader = PersistedTeamDecompositionModeReader(store)

    assert await reader.decomposition_mode(uuid4(), uuid4()) is TeamDecompositionMode.SERVER
    assert (
        await reader.decomposition_mode(project_id, uuid4()) is TeamDecompositionMode.SERVER
    )
    assert (
        await reader.decomposition_mode(project_id, team.repository_id)
        is TeamDecompositionMode.SERVER
    )


@pytest.mark.asyncio
async def test_the_mode_survives_a_real_store_round_trip(
    application_container: ApplicationContainer,
) -> None:
    """The column, not just the dataclass field.

    Driven through the container's own store so the write path, the read path
    and the wiring under test are the ones a deployment runs.
    """

    store = application_container.project_topology_store
    directory, organization_id, organization_leader, teams = await build_agents(1)
    project_id = uuid4()
    await CreateProjectAgentTopology(directory, store).execute(
        CreateProjectAgentTopologyRequest(
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=organization_leader.id,
            repository_teams=(
                RepositoryTeamAssignment(
                    repository_id=teams[0].repository_id,
                    leader_agent_id=teams[0].leader.id,
                    worker_agent_ids=tuple(worker.id for worker in teams[0].workers),
                ),
            ),
        ),
        idempotency_key=f"persisted-adoption-{project_id}",
    )
    principal = await directory.get_view(teams[0].leader.id)
    name = principal.agentteams_resource_name

    # Fresh out of creation the column says what every pre-0037 row says.
    reader = application_container.team_decomposition_mode_reader()
    assert isinstance(reader, PersistedTeamDecompositionModeReader)
    assert (
        await reader.decomposition_mode(project_id, teams[0].repository_id)
        is TeamDecompositionMode.SERVER
    )

    await ReconcileProjectAgentTopology(
        directory, store, LeaderAwareControlPlane({name: external(name)})
    ).execute(project_id)

    assert (
        await reader.decomposition_mode(project_id, teams[0].repository_id)
        is TeamDecompositionMode.LEADER
    )
    stored = await store.get(project_id)
    assert stored.repository_teams[0].decomposition_mode is TeamDecompositionMode.LEADER


# ---------------------------------------------------------------------------
# The wire, end to end: the persisted mode really parks a batch
# ---------------------------------------------------------------------------


def _persisted_topology(
    environment: Environment, *, mode: TeamDecompositionMode
) -> InMemoryProjectTopologyStore:
    """The consumer harness's own topology, as the project module would store it.

    Built from ``Environment``'s ids rather than from fresh ones: the reader
    resolves a mode by ``(project_id, repository_id)``, so a store keyed on
    anything else would answer ``SERVER`` for a reason that has nothing to do
    with what is under test and the lane would look correctly wired while
    reading nobody's row.
    """

    store = InMemoryProjectTopologyStore()
    store._topologies[environment.project_id] = ProjectAgentTopology(  # noqa: SLF001
        organization_id=environment.organization_id,
        project_id=environment.project_id,
        organization_leader_id=environment.organization_leader_id,
        repository_teams=(
            RepositoryTeam(
                project_id=environment.project_id,
                repository_id=environment.repository_ids[0],
                leader_agent_id=environment.leader_ids[0],
                worker_agent_ids=(environment.worker_ids[0],),
                runtime_status=ProjectTeamRuntimeStatus.READY,
                decomposition_mode=mode,
            ),
        ),
    )
    return store


async def _round_with_persisted_mode(mode: TeamDecompositionMode) -> Environment:
    """B track's real lane, reading B track's real consumer through *this* PR's adapter."""

    environment = Environment(repository_count=1, leader_mode_repositories=(0,))
    reader = PersistedTeamDecompositionModeReader(_persisted_topology(environment, mode=mode))
    environment.advancer._leader_lane = replace(  # noqa: SLF001 - the collaborator under test
        environment.leader_lane, modes=reader
    )
    await environment.advancer.start(
        environment.plan(((0,),), tests={0: ("uv run pytest -q",)}),
        idempotency_key=f"persisted-{mode.value}-mode",
    )
    return environment


@pytest.mark.asyncio
async def test_a_persisted_leader_team_actually_parks_the_batch() -> None:
    """Acceptance: the real adapter driven into B track's real lane.

    ``Environment`` is the consumer's own harness, borrowed rather than
    reproduced; the only substitution is the one under test — its fake mode
    reader is replaced by ``PersistedTeamDecompositionModeReader`` over a stored
    topology. A reader that is right and a lane that never sees it would be two
    green halves of a broken feature, and nothing either side tests alone would
    catch it.
    """

    environment = await _round_with_persisted_mode(TeamDecompositionMode.LEADER)
    plan_id = (await environment.plans.list_all())[0].id
    leader_task_id = await environment.leader_task_id(plan_id, 0, 0)

    # Stopped: the leader task exists and nothing was expanded under it.
    assert await environment.tasks.get(leader_task_id) is not None
    assert await environment.tasks.list_by_parent(leader_task_id) == ()
    assert environment.recorded_specifications == []
    # Parked: the assignment the leader-actions surface will read.
    assignment = await environment.leader_assignments.get(leader_task_id)
    assert assignment is not None
    assert assignment.repository_id == environment.repository_ids[0]


@pytest.mark.asyncio
async def test_a_persisted_server_team_takes_the_path_it_always_did() -> None:
    """The other half of the same wire, and the D-2 default.

    Same lane, same adapter, same harness — only the stored mode differs. An
    installation that has adopted nobody reads this path for every team, so
    "unchanged" is a claim worth making against the real reader rather than
    against its absence.
    """

    environment = await _round_with_persisted_mode(TeamDecompositionMode.SERVER)
    plan_id = (await environment.plans.list_all())[0].id
    leader_task_id = await environment.leader_task_id(plan_id, 0, 0)

    assert await environment.tasks.list_by_parent(leader_task_id) != ()
    assert await environment.leader_assignments.get(leader_task_id) is None
