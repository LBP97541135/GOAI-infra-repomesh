from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class WorkerRuntime(StrEnum):
    OPENCLAW = "openclaw"
    COPAW = "copaw"
    HERMES = "hermes"
    OPENHUMAN = "openhuman"
    # First-party runtime running the RepoMesh Runner as PID 1; the wire value
    # must match the controller constant and the CRD enum entry. Contract:
    # contracts/runtime/v1/worker-runtime.md.
    REPOMESH_RUNNER = "repomesh-runner"


class ManagerRuntime(StrEnum):
    OPENCLAW = "openclaw"
    COPAW = "copaw"


class DesiredRuntimeState(StrEnum):
    RUNNING = "Running"
    SLEEPING = "Sleeping"
    STOPPED = "Stopped"


class TeamRole(StrEnum):
    LEADER = "team_leader"
    WORKER = "worker"


class ExternalMemberRole(StrEnum):
    """The two RepoMesh roles a Bridge may serve, spelled as the wire spells them.

    Frozen in ``contracts/agent-bridge/v2/*.schema.json`` as the ``role`` enum
    (adjudication D-11). Deliberately *not* ``AgentRole``: that enum has a third
    member, and the whole point of this one is that ``organization_leader`` is
    not representable — the Organization Leader stays on the AgentTeams Manager,
    so a document describing an external one cannot be built, only refused.

    Lives beside ``TeamRole`` rather than in ``contracts`` because a port needs
    it (``ExternalMemberProvisioner`` below picks an AgentTeams projection by
    role) and ``contracts`` already imports from here, never the other way
    round. ``contracts`` re-exports it, so consumers keep importing one name
    from one place.
    """

    WORKER = "worker"
    REPOSITORY_LEADER = "repository_leader"


@dataclass(frozen=True, slots=True)
class McpServerProjection:
    name: str
    url: str
    transport: str = "http"


@dataclass(frozen=True, slots=True)
class ChannelPolicyProjection:
    group_allow_extra: tuple[str, ...] = ()
    group_deny_extra: tuple[str, ...] = ()
    dm_allow_extra: tuple[str, ...] = ()
    dm_deny_extra: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ManagerProjection:
    name: str
    model: str
    runtime: ManagerRuntime = ManagerRuntime.OPENCLAW
    skills: tuple[str, ...] = ()
    soul: str | None = None
    agents: str | None = None
    #: Container image the projected Manager must run. The controller's image
    #: fallback is role-blind: a Manager CR whose spec image is empty is
    #: handed the *worker* image of its runtime, whose entrypoint demands
    #: ``AGENTTEAMS_WORKER_NAME`` — an env only the worker env builder sets —
    #: so the container exits(1) on boot and the Manager never gains a
    #: Matrix identity. The controller's initializer defaults an image only
    #: for its own built-in manager, so a projected one has to name its
    #: image itself. None keeps the controller's choice for deployments that
    #: pair a manager-capable default.
    image: str | None = None


@dataclass(frozen=True, slots=True)
class WorkerProjection:
    name: str
    model: str
    runtime: WorkerRuntime = WorkerRuntime.OPENCLAW
    identity: str | None = None
    soul: str | None = None
    agents: str | None = None
    skills: tuple[str, ...] = ()
    mcp_servers: tuple[McpServerProjection, ...] = ()
    channel_policy: ChannelPolicyProjection | None = None
    state: DesiredRuntimeState = DesiredRuntimeState.RUNNING
    #: Whether the AgentTeams controller owns this worker's container.
    #:
    #: True — the controller's own default — for every worker RepoMesh
    #: provisions on the ordinary project path, so no existing caller changes
    #: meaning. Only the explicit external provisioning path sets it False,
    #: which makes ``member_reconcile.go`` skip container create/delete while
    #: keeping the Matrix identity, the room and the Team membership: the
    #: worker's body is a process somebody else runs (ADR 0004 decision 2).
    #:
    #: Defaulted rather than required precisely so that "which workers are
    #: external" stays an explicit fact on a provisioning request instead of a
    #: setting or a name pattern.
    container_managed: bool = True


@dataclass(frozen=True, slots=True)
class TeamMemberProjection:
    name: str
    role: TeamRole


