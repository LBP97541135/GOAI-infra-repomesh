"""The Repository Leader's coordination session: same codex, different reading.

A leader decides; it does not code. So this is not a second session stack — it
holds a :class:`~repomesh_agent_bridge.adapters.coding_session.DriverCodingSession`
and uses its ``deliver``, which means one deny-all policy, one allowlisted
environment, one restricted process factory and one cancellation discipline
serve both lanes (adjudication B2-1). What differs is the two ends: the prompt
is a plain-text fact package rendered from the assignment, and the answer is
read as a structured decision rather than projected into a room.

**The leader gets no repository, not even read-only** (adjudication D-8, AC-02
defence in depth). Three separate things make that true rather than one:

* The frozen assignment package has no field that could name a path on the
  machine, so there is nothing to leak into the prompt.
* The session's working directory is the Bridge's own empty per-member scratch
  directory — the same one the conversation lane uses — and never a worktree.
  Nothing under it is a checkout of anything.
* Every tool request is denied, as it is on the conversation lane, so a model
  that went looking anyway runs nothing.

**A malformed answer is refused, never repaired.** The Bridge validates its own
session's output against the freeze before posting it, so a plan with a cycle or
an off-roster assignee is caught here with a sentence the leader can act on
instead of arriving as a 409 whose cause has to be reconstructed (adjudication
B2-2). The server's clamp remains, and remains the authority; it is simply not
this build's error handling.

Each decision is a *fresh* thread rather than a continuation of the last one.
That is the review's requirement made structural: a verdict may only rest on the
evidence in its own package (frozen invariant 1), and a session that remembered
authoring the plan would be reviewing its intentions alongside the evidence.
"""

from __future__ import annotations

import logging

from repomesh_runner.drivers.base import DriverResult, DriverResultStatus

from ..contracts import (
    DecisionProvenance,
    LeaderDocumentInvalid,
    RepositoryAssignmentPackage,
    RepositoryPlanDecision,
    RepositoryReviewDecision,
)
from ..leader_lane import (
    assemble_plan_decision,
    assemble_review_decision,
    extract_json_object,
    render_plan_instructions,
    render_review_instructions,
)
from .coding_session import DriverCodingSession

__all__ = ["LeaderCoordinationSession"]

_logger = logging.getLogger(__name__)


class LeaderCoordinationSession:
    """One Repository Leader's decision-making, over its own codex session."""

    def __init__(self, session: DriverCodingSession) -> None:
        self._session = session

    async def ensure_ready(self) -> None:
        """The same startup gate the conversation lane uses, for the same reason.

        A leader whose CLI is missing or cannot be contained must not begin
        accepting assignments and then answer each one with a failure: it would
        hold a round open that another leader — or a person — could have moved.
        """

        await self._session.ensure_ready()

    async def close(self) -> None:
        await self._session.close()

    async def plan(self, package: RepositoryAssignmentPackage) -> RepositoryPlanDecision:
        """Turn a planning package into the leader's own Spec, DAG and tasks.

        Refuses rather than returns when the session did not deliver, when its
        answer is not the document the contract describes, or when that document
        would violate the assignment's own safety envelope.
        """

        raw, provenance = await self._decide(render_plan_instructions(package), "plan")
        decision = assemble_plan_decision(raw, provenance)
        if reason := package.refuse_plan(decision):
            raise LeaderDocumentInvalid(f"the session's plan is outside its bounds: {reason}")
        return decision

    async def review(self, package: RepositoryAssignmentPackage) -> RepositoryReviewDecision:
        """Turn a review_due package into the leader's evidence-based verdict."""

        raw, provenance = await self._decide(render_review_instructions(package), "review")
        decision = assemble_review_decision(raw, provenance)
        if reason := package.refuse_review(decision):
            raise LeaderDocumentInvalid(f"the session's verdict is not supported: {reason}")
        return decision

    async def _decide(self, prompt: str, what: str) -> tuple[object, DecisionProvenance]:
        """One turn, and the two things a decision needs out of it.

        The provenance is built here rather than anywhere the model can reach:
        it names the thread the driver reported, and a turn that reported none
        is refused, because a decision that cannot say honestly where it came
        from is one the server is right to reject (``plan_invalid_provenance``).
        """

        result, observer = await self._session.deliver(prompt)
        _refuse_undelivered(result, what)
        thread_id = result.native_session_id or observer.native_session_id
        if not thread_id:
            raise LeaderDocumentInvalid(
                f"the session produced a {what} but announced no thread, so its provenance "
                "cannot be stated honestly"
            )
        if observer.denied_requests:
            # Not a refusal: an answer written without tools is exactly what this
            # lane asks for. Logged because a leader reaching for them means the
            # prompt did not make its own bounds clear enough.
            _logger.info(
                "leader %s session asked for %d tool action(s); all were denied",
                what,
                observer.denied_requests,
            )
        return extract_json_object(result.summary), DecisionProvenance(session_thread_id=thread_id)


def _refuse_undelivered(result: DriverResult, what: str) -> None:
    """A turn that did not succeed produced no decision, whatever it produced.

    The status word travels because the leader lane's next move depends on it —
    a timeout is worth asking again after and a refusal to answer is not — while
    the driver's diagnostics stay in this machine's log, the same discipline the
    room projection keeps.
    """

    if result.status is DriverResultStatus.SUCCEEDED:
        return
    if result.diagnostics:
        _logger.warning(
            "leader %s session ended %s: %s", what, result.status.value, result.diagnostics
        )
    raise LeaderDocumentInvalid(
        f"the session did not produce a {what}: it ended {result.status.value.lower()}"
    )
