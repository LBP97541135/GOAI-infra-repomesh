"""Give an existing project topology a runtime (defect B-11).

``scripts/run_pipeline.py`` bootstraps a project in one pass: it registers each
agent with the AgentTeams controller (``RegisterNativeAgent``), writes the
topology, and then reconciles it into Teams and Matrix rooms
(``ReconcileProjectAgentTopology``). Until now those were its *only* callers —
``src/`` had none — so a project born on the console path got directory rows
and a topology and no runtime at all: ``room_id`` NULL, and every dispatch
answering ``CollaborationRouteUnavailable``.

This class is the second half of that pass, on its own, for a topology that
already exists. Two differences from the script, both forced by the situation:

*The principals are already in the directory.* ``ProvisionRepositoryAgentTeam``
created them on the way to the topology, and ``CreateAgent`` is create-shaped
(a repository leader is a global singleton, so a second call raises rather than
converging). So only the controller half of ``RegisterNativeAgent`` runs here;
the resource name comes from the principal that already exists, which is what
makes the two paths converge on one AgentTeams resource per agent.

*The projections must match the script's, field for field.* The controller
compares an existing resource against the one being asked for and answers 409
on a mismatch, so a repository first staffed by ``run_pipeline.py`` and later
reached by the console would conflict on nothing more than a different skill
list. The values below are the script's. Field-for-field parity is the second
line of defence, though, not the first: since track P's P2 this class *reads*
a resource that already exists rather than asking for it again, because a
repository staffed through onboarding carries a runtime, model and skill list
chosen per agent that no global default can reproduce.

``runtime`` is the one field that is no longer copied. It used to be
``OPENCLAW`` written out here *and* in the script, which is two places to keep
in step and a value neither of them could be right about: whether a runtime
works is a property of the controller, which pairs each runtime with its own
image env. Both paths now read
``REPOMESH_AGENTTEAMS_{MANAGER,WORKER}_RUNTIME``, so the parity is structural —
there is no second value left to drift (defect A-6).

Everything here is re-entrant, and has to be: materialize is, so a retry of a
half-executed round runs this again from the top. Re-entrant is not the same
as ensure-shaped, which is what P2 corrected — ``_register`` reads a resource
that exists and only creates the ones that do not, while ``ensure_team`` stays
ensure-shaped because a Team genuinely converges (and adoption, A-8, depends
on it).

``ExternalWorkerProjection`` at the bottom is the same projection with one
field flipped, for the one worker whose body runs outside the cluster (ADR
0004). It lives here rather than beside the use case that drives it precisely
because of the field-for-field rule above: the two projections have to be built
from the same values, and those values are here.
"""

from __future__ import annotations

from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalReader, AgentRole
from repomesh.modules.agent_runtime.ports.agent_team import (
    AgentTeamControlPlane,
    DesiredRuntimeState,
    ExternalMemberRole,
    ManagerProjection,
    ManagerRuntime,
    TeamRuntimeRef,
    WorkerControlPlaneUnavailable,
    WorkerProjection,
    WorkerRuntime,
    WorkerRuntimeRef,
)
from repomesh.modules.project.contracts import ProjectAgentTopologyView
from repomesh.modules.project.domain import ProjectTopologyViolation
from repomesh.modules.project.ports import ProjectTopologyStore
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog

from .control_plane import AgentTeamsConflict, AgentTeamsError, AgentTeamsUnavailable
from .principal_registration import with_task_control
from .project_topology import ReconcileProjectAgentTopology
from .team_skills import agentteams_skills


class AgentTeamsRoomsPending(AgentTeamsError):
    """The controller took the Teams but has not published their rooms yet."""


class AgentTeamsResourceMismatch(AgentTeamsConflict):
    """A read came back naming a resource other than the one asked about.

    RepoMesh and the controller disagree about which AgentTeams resource this
    principal *is*. Registering the topology on that answer would put another
    identity in this repository's Team, so it is a refusal — and an
    ``AgentTeamsConflict`` rather than a wait, because no retry makes two names
    agree (the composition root maps conflicts to 409 and waits to 503).
    """


class AgentTeamsIdentitiesPending(AgentTeamsError):
    """The controller took the Workers but has not given them Matrix identities.

    The worker half of the room defect (A-9). ``POST /api/v1/workers`` returns
    201 with the full spec the moment the resource is written, but the Matrix
    account, the container and the personal room are the work of the
    controller's worker reconciler — which is serial and, on a host carrying
    many Workers, minutes behind the write. Between the two,
    ``GET /api/v1/workers/<name>`` answers with ``matrixUserID`` absent.

    That gap is not cosmetic. ``AgentTeamsMessenger.send_task`` resolves the
    recipient's Matrix ID from exactly that field and refuses without it, so a
    round materialised in the gap starts, dispatches, and dies — and dispatch
    is not a button the operator has. Refusing here spends the same failure on
    materialize, which is.
    """



