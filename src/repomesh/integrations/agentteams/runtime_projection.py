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
list. The values below are the script's.

``runtime`` is the one field that is no longer copied. It used to be
``OPENCLAW`` written out here *and* in the script, which is two places to keep
in step and a value neither of them could be right about: whether a runtime
works is a property of the controller, which pairs each runtime with its own
image env. Both paths now read
``REPOMESH_AGENTTEAMS_{MANAGER,WORKER}_RUNTIME``, so the parity is structural —
there is no second value left to drift (defect A-6).

Everything here is ensure-shaped, and has to be: materialize is re-entrant, so
a retry of a half-executed round runs this again from the top.

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
    ManagerProjection,
    ManagerRuntime,
    WorkerProjection,
    WorkerRuntime,
    WorkerRuntimeRef,
)
from repomesh.modules.project.contracts import ProjectAgentTopologyView
from repomesh.modules.project.domain import ProjectTopologyViolation
from repomesh.modules.project.ports import ProjectTopologyStore

from .control_plane import AgentTeamsError
from .principal_registration import with_task_control
from .project_topology import ReconcileProjectAgentTopology


class AgentTeamsRoomsPending(AgentTeamsError):
    """The controller took the Teams but has not published their rooms yet."""


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



#: Skills per role, copied from ``scripts/run_pipeline.py::_ensure_topology``.
#: Not decoration: ``ensure_worker`` compares this list against an existing
#: worker's and refuses the pair as a conflict when they differ.
_SKILLS: dict[AgentRole, tuple[str, ...]] = {
    AgentRole.ORGANIZATION_LEADER: ("planning", "coordination"),
    AgentRole.REPOSITORY_LEADER: ("code-review", "planning"),
    AgentRole.WORKER: ("coding",),
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
        worker_task_control_url: str | None = None,
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
        self._worker_task_control_url = worker_task_control_url
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
        principal = await self._directory.get_view(agent_id)
        if principal is None:
            raise ProjectTopologyViolation(f"agent binding does not exist: {agent_id}")

        # Keyed on the agent, not on the round: the same agent projected by a
        # second round, or by a retry under a fresh idempotency key, must be
        # the same controller side effect rather than a new one.
        key = f"project:{project_id}:agent:{agent_id}"
        name = principal.agentteams_resource_name
        if principal.role is AgentRole.ORGANIZATION_LEADER:
            await self._control_plane.ensure_manager(
                ManagerProjection(
                    name=name,
                    model=self._model,
                    runtime=self._manager_runtime,
                    skills=_SKILLS[AgentRole.ORGANIZATION_LEADER],
                ),
                idempotency_key=f"{key}:agentteams",
            )
            return name
        await self._control_plane.ensure_worker(
            with_task_control(
                WorkerProjection(
                    name=name,
                    model=self._model,
                    runtime=self._worker_runtime,
                    skills=_SKILLS[principal.role],
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

    async def provision(self, name: str, *, idempotency_key: str) -> WorkerRuntimeRef:
        return await self._control_plane.ensure_worker(
            with_task_control(
                WorkerProjection(
                    name=name,
                    model=self._model,
                    runtime=self._worker_runtime,
                    skills=_SKILLS[AgentRole.WORKER],
                    state=DesiredRuntimeState.RUNNING,
                    container_managed=False,
                ),
                self._worker_task_control_url,
            ),
            idempotency_key=idempotency_key,
        )


__all__ = [
    "AgentTeamsIdentitiesPending",
    "AgentTeamsRoomsPending",
    "ExternalWorkerProjection",
    "ProjectRuntimeProjection",
]