@dataclass(frozen=True, slots=True)
class TeamProjection:
    name: str
    members: tuple[TeamMemberProjection, ...]
    description: str | None = None
    heartbeat_every: str | None = None

    def __post_init__(self) -> None:
        leaders = [member for member in self.members if member.role is TeamRole.LEADER]
        if len(leaders) != 1:
            raise ValueError("an AgentTeams team requires exactly one team leader")
        if len({member.name for member in self.members}) != len(self.members):
            raise ValueError("AgentTeams team members must be unique")


@dataclass(frozen=True, slots=True)
class ManagerRuntimeRef:
    name: str
    phase: str
    room_id: str | None = None
    matrix_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorkerRuntimeRef:
    name: str
    phase: str
    runtime: str | None = None
    room_id: str | None = None
    matrix_user_id: str | None = None
    message: str | None = None
    #: The Team this worker is currently a member of, as the controller
    #: reports it, or None when it belongs to none. Membership is *exclusive*
    #: — a second Team naming the same worker is refused with 400 "is already
    #: a member of Team X" — so this field is the only way to ask "where does
    #: this repository's team already live?" without provoking that refusal.
    #: It is what makes adoption possible (defect A-8, contract §8.7.2).
    team: str | None = None
    #: ``containerManaged`` as the controller reports it — an observation, not
    #: a request. The worker document always carries the field (it is not
    #: ``omitempty``), so ``None`` means the answer did not come from a
    #: controller that knows it; that is "unknown" and must never be read as
    #: "external". The bridge preflight is the caller that cares: it confirms
    #: this is exactly ``False`` before it will bind anything to the worker.
    container_managed: bool | None = None
    #: The MCP servers the controller reports on this worker, empty when the
    #: document carries none (the field is omitted exactly then — absent is
    #: not "empty", but it *is* the detectable signal the projection uses to
    #: heal workers provisioned before the task-control wiring existed).
    mcp_servers: tuple[McpServerProjection, ...] = ()


@dataclass(frozen=True, slots=True)
class TeamRuntimeRef:
    name: str
    phase: str
    team_room_id: str | None
    leader_room_id: str | None
    leader_name: str
    ready_workers: int
    total_workers: int


class WorkerControlPlaneUnavailable(RuntimeError):
    """The AgentTeams control plane could not be reached to answer a read.

    Module-owned on purpose. The bridge preflight router must not import
    ``repomesh.integrations.*`` to catch the integration's own transport
    exception (``AgentTeamsUnavailable``) — that boundary is why ports exist
    at all — so the adapter that actually talks to the controller catches it
    there and raises this instead, chained with ``from`` so the original
    transport failure is not lost. The router maps this to HTTP 503: the
    controller merely did not answer, which a retry may well outlast, unlike
    the 404/409 refusals a controller that *did* answer can hand back.
    """


class WorkerBindingReader(Protocol):
    """The two reads the bridge preflight makes, and nothing else.

    RepoMesh's side of the port the Bridge calls ``WorkerBindingPort``: one
    worker document, one Team document, no writes and no way to reach anything
    else on the controller's surface. ``ResolveExternalWorkerBinding`` and the
    router depend on *this*, not on ``AgentTeamControlPlane``, because narrow
    is the security property — a use case that cannot ensure cannot provision,
    and a Bridge-facing endpoint holding a full control-plane handle is one
    refactor away from being a proxy for it (ADR 0004 decisions 4, 5).

    It is also what the composition root actually supplies: the adapter behind
    the preflight (``ExternalWorkerProjection``) implements these two reads and
    a provisioning method, and never implemented the rest of
    ``AgentTeamControlPlane`` at all.

    Both reads raise ``WorkerControlPlaneUnavailable`` when the controller
    cannot be reached, which is the whole reason the adapter exists.
    """

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None: ...

    async def get_team(self, name: str) -> TeamRuntimeRef | None:
        """Read a team's runtime without creating it.

        `ensure_team` also returns a TeamRuntimeRef, but it provisions when the
        team is absent — unusable from a read-only endpoint.
        """
        ...