def _assert_bound(kind: str, answered: str, expected: str) -> None:
    """Confirm a read is about the resource the principal is bound to.

    The name is the whole binding between a RepoMesh principal and its
    AgentTeams resource, so a read answering under another one means the two
    sides disagree about which resource this is. ``ResolveExternalWorkerBinding``
    makes the same check for the same reason; here it guards the topology,
    because a mismatch registered as a member puts another identity in this
    repository's Team.
    """

    if answered != expected:
        raise AgentTeamsResourceMismatch(
            f"AgentTeams answered for a different {kind} name: {answered} != {expected}"
        )


#: Skills per role, copied from ``scripts/run_pipeline.py::_ensure_topology``.
#: Not decoration: ``ensure_worker`` compares this list against an existing
#: worker's and refuses the pair as a conflict when they differ.
_SKILLS: dict[AgentRole, tuple[str, ...]] = {
    AgentRole.ORGANIZATION_LEADER: ("planning", "coordination"),
    AgentRole.REPOSITORY_LEADER: ("code-review", "planning"),
    AgentRole.WORKER: ("coding",),
}

#: The wire role a Bridge is bound under, back to the directory role whose
#: projection the controller already holds. Total by construction — the enum has
#: no third member — so external provisioning reaches ``_SKILLS`` by the same key
#: the ordinary project path uses, rather than by a constant beside it.
_AGENT_ROLES: dict[ExternalMemberRole, AgentRole] = {
    ExternalMemberRole.WORKER: AgentRole.WORKER,
    ExternalMemberRole.REPOSITORY_LEADER: AgentRole.REPOSITORY_LEADER,
}


