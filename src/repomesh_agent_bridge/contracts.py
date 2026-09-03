"""Wire models for every frozen document the Bridge speaks.

Three families live here. ``contracts/agent-bridge/v1`` is the original: the
enrollment an operator writes, the binding preflight answers with, and the room
observation the outbox renders. ``contracts/agent-bridge/v2`` is that same
enrollment and binding plus exactly one field, ``role``, which is what lets one
Bridge process serve a Worker or a Repository Leader. ``contracts/leader-actions/v1``
is the leader's decision surface: the fact package it plans from, the two
decisions it produces, the two receipts it gets back, and the structured error
every refusal arrives as.

The Bridge is a separate process from the RepoMesh control plane, so it owns
its own model of these documents instead of importing the server's Python
types. The two sides agree because the frozen schema is the contract, not
because they share a class; ``tests/contracts/test_agent_bridge_v1_contract.py``,
``test_agent_bridge_v2_contract.py`` and ``test_leader_actions_v1_contract.py``
are where that agreement is checked.

**v2 is a version of the v1 records, not a second pair of classes.** The
enrollment and binding dataclasses carry a ``schema_version`` and a ``role``
whose defaults are exactly v1's meaning, and each has a second reader for the
v2 document. The alternative — new ``ExternalMember*`` classes — would make
every consumer take a union, including the supervisor, for one added field; and
the wire itself already made this call, keeping ``workerAgentId``/``workerName``
for both roles rather than renaming them (v2 README, adjudication D-6). The
class names are historical for the same reason the field names are.

That the two versions cannot be confused is a property of the records rather
than of a reader remembering to check: ``__post_init__`` refuses a
``repository_leader`` that claims a v1 ``schema_version``, which is the v2
README's "a repository_leader document has no v1 representation" written as a
constructor.

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

ENROLLMENT_V2_SCHEMA_VERSION = "repomesh.agent-bridge.enrollment.v2"
BINDING_V2_SCHEMA_VERSION = "repomesh.agent-bridge.binding.v2"

ASSIGNMENT_PACKAGE_SCHEMA_VERSION = "repomesh.leader-actions.assignment-package.v1"
PLAN_DECISION_SCHEMA_VERSION = "repomesh.leader-actions.plan-decision.v1"
REVIEW_DECISION_SCHEMA_VERSION = "repomesh.leader-actions.review-decision.v1"
PLAN_RECEIPT_SCHEMA_VERSION = "repomesh.leader-actions.plan-receipt.v1"
REVIEW_RECEIPT_SCHEMA_VERSION = "repomesh.leader-actions.review-receipt.v1"

READINESS_SCHEMA_VERSION = "repomesh.agent-bridge.readiness.v1"
"""The document one Bridge instance reports its own liveness with.

A new family rather than a field on an existing one: the enrollment and the
binding describe a *member*, they are frozen, and every deployed Bridge already
round-trips them — while this describes a *process*, which is the thing that
comes and goes.

It is the one document here with no dataclass beside it, and that is not an
omission. The report is written by this side and read by the server, and the
answer that comes back is a receipt whose only field this process acts on is an
integer number of seconds. A wire model for a document nothing here parses would
be a validation boundary with no untrusted bytes on the other side of it.
"""

ROLE_WORKER = "worker"
ROLE_REPOSITORY_LEADER = "repository_leader"
MEMBER_ROLES: tuple[str, ...] = (ROLE_WORKER, ROLE_REPOSITORY_LEADER)
"""The v2 ``role`` enum, spelled once.

``organization_leader`` is deliberately absent and is not an oversight: the
Organization Leader stays on the existing AgentTeams Manager, RepoMesh refuses
one at provision time, and the schema cannot express one either (v2 README,
adjudication D-11). A Bridge that meets the string in a document says no here
rather than somewhere later.
"""

DECISION_PROVENANCE_SOURCE = "leader-codex-session"
"""The only ``provenance.source`` either leader decision may carry.

A constant rather than a per-document literal because it is the same claim in
both: this product came out of the leader's own coding session. The server
refuses a submission that cannot make it (``plan_invalid_provenance``), and the
Bridge only ever writes it beside a session thread id it actually received.
"""

ASSIGNMENT_PHASES: tuple[str, ...] = ("planning", "executing", "review_due", "closed")
REVIEW_VERDICTS: tuple[str, ...] = ("approve", "request_rework", "escalate")
LEADER_TASK_STATUSES: tuple[str, ...] = ("succeeded", "in_progress", "blocked")
WORKER_EVIDENCE_STATUSES: tuple[str, ...] = (
    "succeeded",
    "failed",
    "blocked",
    "cancelled",
    "superseded",
)

LEADER_ACTION_ERROR_CODES: tuple[str, ...] = (
    "invalid_token",
    "forbidden_not_assignee",
    "forbidden_role",
    "assignment_not_found",
    "phase_conflict",
    "decomposition_mode_conflict",
    "plan_invalid_dag_cycle",
    "plan_invalid_dag_coverage",
    "plan_invalid_assignee",
    "plan_invalid_allowed_paths",
    "plan_invalid_tests_removed",
    "plan_invalid_provenance",
    "review_invalid_findings",
)
"""The frozen ``structured-error`` code enum, in the schema's own order.

