import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from repomesh.modules.context.contracts import ExecutionContextGrant

from .ports.agent_team import AGENTTEAMS_NAME_PREFIX as AGENTTEAMS_NAME_PREFIX
from .ports.agent_team import ExternalMemberRole as ExternalMemberRole

# Re-exported for the project module: ``derive_runtime`` (hosted-native spec
# M7, D-17) answers which controller runtime a team's workers run under, and
# the cross-module rule lets it reach this module through ``contracts`` only.
from .ports.agent_team import WorkerRuntime as WorkerRuntime
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
    assignment_attempt_id: UUID | None = None
    assignment_generation: int | None = None
    execution_id: UUID | None = None
    execution_version: int | None = None


@dataclass(frozen=True, slots=True)
class StartAssignedWorkerTaskCommand:
    task_id: UUID
    worker_agent_id: UUID
    adapter_id: str
    base_revision: str = "main"
    task_features: frozenset[str] = frozenset()
    resume_session_id: str | None = None


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

    def to_wire(self) -> dict[str, object]:
        """The provisioning endpoint's body, camelCase spelled once.

        Deliberately four fields and no more. This answers an administrator who
        has just made an agent external, so the useful facts are which principal
        it was, which controller resource carries it, how far the controller has
        got, and the confirmation that the container is not the controller's —
        and a controller address or a credential would be neither useful nor
        safe to echo. ``ExternalWorkerBindingView.to_wire`` is the sibling this
        follows; the Bridge-facing document it produces is versioned and frozen,
        while this one is an operator's receipt and carries no schema version
        precisely so that nothing starts treating it as a contract to bind to.
        """

        return {
            "workerAgentId": str(self.worker_agent_id),
            "workerName": self.worker_name,
            "phase": self.phase,
            "containerManaged": self.container_managed,
        }


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
        _assert_binding_is_well_formed(
            container_managed=self.container_managed,
            team_name=self.team_name,
            member_name=self.worker_name,
            matrix_user_id=self.matrix_user_id,
            allowed_room_ids=self.allowed_room_ids,
        )

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


def _assert_binding_is_well_formed(
    *,
    container_managed: bool,
    team_name: str,
    member_name: str,
    matrix_user_id: str,
    allowed_room_ids: tuple[str, ...],
) -> None:
    """Every constraint the binding schemas state, checked once for both versions.

    v2 is v1 plus ``role`` and nothing else, so the shared fields must be
    validated by the *same* code rather than by two copies that agree today:
    the contract test machine-checks that the two schemas have not drifted, and
    a second hand-kept validator here is exactly the place a drift could hide
    from it. The messages are v1's, unchanged, because they are what an operator
    already reads out of a 409.
    """

    if container_managed:
        raise ExternalWorkerRefused("a binding is only well-formed for containerManaged: false")
    for label, value in (("teamName", team_name), ("workerName", member_name)):
        if not value or len(value) > _MAX_NAME:
            raise ExternalWorkerRefused(f"{label} is not a usable AgentTeams name")
    if not _MATRIX_USER_ID.match(matrix_user_id) or len(matrix_user_id) > _MAX_MATRIX_ID:
        raise ExternalWorkerRefused("matrixUserId is not a Matrix user id")
    if not 1 <= len(allowed_room_ids) <= _MAX_ROOMS:
        raise ExternalWorkerRefused("a binding needs between 1 and 50 confirmed rooms")
    if len(set(allowed_room_ids)) != len(allowed_room_ids):
        raise ExternalWorkerRefused("allowedRoomIds must be unique")
    for room_id in allowed_room_ids:
        if not _MATRIX_ROOM_ID.match(room_id) or len(room_id) > _MAX_MATRIX_ID:
            raise ExternalWorkerRefused(f"not a Matrix room id: {room_id}")


# ---------------------------------------------------------------------------
# External *members*: the same two paths once the member may be a leader
# (adjudication D-11, contracts/agent-bridge/v2)
# ---------------------------------------------------------------------------

EXTERNAL_MEMBER_BINDING_SCHEMA_VERSION = "repomesh.agent-bridge.binding.v2"
"""Wire version of the v2 preflight response.

Frozen in ``contracts/agent-bridge/v2/external-member-binding.schema.json``.
A separate constant rather than a computed suffix of the v1 one: a consumer
checks this exact string, and the two versions are two documents that happen to
share a prefix, not one document with a number in it.
"""

#: The refusals above, under the vocabulary D-11 generalizes them to.
#:
#: Aliases rather than subclasses, deliberately. The router's translation table
#: is what turns a refusal into a status code, and a second exception hierarchy
#: would fork it: an ``except ExternalMemberRefused`` that did not also catch the
#: v1 type (or the reverse) would answer 500 for a refusal that is well
#: classified, on whichever path was added last. Same object, two names, one
#: table.
UnknownExternalMember = UnknownExternalWorker
ExternalMemberRefused = ExternalWorkerRefused


def parse_external_member_role(value: str) -> ExternalMemberRole:
    """The enrolled role as a value, or a refusal — never a 422.

    ``organization_leader`` is the case this exists for. It is a *real* RepoMesh
    role that this contract deliberately cannot express (v2 README), so a Bridge
    presenting it has asked a coherent question and deserves the coherent
    answer: no binding, 409, the same code as an Organization Leader found in
    the directory. Letting the framework reject the value as an unparseable enum
    would answer 422 instead and split one refusal across two status codes.

    The refusal names the allowed roles rather than echoing the input: this
    value arrives on a query string, and a message is a place caller-controlled
    text ends up in an operator's logs.
    """

    try:
        return ExternalMemberRole(value)
    except ValueError as error:
        allowed = ", ".join(role.value for role in ExternalMemberRole)
        raise ExternalMemberRefused(
            f"an external member is one of: {allowed}"
        ) from error