class AgentTeamControlPlane(WorkerBindingReader, Protocol):
    async def ensure_manager(
        self, projection: ManagerProjection, *, idempotency_key: str
    ) -> ManagerRuntimeRef: ...

    async def ensure_worker(
        self, projection: WorkerProjection, *, idempotency_key: str
    ) -> WorkerRuntimeRef: ...

    async def ensure_team(
        self, projection: TeamProjection, *, idempotency_key: str
    ) -> TeamRuntimeRef: ...

    async def get_manager(self, name: str) -> ManagerRuntimeRef | None: ...

    async def ensure_worker_ready(
        self, name: str, *, idempotency_key: str
    ) -> WorkerRuntimeRef: ...

    async def ensure_worker_mcp_servers(
        self,
        name: str,
        servers: tuple[McpServerProjection, ...],
        *,
        idempotency_key: str,
    ) -> WorkerRuntimeRef | None:
        """Align one field of an existing worker: its MCP servers.

        ``ensure_worker`` may not touch a worker somebody else's pass created —
        the read-first rule exists so onboarded model/runtime/skills choices
        are never re-asserted. The task-control server is the one exception
        materialize owns outright, so it gets its own verb: overwrite the
        servers, preserve everything else. ``None`` answers "no such worker";
        a ref answers with the servers now in place.
        """
        ...


class ExternalWorkerProvisioner(Protocol):
    """Project one already-registered principal as an *external* Worker.

    Deliberately narrower than ``AgentTeamControlPlane``: the application use
    case decides *whether* an agent may be external (role, status, and the
    controller's own confirmation), while the adapter decides what the rest of
    the projection's fields are. Those fields are not free — the controller
    compares an existing worker against the one being asked for — so they have
    to be the values the ordinary project path already uses, and those live in
    the integration next to the path that uses them.

    Raises adapter-specific conflict exceptions (e.g. AgentTeamsConflict) if
    the worker already exists with a conflicting projection, including when a
    managed worker conflicts with an external provisioning request. Callers
    must treat such adapter conflict exceptions as refusals, not internal errors.
    """

    async def provision(self, name: str, *, idempotency_key: str) -> WorkerRuntimeRef: ...


class ExternalMemberProvisioner(Protocol):
    """``ExternalWorkerProvisioner`` once the member may be a Repository Leader.

    One added argument, and it is the one fact the adapter cannot derive from a
    name: which role's AgentTeams projection to ask for. It matters because the
    controller *compares* an existing worker against the one being requested —
    a repository leader registered by the ordinary project path carries
    ``("code-review", "planning")``, so provisioning it with a worker's
    ``("coding",)`` would answer 409 about skills and send an operator hunting a
    mismatch that this call created.

    Defaulted to ``WORKER`` so the v1 path keeps calling ``provision(name,
    idempotency_key=...)`` unchanged and every existing implementation still
    satisfies the narrower protocol above; only the v2 external-member path
    passes a role, and only a repository leader changes what is sent.
    """

    async def provision(
        self,
        name: str,
        *,
        idempotency_key: str,
        role: ExternalMemberRole = ExternalMemberRole.WORKER,
    ) -> WorkerRuntimeRef: ...


class AgentTeamMessenger(Protocol):
    async def send_task(
        self,
        room_id: str,
        body: str,
        *,
        transaction_id: str,
    ) -> str: ...


AGENTTEAMS_NAME_PREFIX = "repomesh"
"""Prefix every AgentTeams resource RepoMesh mints carries.

Deliberately not ``rm``. The worker runtime screens every shell command
through a rule whose pattern is ``\\brm\\b``, and a hyphen is a word
boundary -- so a name like ``rm-worker-a-api`` makes ``rm`` a standalone
word wherever it appears. An agent listing its own working directory
therefore tripped the "dangerous rm" rule, and the guard suspended the
tool call waiting for a human to type ``/approve`` in a room that holds
only agents. The task never started. ``repomesh`` has no ``rm`` in it at
all, which is a stronger guarantee than relying on a boundary argument.
"""


def agentteams_resource_name(kind: str, resource_id: UUID) -> str:
    normalized_kind = kind.strip().lower().replace("_", "-")
    if normalized_kind not in {"manager", "worker", "team"}:
        raise ValueError(f"unsupported AgentTeams resource kind: {kind}")
    return f"{AGENTTEAMS_NAME_PREFIX}-{normalized_kind}-{resource_id.hex}"
