"""In-memory and inert sides of the seams.

They ship with the package rather than living in the test tree for two reasons.
The merge gate asks every new HTTP adapter to have an in-memory counterpart, and
one of these is not a test double at all: ``InertCodingSession`` is what the
``run`` subcommand genuinely assembles in this tier, because a real coding CLI
behind a restricted process factory does not arrive until PR 4. Until then the
honest production stand-in is something that hears the room, says so, and codes
nothing.

None of these keeps durable state. The Bridge's local state — cursor, seen set,
turn ledger, outbox, session references — is SQLite, which is its own test
stand-in and never a port.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ..contracts import (
    ExternalWorkerEnrollment,
    PlanReceipt,
    RepositoryAssignmentPackage,
    RepositoryPlanDecision,
    RepositoryReviewDecision,
    ReviewEvidence,
    ReviewReceipt,
    RoomObservation,
    WorkerBinding,
)
from ..ports import (
    GovernedStartReceipt,
    LeaderActionRefused,
    RoomBatch,
    RoomBody,
    TurnOutcome,
    TurnRequest,
)

__all__ = [
    "GovernedStartCall",
    "InertCodingSession",
    "InMemoryGovernedTaskPort",
    "InMemoryLeaderActionPort",
    "InMemoryRoomPort",
    "InMemoryWorkerBindingPort",
    "LeaderActionCall",
    "MemoryReadinessReporter",
    "ScriptedCodingSession",
    "ScriptedLeaderSession",
    "SentMessage",
]

INERT_SESSION_NOTE = (
    "I am in this room and I can hear you, but this build cannot run a coding "
    "session yet."
)
"""What the inert session answers with, every time.