@dataclass(frozen=True, slots=True)
class ProvisionExternalMemberCommand:
    """Make one already-registered principal an external member.

    ``ProvisionExternalWorkerCommand`` with the role restriction lifted, and
    still just an agent id: which role the member holds is read from the agent
    directory, never stated here. A command field would be a caller asserting a
    fact RepoMesh already owns, and the one thing preflight later confirms is
    that the two agree.
    """

    member_agent_id: UUID


@dataclass(frozen=True, slots=True)
class ExternalMemberView:
    """What v2 provisioning answers: the confirmed resource and its role.

    ``ExternalWorkerView`` plus ``role``, and an operator's receipt like it —
    no ``schemaVersion``, on purpose, so that nothing starts binding to it as a
    contract. The Bridge-facing document is the binding below; this one answers
    the human who just pressed the button.
    """

    member_agent_id: UUID
    member_name: str
    role: ExternalMemberRole
    phase: str
    container_managed: bool

    def to_wire(self) -> dict[str, object]:
        """camelCase, with v1's field names kept for the two shared facts.

        ``workerAgentId``/``workerName`` are historical names (adjudication
        D-6, and the v2 schemas keep them for the same reason): for a
        repository leader they name the leader's principal id and its AgentTeams
        resource. Renaming them here would make this receipt disagree with the
        binding document about what one member is called, for no new fact.
        """

        return {
            "workerAgentId": str(self.member_agent_id),
            "workerName": self.member_name,
            "role": self.role.value,
            "phase": self.phase,
            "containerManaged": self.container_managed,
        }


@dataclass(frozen=True, slots=True)
class ExternalMemberBindingQuery:
    """The v2 preflight: one member agent id and the role its enrollment claims.

    The claimed role is an *input to a check*, never a source of truth. RepoMesh
    answers with the role its own directory holds; carrying the enrollment's
    alongside it is what makes "these two disagree" a server-side refusal
    instead of something each Bridge is trusted to notice about itself. It is
    required rather than optional for that reason — an optional check is one a
    caller can decline to be checked by.
    """

    member_agent_id: UUID
    enrolled_role: ExternalMemberRole


@dataclass(frozen=True, slots=True)
class ExternalMemberBindingView:
    """The ``repomesh.agent-bridge.binding.v2`` document, in Python.

    Mirrors ``contracts/agent-bridge/v2/external-member-binding.schema.json``
    field for field. Every shared constraint is checked by the same function
    ``ExternalWorkerBindingView`` uses, so the two versions cannot drift apart
    in validation while the contract test says their schemas agree.

    ``role`` is RepoMesh's own answer, joined from the agent directory rather
    than echoed from the enrollment, which is the whole reason a Bridge asks.
    ``member_agent_id``/``member_name`` carry the historical ``workerAgentId``/
    ``workerName`` wire names (D-6); for a repository leader they name the
    leader's principal and its AgentTeams resource.
    """

    role: ExternalMemberRole
    organization_id: UUID
    team_name: str
    member_agent_id: UUID
    member_name: str
    matrix_user_id: str
    allowed_room_ids: tuple[str, ...]
    container_managed: bool = False

    def __post_init__(self) -> None:
        _assert_binding_is_well_formed(
            container_managed=self.container_managed,
            team_name=self.team_name,
            member_name=self.member_name,
            matrix_user_id=self.matrix_user_id,
            allowed_room_ids=self.allowed_room_ids,
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "schemaVersion": EXTERNAL_MEMBER_BINDING_SCHEMA_VERSION,
            "role": self.role.value,
            "organizationId": str(self.organization_id),
            "teamName": self.team_name,
            "workerAgentId": str(self.member_agent_id),
            "workerName": self.member_name,
            "matrixUserId": self.matrix_user_id,
            "allowedRoomIds": list(self.allowed_room_ids),
            "containerManaged": False,
        }


class WorkerExecutionStatus(StrEnum):
    PREPARING = "preparing"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class WorkerExecutionReservation:
    id: UUID
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    task_id: UUID
    worker_agent_id: UUID
    run_id: UUID
    status: WorkerExecutionStatus
    attempt: int
    version: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    task_payload: Mapping[str, object] | None
    error_detail: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    assignment_attempt_id: UUID | None = None
    assignment_generation: int | None = None


@dataclass(frozen=True, slots=True)
class ReservedWorkerExecution:
    reservation: WorkerExecutionReservation
    created: bool


class WorkerExecutionReservationPort(Protocol):
    async def reserve(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        repository_id: UUID,
        task_id: UUID,
        worker_agent_id: UUID,
        lease_owner: str,
        lease_seconds: int,
        assignment_attempt_id: UUID | None = None,
        assignment_generation: int | None = None,
    ) -> ReservedWorkerExecution: ...

    async def get_active(self, task_id: UUID) -> WorkerExecutionReservation | None: ...

    async def get(self, execution_id: UUID) -> WorkerExecutionReservation | None: ...

    async def bind_payload(
        self,
        reservation_id: UUID,
        payload: Mapping[str, object],
        *,
        lease_owner: str,
        fencing_version: int,
    ) -> WorkerExecutionReservation: ...

    async def renew(
        self,
        reservation_id: UUID,
        *,
        lease_owner: str,
        fencing_version: int,
        lease_seconds: int,
    ) -> WorkerExecutionReservation: ...

    async def fail_preparation(
        self,
        reservation_id: UUID,
        error: str,
        *,
        lease_owner: str,
        fencing_version: int,
    ) -> WorkerExecutionReservation: ...