Held as data rather than as a Python enum because the Bridge is the *consumer*:
its job is to carry the server's own word for a refusal to whoever has to act on
it, and a build that turned an unrecognised code into an ``AttributeError``
would fail hardest exactly when the two sides disagreed. An unknown code is
reported as itself; this tuple is what lets a test say the two sides still agree.
"""

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


class LeaderDocumentInvalid(RuntimeError):
    """A ``contracts/leader-actions/v1`` document is not what the freeze says.

    Deliberately outside :class:`BridgeStartupError`: none of these documents is
    read at startup and none of them stops a process from starting. One of them
    arrives from the control plane mid-session and one is produced by the
    leader's own coding session, and the two callers grade the same refusal
    differently — the HTTP adapter turns an unreadable *answer* into a
    ``LeaderActionRefused``, because a receipt this process cannot read is not a
    receipt; the coordination session turns an unreadable *decision* into a note
    the leader can act on, because the model wrote something the contract does
    not describe and quietly repairing it would be the Bridge authoring part of
    the leader's product.

    The message names the field and what was wrong with it, and never carries a
    credential, a path, or the document itself.
    """


# ---------------------------------------------------------------------------
# Schema primitives
#
# Shared by every document here and parameterised by the exception type, so the
# enrollment reader raises stage-1 refusals, the binding reader raises stage-2
# refusals, and the leader-actions readers raise their own, out of one set of
# rules. The parameter is typed ``type[Exception]`` rather than the startup
# family because that family is now one of three callers, not all of them.
# ---------------------------------------------------------------------------

WireError = type[Exception]
"""What a reader raises when a document does not match its schema."""


def _mapping(payload: object, *, document: str, error: WireError) -> Mapping[str, object]:
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
    error: WireError,
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
    error: WireError,
) -> str:
    if payload[key] != expected:
        raise error(f"{document}.{key} must be {expected!r}")
    return expected


def _string(
    payload: Mapping[str, object],
    key: str,
    *,
    document: str,
    error: WireError,
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
    error: WireError,
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
    payload: Mapping[str, object], key: str, *, document: str, error: WireError
) -> UUID:
    value = payload[key]
    if not isinstance(value, str):
        raise error(f"{document}.{key} must be a string")
    try:
        return UUID(value)
    except ValueError as invalid:
        raise error(f"{document}.{key} is not a uuid") from invalid


def _optional_uuid(
    payload: Mapping[str, object], key: str, *, document: str, error: WireError
) -> UUID | None:
    if payload.get(key) is None:
        return None
    return _uuid(payload, key, document=document, error=error)


def _http_url(
    payload: Mapping[str, object], key: str, *, document: str, error: WireError
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
    payload: Mapping[str, object], key: str, *, document: str, error: WireError
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


def _enum(
    payload: Mapping[str, object],
    key: str,
    allowed: tuple[str, ...],
    *,
    document: str,
    error: WireError,
) -> str:
    value = _string(payload, key, document=document, error=error, maximum=_MAX_NAME)
    if value not in allowed:
        raise error(f"{document}.{key} must be one of {', '.join(allowed)}")
    return value


def _object(
    payload: Mapping[str, object], key: str, *, document: str, error: WireError
) -> Mapping[str, object]:
    return _mapping(payload[key], document=f"{document}.{key}", error=error)


def _array(
    payload: Mapping[str, object],
    key: str,
    *,
    document: str,
    error: WireError,
    minimum_items: int = 0,
) -> list[object]:
    value = payload[key]
    if not isinstance(value, list):
        raise error(f"{document}.{key} must be an array")
    if len(value) < minimum_items:
        raise error(f"{document}.{key} must hold at least {minimum_items} item(s)")
    return value


def _string_array(
    payload: Mapping[str, object],
    key: str,
    *,
    document: str,
    error: WireError,
    minimum_items: int = 0,
) -> tuple[str, ...]:
    items = _array(payload, key, document=document, error=error, minimum_items=minimum_items)
    for item in items:
        if not isinstance(item, str) or not item:
            raise error(f"{document}.{key} holds something that is not a non-empty string")
    return tuple(str(item) for item in items)


def _integer(
    payload: Mapping[str, object],
    key: str,
    *,
    document: str,
    error: WireError,
    minimum: int | None = None,
) -> int:
    value = payload[key]
    # bool is an int in Python and is not one on the wire.
    if isinstance(value, bool) or not isinstance(value, int):
        raise error(f"{document}.{key} must be an integer")
    if minimum is not None and value < minimum:
        raise error(f"{document}.{key} must be at least {minimum}")
    return value


def _member_role(
    payload: Mapping[str, object], *, document: str, error: WireError
) -> str:
    """Read the v2 ``role`` field, refusing anything outside the frozen enum.

    ``organization_leader`` reaches here as an ordinary enum miss and is refused
    with the same sentence as a typo, which is the honest answer: the string
    names a real RepoMesh role, but this contract cannot describe one, so there
    is nothing for a Bridge to do with it either way.
    """

    return _enum(payload, "role", MEMBER_ROLES, document=document, error=error)


# ---------------------------------------------------------------------------
# repomesh.agent-bridge.enrollment.v1 / .v2
# ---------------------------------------------------------------------------

_CREDENTIAL_SLOTS: tuple[str, ...] = ("matrix", "model", "scm", "repomesh")

_ENROLLMENT_V1_FIELDS: tuple[str, ...] = (
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
)
_BINDING_V1_FIELDS: tuple[str, ...] = (
    "schemaVersion",
    "organizationId",
    "teamName",
    "workerAgentId",
    "workerName",
    "matrixUserId",
    "allowedRoomIds",
    "containerManaged",
)


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
    """The non-secret binding one Bridge instance needs to serve one member.

    Named for the worker because that is what v1 described and what every
    deployed consumer calls it; under v2 the same record also describes a
    Repository Leader, and ``role`` is how it says which. The wire made the
    identical call one field earlier, keeping ``workerAgentId``/``workerName``
    for both roles rather than renaming them for zero information (v2 README).
    """

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
    role: str = ROLE_WORKER
    """Which member this Bridge serves as. ``worker`` is v1's only meaning and
    therefore the default: a record built from a v1 document, or by a caller
    written before v2 existed, describes a worker and says so."""
    schema_version: str = ENROLLMENT_SCHEMA_VERSION
    """Which version this record came from and round-trips back to.

    Carried rather than inferred from ``role`` because the two are not the same
    question: a *worker* is expressible in both versions, and a Bridge that
    silently answered a v1 deployment in v2 would break the compatibility the
    whole second file exists to preserve.
    """

    def __post_init__(self) -> None:
        _check_member_version(self.role, self.schema_version, ENROLLMENT_SCHEMA_VERSION)

    @classmethod
    def from_wire(cls, payload: object) -> "ExternalWorkerEnrollment":
        """Read a v1 enrollment. Byte-for-byte the reader v1 consumers had."""

        return cls._read(payload, schema_version=ENROLLMENT_SCHEMA_VERSION)

    @classmethod
    def from_wire_v2(cls, payload: object) -> "ExternalWorkerEnrollment":
        """Read a v2 enrollment: v1's document plus the required ``role``."""

        return cls._read(payload, schema_version=ENROLLMENT_V2_SCHEMA_VERSION)

    @classmethod
    def _read(cls, payload: object, *, schema_version: str) -> "ExternalWorkerEnrollment":
        document = "enrollment"
        body = _mapping(payload, document=document, error=EnrollmentInvalid)
        versioned = schema_version == ENROLLMENT_V2_SCHEMA_VERSION
        _fields(
            body,
            document=document,
            required=_ENROLLMENT_V1_FIELDS + (("role",) if versioned else ()),
            optional=("displayName",),
            error=EnrollmentInvalid,
        )
        _const(
            body,
            "schemaVersion",
            schema_version,
            document=document,
            error=EnrollmentInvalid,
        )
        role = (
            _member_role(body, document=document, error=EnrollmentInvalid)
            if versioned
            else ROLE_WORKER
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
            role=role,
            schema_version=schema_version,
        )

    @property
    def display(self) -> str:
        """What a room should call this member."""

        return self.display_name or self.worker_name

    @property
    def is_repository_leader(self) -> bool:
        """Does this enrollment serve a Repository Leader?

        A property rather than a comparison at each call site because the answer
        gates three unrelated things — the preflight endpoint, the CLI's refusal
        of ``--workspace-root``, and whether a Runner queue is consumed at all —
        and a spelling mistake in any one of them fails open.
        """

        return self.role == ROLE_REPOSITORY_LEADER

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {
            "schemaVersion": self.schema_version,
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
        if self.schema_version == ENROLLMENT_V2_SCHEMA_VERSION:
            wire["role"] = self.role
        if self.display_name is not None:
            wire["displayName"] = self.display_name
        return wire


def _check_member_version(role: str, schema_version: str, v1_version: str) -> None:
    """Refuse a record no version of the contract could have produced.

    Two rules, both from the v2 README's round-trip section. A role outside the
    frozen enum is not a member this contract describes. And a
    ``repository_leader`` at a v1 ``schemaVersion`` is the downgrade the README
    calls an error rather than a lossy conversion — v1 has no field to put the
    role in, so such a document would silently read back as a worker, which is
    the one confusion the whole second version exists to prevent.

    Raised as ``ValueError`` rather than a wire refusal because no wire reader
    can produce it: both readers pin ``schemaVersion`` to a constant before they
    look at ``role``. Only a caller constructing a record by hand can get here,
    and that is a bug in this process, not a bad document from another one.
    """

    if role not in MEMBER_ROLES:
        raise ValueError(f"role must be one of {', '.join(MEMBER_ROLES)}")
    if schema_version == v1_version and role != ROLE_WORKER:
        raise ValueError(f"a {role} has no v1 representation; it is only expressible in v2")


def read_enrollment(payload: object) -> ExternalWorkerEnrollment:
    """Read an enrollment of whichever version it declares itself to be.

    The one place a version is *chosen* rather than asserted, so a document that
    declares neither is refused once, here, with both spellings named. The two
    readers stay separate underneath: a caller that knows which version it holds
    — the contract tests, a v1-only consumer — says so and is not routed.
    """

    body = _mapping(payload, document="enrollment", error=EnrollmentInvalid)
    version = body.get("schemaVersion")
    if version == ENROLLMENT_V2_SCHEMA_VERSION:
        return ExternalWorkerEnrollment.from_wire_v2(body)
    if version == ENROLLMENT_SCHEMA_VERSION:
        return ExternalWorkerEnrollment.from_wire(body)
    raise EnrollmentInvalid(
        f"enrollment.schemaVersion must be {ENROLLMENT_SCHEMA_VERSION!r} or "
        f"{ENROLLMENT_V2_SCHEMA_VERSION!r}"
    )


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
    role: str = ROLE_WORKER
    """The role RepoMesh has on file, confirmed from its own agent directory.

    Never echoed back from the enrollment: that is what makes an
    enrollment/binding disagreement a preflight failure rather than a value the
    Bridge resolves locally (v2 README).
    """
    schema_version: str = BINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _check_member_version(self.role, self.schema_version, BINDING_SCHEMA_VERSION)

    @classmethod
    def from_wire(cls, payload: object) -> "WorkerBinding":
        """Read a v1 binding. Byte-for-byte the reader v1 consumers had."""

        return cls._read(payload, schema_version=BINDING_SCHEMA_VERSION)

    @classmethod
    def from_wire_v2(cls, payload: object) -> "WorkerBinding":
        """Read a v2 binding: v1's document plus the confirmed ``role``."""

        return cls._read(payload, schema_version=BINDING_V2_SCHEMA_VERSION)

    @classmethod
    def _read(cls, payload: object, *, schema_version: str) -> "WorkerBinding":
        document = "binding"
        body = _mapping(payload, document=document, error=BindingRefused)
        versioned = schema_version == BINDING_V2_SCHEMA_VERSION
        _fields(
            body,
            document=document,
            required=_BINDING_V1_FIELDS + (("role",) if versioned else ()),
            error=BindingRefused,
        )
        _const(body, "schemaVersion", schema_version, document=document, error=BindingRefused)
        role = (
            _member_role(body, document=document, error=BindingRefused)
            if versioned
            else ROLE_WORKER
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
            role=role,
            schema_version=schema_version,
        )

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {
            "schemaVersion": self.schema_version,
            "organizationId": str(self.organization_id),
            "teamName": self.team_name,
            "workerAgentId": str(self.worker_agent_id),
            "workerName": self.worker_name,
            "matrixUserId": self.matrix_user_id,
            "allowedRoomIds": list(self.allowed_room_ids),
            "containerManaged": False,
        }
        if self.schema_version == BINDING_V2_SCHEMA_VERSION:
            wire["role"] = self.role
        return wire


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


# ---------------------------------------------------------------------------
# contracts/leader-actions/v1
#
# The Repository Leader's decision surface. Three of these documents arrive from
# RepoMesh (the assignment package and the two receipts) and two leave for it
# (the plan and the review verdict) — and the two that leave are *also* read
# from the wire, because they are produced by the leader's own coding session as
# JSON and must be held against the freeze before they are posted. That is the
# whole reason both directions have a reader: the Bridge validates its own
# session's output rather than discovering a malformed plan from a 409. The
# server's clamp is a second line of defence, never this one's.
# ---------------------------------------------------------------------------

_LEADER_COMMIT_SHA = re.compile(r"^[0-9a-f]{7,40}$")
"""Narrower than the room observation's ``7..64``, because the leader-actions
schema says so. Two documents, two literals, and the difference is the
contract's rather than an oversight."""

_MAX_TEXT = 200_000
"""The bound this package puts on a wire string the schema leaves unbounded.

An instruction, a spec body and a diffstat have no ``maxLength``; a reader that
took that literally would let a control plane hand this process an arbitrarily
large string. The number is far above any real document, so it refuses nothing
the contract allows and still refuses a body that could only be a bug.
"""
_MAX_NODE_ID = 100
_MAX_TITLE = 200
_MAX_SUMMARY = 4000
_MAX_SPEC_SUMMARY = 500
_MAX_NOTE = 2000
_MAX_THREAD_ID = 200


@dataclass(frozen=True, slots=True)
class RepositoryTaskFacts:
    """The repository-level task as the Organization Manager assigned it.

    Facts, not hints: the leader plans *this*, and nothing here is negotiable.
    """

    title: str
    instruction: str
    acceptance: str

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "RepositoryTaskFacts":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=("title", "instruction", "acceptance"),
            error=LeaderDocumentInvalid,
        )
        return cls(
            title=_string(
                body, "title", document=document, error=LeaderDocumentInvalid, maximum=_MAX_TITLE
            ),
            instruction=_string(
                body,
                "instruction",
                document=document,
                error=LeaderDocumentInvalid,
                maximum=_MAX_TEXT,
            ),
            # The only field of the three the schema lets be empty.
            acceptance=_string(
                body,
                "acceptance",
                document=document,
                error=LeaderDocumentInvalid,
                minimum=0,
                maximum=_MAX_TEXT,
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "title": self.title,
            "instruction": self.instruction,
            "acceptance": self.acceptance,
        }


@dataclass(frozen=True, slots=True)
class WorkerRosterEntry:
    """One worker the leader may assign to. Anything else is plan_invalid_assignee."""

    worker_agent_id: UUID
    worker_name: str
    responsibility_paths: tuple[str, ...]

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "WorkerRosterEntry":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=("workerAgentId", "workerName", "responsibilityPaths"),
            error=LeaderDocumentInvalid,
        )
        return cls(
            worker_agent_id=_uuid(
                body, "workerAgentId", document=document, error=LeaderDocumentInvalid
            ),
            worker_name=_string(
                body,
                "workerName",
                document=document,
                error=LeaderDocumentInvalid,
                maximum=_MAX_NAME,
            ),
            responsibility_paths=_string_array(
                body, "responsibilityPaths", document=document, error=LeaderDocumentInvalid
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "workerAgentId": str(self.worker_agent_id),
            "workerName": self.worker_name,
            "responsibilityPaths": list(self.responsibility_paths),
        }


@dataclass(frozen=True, slots=True)
class SafetyEnvelope:
    """The server-derived hard bounds a plan is validated against.

    The envelope constrains the plan; it does not write it. Both of its clamp
    bounds are checked here before a plan is posted — see
    :meth:`RepositoryAssignmentPackage.refuse_plan` — so a leader learns about a
    violation from its own Bridge rather than from a 409 whose cause it has to
    guess.
    """

    allowed_path_roots: tuple[str, ...]
    test_paths: tuple[str, ...]
    test_commands: tuple[str, ...]

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "SafetyEnvelope":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=("allowedPathRoots", "testPaths", "testCommands"),
            error=LeaderDocumentInvalid,
        )
        return cls(
            allowed_path_roots=_string_array(
                body,
                "allowedPathRoots",
                document=document,
                error=LeaderDocumentInvalid,
                minimum_items=1,
            ),
            test_paths=_string_array(
                body, "testPaths", document=document, error=LeaderDocumentInvalid
            ),
            test_commands=_string_array(
                body, "testCommands", document=document, error=LeaderDocumentInvalid
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "allowedPathRoots": list(self.allowed_path_roots),
            "testPaths": list(self.test_paths),
            "testCommands": list(self.test_commands),
        }


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    """A cross-repository dependency fact: downstream depends on upstream."""

    upstream_repository_id: UUID
    downstream_repository_id: UUID

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "DependencyEdge":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=("upstreamRepositoryId", "downstreamRepositoryId"),
            error=LeaderDocumentInvalid,
        )
        return cls(
            upstream_repository_id=_uuid(
                body, "upstreamRepositoryId", document=document, error=LeaderDocumentInvalid
            ),
            downstream_repository_id=_uuid(
                body, "downstreamRepositoryId", document=document, error=LeaderDocumentInvalid
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "upstreamRepositoryId": str(self.upstream_repository_id),
            "downstreamRepositoryId": str(self.downstream_repository_id),
        }


@dataclass(frozen=True, slots=True)
class AdvisoryContext:
    """Everything the server offers that the leader is free to ignore.

    ``authoritative`` is a required ``const: false`` on the wire and is not a
    field here: it is the same word every time, and storing it would invite a
    caller to set it. :meth:`to_wire` writes the constant and :meth:`from_wire`
    refuses a document that claims otherwise — a hint that called itself
    authoritative would be describing a different contract.

    An absent array and an empty one mean the same thing (no edges), so
    ``to_wire`` omits an empty one rather than writing a column of nothing. The
    room observation next door normalises its optionals the same way.
    """

    discovery_evidence: str | None = None
    dependency_edges: tuple[DependencyEdge, ...] = ()
    decomposition_hint: str | None = None

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "AdvisoryContext":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=("authoritative",),
            optional=("discoveryEvidence", "dependencyEdges", "decompositionHint"),
            error=LeaderDocumentInvalid,
        )
        if body["authoritative"] is not False:
            raise LeaderDocumentInvalid(f"{document}.authoritative must be the boolean false")
        edges: tuple[DependencyEdge, ...] = ()
        if body.get("dependencyEdges") is not None:
            edges = tuple(
                DependencyEdge.from_wire(edge, document=f"{document}.dependencyEdges[{i}]")
                for i, edge in enumerate(
                    _array(
                        body, "dependencyEdges", document=document, error=LeaderDocumentInvalid
                    )
                )
            )
        return cls(
            discovery_evidence=_optional_string(
                body,
                "discoveryEvidence",
                document=document,
                error=LeaderDocumentInvalid,
                maximum=_MAX_TEXT,
            ),
            dependency_edges=edges,
            decomposition_hint=_optional_string(
                body,
                "decompositionHint",
                document=document,
                error=LeaderDocumentInvalid,
                maximum=_MAX_TEXT,
            ),
        )

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {"authoritative": False}
        if self.discovery_evidence is not None:
            wire["discoveryEvidence"] = self.discovery_evidence
        if self.dependency_edges:
            wire["dependencyEdges"] = [edge.to_wire() for edge in self.dependency_edges]
        if self.decomposition_hint is not None:
            wire["decompositionHint"] = self.decomposition_hint
        return wire


