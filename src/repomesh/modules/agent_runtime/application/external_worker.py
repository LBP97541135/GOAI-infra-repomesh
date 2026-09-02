"""External members: provisioning one, and answering a Bridge about it.

Two subjects, four use cases, one shape. ADR 0004 splits the "agent in the
room" from the "agent that writes code" by letting a member's body live outside
the cluster: the AgentTeams controller keeps the Matrix identity, the room and
the Team membership, and skips container create/delete when ``containerManaged``
is false (``member_reconcile.go``). RepoMesh's side of that is exactly these
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
* :class:`ProvisionExternalMember` and :class:`ResolveExternalMemberBinding` —
  the same two paths under adjudication D-11, which generalizes "external
  worker" to "external member": a Repository Leader is also served by a Bridge,
  so it must be provisionable and bindable, while the Organization Leader stays
  on the AgentTeams Manager and is refused by both.

The v1 pair is left exactly as it was rather than widened in place. Its request,
its response and its refusals are a frozen contract that deployed Bridges read
(``contracts/agent-bridge/v1``), and "the worker path also accepts leaders now"
is not a compatible change to a document whose schema cannot say which one it
described. So v2 is a sibling, and what the two share is *code* — the room
allowlist, the principal join, the well-formedness checks — not a widened
signature.

All four are fail-closed. Preflight answers a whole binding or refuses: there is
no partial answer, because every field of it is something the Bridge is about to
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
    ExternalMemberBindingQuery,
    ExternalMemberBindingView,
    ExternalMemberRefused,
    ExternalMemberRole,
    ExternalMemberView,
    ExternalWorkerBindingQuery,
    ExternalWorkerBindingView,
    ExternalWorkerRefused,
    ExternalWorkerView,
    ProvisionExternalMemberCommand,
    ProvisionExternalWorkerCommand,
    UnknownExternalMember,
    UnknownExternalWorker,
)
from repomesh.modules.agent_runtime.ports.agent_team import (
    ExternalMemberProvisioner,
    ExternalWorkerProvisioner,
    TeamRuntimeRef,
    WorkerBindingReader,
    WorkerRuntimeRef,
)

#: Which RepoMesh role becomes which wire role. The absent key is the point:
#: ``ORGANIZATION_LEADER`` has no external-member spelling (v2 README), so a
#: lookup miss is the refusal rather than something a branch has to remember.
#:
#: Public because ``readiness`` asks the same question and must get the same
#: answer: which principals may be served by a Bridge at all. Only the refusal
#: differs between the two — a binding and a readiness report are refused
#: through different exception types — so the *mapping* is shared and the
#: ``raise`` is not.
MEMBER_ROLES: dict[AgentRole, ExternalMemberRole] = {
    AgentRole.WORKER: ExternalMemberRole.WORKER,
    AgentRole.REPOSITORY_LEADER: ExternalMemberRole.REPOSITORY_LEADER,
}


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
    cannot reach the controller's surface through it. That is held by the
    dependency as much as by the code — ``WorkerBindingReader`` is two reads,
    so there is no ``ensure_*`` in scope to be tempted by and no widening of
    this endpoint that does not first widen a port.
    """

    def __init__(
        self, directory: AgentPrincipalReader, control_plane: WorkerBindingReader
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


class ProvisionExternalMember:
    """Provision one principal as a ``containerManaged: false`` AgentTeams member.

    :class:`ProvisionExternalWorker` with the role restriction lifted to what
    D-11 allows: a Worker or a Repository Leader, never an Organization Leader.
    The role is read from RepoMesh's own directory and passed to the adapter,
    because it changes the projection the controller is asked for — a repository
    leader registered by the ordinary project path carries different skills, and
    provisioning it as a worker would collide with itself on the controller and
    report the mismatch as if an operator had caused it.

    The idempotency key is v1's, unchanged and unqualified by role: it names the
    agent, and the agent has one AgentTeams resource whichever route asked for
    it. A key with a version or a role in it would let the same principal be
    provisioned twice under two spellings of one decision.
    """

    def __init__(
        self, directory: AgentPrincipalReader, provisioner: ExternalMemberProvisioner
    ) -> None:
        self._directory = directory
        self._provisioner = provisioner

    async def execute(self, command: ProvisionExternalMemberCommand) -> ExternalMemberView:
        principal = await _member_principal(self._directory, command.member_agent_id)
        role = _member_role(principal)
        worker = await self._provisioner.provision(
            principal.agentteams_resource_name,
            idempotency_key=f"external-worker:{principal.id}:agentteams",
            role=role,
        )
        if worker.container_managed is not False:
            raise ExternalMemberRefused(
                "the AgentTeams controller did not confirm containerManaged: false for "
                f"{principal.agentteams_resource_name}"
            )
        return ExternalMemberView(
            member_agent_id=principal.id,
            member_name=principal.agentteams_resource_name,
            role=role,
            phase=worker.phase,
            container_managed=False,
        )


class ResolveExternalMemberBinding:
    """Answer a v2 Bridge's preflight, or refuse it.

    :class:`ResolveExternalWorkerBinding` plus the two things ``role`` brings.

    *The role is confirmed, not echoed.* The enrollment says what the Bridge
    believes it is; this answers what RepoMesh's directory holds, and a
    disagreement is a refusal rather than something reconciled in favour of
    either side. A Bridge that enrolled as a worker and is a leader would
    otherwise start a Runner execution path for an identity that must never
    enter one, and one that enrolled as a leader and is a worker would sit
    waiting for decisions nobody will ask it for.

    *The room allowlist is role-aware.* A worker gets the Team room and its own
    DM; a repository leader gets the Team room and the leader DM — the room the
    collaboration router already uses for leader traffic
    (``SendCollaborationMessage._route``) and the one the read model already
    labels ``leader_dm``. Neither is given the other's DM: an allowlist is what
    a Bridge will accept work from, so a room belonging to another identity in
    it is the whole failure this endpoint exists to prevent.
    """

    def __init__(
        self, directory: AgentPrincipalReader, control_plane: WorkerBindingReader
    ) -> None:
        self._directory = directory
        self._control_plane = control_plane

    async def execute(self, query: ExternalMemberBindingQuery) -> ExternalMemberBindingView:
        principal = await _member_principal(self._directory, query.member_agent_id)
        role = _member_role(principal)
        if query.enrolled_role is not role:
            raise ExternalMemberRefused(
                f"agent {principal.id} is a {role.value} on file, "
                f"but the enrollment claims {query.enrolled_role.value}"
            )
        name = principal.agentteams_resource_name

        worker = await self._control_plane.get_worker(name)
        if worker is None:
            raise ExternalMemberRefused(f"AgentTeams worker is not provisioned: {name}")
        if worker.name != name:
            raise ExternalMemberRefused(
                f"AgentTeams answered for a different worker name: {worker.name} != {name}"
            )
        if worker.container_managed is not False:
            raise ExternalMemberRefused(
                f"AgentTeams worker {name} is not confirmed as containerManaged: false"
            )
        if not worker.matrix_user_id:
            raise ExternalMemberRefused(f"AgentTeams worker {name} has no Matrix identity yet")
        if not worker.team:
            raise ExternalMemberRefused(f"AgentTeams worker {name} belongs to no Team")

        team = await self._control_plane.get_team(worker.team)
        if team is None:
            raise ExternalMemberRefused(f"AgentTeams Team does not exist: {worker.team}")

        return ExternalMemberBindingView(
            role=role,
            organization_id=principal.organization_id,
            team_name=team.name or worker.team,
            member_agent_id=principal.id,
            member_name=worker.name,
            matrix_user_id=worker.matrix_user_id,
            allowed_room_ids=_member_allowed_rooms(role, worker=worker, team=team),
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
    the traffic of whoever leads this Team, and a worker is not a party to it.
    """

    return _member_rooms(
        team_room_id=team_room_id,
        dm_room_id=worker_room_id,
        dm_required=False,
        member=worker,
    )


def _member_rooms(
    *, team_room_id: str | None, dm_room_id: str | None, dm_required: bool, member: str
) -> tuple[str, ...]:
    """Team room first, then this member's own DM room.

    One assembler for both versions, because the ordering and the "the Team room
    is mandatory" rule are the same fact under either. What differs is *which*
    room is the member's DM and whether its absence is fatal, and both of those
    are decided by the caller that knows the role — this function is deliberately
    not role-aware, so there is no second place a role could be interpreted.
    """

    if not team_room_id:
        raise ExternalWorkerRefused(f"the Team room for {member} is not ready")
    if dm_required and not dm_room_id:
        raise ExternalWorkerRefused(f"the DM room for {member} is not ready")
    rooms = [team_room_id]
    if dm_room_id and dm_room_id not in rooms:
        rooms.append(dm_room_id)
    return tuple(rooms)


def _member_allowed_rooms(
    role: ExternalMemberRole, *, worker: WorkerRuntimeRef, team: TeamRuntimeRef
) -> tuple[str, ...]:
    """The Team room plus this member's own DM, chosen by role.

    For a worker that DM is the worker document's ``roomID``, exactly as under
    v1. For a repository leader it is the Team document's ``leaderDMRoomID``:
    RepoMesh projects a repository's leader as its AgentTeams Team leader
    (``ReconcileProjectAgentTopology``), so that field *is* this identity's DM
    room, and it is where an Organization Leader's messages to it already land.

    Which makes the leader's name worth checking before the room is handed over.
    A Team whose leader is somebody else would still answer with a
    ``leaderDMRoomID``, and putting it in this binding would tell a Bridge it may
    read and post in another agent's DM. The check is skipped only when the
    controller did not name a leader at all — an absent field is not a
    disagreement, and the room was still read from the Team this member's own
    worker document points at.

    The DM is mandatory for a leader and optional for a worker, and that
    asymmetry is v1's compatibility rather than a judgement: a worker whose room
    the controller has not published yet already binds today with the Team room
    alone, and narrowing that would break a live Bridge. A leader has no such
    history, and a leader with no DM room cannot be given work at all.
    """

    if role is ExternalMemberRole.WORKER:
        return _member_rooms(
            team_room_id=team.team_room_id,
            dm_room_id=worker.room_id,
            dm_required=False,
            member=worker.name,
        )
    if team.leader_name and team.leader_name != worker.name:
        raise ExternalMemberRefused(
            f"AgentTeams Team {team.name} is led by {team.leader_name}, not {worker.name}"
        )
    return _member_rooms(
        team_room_id=team.team_room_id,
        dm_room_id=team.leader_room_id,
        dm_required=True,
        member=worker.name,
    )


def _member_role(principal: AgentPrincipalView) -> ExternalMemberRole:
    """This principal's role as v2 spells it, or a refusal.

    The Organization Leader is the one refusal here, and it is a design decision
    rather than a gap: it stays the existing AgentTeams Manager (D-11), so the
    v2 contract cannot even describe an external one. Refusing at the server is
    the half that matters — a schema that cannot express something only stops
    the documents, not the identities.
    """

    role = MEMBER_ROLES.get(principal.role)
    if role is None:
        raise ExternalMemberRefused(
            f"agent {principal.id} is a {principal.role.value}, which stays on the "
            "AgentTeams Manager and cannot be an external member"
        )
    return role


async def _member_principal(
    directory: AgentPrincipalReader, agent_id: UUID
) -> AgentPrincipalView:
    """An active principal, whatever its role.

    The role verdict is :func:`_member_role`'s, one step later, so that "RepoMesh
    has never heard of this agent" (404) and "this agent may not be an external
    member" (409) stay the two different answers they are. A disabled principal
    is refused here for v1's reason: the controller keeps answering about a
    resource RepoMesh has retired.
    """

    principal = await directory.get_view(agent_id)
    if principal is None:
        raise UnknownExternalMember(f"agent principal does not exist: {agent_id}")
    if principal.status is not AgentPrincipalStatus.ACTIVE:
        raise ExternalMemberRefused(f"agent {agent_id} is not active")
    return principal


async def _worker_principal(
    directory: AgentPrincipalReader, agent_id: UUID
) -> AgentPrincipalView:
    """The one principal the v1 paths accept: an active worker identity.

    A repository leader is also an AgentTeams *Worker resource*, so the role
    check cannot be left to the controller — it would happily make a leader
    external, and v1 has no way to say that it did: the binding document it
    answers with carries no role, so a Bridge reading it could not tell a leader
    from a worker. That is what v2 is for, and it is why this stays exactly as
    strict. A disabled principal is refused for the mirror-image reason: the
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
