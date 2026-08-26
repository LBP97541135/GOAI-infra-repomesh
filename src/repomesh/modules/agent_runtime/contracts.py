import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from repomesh.modules.context.contracts import ExecutionContextGrant

from .ports.agent_team import AGENTTEAMS_NAME_PREFIX as AGENTTEAMS_NAME_PREFIX
from .ports.coding_agent import CodingRunRequest


@dataclass(frozen=True, slots=True)
class CodingRunFinished:
    run_id: UUID
    task_id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class AuthorizedCodingRunRequest:
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    agent_id: UUID
    coding_request: CodingRunRequest
    context_grant: ExecutionContextGrant
    requested_paths: tuple[str, ...]
    requested_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DispatchWorkerTaskCommand:
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    task_id: UUID
    worker_agent_id: UUID
    bundle_id: UUID
    run_id: UUID
    correlation_id: UUID
    adapter_id: str
    base_revision: str = "main"
    attempt: int = 1
    permission_mode: str = "accept_edits"
    resume_session_id: str | None = None
    credential_refs: tuple[str, ...] = ()
    task_features: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class StartAssignedWorkerTaskCommand:
    task_id: UUID
    worker_agent_id: UUID
    adapter_id: str
    base_revision: str = "main"
    task_features: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ActiveWorkerDispatch:
    """A Runner dispatch the execution plane has not finished yet.

    ``task_payload`` is the stored ``runtime.v1`` task envelope, so callers can recover the run's
    workspace and context binding without re-deriving them.
    """

    run_id: UUID
    task_id: UUID
    worker_agent_id: UUID
    attempt: int
    status: str
    task_payload: Mapping[str, object]


class WorkerDispatchReader(Protocol):
    async def get_active_dispatch_for_task(
        self, task_id: UUID, *, worker_agent_id: UUID
    ) -> ActiveWorkerDispatch | None: ...


# ---------------------------------------------------------------------------
# External workers and the agent-bridge preflight (ADR 0004 decisions 2, 4, 5)
# ---------------------------------------------------------------------------

EXTERNAL_WORKER_BINDING_SCHEMA_VERSION = "repomesh.agent-bridge.binding.v1"
"""Wire version of the preflight response.

Frozen in ``contracts/agent-bridge/v1/external-worker-binding.schema.json``.
A consumer checks this exact string, not the directory version, so it is a
value in the code rather than a comment about one.
"""

_MATRIX_USER_ID = re.compile(r"^@[^:]+:.+$")
_MATRIX_ROOM_ID = re.compile(r"^![^:]+:.+$")
_MAX_NAME = 100
_MAX_MATRIX_ID = 255
_MAX_ROOMS = 50


class ExternalWorkerError(RuntimeError):
    """Base of this module's external-worker refusals.

    Declared in contracts rather than in the application layer for the same
    reason ``agent_directory`` declares its own here: the API layer has to
    translate them, and it should not have to import a use case to do it.
    """


class UnknownExternalWorker(ExternalWorkerError):
    """RepoMesh has no agent principal with this id."""


class ExternalWorkerRefused(ExternalWorkerError):
    """The facts exist but do not add up to an external-worker binding.

    One type for every remaining failure, on purpose: preflight is fail-closed,
    so "not a worker", "still managed", "no Matrix identity", "no confirmed
    room" and "the names disagree" are all the same answer to the caller — no
    binding — and only the message distinguishes them.
    """


@dataclass(frozen=True, slots=True)
class ProvisionExternalWorkerCommand:
    """Make one already-registered worker principal an external worker.

    The whole command is the agent id: external-ness is a decision somebody
    takes about a specific principal, and this request is where it is recorded.
    It is not a setting, not a name pattern, and not a list somewhere in the
    environment — that is the point of it being a command at all.
    """

    worker_agent_id: UUID


@dataclass(frozen=True, slots=True)
class ExternalWorkerView:
    """What provisioning answers: the confirmed resource, no secrets.

    ``container_managed`` is the controller's confirmation rather than the
    request's intent, which is why it is here at all — the command already said
    what was wanted.
    """

    worker_agent_id: UUID
    worker_name: str
    phase: str
    container_managed: bool


@dataclass(frozen=True, slots=True)
class ExternalWorkerBindingQuery:
    """The bridge preflight: one worker agent id, read-only."""

    worker_agent_id: UUID


@dataclass(frozen=True, slots=True)
class ExternalWorkerBindingView:
    """The ``repomesh.agent-bridge.binding.v1`` document, in Python.

    Mirrors ``contracts/agent-bridge/v1/external-worker-binding.schema.json``
    field for field; ``to_wire`` is the only place the camelCase names are
    spelled. Every constraint the schema states is checked on construction
    rather than trusted, so a binding that could not validate cannot exist:
    the Bridge aborts startup on a malformed answer either way, and a refusal
    here names the missing fact instead of leaving PR 2 to guess.

    Carries no secret and no controller address: the Bridge holds no AgentTeams
    management credential and never talks to the Go controller.
    """

    organization_id: UUID
    team_name: str
    worker_agent_id: UUID
    worker_name: str
    matrix_user_id: str
    allowed_room_ids: tuple[str, ...]
    container_managed: bool = False

    def __post_init__(self) -> None:
        if self.container_managed:
            raise ExternalWorkerRefused(
                "a binding is only well-formed for containerManaged: false"
            )
        for label, value in (("teamName", self.team_name), ("workerName", self.worker_name)):
            if not value or len(value) > _MAX_NAME:
                raise ExternalWorkerRefused(f"{label} is not a usable AgentTeams name")
        if (
            not _MATRIX_USER_ID.match(self.matrix_user_id)
            or len(self.matrix_user_id) > _MAX_MATRIX_ID
        ):
            raise ExternalWorkerRefused("matrixUserId is not a Matrix user id")
        if not 1 <= len(self.allowed_room_ids) <= _MAX_ROOMS:
            raise ExternalWorkerRefused("a binding needs between 1 and 50 confirmed rooms")
        if len(set(self.allowed_room_ids)) != len(self.allowed_room_ids):
            raise ExternalWorkerRefused("allowedRoomIds must be unique")
        for room_id in self.allowed_room_ids:
            if not _MATRIX_ROOM_ID.match(room_id) or len(room_id) > _MAX_MATRIX_ID:
                raise ExternalWorkerRefused(f"not a Matrix room id: {room_id}")

    def to_wire(self) -> dict[str, object]:
        return {
            "schemaVersion": EXTERNAL_WORKER_BINDING_SCHEMA_VERSION,
            "organizationId": str(self.organization_id),
            "teamName": self.team_name,
            "workerAgentId": str(self.worker_agent_id),
            "workerName": self.worker_name,
            "matrixUserId": self.matrix_user_id,
            "allowedRoomIds": list(self.allowed_room_ids),
            "containerManaged": False,
        }
