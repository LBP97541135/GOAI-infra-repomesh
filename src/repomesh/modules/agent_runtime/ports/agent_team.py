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


@dataclass(frozen=True, slots=True)
class TeamRuntimeRef:
    name: str
    phase: str
    team_room_id: str | None
    leader_room_id: str | None
    leader_name: str
    ready_workers: int
    total_workers: int


class AgentTeamControlPlane(Protocol):
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

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None: ...

    async def get_team(self, name: str) -> TeamRuntimeRef | None:
        """Read a team's runtime without creating it.

        `ensure_team` also returns a TeamRuntimeRef, but it provisions when the
        team is absent — unusable from a read-only endpoint.
        """
        ...

    async def ensure_worker_ready(
        self, name: str, *, idempotency_key: str
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