Deliberately a whole sentence rather than a status word: the person who @-ed a
worker deserves to learn from the room itself that the answer is a limitation of
this build and not a failure they should retry.
"""


class InMemoryWorkerBindingPort:
    """A control plane that answers from memory.

    Records what it was asked and how often, so a caller can assert the thing
    that matters most about stage 1: that it did not get here at all.
    """

    def __init__(
        self,
        binding: WorkerBinding | None = None,
        *,
        failure: Exception | None = None,
        requires_credential: bool = False,
    ) -> None:
        if binding is None and failure is None:
            raise ValueError("give the port either a binding to answer or a failure to raise")
        self.requires_credential = requires_credential
        self._binding = binding
        self._failure = failure
        self.calls = 0
        self.credentials: list[str | None] = []

    async def fetch_binding(
        self, enrollment: ExternalWorkerEnrollment, *, credential: str | None
    ) -> WorkerBinding:
        self.calls += 1
        self.credentials.append(credential)
        if self._failure is not None:
            raise self._failure
        assert self._binding is not None
        return self._binding


@dataclass(frozen=True, slots=True)
class GovernedStartCall:
    """One request to start a governed run, exactly as the Bridge made it."""

    task_id: UUID
    worker_agent_id: UUID


class InMemoryGovernedTaskPort:
    """A control plane whose answers to the start action a caller writes in advance.

    Answers are consumed in order and an exception in the script is raised
    instead of returned, so "RepoMesh said this worker is not the assignee" is
    one entry in a list rather than a subclass.

    Once the script is spent the last answer repeats, which is not a convenience:
    it is what RepoMesh does. A second start for a task whose run has not
    finished returns that run's own receipt rather than dispatching again, so a
    double that answered differently the second time would make a replay look
    recoverable when it is not — or unrecoverable when it is.

    ``calls`` is the record that matters most. "The port was never touched" and
    "the port was asked exactly once, with the id from the message" are the two
    claims the wake-up half rests on, and both are read off that list.
    """

    def __init__(self, *answers: GovernedStartReceipt | BaseException) -> None:
        if not answers:
            raise ValueError("give the port at least one receipt to answer or failure to raise")
        self._answers: list[GovernedStartReceipt | BaseException] = list(answers)
        self.calls: list[GovernedStartCall] = []

    async def start_task(
        self, *, task_id: UUID, worker_agent_id: UUID
    ) -> GovernedStartReceipt:
        self.calls.append(GovernedStartCall(task_id=task_id, worker_agent_id=worker_agent_id))
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer


@dataclass(frozen=True, slots=True)
class LeaderActionCall:
    """One leader action, exactly as the Bridge made it."""

    action: str
    """``fetch`` | ``plan`` | ``review``."""
    task_id: UUID


class InMemoryLeaderActionPort:
    """The leader decision surface, with its phase machine actually implemented.

    The second implementation of :class:`~repomesh_agent_bridge.ports.LeaderActionPort`,
    and the one that makes it a seam rather than an interface with a single
    caller. It is not a stub that echoes canned answers: the phases really
    advance, the envelope clamp really rejects, the receipts really repeat, and
    the review revision really increments — because every one of those is a
    claim the leader lane rests on, and a double that faked them would let the
    lane pass while depending on behaviour no server has.

    What it deliberately does *not* decide is anything only a real deployment
    knows: an invalid token, a caller who is not the assignee, a team whose
    planning happens server-side. Those arrive through :attr:`refusals`, one per
    call, so a test that wants a particular frozen code says so in one line
    instead of contorting the world into producing it.

    ``worker task ids`` come from the review evidence when a caller supplies
    some, so the fake reproduces the frozen fixture scenario exactly: the ids
    the plan receipt names are the ids the evidence later reports on, which is
    the cross-document identity the contract test pins.
    """

    def __init__(
        self,
        package: RepositoryAssignmentPackage,
        *,
        review_evidence: ReviewEvidence | None = None,
    ) -> None:
        if package.phase != "planning":
            raise ValueError("the fake starts a round at the beginning: give it a planning package")
        self._planning = package
        self._evidence = review_evidence
        self._package = package
        self._plan: RepositoryPlanDecision | None = None
        self._plan_receipt: PlanReceipt | None = None
        self._review: RepositoryReviewDecision | None = None
        self._review_receipt: ReviewReceipt | None = None
        self.refusals: list[BaseException] = []
        """Raised one per call, before anything else. Empty means "behave"."""
        self.calls: list[LeaderActionCall] = []

    # -- the port ----------------------------------------------------------

    async def fetch_assignment(self, task_id: UUID) -> RepositoryAssignmentPackage:
        self.calls.append(LeaderActionCall(action="fetch", task_id=task_id))
        self._scripted()
        self._known(task_id)
        return self._package

    async def submit_plan(
        self, task_id: UUID, decision: RepositoryPlanDecision
    ) -> PlanReceipt:
        self.calls.append(LeaderActionCall(action="plan", task_id=task_id))
        self._scripted()
        self._known(task_id)
        if self._plan_receipt is not None:
            # Frozen invariant 2: the same plan replays onto the same receipt;
            # a different one under the same key is refused, never substituted.
            if decision == self._plan:
                return self._plan_receipt
            raise LeaderActionRefused(
                "this leader task already has an accepted plan", code="phase_conflict"
            )
        if self._package.phase != "planning":
            raise LeaderActionRefused(
                f"a plan may only be submitted while planning, not while {self._package.phase}",
                code="phase_conflict",
            )
        if reason := self._package.refuse_plan(decision):
            raise LeaderActionRefused(reason, code=_plan_refusal_code(reason))
        worker_task_ids = (
            tuple(entry.worker_task_id for entry in self._evidence.worker_evidence)
            if self._evidence is not None
            else tuple(uuid4() for _ in decision.worker_tasks)
        )
        self._plan = decision
        self._plan_receipt = PlanReceipt(
            leader_task_id=self._package.leader_task_id,
            plan_revision=1,
            worker_task_ids=worker_task_ids,
        )
        self._package = replace(self._package, phase="executing")
        return self._plan_receipt

    async def submit_review(
        self, task_id: UUID, decision: RepositoryReviewDecision
    ) -> ReviewReceipt:
        self.calls.append(LeaderActionCall(action="review", task_id=task_id))
        self._scripted()
        self._known(task_id)
        if self._review_receipt is not None:
            if decision == self._review:
                return self._review_receipt
            raise LeaderActionRefused(
                "this review round already has a verdict", code="phase_conflict"
            )
        if self._package.phase != "review_due":
            raise LeaderActionRefused(
                f"a verdict may only be submitted while review_due, not while "
                f"{self._package.phase}",
                code="phase_conflict",
            )
        if reason := self._package.refuse_review(decision):
            raise LeaderActionRefused(reason, code="review_invalid_findings")
        assert self._package.review_evidence is not None  # noqa: S101 - the phase guarantees it
        revision = self._package.review_evidence.review_revision
        rework = (
            tuple(uuid4() for finding in decision.findings if finding.rework_instruction)
            if decision.verdict == "request_rework"
            else ()
        )
        self._review = decision
        self._review_receipt = ReviewReceipt(
            leader_task_id=self._package.leader_task_id,
            verdict=decision.verdict,
            review_revision=revision,
            leader_task_status={
                "approve": "succeeded",
                "request_rework": "in_progress",
                "escalate": "blocked",
            }[decision.verdict],
            rework_task_ids=rework,
        )
        # Rework sends the round back to ``executing`` with the new revision
        # tasks in flight, and frozen invariant 1 says an executing package
        # carries no evidence: the round it belonged to is over, and the next
        # verdict must be based on the next round's evidence, not this one's.
        self._package = (
            replace(self._package, phase="executing", review_evidence=None)
            if decision.verdict == "request_rework"
            else replace(self._package, phase="closed")
        )
        return self._review_receipt

    # -- what a real deployment's worker tasks would do ---------------------

    def worker_tasks_finished(self, evidence: ReviewEvidence | None = None) -> None:
        """Every worker task has reached a terminal status; review is now due.

        The one thing this fake cannot derive: worker tasks execute somewhere
        else entirely, and the round moves when they finish. A test says so
        explicitly rather than the double guessing, which also makes "the leader
        did not review before the evidence existed" an observable claim.
        """

        carried = evidence or self._evidence
        if carried is None:
            raise ValueError("review_due needs evidence: give the fake some, here or at build time")
        self._evidence = carried
        self._package = replace(self._package, phase="review_due", review_evidence=carried)
        self._review = None
        self._review_receipt = None

    @property
    def phase(self) -> str:
        """Where the round is now. Read by tests, never by the lane under test."""

        return self._package.phase

    def _known(self, task_id: UUID) -> None:
        if task_id != self._planning.leader_task_id:
            raise LeaderActionRefused(
                "no assignment for that leader task", code="assignment_not_found"
            )

    def _scripted(self) -> None:
        if self.refusals:
            raise self.refusals.pop(0)


def _plan_refusal_code(reason: str) -> str:
    """Which frozen code the envelope clamp's own sentence corresponds to.

    The clamp check returns prose because its first reader is the leader's own
    session, which needs a sentence rather than an enum. A server refusing the
    same plan would name a code, so the fake names the same one — otherwise a
    lane that branched on the code would pass here and fail against production.
    """

    if "roster" in reason:
        return "plan_invalid_assignee"
    if "outside the safety envelope" in reason:
        return "plan_invalid_allowed_paths"
    return "plan_invalid_tests_removed"


LeaderDecision = RepositoryPlanDecision | RepositoryReviewDecision
"""What one leader turn produces, whichever half of the round asked for it."""


class ScriptedLeaderSession:
    """A coordination session whose decisions a caller writes in advance.

    A test double, and the counterpart of :class:`ScriptedCodingSession` on the
    other reading of the same stack. Answers are consumed in order across both
    methods, because a round asks for exactly one decision at a time and the
    order they come back in is the order the lane asked; an exception in the
    script is raised instead of returned, so "the model wrote prose instead of a
    plan" is one entry in a list rather than a subclass.

    Running past the end of the script is an error rather than a default. The
    claims this double supports are mostly negative — *this* notice produced no
    decision, a replay asked for none — and a double that quietly invented an
    extra answer would let a lane that decided twice look like one that decided
    once.

    ``asked`` is the record those claims are read off: what was asked for, and
    about which leader task.
    """

    def __init__(self, *answers: "LeaderDecision | BaseException") -> None:
        self._answers: list[LeaderDecision | BaseException] = list(answers)
        self.asked: list[tuple[str, UUID]] = []
        """``("plan" | "review", leader task id)`` per call, in order."""

    async def plan(self, package: RepositoryAssignmentPackage) -> RepositoryPlanDecision:
        answer = self._next("plan", package)
        assert isinstance(answer, RepositoryPlanDecision), "the script owes a plan here"
        return answer

    async def review(self, package: RepositoryAssignmentPackage) -> RepositoryReviewDecision:
        answer = self._next("review", package)
        assert isinstance(answer, RepositoryReviewDecision), "the script owes a verdict here"
        return answer

    def _next(self, what: str, package: RepositoryAssignmentPackage) -> "LeaderDecision":
        self.asked.append((what, package.leader_task_id))
        if not self._answers:
            raise AssertionError(
                f"the scripted leader session was asked for a {what} it has no answer for"
            )
        answer = self._answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class MemoryReadinessReporter:
    """A readiness lease held in memory, with the reports recorded in order.

    Order is what it is for. The claims the reporting design rests on are
    sequences — the first report is made before ``bridge ready``, a renewal
    only happens after one, the goodbye is last and happens once — and a double
    that kept counters could not settle any of them.

    ``renew_after_seconds`` is a float here and a whole number of seconds on the
    wire. A double that could only answer in seconds would make every test of
    the renewal loop spend one, and what those tests are about is the loop's
    shape rather than the lease's duration; the reporter under test does nothing
    with the number except sleep it.

    ``retuned_to`` is what every renewal answers with instead, which is how a
    deployment that changes its TTL while a fleet is running is expressed. The
    period is the server's to choose and is returned by every call rather than
    once at startup, so a loop that kept the first answer would be a fleet that
    has to be restarted to learn a new one — and that is only observable against
    a double whose two answers differ.

    Failures are scripted rather than subclassed: one for the blocking first
    report, one for the goodbye, and one positional entry per renewal — ``None``
    meaning "answer normally" — so "the second renewal failed and the third
    still happened" is a list rather than a class. Once the script is spent
    every further renewal succeeds, which is the recovery the loop claims to
    have.
    """

    def __init__(
        self,
        *renew_failures: BaseException | None,
        renew_after_seconds: float = 45,
        retuned_to: float | None = None,
        startup_failure: BaseException | None = None,
        shutdown_failure: BaseException | None = None,
    ) -> None:
        self._renew_failures = list(renew_failures)
        self._renew_after_seconds = renew_after_seconds
        self._retuned_to = retuned_to
        self._startup_failure = startup_failure
        self._shutdown_failure = shutdown_failure
        self.calls: list[str] = []
        """``startup`` | ``renew`` | ``shutdown`` per report, in order.

        Recorded before any scripted failure is raised, so a report that was
        attempted and refused is distinguishable from one that never happened.
        """

    async def report_startup(self) -> float:
        self.calls.append("startup")
        if self._startup_failure is not None:
            raise self._startup_failure
        return self._renew_after_seconds

    async def report_renew(self) -> float:
        self.calls.append("renew")
        if self._renew_failures and (failure := self._renew_failures.pop(0)) is not None:
            raise failure
        return self._renew_after_seconds if self._retuned_to is None else self._retuned_to

    async def report_shutdown(self) -> None:
        self.calls.append("shutdown")
        if self._shutdown_failure is not None:
            raise self._shutdown_failure


@dataclass(frozen=True, slots=True)
class SentMessage:
    """One message the Bridge handed to a room, exactly as it handed it over."""

    room_id: str
    thread_root_id: str | None
    txn_id: str
    body: RoomBody


class InMemoryRoomPort:
    """A room port whose homeserver a caller writes in advance.

    Answers ``sync`` from a script and records everything the Bridge did, in
    order. The order is the point: "the outbox drained before the first sync"
    and "the invitation was accepted on the baseline round" are claims about
    sequence, and a double that kept only per-method lists could not settle
    them — hence ``calls`` alongside the detailed records.

    When the script runs out, ``sync`` sets :attr:`idle` and then waits. That is
    not a convenience for tests: it is the shape a real long poll has when a room
    is quiet, and it means a caller is always stopped by cancellation rather than
    by running off the end of a fixture.
    """

    def __init__(self, *answers: RoomBatch | BaseException) -> None:
        self._answers: list[RoomBatch | BaseException] = list(answers)
        self.started_rooms: tuple[str, ...] = ()
        self.user_id: str | None = None
        self.homeserver_url: str | None = None
        self.access_token: str | None = None
        self.closed = False
        self.ready = asyncio.Event()
        """Set once ``start`` has run: the local readiness signal this tier has."""
        self.idle = asyncio.Event()
        """Set when the script is exhausted and the port is merely waiting."""
        self.calls: list[str] = []
        self.syncs: list[str | None] = []
        self.joined: list[str] = []
        self.sent: list[SentMessage] = []

    async def start(
        self,
        *,
        homeserver_url: str,
        user_id: str,
        room_ids: Sequence[str],
        access_token: str,
    ) -> None:
        self.calls.append("start")
        self.homeserver_url = homeserver_url
        self.user_id = user_id
        self.started_rooms = tuple(room_ids)
        self.access_token = access_token
        self.ready.set()

    async def sync(self, *, since: str | None, timeout_ms: int) -> RoomBatch:
        del timeout_ms  # nothing here waits on the wire
        self.calls.append("sync")
        self.syncs.append(since)
        while not self._answers:
            self.idle.set()
            await asyncio.Event().wait()
        answer = self._answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def join(self, room_id: str) -> None:
        self.calls.append("join")
        self.joined.append(room_id)

    async def send(
        self, *, room_id: str, thread_root_id: str | None, txn_id: str, body: RoomBody
    ) -> str:
        self.calls.append("send")
        self.sent.append(
            SentMessage(
                room_id=room_id, thread_root_id=thread_root_id, txn_id=txn_id, body=body
            )
        )
        return event_id_for(txn_id)

    async def close(self) -> None:
        self.closed = True
        self.ready.clear()


def event_id_for(txn_id: str) -> str:
    """The event id this double hands back for a transaction id.

    Derived from the transaction id and from nothing else, because that is the
    property a real homeserver has and the property the whole outbox design
    rests on: resending an unacknowledged transaction returns the *original*
    event rather than creating a second one. A double that minted a fresh id per
    call would make a crashed-and-resent turn look like a duplicate that never
    happens in production.
    """

    return f"$sent-{txn_id}"


class InertCodingSession:
    """A coding session that never starts a process.

    This is the production stand-in, not a test double: ``run`` assembles it, so
    what a room gets when it @-mentions this build is one honest note. It spawns
    nothing, so ``close`` is always called on a session that was never opened —
    which is the behaviour every implementation of this port owes the shutdown
    path anyway.
    """

    def __init__(self, *, worker_name: str = "bridge") -> None:
        self.worker_name = worker_name
        self.turns: list[TurnRequest] = []
        self.closed = False

    async def ensure_ready(self) -> None:
        """Nothing to verify, so nothing is verified.

        Not a stub standing in for work this class ought to do: there is no
        binary to resolve, no credential to check and no isolation to prove,
        because this session spawns nothing. The gate exists so that a runtime
        with something to prove has somewhere to prove it, and the honest answer
        from a runtime with nothing to prove is to return.
        """

    async def respond(self, turn: TurnRequest) -> TurnOutcome:
        """Answer with a single note, and never with anything else.

        The observation's own id and timestamp are placeholders: the outbox
        derives both from the trigger when it writes the row, precisely so that
        a replayed turn lands on the same names, so the values put here reach
        neither a room nor a wire.
        """

        self.turns.append(turn)
        return TurnOutcome(
            observations=(
                RoomObservation(
                    observation_id=uuid4(),
                    emitted_at=datetime.now(UTC),
                    worker_name=self.worker_name,
                    room_id=turn.room_id,
                    kind="note",
                    body=INERT_SESSION_NOTE,
                ),
            ),
            native_session_id=None,
            status="completed",
        )

    async def close(self) -> None:
        self.closed = True


class ScriptedCodingSession:
    """A coding session whose answers a caller writes in advance.

    A test double, unlike its inert sibling. Answers are consumed in order and
    an exception in the script is raised instead of returned, so "the CLI blew
    up on turn two" is one entry in a list rather than a subclass. Once the
    script is spent every further turn gets ``default``, which keeps a test that
    cares about the first two turns from having to describe the rest.

    ``turns`` is the record that matters most: it is where the resume handle the
    supervisor offered shows up, and "the second mention in this thread carried
    the handle the first one announced" is only observable there.

    ``not_ready`` scripts the startup gate the same way: a real CLI adapter
    refuses when the binary is missing or the machine cannot isolate it, and
    handing this double the same refusal is what lets "a Bridge that cannot code
    never touches a room" be tested without one.
    """

    def __init__(
        self,
        *answers: TurnOutcome | BaseException,
        default: TurnOutcome | None = None,
        not_ready: BaseException | None = None,
    ) -> None:
        self._answers: list[TurnOutcome | BaseException] = list(answers)
        self._default = default or TurnOutcome(
            observations=(), native_session_id=None, status="completed"
        )
        self._not_ready = not_ready
        self.turns: list[TurnRequest] = []
        self.ready_calls = 0
        """How often the gate was asked. Counted rather than flagged so a caller
        can tell "never reached" from "reached once" from "reached every round",
        which are three different bugs."""
        self.closed = False

    async def ensure_ready(self) -> None:
        self.ready_calls += 1
        if self._not_ready is not None:
            raise self._not_ready

    async def respond(self, turn: TurnRequest) -> TurnOutcome:
        self.turns.append(turn)
        answer = self._answers.pop(0) if self._answers else self._default
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def close(self) -> None:
        self.closed = True
