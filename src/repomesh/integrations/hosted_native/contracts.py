"""The seams of the hosted-native round (spec §4.2 M1/M2, D-6, D-8, D-9, D-12).

Everything two or more of ``round``, ``observer``, ``approval`` and ``store``
agree on lives here so that each can be built and tested against the others'
fakes: the attempt record and its phases, the event inbox row, the store port,
the shared-directory reader port (the one external seam this spec opens), and
the two ports the round needs from work that lands later (the base-bundle
builder M6 and the candidate verification launcher M5 + dispatch).

Vocabulary: one **attempt** is one copaw-native task directory whose name is the
attempt id (D-8); the attempt's **generation** is the task assignment generation
it was opened under (D-9); an attempt is **fenced** when anything it still
writes must be ignored (budget expired, worker restarted, generation advanced,
Leader asked for a revision).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from repomesh.modules.task_orchestration.contracts import PathPolicy


class AttemptPhase(StrEnum):
    """Where one attempt stands. The observer only reads directories of attempts
    in a non-terminal phase; a terminal attempt's directory is dead to the platform."""

    NOTIFIED = "notified"
    ACKNOWLEDGED = "acknowledged"
    REVIEW_PENDING = "review_pending"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    FAILED = "failed"
    BLOCKED = "blocked"
    FENCED = "fenced"


TERMINAL_PHASES: frozenset[AttemptPhase] = frozenset(
    {AttemptPhase.VERIFIED, AttemptPhase.FAILED, AttemptPhase.BLOCKED, AttemptPhase.FENCED}
)
"""Phases an attempt never leaves."""

WORKER_SIDE_PHASES: frozenset[AttemptPhase] = frozenset(
    {AttemptPhase.NOTIFIED, AttemptPhase.ACKNOWLEDGED}
)
"""Phases in which the worker holds the attempt: the attempt budget (D-12 ①) applies
here and only here. ``REVIEW_PENDING`` has its own budget (D-13); ``VERIFYING`` is
bounded by the verifier dispatch's own lease."""

#: SQL literal for the partial unique index "one non-terminal attempt per task"
#: (spec §5.3.1). Spelled once so the model, the migration and the in-memory
#: store agree on what "open" means.
OPEN_PHASES_SQL = "phase IN ('notified', 'acknowledged', 'review_pending', 'verifying')"


class SubmitStatus(StrEnum):
    """copaw's ``submit_task`` statuses as they appear in ``result.md``."""

    SUCCESS = "SUCCESS"
    SUCCESS_WITH_NOTES = "SUCCESS_WITH_NOTES"
    BLOCKED = "BLOCKED"
    REVISION_NEEDED = "REVISION_NEEDED"


class ReviewVerdict(StrEnum):
    """The Leader's answer, mapped from its ``submit_task`` status (contracts v2 ``review.md``)."""

    ACCEPT = "ACCEPT"
    REVISION = "REVISION"
    BLOCKED = "BLOCKED"


def verdict_for(status: SubmitStatus) -> ReviewVerdict:
    """The fixed status→verdict mapping of ``review.md``; the status wins over the text."""

    if status in (SubmitStatus.SUCCESS, SubmitStatus.SUCCESS_WITH_NOTES):
        return ReviewVerdict.ACCEPT
    if status is SubmitStatus.REVISION_NEEDED:
        return ReviewVerdict.REVISION
    return ReviewVerdict.BLOCKED


@dataclass(frozen=True, slots=True)
class HostedNativeAttempt:
    """One row of ``agent_runtime.hosted_native_attempts``.

    ``id`` is the attempt id and the shared task directory's name.
    ``package_dir`` is the object prefix the publisher wrote
    (``teams/<team>/shared/tasks/<id>``); ``review_dir`` the review package's,
    once one exists. ``base_sha`` is the commit the base bundle was pinned at;
    the candidate's parent must be it and the verifier re-checks that (D-11).
    ``room_id`` is the team room the notice went to — the auto-approval matches
    Tool Guard requests by room and sender. Fencing data
    (``assignment_attempt_id``, ``generation``, ``execution_id``) is copied from
    the assignment and reservation the attempt was opened under (D-9).
    """

    id: UUID
    task_id: UUID
    worker_agent_id: UUID
    leader_agent_id: UUID
    team_name: str
    room_id: str
    assignment_attempt_id: UUID
    generation: int
    execution_id: UUID
    phase: AttemptPhase
    package_dir: str
    base_sha: str
    budget_until: datetime
    notified_at: datetime
    created_at: datetime
    updated_at: datetime
    review_dir: str | None = None
    review_budget_until: datetime | None = None
    acknowledged_at: datetime | None = None
    submitted_at: datetime | None = None
    submit_status: SubmitStatus | None = None
    review_verdict: ReviewVerdict | None = None
    verification_run_id: UUID | None = None
    fenced_at: datetime | None = None
    fence_reason: str | None = None

    @property
    def is_open(self) -> bool:
        return self.phase not in TERMINAL_PHASES

    def with_phase(
        self, phase: AttemptPhase, *, at: datetime, **changes: object
    ) -> HostedNativeAttempt:
        """A copy in ``phase`` with ``updated_at`` stamped; ``changes`` are other fields."""

        return replace(self, phase=phase, updated_at=at, **changes)  # type: ignore[arg-type]


