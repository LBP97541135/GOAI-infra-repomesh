"""Wire models for the three ``contracts/agent-bridge/v1`` documents.

The Bridge is a separate process from the RepoMesh control plane, so it owns
its own model of these documents instead of importing the server's Python
types. The two sides agree because the frozen schema is the contract, not
because they share a class; ``tests/contracts/test_agent_bridge_v1_contract.py``
is where that agreement is checked.

Validation lives at the wire boundary — ``from_wire`` — because that is where
untrusted bytes become facts: an enrollment file an operator edited by hand, and
an HTTP body from a control plane that may be a different version than this
build. The dataclasses themselves stay plain frozen records so a caller that
already holds validated values (a test, a later composition root) is not forced
back through JSON.

Every constraint the schema states is checked here rather than trusted, and each
refusal names the field it refused. There is one refusal *type* per stage, never
one per message: stage 1 raises :class:`EnrollmentInvalid`, stage 2 raises
:class:`BindingRefused`, and callers must branch on the type, never on the text.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse
from uuid import UUID

ENROLLMENT_SCHEMA_VERSION = "repomesh.agent-bridge.enrollment.v1"
BINDING_SCHEMA_VERSION = "repomesh.agent-bridge.binding.v1"
ROOM_OBSERVATION_SCHEMA_VERSION = "repomesh.room-observation.v1"

CODING_PROFILES: tuple[str, ...] = ("codex", "claude-code", "kimi")
"""The enrollment schema's ``codingProfile`` enum, spelled once.

