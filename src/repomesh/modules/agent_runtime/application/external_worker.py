"""External workers: provisioning one, and answering a Bridge about it.

Two use cases, one subject. ADR 0004 splits the "agent in the room" from the
"agent that writes code" by letting a Worker's body live outside the cluster:
the AgentTeams controller keeps the Matrix identity, the room and the Team
membership, and skips container create/delete when ``containerManaged`` is
false (``member_reconcile.go``). RepoMesh's side of that is exactly these two
paths and nothing else:

* :class:`ProvisionExternalWorker` — the explicit command. External-ness is a
  decision recorded against one agent principal, never a settings flag, a name
  pattern, or an implicit list; the default project path keeps provisioning
  managed workers and is not touched.
* :class:`ResolveExternalWorkerBinding` — the preflight a Bridge calls after
  its local enrollment checks and strictly before Matrix sync or any CLI spawn.
  It is the *only* place ``containerManaged: false``, worker binding and room
  ownership are confirmed against live state, because the Bridge holds no
  AgentTeams management credential and never queries the Go controller itself.

Both are fail-closed. Preflight answers a whole binding or refuses: there is no
partial answer, because every field of it is something the Bridge is about to
act on — the identity it syncs as, and the rooms it will accept work from.
"""

from __future__ import annotations

from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.contracts import (
    ExternalWorkerBindingQuery,
    ExternalWorkerBindingView,
    ExternalWorkerRefused,
    ExternalWorkerView,
    ProvisionExternalWorkerCommand,
    UnknownExternalWorker,
)
from repomesh.modules.agent_runtime.ports.agent_team import (
    AgentTeamControlPlane,
    ExternalWorkerProvisioner,
)


class ProvisionExternalWorker:
    """Provision one worker principal as a ``containerManaged: false`` Worker.

    The controller's confirmation is part of the result, not an assumption: a
    build that ignored the field would answer 201 with a managed worker, and
    reporting success on that would hand a Bridge an identity whose container is
    about to start under it.

    The adapter's ``provision`` method raises its own conflict exception (e.g.
    AgentTeamsConflict) when the worker already exists with a conflicting
    projection or managed/external mismatch. This is deliberately passed through
    unchanged; the HTTP layer must map such adapter conflicts to a refusal,
    not an internal error.
    """

    def __init__(
        self, directory: AgentPrincipalReader, provisioner: ExternalWorkerProvisioner
    ) -> None:
        self._directory = directory
        self._provisioner = provisioner

    async def execute(self, command: ProvisionExternalWorkerCommand) -> ExternalWorkerView:
        principal = await _worker_principal(self._directory, command.worker_agent_id)
        worker = await self._provisioner.provision(
            principal.agentteams_resource_name,
            # Keyed on the agent alone: an external worker belongs to no round,
            # so re-running the command is the same side effect rather than a
            # second one.
            idempotency_key=f"external-worker:{principal.id}:agentteams",
        )
        if worker.container_managed is not False:
            raise ExternalWorkerRefused(
                "the AgentTeams controller did not confirm containerManaged: false for "
                f"{principal.agentteams_resource_name}"
            )
        return ExternalWorkerView(
            worker_agent_id=principal.id,
            worker_name=principal.agentteams_resource_name,
            phase=worker.phase,
            container_managed=False,
        )


class ResolveExternalWorkerBinding:
    """Answer a Bridge's preflight, or refuse it.

    Reads two documents and joins them to RepoMesh's own principal: the worker
    (identity, room, Team, ``containerManaged``) and its Team (the team room).
    Nothing is echoed from a request — the Bridge's enrollment file says what it
    believes, and this answers what is true, which is the only reason the call
    is worth a network round-trip at all.

    Not a proxy: it exposes these joined facts and nothing else, so a Bridge
    cannot reach the controller's surface through it.
    """

    def __init__(
        self, directory: AgentPrincipalReader, control_plane: AgentTeamControlPlane
    ) -> None:
        self._directory = directory
        self._control_plane = control_plane

    async def execute(self, query: ExternalWorkerBindingQuery) -> ExternalWorkerBindingView:
        principal = await _worker_principal(self._directory, query.worker_agent_id)
        name = principal.agentteams_resource_name

        worker = await self._control_plane.get_worker(name)
        if worker is None:
            raise ExternalWorkerRefused(f"AgentTeams worker is not provisioned: {name}")
        if worker.name != name:
            raise ExternalWorkerRefused(
                f"AgentTeams answered for a different worker name: {worker.name} != {name}"
            )
        if worker.container_managed is not False:
            raise ExternalWorkerRefused(
                f"AgentTeams worker {name} is not confirmed as containerManaged: false"
            )
        if not worker.matrix_user_id:
            raise ExternalWorkerRefused(f"AgentTeams worker {name} has no Matrix identity yet")
        if not worker.team:
            raise ExternalWorkerRefused(f"AgentTeams worker {name} belongs to no Team")

        team = await self._control_plane.get_team(worker.team)
        if team is None:
            raise ExternalWorkerRefused(f"AgentTeams Team does not exist: {worker.team}")

        return ExternalWorkerBindingView(
            organization_id=principal.organization_id,
            team_name=team.name or worker.team,
            worker_agent_id=principal.id,
            worker_name=worker.name,
            matrix_user_id=worker.matrix_user_id,
            allowed_room_ids=_allowed_rooms(team.team_room_id, worker.room_id, worker=name),
            container_managed=False,
        )


def _allowed_rooms(
    team_room_id: str | None, worker_room_id: str | None, *, worker: str
) -> tuple[str, ...]:
    """The rooms RepoMesh owns for this worker identity, most public first.

    The Team room is where RepoMesh routes a worker's collaboration
    (``SendCollaborationMessage._route`` picks it for every pair that does not
    involve the organization leader), so its absence is a refusal: a binding
    without it is a Bridge that will never be handed work. The worker's own
    room is the identity's DM room and is included when the controller has
    published one. The leader's DM room is deliberately not: it carries
    organization-leader traffic, and this identity is not a party to it.
    """

    if not team_room_id:
        raise ExternalWorkerRefused(f"the Team room for {worker} is not ready")
    rooms = [team_room_id]
    if worker_room_id and worker_room_id not in rooms:
        rooms.append(worker_room_id)
    return tuple(rooms)


async def _worker_principal(
    directory: AgentPrincipalReader, agent_id: UUID
) -> AgentPrincipalView:
    """The one principal both paths accept: an active worker identity.

    A repository leader is also an AgentTeams *Worker resource*, so the role
    check cannot be left to the controller — it would happily make a leader
    external, and a leader with no body is a repository whose reviews never
    happen. A disabled principal is refused for the mirror-image reason: the
    controller keeps answering about a resource RepoMesh has retired.
    """

    principal = await directory.get_view(agent_id)
    if principal is None:
        raise UnknownExternalWorker(f"agent principal does not exist: {agent_id}")
    if principal.role is not AgentRole.WORKER:
        raise ExternalWorkerRefused(
            f"agent {agent_id} is a {principal.role.value}, not a worker identity"
        )
    if principal.status is not AgentPrincipalStatus.ACTIVE:
        raise ExternalWorkerRefused(f"agent {agent_id} is not active")
    return principal