class EventKind(StrEnum):
    """What the observer (or the approval branch) saw. Together with ``marker`` it
    is the idempotency key of ``hosted_native_events`` (``UNIQUE(attempt_id, kind, marker)``)."""

    ACKNOWLEDGED = "acknowledged"
    SUBMITTED = "submitted"
    REVIEW_SUBMITTED = "review_submitted"
    AUTO_APPROVED = "auto_approved"
    FENCED = "fenced"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class HostedNativeEvent:
    """One row of ``agent_runtime.hosted_native_events``.

    ``marker`` is what makes the observation unique for its kind: the
    ``acknowledged_at`` / ``submitted_at`` copaw wrote into ``meta.json``, or
    the Matrix event id of a Tool Guard request. ``applied_at`` is set once the
    round (or the approval sender) has acted on it.
    """

    id: UUID
    attempt_id: UUID
    kind: EventKind
    marker: str
    payload: Mapping[str, object]
    observed_at: datetime
    applied_at: datetime | None = None


class HostedNativeConflict(RuntimeError):
    """The store refused a write: a second open attempt for a task, or a save
    against an attempt that no longer exists."""


class HostedNativeAttemptStore(Protocol):
    """Postgres and in-memory implementations in ``store.py``."""

    async def add(self, attempt: HostedNativeAttempt) -> None:
        """Insert. Raises ``HostedNativeConflict`` when the task already has an open attempt."""
        ...

    async def get(self, attempt_id: UUID) -> HostedNativeAttempt | None: ...

    async def get_open_for_task(self, task_id: UUID) -> HostedNativeAttempt | None: ...

    async def list_open(self) -> tuple[HostedNativeAttempt, ...]:
        """Every non-terminal attempt, oldest ``notified_at`` first."""
        ...

    async def list_for_task(self, task_id: UUID) -> tuple[HostedNativeAttempt, ...]:
        """Every attempt of a task, by generation then ``created_at``."""
        ...

    async def save(self, attempt: HostedNativeAttempt) -> None:
        """Full-row update by id. Raises ``HostedNativeConflict`` when the row is missing."""
        ...

    async def record_event(self, event: HostedNativeEvent) -> bool:
        """Insert; ``False`` (and nothing written) when ``(attempt_id, kind, marker)`` exists."""
        ...

    async def find_event(
        self, attempt_id: UUID, kind: EventKind, marker: str
    ) -> HostedNativeEvent | None: ...

    async def mark_applied(self, event_id: UUID, *, applied_at: datetime) -> None: ...

    async def list_events(self, attempt_id: UUID) -> tuple[HostedNativeEvent, ...]:
        """Chronological by ``observed_at`` then insertion."""
        ...


@dataclass(frozen=True, slots=True)
class ObjectStat:
    size: int
    etag: str | None
    last_modified: datetime | None


class SharedTaskDirectoryReader(Protocol):
    """Read one file of one task directory under a team's shared storage.

    The only new external seam this spec opens (§4.2 M2). ``task_dir`` is the
    directory *name* (the attempt id), never a path; ``name`` is the file's
    path inside it (``meta.json``, ``result.md``, ``base/package.json``,
    ``candidate/evidence.json``). Implementations: MinIO (same client and
    bucket as the object publisher), disk (``agentteams_storage_root``) and an
    in-memory dict for tests, all in ``storage.py``.
    """

    async def read(self, team_name: str, task_dir: str, name: str) -> bytes | None: ...

    async def stat(self, team_name: str, task_dir: str, name: str) -> ObjectStat | None: ...