class ProjectRuntimeProjection:
    """Register a topology's agents with the controller, then reconcile it.

    Satisfies ``repository_intelligence.ports.TopologyRuntimeProjector``
    structurally; the composition root translates this module's error taxonomy
    into that port's single refusal.
    """

    def __init__(
        self,
        directory: AgentPrincipalReader,
        store: ProjectTopologyStore,
        control_plane: AgentTeamControlPlane,
        *,
        model: str,
        manager_runtime: ManagerRuntime,
        worker_runtime: WorkerRuntime,
        manager_image: str | None = None,
        worker_task_control_url: str | None = None,
        repository_catalog: RepositoryCatalog | None = None,
    ) -> None:
        self._directory = directory
        self._store = store
        self._control_plane = control_plane
        self._model = model
        # Injected, not read: this package never touches ``settings`` — the
        # composition root owns configuration. Required rather than defaulted,
        # so the value cannot quietly disagree with the one
        # ``scripts/run_pipeline.py`` uses; both take it from
        # ``REPOMESH_AGENTTEAMS_{MANAGER,WORKER}_RUNTIME``.
        self._manager_runtime = manager_runtime
        self._worker_runtime = worker_runtime
        # Same injection rule, same single source
        # (``REPOMESH_AGENTTEAMS_MANAGER_IMAGE``): the controller's image
        # fallback is role-blind, so an imageless manager CR boots on the
        # worker image and exits before its first Matrix sync. Optional and
        # None by default — a deployment whose controller pairs a
        # manager-capable default keeps the controller's choice.
        self._manager_image = manager_image
        self._worker_task_control_url = worker_task_control_url
        # Optional because the skills overlay is the only thing this class
        # wants from the catalog, and a composition root without one (tests)
        # must keep projecting default-profile resources exactly as before.
        self._repository_catalog = repository_catalog
        self._reconcile = ReconcileProjectAgentTopology(directory, store, control_plane)

    async def project(self, project_id: UUID) -> ProjectAgentTopologyView:
        topology = await self._store.get(project_id)
        if topology is None:
            raise ProjectTopologyViolation(
                f"project topology does not exist: {project_id}"
            )

        await self._register(topology.organization_leader_id, project_id)
        workers: list[str] = []
        for team in topology.repository_teams:
            for agent_id in (team.leader_agent_id, *team.worker_agent_ids):
                workers.append(await self._register(agent_id, project_id))

        view = await self._reconcile.execute(project_id)
        self._assert_rooms(view)
        await self._assert_identities(workers)
        return view

    # ------------------------------------------------------------ registration

    async def _register(self, agent_id: UUID, project_id: UUID) -> str:
        """Read the resource this principal is bound to, or create it.

        Read *first*, and this is the correction that ends the 409 deadlock
        (arch-team T2 §6). A repository staffed through onboarding already has
        its Manager and Workers, each carrying the runtime, model and skills
        chosen for it — the console offers a runtime per worker while this pass
        has one global default for all of them. Re-``ensure``-ing them with that
        global spec is not idempotent: the controller compares an existing
        resource against the one being asked for and answers
        ``409 ... differs in: runtime``, which no retry clears, so materialize
        was unreachable for every repository that had ever been staffed.

        Deliberately not fixed by agreeing on a better default. Onboarding
        creates and binds; materialize reads and validates. A default of a
        different colour would only move the conflict to ``model``, then to
        ``skills``, then to the MCP servers — the same deadlock one field along.

        What is validated of an existing resource is what this pass is about to
        rely on, and no more: that it is the *kind* the principal's role calls
        for (which is the endpoint the read goes to — a Manager and a Worker are
        different collections) and that the controller answered about the name
        this principal is bound to. Identity and readiness are checked too, but
        after the reconcile rather than here: ``_assert_identities`` re-reads
        every worker once the Teams exist, on purpose (its docstring), because
        refusing before the reconcile turns a wait into a deadlock — the retry
        would find the reconcile exactly where it left it.

        A resource that does not exist takes the unchanged create path, spec and
        idempotency key and all: this pass is still the only thing that staffs a
        fresh project.
        """

        principal = await self._directory.get_view(agent_id)
        if principal is None:
            raise ProjectTopologyViolation(f"agent binding does not exist: {agent_id}")

        # The capability profile reaches the controller only on creation (the
        # read-first rule above), so it is resolved per principal here — a
        # repository-level property, read from the catalog when one is wired.
        # The org leader has no repository and keeps the default tuple.
        profile = None
        if self._repository_catalog is not None and principal.repository_id is not None:
            repository = await self._repository_catalog.get(principal.repository_id)
            profile = repository.capability_profile if repository is not None else None

        # Keyed on the agent, not on the round: the same agent projected by a
        # second round, or by a retry under a fresh idempotency key, must be
        # the same controller side effect rather than a new one.
        key = f"project:{project_id}:agent:{agent_id}"
        name = principal.agentteams_resource_name
        if principal.role is AgentRole.ORGANIZATION_LEADER:
            manager = await self._control_plane.get_manager(name)
            if manager is not None:
                _assert_bound("manager", manager.name, name)
                return name
            await self._control_plane.ensure_manager(
                ManagerProjection(
                    name=name,
                    model=self._model,
                    runtime=self._manager_runtime,
                    image=self._manager_image,
                    skills=_SKILLS[AgentRole.ORGANIZATION_LEADER],
                ),
                idempotency_key=f"{key}:agentteams",
            )
            return name
        worker = await self._control_plane.get_worker(name)
        if worker is not None:
            _assert_bound("worker", worker.name, name)
            return name
        await self._control_plane.ensure_worker(
            with_task_control(
                WorkerProjection(
                    name=name,
                    model=self._model,
                    runtime=self._worker_runtime,
                    skills=agentteams_skills(
                        principal.role, _SKILLS[principal.role], profile=profile
                    ),
                    state=DesiredRuntimeState.RUNNING,
                ),
                self._worker_task_control_url,
            ),
            idempotency_key=f"{key}:agentteams",
        )
        return name

    # ------------------------------------------------------------------ rooms

    @staticmethod
    def _assert_rooms(view: ProjectAgentTopologyView) -> None:
        """Refuse a reconcile that produced teams without rooms.

        The point of the whole step. ``ensure_team`` answers as soon as the
        Team resource exists, which can be before the controller has created
        its Matrix rooms — and a team with ``room_id`` NULL is exactly the
        state B-11 was: the round starts, the first dispatch raises
        ``CollaborationRouteUnavailable``, and there is no button for
        "dispatch it again". Saying so before the plan starts turns that into
        a retry of materialize, which is a button the operator has.

        Both rooms are required because both are used: the team room carries
        leader-to-worker traffic and the leader room carries the organization
        leader's assignments (``collaboration.SendCollaborationMessage._route``).
        """

        pending = tuple(
            team.agentteams_team_name or str(team.repository_id)
            for team in view.repository_teams
            if not team.room_id or not team.leader_room_id
        )
        if pending:
            raise AgentTeamsRoomsPending(
                "the AgentTeams controller has not created rooms for "
                f"{', '.join(sorted(pending))} yet"
            )

    # -------------------------------------------------------------- identities

    async def _assert_identities(self, workers: list[str]) -> None:
        """Refuse workers the controller has not yet given a Matrix identity.

        Re-read rather than kept from ``ensure_worker``: that answer is from
        before the teams were reconciled, and a worker whose reconciler ran in
        between would be refused on a stale reading. The extra GET is per
        worker in one project, and only on the path that already talks to the
        controller several times.

        ``matrixUserID`` and not the phase or the container state: it is the
        one field dispatch reads (``AgentTeamsMessenger.send_task``), so it is
        the one field whose absence is a refusal here rather than a slower
        first turn.
        """

        pending = []
        for name in workers:
            worker = await self._control_plane.get_worker(name)
            if worker is None or not worker.matrix_user_id:
                pending.append(name)
        if pending:
            raise AgentTeamsIdentitiesPending(
                "the AgentTeams controller has not provisioned Matrix "
                f"identities for {', '.join(sorted(pending))} yet"
            )


