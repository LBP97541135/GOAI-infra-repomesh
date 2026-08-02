from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class WorkerRuntime(StrEnum):
    OPENCLAW = "openclaw"
    COPAW = "copaw"
    HERMES = "hermes"
    OPENHUMAN = "openhuman"


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
    skills: tuple[str, ...] = ()
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


@dataclass(frozen=True, slots=True)
class TeamRuntimeRef:
    name: str
    phase: str
    team_room_id: str | None
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

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None: ...

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


def agentteams_resource_name(kind: str, resource_id: UUID) -> str:
    normalized_kind = kind.strip().lower().replace("_", "-")
    if normalized_kind not in {"manager", "worker", "team"}:
        raise ValueError(f"unsupported AgentTeams resource kind: {kind}")
    return f"rm-{normalized_kind}-{resource_id.hex}"