A subset of ``repomesh_runner.profiles.PROFILES`` on purpose: the Runner may
carry profiles a Bridge must not drive (the validation ``mock`` profile is
exactly that), so the direction of the check is enum ⊆ Runner and never the
reverse. ``tests/agent_bridge/test_wire_contracts.py`` pins both halves — this
tuple against the frozen schema, and the schema against the real Runner ids.
"""

OBSERVATION_KINDS: tuple[str, ...] = (
    "run_accepted",
    "run_started",
    "phase_changed",
    "tool_action",
    "files_changed",
    "test_completed",
    "question",
    "blocked",
    "resumed",
    "run_completed",
    "run_failed",
    "run_interrupted",
    "note",
)
OBSERVATION_PHASES: tuple[str, ...] = ("reading", "modifying", "verifying", "awaiting_input")

_MATRIX_USER_ID = re.compile(r"^@[^:]+:.+$")
_MATRIX_ROOM_ID = re.compile(r"^![^:]+:.+$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{7,64}$")
_MAX_NAME = 100
_MAX_MATRIX_ID = 255
_MAX_ROOMS = 50
_MAX_CREDENTIAL_REF = 500


class BridgeStartupError(RuntimeError):
    """Anything that stops a Bridge instance from starting.

    A supervisor only ever needs the answer "this instance did not start", so
    the CLI maps this whole family onto one exit code; the subclasses exist for
    tests and operators, which need to know *which* stage said no.
    """


class EnrollmentInvalid(BridgeStartupError):
    """Stage 1 refused: the local configuration never justified a network call.

    One type, many messages: a malformed document, an unknown coding profile, an
    empty credential reference and an unresolvable one are the same answer to
    the caller — this enrollment cannot start a Bridge — and only the message
    distinguishes them. Tests assert the type and the fact that no socket was
    opened, never the wording.
    """


class BindingRefused(BridgeStartupError):
    """Stage 2 refused: retrying will not change the answer.

    Covers both halves of preflight because they mean the same thing to the
    caller: RepoMesh answered but the answer is not a usable binding (4xx, a
    body that is not ``repomesh.agent-bridge.binding.v1``, a worker that is
    still container-managed, identity that disagrees with the enrollment, or no
    room both sides confirm). Fail-fast, one type, many messages.
    """


class BindingUnavailable(BridgeStartupError):
    """Stage 2 could not get an answer, and a retry may well get one.

    Split from :class:`BindingRefused` by "can a retry fix it", not by HTTP
    semantics: connection failures, timeouts, 429 and every 5xx (503 included)
    land here, and the adapter's bounded retry loop is built on exactly this
    distinction.
    """


class SessionNotReady(BridgeStartupError):
    """The local coding runtime cannot serve, so this instance will not start.

    Raised by ``CodingSessionPort.ensure_ready``, which runs after preflight and
    before anything durable exists. It is a startup refusal rather than a
    steady-state failure on purpose: a Bridge whose CLI is missing, logged out,
    or unable to run under this machine's restrictions would otherwise establish
    its baseline — writing off the room's backlog as already read — and then
    answer every mention with a note about a failure the operator was never told
    how to fix. Refusing costs one restart; starting costs the backlog.

    The message names what is missing and, where there is one, the command that
    supplies it. It never carries a credential or a probe's raw output.
    """


# ---------------------------------------------------------------------------
# Schema primitives
#
# Shared by all three documents and parameterised by the exception type, so the
# enrollment reader raises stage-1 refusals and the binding reader raises
# stage-2 refusals out of one set of rules.
# ---------------------------------------------------------------------------


def _mapping(
    payload: object, *, document: str, error: type[BridgeStartupError]
) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise error(f"{document} must be a JSON object")
    for key in payload:
        if not isinstance(key, str):
            raise error(f"{document} has a non-string field name")
    return payload


def _fields(
    payload: Mapping[str, object],
    *,
    document: str,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
    error: type[BridgeStartupError],
) -> None:
    if missing := sorted(set(required) - set(payload)):
        raise error(f"{document} is missing required fields: {', '.join(missing)}")
    if unknown := sorted(set(payload) - set(required) - set(optional)):
        raise error(f"{document} carries unknown fields: {', '.join(unknown)}")


def _const(
    payload: Mapping[str, object],
    key: str,
    expected: str,
    *,
    document: str,
    error: type[BridgeStartupError],
) -> str:
    if payload[key] != expected:
        raise error(f"{document}.{key} must be {expected!r}")
    return expected


def _string(
    payload: Mapping[str, object],
    key: str,
    *,
    document: str,
    error: type[BridgeStartupError],
    minimum: int = 1,
    maximum: int,
    pattern: re.Pattern[str] | None = None,
    shape: str = "",
) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise error(f"{document}.{key} must be a string")
    if not minimum <= len(value) <= maximum:
        raise error(f"{document}.{key} must be {minimum}..{maximum} characters")
    if pattern is not None and not pattern.match(value):
        raise error(f"{document}.{key} is not {shape}")
    return value


def _optional_string(
    payload: Mapping[str, object],
    key: str,
    *,
    document: str,
    error: type[BridgeStartupError],
    minimum: int = 1,
    maximum: int,
    pattern: re.Pattern[str] | None = None,
    shape: str = "",
) -> str | None:
    if payload.get(key) is None:
        return None
    return _string(
        payload,
        key,
        document=document,
        error=error,
        minimum=minimum,
        maximum=maximum,
        pattern=pattern,
        shape=shape,
    )


def _uuid(
    payload: Mapping[str, object], key: str, *, document: str, error: type[BridgeStartupError]
) -> UUID:
    value = payload[key]
    if not isinstance(value, str):
        raise error(f"{document}.{key} must be a string")
    try:
        return UUID(value)
    except ValueError as invalid:
        raise error(f"{document}.{key} is not a uuid") from invalid


def _optional_uuid(
    payload: Mapping[str, object], key: str, *, document: str, error: type[BridgeStartupError]
) -> UUID | None:
    if payload.get(key) is None:
        return None
    return _uuid(payload, key, document=document, error=error)


def _http_url(
    payload: Mapping[str, object], key: str, *, document: str, error: type[BridgeStartupError]
) -> str:
    value = _string(payload, key, document=document, error=error, maximum=2000)
    parsed = urlparse(value)
    # The schema says "format": "uri"; the Bridge additionally requires a scheme
    # it can actually open, because the only two consumers of these fields are
    # an httpx client and a Matrix client.
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise error(f"{document}.{key} must be an http(s) URL")
    return value


def _room_ids(
    payload: Mapping[str, object], key: str, *, document: str, error: type[BridgeStartupError]
) -> tuple[str, ...]:
    value = payload[key]
    if not isinstance(value, list):
        raise error(f"{document}.{key} must be an array")
    if not 1 <= len(value) <= _MAX_ROOMS:
        raise error(f"{document}.{key} must hold 1..{_MAX_ROOMS} room ids")
    if len(set(value)) != len(value):
        raise error(f"{document}.{key} must be unique")
    for room_id in value:
        if (
            not isinstance(room_id, str)
            or not _MATRIX_ROOM_ID.match(room_id)
            or len(room_id) > _MAX_MATRIX_ID
        ):
            raise error(f"{document}.{key} holds something that is not a Matrix room id")
    return tuple(value)


# ---------------------------------------------------------------------------
# repomesh.agent-bridge.enrollment.v1
# ---------------------------------------------------------------------------

_CREDENTIAL_SLOTS: tuple[str, ...] = ("matrix", "model", "scm", "repomesh")


@dataclass(frozen=True, slots=True)
class CredentialRefs:
    """Opaque locators, never secrets.

    A ref names *where* a secret lives; the resolver turns it into a value at
    the last possible moment and that value never comes back into this object,
    a log line, stdout, or an exception message.
    """

    matrix: str
    model: str | None = None
    scm: str | None = None
    repomesh: str | None = None

    @classmethod
    def from_wire(cls, payload: object) -> "CredentialRefs":
        document = "credentialRefs"
        body = _mapping(payload, document=document, error=EnrollmentInvalid)
        _fields(
            body,
            document=document,
            required=("matrix",),
            optional=("model", "scm", "repomesh"),
            error=EnrollmentInvalid,
        )
        read = {
            name: _optional_string(
                body,
                name,
                document=document,
                error=EnrollmentInvalid,
                maximum=_MAX_CREDENTIAL_REF,
            )
            for name in _CREDENTIAL_SLOTS
        }
        if read["matrix"] is None:
            raise EnrollmentInvalid(f"{document}.matrix must be a non-empty reference")
        return cls(**read)

    def items(self) -> tuple[tuple[str, str], ...]:
        """Present refs in a fixed order, so output never depends on dict order."""

        return tuple(
            (name, value)
            for name in _CREDENTIAL_SLOTS
            if (value := getattr(self, name)) is not None
        )

    def names(self) -> tuple[str, ...]:
        """The slot names an operator may see. The locators themselves stay in."""

        return tuple(name for name, _ in self.items())

    def to_wire(self) -> dict[str, str]:
        return dict(self.items())


@dataclass(frozen=True, slots=True)
class ExternalWorkerEnrollment:
    """The non-secret binding one Bridge instance needs to serve one worker."""

    organization_id: UUID
    worker_agent_id: UUID
    worker_name: str
    team_name: str
    matrix_user_id: str
    matrix_homeserver_url: str
    allowed_room_ids: tuple[str, ...]
    repomesh_endpoint: str
    coding_profile: str
    credential_refs: CredentialRefs
    display_name: str | None = None

    @classmethod
    def from_wire(cls, payload: object) -> "ExternalWorkerEnrollment":
        document = "enrollment"
        body = _mapping(payload, document=document, error=EnrollmentInvalid)
        _fields(
            body,
            document=document,
            required=(
                "schemaVersion",
                "organizationId",
                "workerAgentId",
                "workerName",
                "teamName",
                "matrixUserId",
                "matrixHomeserverUrl",
                "allowedRoomIds",
                "repomeshEndpoint",
                "codingProfile",
                "credentialRefs",
            ),
            optional=("displayName",),
            error=EnrollmentInvalid,
        )
        _const(
            body,
            "schemaVersion",
            ENROLLMENT_SCHEMA_VERSION,
            document=document,
            error=EnrollmentInvalid,
        )
        profile = _string(
            body, "codingProfile", document=document, error=EnrollmentInvalid, maximum=_MAX_NAME
        )
        if profile not in CODING_PROFILES:
            raise EnrollmentInvalid(
                f"{document}.codingProfile must be one of {', '.join(CODING_PROFILES)}"
            )
        return cls(
            organization_id=_uuid(
                body, "organizationId", document=document, error=EnrollmentInvalid
            ),
            worker_agent_id=_uuid(
                body, "workerAgentId", document=document, error=EnrollmentInvalid
            ),
            worker_name=_string(
                body, "workerName", document=document, error=EnrollmentInvalid, maximum=_MAX_NAME
            ),
            team_name=_string(
                body, "teamName", document=document, error=EnrollmentInvalid, maximum=_MAX_NAME
            ),
            matrix_user_id=_string(
                body,
                "matrixUserId",
                document=document,
                error=EnrollmentInvalid,
                maximum=_MAX_MATRIX_ID,
                pattern=_MATRIX_USER_ID,
                shape="a Matrix user id",
            ),
            matrix_homeserver_url=_http_url(
                body, "matrixHomeserverUrl", document=document, error=EnrollmentInvalid
            ),
            allowed_room_ids=_room_ids(
                body, "allowedRoomIds", document=document, error=EnrollmentInvalid
            ),
            repomesh_endpoint=_http_url(
                body, "repomeshEndpoint", document=document, error=EnrollmentInvalid
            ),
            coding_profile=profile,
            credential_refs=CredentialRefs.from_wire(body["credentialRefs"]),
            display_name=_optional_string(
                body, "displayName", document=document, error=EnrollmentInvalid, maximum=_MAX_NAME
            ),
        )

    @property
    def display(self) -> str:
        """What a room should call this worker."""

        return self.display_name or self.worker_name

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {
            "schemaVersion": ENROLLMENT_SCHEMA_VERSION,
            "organizationId": str(self.organization_id),
            "workerAgentId": str(self.worker_agent_id),
            "workerName": self.worker_name,
            "teamName": self.team_name,
            "matrixUserId": self.matrix_user_id,
            "matrixHomeserverUrl": self.matrix_homeserver_url,
            "allowedRoomIds": list(self.allowed_room_ids),
            "repomeshEndpoint": self.repomesh_endpoint,
            "codingProfile": self.coding_profile,
            "credentialRefs": self.credential_refs.to_wire(),
        }
        if self.display_name is not None:
            wire["displayName"] = self.display_name
        return wire


# ---------------------------------------------------------------------------
# repomesh.agent-bridge.binding.v1
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkerBinding:
    """RepoMesh's preflight answer: what it has on file for this worker.

    Mirrors ``external-worker-binding.schema.json`` field for field. The Bridge
    never overrides a value here with its enrollment's; a disagreement is a
    refusal, which is the whole point of asking.
    """

    organization_id: UUID
    team_name: str
    worker_agent_id: UUID
    worker_name: str
    matrix_user_id: str
    allowed_room_ids: tuple[str, ...]
    container_managed: bool = False

    @classmethod
    def from_wire(cls, payload: object) -> "WorkerBinding":
        document = "binding"
        body = _mapping(payload, document=document, error=BindingRefused)
        _fields(
            body,
            document=document,
            required=(
                "schemaVersion",
                "organizationId",
                "teamName",
                "workerAgentId",
                "workerName",
                "matrixUserId",
                "allowedRoomIds",
                "containerManaged",
            ),
            error=BindingRefused,
        )
        _const(
            body, "schemaVersion", BINDING_SCHEMA_VERSION, document=document, error=BindingRefused
        )
        # ``is not False`` rather than a truth test: 0, "", None and an absent
        # value are all falsey and none of them is the schema's const false.
        # A control plane that answers with the wrong type has not confirmed
        # anything, and this is the field the whole preflight exists to confirm.
        if body["containerManaged"] is not False:
            raise BindingRefused(f"{document}.containerManaged must be the boolean false")
        return cls(
            organization_id=_uuid(
                body, "organizationId", document=document, error=BindingRefused
            ),
            team_name=_string(
                body, "teamName", document=document, error=BindingRefused, maximum=_MAX_NAME
            ),
            worker_agent_id=_uuid(
                body, "workerAgentId", document=document, error=BindingRefused
            ),
            worker_name=_string(
                body, "workerName", document=document, error=BindingRefused, maximum=_MAX_NAME
            ),
            matrix_user_id=_string(
                body,
                "matrixUserId",
                document=document,
                error=BindingRefused,
                maximum=_MAX_MATRIX_ID,
                pattern=_MATRIX_USER_ID,
                shape="a Matrix user id",
            ),
            allowed_room_ids=_room_ids(
                body, "allowedRoomIds", document=document, error=BindingRefused
            ),
            container_managed=False,
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "schemaVersion": BINDING_SCHEMA_VERSION,
            "organizationId": str(self.organization_id),
            "teamName": self.team_name,
            "workerAgentId": str(self.worker_agent_id),
            "workerName": self.worker_name,
            "matrixUserId": self.matrix_user_id,
            "allowedRoomIds": list(self.allowed_room_ids),
            "containerManaged": False,
        }


# ---------------------------------------------------------------------------
# repomesh.room-observation.v1
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoomObservation:
    """One observable fact the Bridge may project into a room.

    PR 2 ships the wire model only — no projection logic and no sender. It
    exists now so the server side can swap its observation fixtures for a real
    ``to_wire()`` payload, and so PR 3/PR 5 inherit a shape that was checked
    against the frozen schema instead of inventing one under deadline.

    Absent and explicit-null optionals mean the same thing here, and ``to_wire``
    normalises to absent: the schema allows both, and a room payload should not
    carry a column of nulls.
    """

    observation_id: UUID
    emitted_at: datetime
    worker_name: str
    room_id: str
    kind: str
    body: str
    task_id: UUID | None = None
    run_id: UUID | None = None
    phase: str | None = None
    tool_name: str | None = None
    changed_files: tuple[str, ...] | None = None
    test_command: str | None = None
    test_exit_code: int | None = None
    commit_sha: str | None = None
    question_id: UUID | None = None

    @classmethod
    def from_wire(cls, payload: object) -> "RoomObservation":
        document = "observation"
        body = _mapping(payload, document=document, error=EnrollmentInvalid)
        _fields(
            body,
            document=document,
            required=(
                "schemaVersion",
                "observationId",
                "emittedAt",
                "workerName",
                "roomId",
                "kind",
                "body",
            ),
            optional=(
                "taskId",
                "runId",
                "phase",
                "toolName",
                "changedFiles",
                "testCommand",
                "testExitCode",
                "commitSha",
                "questionId",
            ),
            error=EnrollmentInvalid,
        )
        _const(
            body,
            "schemaVersion",
            ROOM_OBSERVATION_SCHEMA_VERSION,
            document=document,
            error=EnrollmentInvalid,
        )
        kind = _string(body, "kind", document=document, error=EnrollmentInvalid, maximum=_MAX_NAME)
        if kind not in OBSERVATION_KINDS:
            raise EnrollmentInvalid(f"{document}.kind is not a room-observation kind")
        phase = _optional_string(
            body, "phase", document=document, error=EnrollmentInvalid, maximum=_MAX_NAME
        )
        if phase is not None and phase not in OBSERVATION_PHASES:
            raise EnrollmentInvalid(f"{document}.phase is not a room-observation phase")
        return cls(
            observation_id=_uuid(
                body, "observationId", document=document, error=EnrollmentInvalid
            ),
            emitted_at=_timestamp(body, "emittedAt", document=document),
            worker_name=_string(
                body, "workerName", document=document, error=EnrollmentInvalid, maximum=_MAX_NAME
            ),
            room_id=_string(
                body,
                "roomId",
                document=document,
                error=EnrollmentInvalid,
                maximum=_MAX_MATRIX_ID,
                pattern=_MATRIX_ROOM_ID,
                shape="a Matrix room id",
            ),
            kind=kind,
            body=_string(body, "body", document=document, error=EnrollmentInvalid, maximum=4000),
            task_id=_optional_uuid(body, "taskId", document=document, error=EnrollmentInvalid),
            run_id=_optional_uuid(body, "runId", document=document, error=EnrollmentInvalid),
            phase=phase,
            tool_name=_optional_string(
                body, "toolName", document=document, error=EnrollmentInvalid, maximum=_MAX_NAME
            ),
            changed_files=_changed_files(body, "changedFiles", document=document),
            test_command=_optional_string(
                body, "testCommand", document=document, error=EnrollmentInvalid, maximum=500
            ),
            test_exit_code=_exit_code(body, "testExitCode", document=document),
            commit_sha=_optional_string(
                body,
                "commitSha",
                document=document,
                error=EnrollmentInvalid,
                maximum=64,
                minimum=7,
                pattern=_COMMIT_SHA,
                shape="a commit sha",
            ),
            question_id=_optional_uuid(
                body, "questionId", document=document, error=EnrollmentInvalid
            ),
        )

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {
            "schemaVersion": ROOM_OBSERVATION_SCHEMA_VERSION,
            "observationId": str(self.observation_id),
            "emittedAt": self.emitted_at.isoformat(),
            "workerName": self.worker_name,
            "roomId": self.room_id,
            "kind": self.kind,
            "body": self.body,
        }
        optional: tuple[tuple[str, object], ...] = (
            ("taskId", None if self.task_id is None else str(self.task_id)),
            ("runId", None if self.run_id is None else str(self.run_id)),
            ("phase", self.phase),
            ("toolName", self.tool_name),
            ("changedFiles", None if self.changed_files is None else list(self.changed_files)),
            ("testCommand", self.test_command),
            ("testExitCode", self.test_exit_code),
            ("commitSha", self.commit_sha),
            ("questionId", None if self.question_id is None else str(self.question_id)),
        )
        wire.update({key: value for key, value in optional if value is not None})
        return wire


def _timestamp(payload: Mapping[str, object], key: str, *, document: str) -> datetime:
    value = payload[key]
    if not isinstance(value, str):
        raise EnrollmentInvalid(f"{document}.{key} must be a string")
    try:
        return datetime.fromisoformat(value)
    except ValueError as invalid:
        raise EnrollmentInvalid(f"{document}.{key} is not an ISO-8601 timestamp") from invalid


def _changed_files(
    payload: Mapping[str, object], key: str, *, document: str
) -> tuple[str, ...] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 200:
        raise EnrollmentInvalid(f"{document}.{key} must be an array of at most 200 paths")
    for path in value:
        if not isinstance(path, str) or not 1 <= len(path) <= 500:
            raise EnrollmentInvalid(f"{document}.{key} holds something that is not a path")
    return tuple(value)


def _exit_code(payload: Mapping[str, object], key: str, *, document: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    # bool is an int in Python and is not one on the wire.
    if isinstance(value, bool) or not isinstance(value, int):
        raise EnrollmentInvalid(f"{document}.{key} must be an integer")
    return value