class ExternalWorkerProjection:
    """Project one worker the controller must *not* containerize (ADR 0004 §2).

    Satisfies ``agent_runtime.ports.agent_team.ExternalWorkerProvisioner``. It
    is deliberately the same projection ``ProjectRuntimeProjection._register``
    builds, with one field flipped, and that is the whole design: the
    controller compares an existing worker against the one being asked for, so
    a projection that also drifted on skills, model, runtime or the
    task-control MCP server would answer 409 about *that*. The operator would
    read a spurious mismatch where the real answer is "this worker is already
    managed, and converting it is not something you get to do silently".

    Nothing here decides *whether* an agent may be external — that is the
    application use case's question, asked of RepoMesh's own principal.

    ``get_worker``/``get_team`` below make it a ``WorkerBindingReader`` —
    RepoMesh's server-side mirror of the Bridge's ``WorkerBindingPort``, and
    the only shape the preflight is handed. They answer the same question a
    plain ``AgentTeamControlPlane`` would, for the one caller that needs an
    unreachable controller to read as ``WorkerControlPlaneUnavailable``
    rather than this module's own ``AgentTeamsUnavailable``:
    ``ResolveExternalWorkerBinding``, the bridge preflight's use case, is
    application code and may not import ``repomesh.integrations.*`` to catch
    the latter itself. Translating here — the adapter that already imports
    it — is what lets the router catch a module-owned exception instead.
    """

    def __init__(
        self,
        control_plane: AgentTeamControlPlane,
        *,
        model: str,
        worker_runtime: WorkerRuntime,
        worker_task_control_url: str | None = None,
    ) -> None:
        self._control_plane = control_plane
        self._model = model
        self._worker_runtime = worker_runtime
        self._worker_task_control_url = worker_task_control_url

    async def provision(
        self,
        name: str,
        *,
        idempotency_key: str,
        role: ExternalMemberRole = ExternalMemberRole.WORKER,
    ) -> WorkerRuntimeRef:
        """Ask the controller for this member's projection with one field flipped.

        ``role`` picks the skills, and only the skills — everything else about
        an external member is what the ordinary path already sends. It has a
        default because the v1 provisioning path has no role to pass and must
        keep sending exactly what it sent before; ``ExternalMemberRole.WORKER``
        reproduces the previous constant.

        It is not cosmetic. ``ensure_worker`` compares an existing worker
        against the one being requested, and a repository leader carries
        ``("code-review", "planning")`` wherever the ordinary project path
        registered it — so provisioning that same principal with a worker's
        ``("coding",)`` answers 409 about skills. That is the R0 failure mode
        one field over: a Repository Leader who parses v2 fine and still cannot
        be provisioned.
        """

        return await self._control_plane.ensure_worker(
            with_task_control(
                WorkerProjection(
                    name=name,
                    model=self._model,
                    runtime=self._worker_runtime,
                    skills=_SKILLS[_AGENT_ROLES[role]],
                    state=DesiredRuntimeState.RUNNING,
                    container_managed=False,
                ),
                self._worker_task_control_url,
            ),
            idempotency_key=idempotency_key,
        )

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        try:
            return await self._control_plane.get_worker(name)
        except AgentTeamsUnavailable as error:
            raise WorkerControlPlaneUnavailable(str(error)) from error

    async def get_team(self, name: str) -> TeamRuntimeRef | None:
        try:
            return await self._control_plane.get_team(name)
        except AgentTeamsUnavailable as error:
            raise WorkerControlPlaneUnavailable(str(error)) from error


__all__ = [
    "AgentTeamsIdentitiesPending",
    "AgentTeamsResourceMismatch",
    "AgentTeamsRoomsPending",
    "ExternalWorkerProjection",
    "ProjectRuntimeProjection",
]
