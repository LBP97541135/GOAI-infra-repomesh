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

Everything here is ensure-shaped, and has to be: materialize is re-entrant, so
a retry of a half-executed round runs this again from the top.
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
)
from repomesh.modules.project.contracts import ProjectAgentTopologyView
from repomesh.modules.project.domain import ProjectTopologyViolation
from repomesh.modules.project.ports import ProjectTopologyStore

from .control_plane import AgentTeamsError
from .principal_registration import with_task_control
from .project_topology import ReconcileProjectAgentTopology


class AgentTeamsRoomsPending(AgentTeamsError):
    """The controller took the Teams but has not published their rooms yet."""



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
        worker_task_control_url: str | None = None,
    ) -> None:
        self._directory = directory
        self._store = store
        self._control_plane = control_plane
        self._model = model
        self._worker_task_control_url = worker_task_control_url
        self._reconcile = ReconcileProjectAgentTopology(directory, store, control_plane)

    async def project(self, project_id: UUID) -> ProjectAgentTopologyView:
        topology = await self._store.get(project_id)
        if topology is None:
            raise ProjectTopologyViolation(
                f"project topology does not exist: {project_id}"
            )

        await self._register(topology.organization_leader_id, project_id)
        for team in topology.repository_teams:
            for agent_id in (team.leader_agent_id, *team.worker_agent_ids):
                await self._register(agent_id, project_id)

        view = await self._reconcile.execute(project_id)
        self._assert_rooms(view)
        return view

    # ------------------------------------------------------------ registration

    async def _register(self, agent_id: UUID, project_id: UUID) -> None:
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
                    runtime=ManagerRuntime.OPENCLAW,
                    skills=_SKILLS[AgentRole.ORGANIZATION_LEADER],
                ),
                idempotency_key=f"{key}:agentteams",
            )
            return
        await self._control_plane.ensure_worker(
            with_task_control(
                WorkerProjection(
                    name=name,
                    model=self._model,
                    runtime=WorkerRuntime.OPENCLAW,
                    skills=_SKILLS[principal.role],
                    state=DesiredRuntimeState.RUNNING,
                ),
                self._worker_task_control_url,
            ),
            idempotency_key=f"{key}:agentteams",
        )

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


__all__ = ["AgentTeamsRoomsPending", "ProjectRuntimeProjection"]