@dataclass(frozen=True, slots=True)
class SubmittedResult:
    """``result.md`` as copaw's ``submit_task`` wrote it (``copaw_worker/task.py``
    ``parse_task_result``): ``STATUS:``, ``SUMMARY:``, then ``DELIVERABLES:`` and
    ``NOTES:`` bullet lists."""

    status: SubmitStatus
    summary: str
    deliverables: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SharedTaskEvent:
    """What the observer hands the round after the event inbox accepted it.

    ``kind`` is one of ``ACKNOWLEDGED``, ``SUBMITTED`` (the worker's
    ``result.md``) or ``REVIEW_SUBMITTED`` (the Leader's). ``result`` is set for
    the two submitted kinds. ``observed_at`` is the copaw timestamp the marker
    came from, when it parsed, else the observation time.
    """

    attempt_id: UUID
    kind: EventKind
    marker: str
    observed_at: datetime
    result: SubmittedResult | None = None
    payload: Mapping[str, object] = field(default_factory=dict)


class RoundOutcome(StrEnum):
    APPLIED = "applied"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class RoundTransition:
    """What ``observe`` / ``expire`` did. ``IGNORED`` never moved the task (D-9):
    ``reason`` says why (``fenced_generation``, ``attempt_terminal``,
    ``unknown_attempt``, ``phase_mismatch`` …). ``next_attempt_id`` is set when
    the transition opened a successor attempt (a Leader ``REVISION``); ``phase``
    is then the successor's."""

    attempt_id: UUID
    outcome: RoundOutcome
    phase: AttemptPhase | None
    reason: str | None = None
    next_attempt_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RoundOpened:
    """``created`` is ``False`` on an idempotent replay: the task already had an
    open attempt for the current generation and nothing was published or sent."""

    attempt: HostedNativeAttempt
    created: bool


@dataclass(frozen=True, slots=True)
class BaseBundle:
    """What the worker's ``init`` clones from: a ``git bundle`` pinned at
    ``base_sha`` carrying ``HEAD`` and the branch ref (spike S-10)."""

    base_sha: str
    bundle: bytes


class BaseBundleSource(Protocol):
    """M6 ``BaseBundleBuilder`` (git over the mirror repository) lands with T5;
    until then the round is tested against an in-memory source."""

    async def build(self, repository_id: UUID) -> BaseBundle: ...


@dataclass(frozen=True, slots=True)
class ConstructionPolicy:
    """The path policy and frozen test commands one attempt is told (D-14).

    Same sources the runner projection uses: the Specification's coding package
    for ``allowed_paths`` / ``test_commands`` / ``forbidden_paths``, the catalog's
    ``test_paths`` unioned into the allowed set and its ``test_commands`` as the
    fallback (``integrations/runner/task_projection.py``).
    """

    policy: PathPolicy
    test_commands: tuple[str, ...]


class ConstructionPolicySource(Protocol):
    async def resolve(self, task_id: UUID, *, worker_agent_id: UUID) -> ConstructionPolicy: ...


@dataclass(frozen=True, slots=True)
class CandidateForVerification:
    """The accepted candidate, ready for M5 (worktree) and the verifier dispatch (D-10, D-11)."""

    attempt_id: UUID
    task_id: UUID
    repository_id: UUID
    base_sha: str
    head_sha: str
    candidate_bundle: bytes
    changes_json: str
    evidence_json: str
    policy: PathPolicy
    test_commands: tuple[str, ...]


class CandidateVerificationLauncher(Protocol):
    """Materialise the candidate and enqueue the ``repomesh-verifier`` dispatch,
    returning its run id. The real adapter (M5 + ``RunnerControlGateway.enqueue``)
    lands with T5; the round records the run id and moves to ``VERIFYING``."""

    async def launch(
        self, candidate: CandidateForVerification, *, attempt: HostedNativeAttempt
    ) -> UUID: ...


class MatrixSenderResolver(Protocol):
    """Matrix user id -> the principal behind it (``AgentTeamsMatrixIdentityResolver``)."""

    async def resolve(self, matrix_user_id: str) -> UUID | None: ...


class ApprovalSender(Protocol):
    """Send the one shape copaw accepts (spec §8.10): body exactly ``/approve``,
    ``m.mentions.user_ids`` naming the worker. Returns the Matrix event id. The
    Matrix adapter's ``send_approval`` implements it."""

    async def send_approval(
        self, room_id: str, worker_matrix_user_id: str, *, transaction_id: str
    ) -> str: ...


def utcnow() -> datetime:
    return datetime.now(UTC)