@dataclass(frozen=True, slots=True)
class TestResult:
    """One verification command and what it exited with."""

    command: str
    exit_code: int

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "TestResult":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=("command", "exitCode"),
            error=LeaderDocumentInvalid,
        )
        return cls(
            command=_string(
                body, "command", document=document, error=LeaderDocumentInvalid, maximum=_MAX_TEXT
            ),
            exit_code=_integer(body, "exitCode", document=document, error=LeaderDocumentInvalid),
        )

    def to_wire(self) -> dict[str, object]:
        return {"command": self.command, "exitCode": self.exit_code}


@dataclass(frozen=True, slots=True)
class WorkerEvidence:
    """What one worker task actually produced, as RepoMesh recorded it.

    The chain a review verdict has to be traceable along: task, run, commit,
    files, tests. ``run_id`` and ``commit_sha`` are nullable because a task can
    end without ever producing either, and a reviewer has to be able to tell
    "there was no commit" from "there was one and I was not shown it".
    """

    worker_task_id: UUID
    worker_agent_id: UUID
    status: str
    run_id: UUID | None
    commit_sha: str | None
    changed_files: tuple[str, ...]
    test_results: tuple[TestResult, ...]
    diff_stat: str | None = None
    summary: str | None = None

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "WorkerEvidence":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=(
                "workerTaskId",
                "workerAgentId",
                "status",
                "runId",
                "commitSha",
                "changedFiles",
                "testResults",
            ),
            optional=("diffStat", "summary"),
            error=LeaderDocumentInvalid,
        )
        return cls(
            worker_task_id=_uuid(
                body, "workerTaskId", document=document, error=LeaderDocumentInvalid
            ),
            worker_agent_id=_uuid(
                body, "workerAgentId", document=document, error=LeaderDocumentInvalid
            ),
            status=_enum(
                body,
                "status",
                WORKER_EVIDENCE_STATUSES,
                document=document,
                error=LeaderDocumentInvalid,
            ),
            run_id=_optional_uuid(body, "runId", document=document, error=LeaderDocumentInvalid),
            commit_sha=_optional_string(
                body,
                "commitSha",
                document=document,
                error=LeaderDocumentInvalid,
                minimum=7,
                maximum=40,
                pattern=_LEADER_COMMIT_SHA,
                shape="a commit sha",
            ),
            changed_files=_string_array(
                body, "changedFiles", document=document, error=LeaderDocumentInvalid
            ),
            test_results=tuple(
                TestResult.from_wire(result, document=f"{document}.testResults[{i}]")
                for i, result in enumerate(
                    _array(body, "testResults", document=document, error=LeaderDocumentInvalid)
                )
            ),
            diff_stat=_optional_string(
                body,
                "diffStat",
                document=document,
                error=LeaderDocumentInvalid,
                maximum=_MAX_TEXT,
            ),
            summary=_optional_string(
                body, "summary", document=document, error=LeaderDocumentInvalid, maximum=_MAX_TEXT
            ),
        )

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {
            "workerTaskId": str(self.worker_task_id),
            "workerAgentId": str(self.worker_agent_id),
            "status": self.status,
            "runId": None if self.run_id is None else str(self.run_id),
            "commitSha": self.commit_sha,
            "changedFiles": list(self.changed_files),
            "testResults": [result.to_wire() for result in self.test_results],
        }
        if self.diff_stat is not None:
            wire["diffStat"] = self.diff_stat
        if self.summary is not None:
            wire["summary"] = self.summary
        return wire


@dataclass(frozen=True, slots=True)
class ReviewEvidence:
    """The immutable evidence one review round must be based on.

    ``review_revision`` is half the review's idempotent identity — the other
    half is the leader task id — so it travels with the evidence rather than
    being remembered by whoever read it.
    """

    review_revision: int
    worker_evidence: tuple[WorkerEvidence, ...]

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "ReviewEvidence":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=("reviewRevision", "workerEvidence"),
            error=LeaderDocumentInvalid,
        )
        return cls(
            review_revision=_integer(
                body, "reviewRevision", document=document, error=LeaderDocumentInvalid, minimum=1
            ),
            worker_evidence=tuple(
                WorkerEvidence.from_wire(entry, document=f"{document}.workerEvidence[{i}]")
                for i, entry in enumerate(
                    _array(
                        body,
                        "workerEvidence",
                        document=document,
                        error=LeaderDocumentInvalid,
                        minimum_items=1,
                    )
                )
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "reviewRevision": self.review_revision,
            "workerEvidence": [entry.to_wire() for entry in self.worker_evidence],
        }


@dataclass(frozen=True, slots=True)
class RepositoryAssignmentPackage:
    """Everything the server gives a leader, and nothing it decides for it.

    Facts, a safety envelope and explicitly non-authoritative hints. The
    Engineering Spec, the DAG and the worker tasks are the leader's product and
    appear nowhere in this document.

    Nothing here names a place on disk (adjudication D-8). That is a property of
    the frozen schema rather than of this class, and it is what makes it
    possible to render the whole package into a coordination session's prompt
    without handing that session a repository.
    """

    leader_task_id: UUID
    phase: str
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    repository_task: RepositoryTaskFacts
    worker_roster: tuple[WorkerRosterEntry, ...]
    safety_envelope: SafetyEnvelope
    advisory_context: AdvisoryContext
    review_evidence: ReviewEvidence | None = None

    def __post_init__(self) -> None:
        """Hold the package against frozen invariant 1 before anybody reads it.

        Phase and evidence are coupled: no evidence before ``review_due``, and
        evidence from it on. A package that broke the coupling would send a
        leader to review nothing, or offer it evidence for work still in
        flight — and "verify rather than trust the control plane" is the same
        discipline preflight applies to a binding.
        """

        if self.phase not in ASSIGNMENT_PHASES:
            raise ValueError(f"phase must be one of {', '.join(ASSIGNMENT_PHASES)}")
        expects_evidence = self.phase in ("review_due", "closed")
        if expects_evidence and self.review_evidence is None:
            raise ValueError(f"a {self.phase} package must carry review evidence")
        if not expects_evidence and self.review_evidence is not None:
            raise ValueError(f"a {self.phase} package must not carry review evidence")

    @classmethod
    def from_wire(cls, payload: object) -> "RepositoryAssignmentPackage":
        document = "assignmentPackage"
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=(
                "schemaVersion",
                "leaderTaskId",
                "phase",
                "organizationId",
                "projectId",
                "repositoryId",
                "repositoryTask",
                "workerRoster",
                "safetyEnvelope",
                "advisoryContext",
                "reviewEvidence",
            ),
            error=LeaderDocumentInvalid,
        )
        _const(
            body,
            "schemaVersion",
            ASSIGNMENT_PACKAGE_SCHEMA_VERSION,
            document=document,
            error=LeaderDocumentInvalid,
        )
        evidence = body["reviewEvidence"]
        return _built(
            cls,
            leader_task_id=_uuid(
                body, "leaderTaskId", document=document, error=LeaderDocumentInvalid
            ),
            phase=_enum(
                body, "phase", ASSIGNMENT_PHASES, document=document, error=LeaderDocumentInvalid
            ),
            organization_id=_uuid(
                body, "organizationId", document=document, error=LeaderDocumentInvalid
            ),
            project_id=_uuid(body, "projectId", document=document, error=LeaderDocumentInvalid),
            repository_id=_uuid(
                body, "repositoryId", document=document, error=LeaderDocumentInvalid
            ),
            repository_task=RepositoryTaskFacts.from_wire(
                body["repositoryTask"], document=f"{document}.repositoryTask"
            ),
            worker_roster=tuple(
                WorkerRosterEntry.from_wire(entry, document=f"{document}.workerRoster[{i}]")
                for i, entry in enumerate(
                    _array(
                        body,
                        "workerRoster",
                        document=document,
                        error=LeaderDocumentInvalid,
                        minimum_items=1,
                    )
                )
            ),
            safety_envelope=SafetyEnvelope.from_wire(
                body["safetyEnvelope"], document=f"{document}.safetyEnvelope"
            ),
            advisory_context=AdvisoryContext.from_wire(
                body["advisoryContext"], document=f"{document}.advisoryContext"
            ),
            review_evidence=(
                None
                if evidence is None
                else ReviewEvidence.from_wire(evidence, document=f"{document}.reviewEvidence")
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "schemaVersion": ASSIGNMENT_PACKAGE_SCHEMA_VERSION,
            "leaderTaskId": str(self.leader_task_id),
            "phase": self.phase,
            "organizationId": str(self.organization_id),
            "projectId": str(self.project_id),
            "repositoryId": str(self.repository_id),
            "repositoryTask": self.repository_task.to_wire(),
            "workerRoster": [entry.to_wire() for entry in self.worker_roster],
            "safetyEnvelope": self.safety_envelope.to_wire(),
            "advisoryContext": self.advisory_context.to_wire(),
            "reviewEvidence": (
                None if self.review_evidence is None else self.review_evidence.to_wire()
            ),
        }

    def refuse_plan(self, plan: "RepositoryPlanDecision") -> str | None:
        """Why this package would reject ``plan``, or ``None`` if it would not.

        Frozen invariant 4, the envelope clamp, evaluated where both halves are
        in hand. It is a *pre*-check, not a substitute for the server's: the
        server clamps because it must, and this exists so that a leader whose
        session named a worker from another team, wrote outside the allowed
        roots, or dropped a mandatory test command hears it as a sentence it can
        act on instead of as a 409 whose cause it has to reconstruct.

        A sentence rather than an exception because the caller's next move is to
        tell the leader's own session what was wrong and ask again; raising
        would make the ordinary case an error path.
        """

        roster = {entry.worker_agent_id for entry in self.worker_roster}
        roots = tuple(self.safety_envelope.allowed_path_roots)
        mandatory = set(self.safety_envelope.test_commands)
        for task in plan.worker_tasks:
            if task.assignee_worker_agent_id not in roster:
                return (
                    f"worker task {task.node_id!r} is assigned to "
                    f"{task.assignee_worker_agent_id}, who is not on this team's roster"
                )
            for allowed in task.allowed_paths:
                if not _within_roots(allowed, roots):
                    return (
                        f"worker task {task.node_id!r} allows {allowed!r}, which is outside "
                        f"the safety envelope roots {', '.join(roots)}"
                    )
            if dropped := sorted(mandatory - set(task.tests)):
                return (
                    f"worker task {task.node_id!r} drops the envelope test command(s) "
                    f"{', '.join(dropped)}"
                )
        return None

    def refuse_review(self, review: "RepositoryReviewDecision") -> str | None:
        """Why this package would reject ``review``, or ``None`` if it would not.

        The relational half of frozen invariant 5: a finding must name a worker
        task this package's own evidence reports. A verdict about work the
        leader was never shown is not evidence-based, whatever it says.
        """

        if self.review_evidence is None:
            return "this package carries no review evidence, so no verdict can be based on it"
        known = {entry.worker_task_id for entry in self.review_evidence.worker_evidence}
        for finding in review.findings:
            if finding.worker_task_id not in known:
                return (
                    f"finding names worker task {finding.worker_task_id}, which is not in "
                    "this package's review evidence"
                )
        return None


def _built(constructor, **values):  # type: ignore[no-untyped-def]
    """Construct a record, reporting a refused construction as a bad document.

    The cross-field invariants live in ``__post_init__`` so that a record built
    in this process obeys them too, and they raise ``ValueError`` because that
    is what a bad *argument* is. Reached from a wire reader, though, the cause is
    a document that did not say what the freeze requires, and the caller's
    vocabulary for that is :class:`LeaderDocumentInvalid`. One translation, in
    one place, rather than each reader re-checking what the constructor already
    knows.
    """

    try:
        return constructor(**values)
    except ValueError as invalid:
        raise LeaderDocumentInvalid(str(invalid)) from invalid


@dataclass(frozen=True, slots=True)
class EngineeringSpec:
    """The leader's own Spec. The server persists it verbatim and never edits it."""

    summary: str
    markdown: str

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "EngineeringSpec":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=("summary", "markdown"),
            error=LeaderDocumentInvalid,
        )
        return cls(
            summary=_string(
                body,
                "summary",
                document=document,
                error=LeaderDocumentInvalid,
                maximum=_MAX_SPEC_SUMMARY,
            ),
            markdown=_string(
                body, "markdown", document=document, error=LeaderDocumentInvalid, maximum=_MAX_TEXT
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {"summary": self.summary, "markdown": self.markdown}


@dataclass(frozen=True, slots=True)
class TaskDagEdge:
    """``from_node`` executes before ``to_node``.

    Spelled with the suffix because ``from`` is a Python keyword and ``to``
    beside it would then read as a different kind of name. The wire keeps the
    contract's own ``from``/``to``; the rename stops at this class.
    """

    from_node: str
    to_node: str

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "TaskDagEdge":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(body, document=document, required=("from", "to"), error=LeaderDocumentInvalid)
        return cls(
            from_node=_string(
                body, "from", document=document, error=LeaderDocumentInvalid, maximum=_MAX_NODE_ID
            ),
            to_node=_string(
                body, "to", document=document, error=LeaderDocumentInvalid, maximum=_MAX_NODE_ID
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {"from": self.from_node, "to": self.to_node}


@dataclass(frozen=True, slots=True)
class TaskDag:
    """The leader's decomposition graph. One node per worker task, no cycles."""

    node_ids: tuple[str, ...]
    edges: tuple[TaskDagEdge, ...]

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "TaskDag":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(body, document=document, required=("nodes", "edges"), error=LeaderDocumentInvalid)
        nodes: list[str] = []
        for i, node in enumerate(
            _array(body, "nodes", document=document, error=LeaderDocumentInvalid, minimum_items=1)
        ):
            where = f"{document}.nodes[{i}]"
            entry = _mapping(node, document=where, error=LeaderDocumentInvalid)
            _fields(entry, document=where, required=("nodeId",), error=LeaderDocumentInvalid)
            nodes.append(
                _string(
                    entry,
                    "nodeId",
                    document=where,
                    error=LeaderDocumentInvalid,
                    maximum=_MAX_NODE_ID,
                )
            )
        return cls(
            node_ids=tuple(nodes),
            edges=tuple(
                TaskDagEdge.from_wire(edge, document=f"{document}.edges[{i}]")
                for i, edge in enumerate(
                    _array(body, "edges", document=document, error=LeaderDocumentInvalid)
                )
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "nodes": [{"nodeId": node_id} for node_id in self.node_ids],
            "edges": [edge.to_wire() for edge in self.edges],
        }

    def has_cycle(self) -> bool:
        """Kahn's algorithm: fewer nodes drained than declared means a cycle."""

        remaining = dict.fromkeys(self.node_ids, 0)
        dependants: dict[str, list[str]] = {node_id: [] for node_id in self.node_ids}
        for edge in self.edges:
            remaining[edge.to_node] += 1
            dependants[edge.from_node].append(edge.to_node)
        frontier = [node for node, degree in remaining.items() if degree == 0]
        drained = 0
        while frontier:
            node = frontier.pop()
            drained += 1
            for dependant in dependants[node]:
                remaining[dependant] -= 1
                if remaining[dependant] == 0:
                    frontier.append(dependant)
        return drained != len(self.node_ids)


@dataclass(frozen=True, slots=True)
class PlannedWorkerTask:
    """One unit of work the leader is handing to one worker."""

    node_id: str
    assignee_worker_agent_id: UUID
    title: str
    instruction: str
    allowed_paths: tuple[str, ...]
    tests: tuple[str, ...]
    database_change: dict[str, object] | None = None

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "PlannedWorkerTask":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=(
                "nodeId",
                "assigneeWorkerAgentId",
                "title",
                "instruction",
                "allowedPaths",
                "tests",
            ),
            optional=("databaseChange",),
            error=LeaderDocumentInvalid,
        )
        return cls(
            node_id=_string(
                body, "nodeId", document=document, error=LeaderDocumentInvalid,
                maximum=_MAX_NODE_ID,
            ),
            assignee_worker_agent_id=_uuid(
                body, "assigneeWorkerAgentId", document=document, error=LeaderDocumentInvalid
            ),
            title=_string(
                body, "title", document=document, error=LeaderDocumentInvalid, maximum=_MAX_TITLE
            ),
            instruction=_string(
                body,
                "instruction",
                document=document,
                error=LeaderDocumentInvalid,
                maximum=_MAX_TEXT,
            ),
            allowed_paths=_string_array(
                body,
                "allowedPaths",
                document=document,
                error=LeaderDocumentInvalid,
                minimum_items=1,
            ),
            tests=_string_array(
                body, "tests", document=document, error=LeaderDocumentInvalid
            ),
            database_change=(
                dict(
                    _mapping(
                        body["databaseChange"],
                        document=f"{document}.databaseChange",
                        error=LeaderDocumentInvalid,
                    )
                )
                if "databaseChange" in body
                else None
            ),
        )

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {
            "nodeId": self.node_id,
            "assigneeWorkerAgentId": str(self.assignee_worker_agent_id),
            "title": self.title,
            "instruction": self.instruction,
            "allowedPaths": list(self.allowed_paths),
            "tests": list(self.tests),
        }
        if self.database_change is not None:
            wire["databaseChange"] = self.database_change
        return wire


@dataclass(frozen=True, slots=True)
class DecisionProvenance:
    """Where a decision came from: the leader's own session, named.

    ``source`` is a ``const`` on the wire and is a defaulted field here rather
    than a bare constant, because a *reader* has to be able to refuse a document
    that carries something else. It is the one claim in either decision that a
    server-authored plan could not make honestly.
    """

    session_thread_id: str
    turn_id: str | None = None
    source: str = DECISION_PROVENANCE_SOURCE

    def __post_init__(self) -> None:
        if self.source != DECISION_PROVENANCE_SOURCE:
            raise ValueError(f"provenance.source must be {DECISION_PROVENANCE_SOURCE!r}")

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "DecisionProvenance":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=("source", "sessionThreadId"),
            optional=("turnId",),
            error=LeaderDocumentInvalid,
        )
        _const(
            body,
            "source",
            DECISION_PROVENANCE_SOURCE,
            document=document,
            error=LeaderDocumentInvalid,
        )
        return cls(
            session_thread_id=_string(
                body,
                "sessionThreadId",
                document=document,
                error=LeaderDocumentInvalid,
                maximum=_MAX_THREAD_ID,
            ),
            turn_id=_optional_string(
                body,
                "turnId",
                document=document,
                error=LeaderDocumentInvalid,
                maximum=_MAX_THREAD_ID,
            ),
        )

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {
            "source": DECISION_PROVENANCE_SOURCE,
            "sessionThreadId": self.session_thread_id,
        }
        if self.turn_id is not None:
            wire["turnId"] = self.turn_id
        return wire


@dataclass(frozen=True, slots=True)
class RepositoryPlanDecision:
    """The leader's product: Spec, DAG and worker tasks, with provenance.

    The leader task id lives only in the URL path — it is the idempotency key —
    so this document deliberately does not repeat it.
    """

    engineering_spec: EngineeringSpec
    task_dag: TaskDag
    worker_tasks: tuple[PlannedWorkerTask, ...]
    provenance: DecisionProvenance

    def __post_init__(self) -> None:
        """Frozen invariant 3, checked where it is decidable: inside the plan.

        Node/task correspondence, edge endpoints and acyclicity need nothing but
        this document, so the Bridge settles them before posting rather than
        letting the leader learn of a cycle from a 409. The envelope clamp is
        the other half and needs the assignment package, which is why it lives
        on :meth:`RepositoryAssignmentPackage.refuse_plan` instead.
        """

        node_ids = list(self.task_dag.node_ids)
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("taskDag declares the same nodeId twice")
        task_nodes = [task.node_id for task in self.worker_tasks]
        if sorted(task_nodes) != sorted(node_ids):
            raise ValueError(
                "taskDag nodes and workerTasks must correspond one-to-one by nodeId"
            )
        declared = set(node_ids)
        for edge in self.task_dag.edges:
            if edge.from_node not in declared or edge.to_node not in declared:
                raise ValueError(
                    f"taskDag edge {edge.from_node!r} -> {edge.to_node!r} names an "
                    "undeclared node"
                )
        if self.task_dag.has_cycle():
            raise ValueError("taskDag is not acyclic")

    @classmethod
    def from_wire(cls, payload: object) -> "RepositoryPlanDecision":
        document = "planDecision"
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=(
                "schemaVersion",
                "engineeringSpec",
                "taskDag",
                "workerTasks",
                "provenance",
            ),
            error=LeaderDocumentInvalid,
        )
        _const(
            body,
            "schemaVersion",
            PLAN_DECISION_SCHEMA_VERSION,
            document=document,
            error=LeaderDocumentInvalid,
        )
        return _built(
            cls,
            engineering_spec=EngineeringSpec.from_wire(
                body["engineeringSpec"], document=f"{document}.engineeringSpec"
            ),
            task_dag=TaskDag.from_wire(body["taskDag"], document=f"{document}.taskDag"),
            worker_tasks=tuple(
                PlannedWorkerTask.from_wire(task, document=f"{document}.workerTasks[{i}]")
                for i, task in enumerate(
                    _array(
                        body,
                        "workerTasks",
                        document=document,
                        error=LeaderDocumentInvalid,
                        minimum_items=1,
                    )
                )
            ),
            provenance=DecisionProvenance.from_wire(
                body["provenance"], document=f"{document}.provenance"
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "schemaVersion": PLAN_DECISION_SCHEMA_VERSION,
            "engineeringSpec": self.engineering_spec.to_wire(),
            "taskDag": self.task_dag.to_wire(),
            "workerTasks": [task.to_wire() for task in self.worker_tasks],
            "provenance": self.provenance.to_wire(),
        }


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    """One thing the leader found. ``rework_instruction`` makes it a demand."""

    worker_task_id: UUID
    note: str
    rework_instruction: str | None = None

    @classmethod
    def from_wire(cls, payload: object, *, document: str) -> "ReviewFinding":
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=("workerTaskId", "note"),
            optional=("reworkInstruction",),
            error=LeaderDocumentInvalid,
        )
        return cls(
            worker_task_id=_uuid(
                body, "workerTaskId", document=document, error=LeaderDocumentInvalid
            ),
            note=_string(
                body, "note", document=document, error=LeaderDocumentInvalid, maximum=_MAX_NOTE
            ),
            rework_instruction=_optional_string(
                body,
                "reworkInstruction",
                document=document,
                error=LeaderDocumentInvalid,
                maximum=_MAX_TEXT,
            ),
        )

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {
            "workerTaskId": str(self.worker_task_id),
            "note": self.note,
        }
        if self.rework_instruction is not None:
            wire["reworkInstruction"] = self.rework_instruction
        return wire


@dataclass(frozen=True, slots=True)
class RepositoryReviewDecision:
    """The leader's evidence-based verdict over one review round."""

    verdict: str
    summary: str
    findings: tuple[ReviewFinding, ...]
    provenance: DecisionProvenance

    def __post_init__(self) -> None:
        """Frozen invariant 5's intrinsic half: rework has to say what to redo.

        Each ``reworkInstruction`` becomes a new revision worker task, so a
        ``request_rework`` with none would ask the server to create nothing and
        leave the round waiting on work nobody was told to do.
        """

        if self.verdict not in REVIEW_VERDICTS:
            raise ValueError(f"verdict must be one of {', '.join(REVIEW_VERDICTS)}")
        if self.verdict == "request_rework" and not any(
            finding.rework_instruction for finding in self.findings
        ):
            raise ValueError(
                "a request_rework verdict needs at least one finding with a reworkInstruction"
            )

    @classmethod
    def from_wire(cls, payload: object) -> "RepositoryReviewDecision":
        document = "reviewDecision"
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=("schemaVersion", "verdict", "summary", "findings", "provenance"),
            error=LeaderDocumentInvalid,
        )
        _const(
            body,
            "schemaVersion",
            REVIEW_DECISION_SCHEMA_VERSION,
            document=document,
            error=LeaderDocumentInvalid,
        )
        return _built(
            cls,
            verdict=_enum(
                body, "verdict", REVIEW_VERDICTS, document=document, error=LeaderDocumentInvalid
            ),
            summary=_string(
                body,
                "summary",
                document=document,
                error=LeaderDocumentInvalid,
                maximum=_MAX_SUMMARY,
            ),
            findings=tuple(
                ReviewFinding.from_wire(finding, document=f"{document}.findings[{i}]")
                for i, finding in enumerate(
                    _array(body, "findings", document=document, error=LeaderDocumentInvalid)
                )
            ),
            provenance=DecisionProvenance.from_wire(
                body["provenance"], document=f"{document}.provenance"
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "schemaVersion": REVIEW_DECISION_SCHEMA_VERSION,
            "verdict": self.verdict,
            "summary": self.summary,
            "findings": [finding.to_wire() for finding in self.findings],
            "provenance": self.provenance.to_wire(),
        }


@dataclass(frozen=True, slots=True)
class PlanReceipt:
    """What the server hands back for an accepted plan, and for every replay.

    Identical resubmission returns this same document: same ``worker_task_ids``,
    same ``plan_revision``. That is the whole idempotency contract, and it is why
    the adapter that posts a plan never retries — a replay is safe because the
    *server* makes it so, not because the client counted attempts.
    """

    leader_task_id: UUID
    plan_revision: int
    worker_task_ids: tuple[UUID, ...]

    @classmethod
    def from_wire(cls, payload: object) -> "PlanReceipt":
        document = "planReceipt"
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=("schemaVersion", "leaderTaskId", "planRevision", "workerTaskIds"),
            error=LeaderDocumentInvalid,
        )
        _const(
            body,
            "schemaVersion",
            PLAN_RECEIPT_SCHEMA_VERSION,
            document=document,
            error=LeaderDocumentInvalid,
        )
        return cls(
            leader_task_id=_uuid(
                body, "leaderTaskId", document=document, error=LeaderDocumentInvalid
            ),
            plan_revision=_integer(
                body, "planRevision", document=document, error=LeaderDocumentInvalid, minimum=1
            ),
            worker_task_ids=_uuid_array(
                body,
                "workerTaskIds",
                document=document,
                error=LeaderDocumentInvalid,
                minimum_items=1,
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "schemaVersion": PLAN_RECEIPT_SCHEMA_VERSION,
            "leaderTaskId": str(self.leader_task_id),
            "planRevision": self.plan_revision,
            "workerTaskIds": [str(task_id) for task_id in self.worker_task_ids],
        }


@dataclass(frozen=True, slots=True)
class ReviewReceipt:
    """What the server hands back for an accepted verdict.

    The verdict-to-outcome mapping is frozen: approve makes the leader task
    succeeded with no rework tasks, request_rework leaves it in progress with
    the new revision tasks named, escalate blocks it with no rework tasks.
    """

    leader_task_id: UUID
    verdict: str
    review_revision: int
    leader_task_status: str
    rework_task_ids: tuple[UUID, ...]

    @classmethod
    def from_wire(cls, payload: object) -> "ReviewReceipt":
        document = "reviewReceipt"
        body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
        _fields(
            body,
            document=document,
            required=(
                "schemaVersion",
                "leaderTaskId",
                "verdict",
                "reviewRevision",
                "leaderTaskStatus",
                "reworkTaskIds",
            ),
            error=LeaderDocumentInvalid,
        )
        _const(
            body,
            "schemaVersion",
            REVIEW_RECEIPT_SCHEMA_VERSION,
            document=document,
            error=LeaderDocumentInvalid,
        )
        return cls(
            leader_task_id=_uuid(
                body, "leaderTaskId", document=document, error=LeaderDocumentInvalid
            ),
            verdict=_enum(
                body, "verdict", REVIEW_VERDICTS, document=document, error=LeaderDocumentInvalid
            ),
            review_revision=_integer(
                body, "reviewRevision", document=document, error=LeaderDocumentInvalid, minimum=1
            ),
            leader_task_status=_enum(
                body,
                "leaderTaskStatus",
                LEADER_TASK_STATUSES,
                document=document,
                error=LeaderDocumentInvalid,
            ),
            rework_task_ids=_uuid_array(
                body, "reworkTaskIds", document=document, error=LeaderDocumentInvalid
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "schemaVersion": REVIEW_RECEIPT_SCHEMA_VERSION,
            "leaderTaskId": str(self.leader_task_id),
            "verdict": self.verdict,
            "reviewRevision": self.review_revision,
            "leaderTaskStatus": self.leader_task_status,
            "reworkTaskIds": [str(task_id) for task_id in self.rework_task_ids],
        }


def _uuid_array(
    payload: Mapping[str, object],
    key: str,
    *,
    document: str,
    error: WireError,
    minimum_items: int = 0,
) -> tuple[UUID, ...]:
    items = _array(payload, key, document=document, error=error, minimum_items=minimum_items)
    if len(set(map(str, items))) != len(items):
        raise error(f"{document}.{key} must be unique")
    parsed: list[UUID] = []
    for item in items:
        if not isinstance(item, str):
            raise error(f"{document}.{key} holds something that is not a uuid")
        try:
            parsed.append(UUID(item))
        except ValueError as invalid:
            raise error(f"{document}.{key} holds something that is not a uuid") from invalid
    return tuple(parsed)


def read_leader_action_error(payload: object) -> tuple[str, str]:
    """The ``(code, message)`` inside a structured error body.

    An unrecognised code is returned as itself rather than refused. The Bridge
    is the consumer here: its job is to carry the server's own word for a
    refusal to whoever has to act on it, and a build that raised on a code the
    freeze grew after it shipped would fail hardest exactly when the two sides
    had drifted — which is when the message matters most.
    """

    document = "structuredError"
    body = _mapping(payload, document=document, error=LeaderDocumentInvalid)
    _fields(body, document=document, required=("detail",), error=LeaderDocumentInvalid)
    detail = _mapping(body["detail"], document=f"{document}.detail", error=LeaderDocumentInvalid)
    _fields(
        detail,
        document=f"{document}.detail",
        required=("code", "message"),
        error=LeaderDocumentInvalid,
    )
    return (
        _string(
            detail,
            "code",
            document=f"{document}.detail",
            error=LeaderDocumentInvalid,
            maximum=_MAX_NAME,
        ),
        _string(
            detail,
            "message",
            document=f"{document}.detail",
            error=LeaderDocumentInvalid,
            maximum=_MAX_TEXT,
        ),
    )


def leader_action_error_wire(code: str, message: str) -> dict[str, object]:
    """The structured error body for ``code``. The inverse of the reader above."""

    return {"detail": {"code": code, "message": message}}


def _within_roots(path: str, roots: tuple[str, ...]) -> bool:
    """Is this path under one of the envelope's roots?

    The same rule the server applies (``task_orchestration.application
    ._within_roots``): roots are the glob-ish strings ``derive_allowed_paths``
    produces, so a trailing ``**`` / ``*`` is stripped before a plain prefix
    comparison, and a bare ``**`` root admits everything. A pre-check that
    compared the raw glob text refused every concrete path under ``**`` and
    sent the leader a sentence the server would never have said.
    """

    candidate = path.strip().lstrip("./")
    for root in roots:
        normalised = root.strip().lstrip("./").removesuffix("**").removesuffix("*")
        if not normalised or candidate.startswith(normalised):
            return True
    return False
