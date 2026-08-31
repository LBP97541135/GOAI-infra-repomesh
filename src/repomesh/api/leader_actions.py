"""The Repository Leader's HTTP decision surface (``contracts/leader-actions/v1``).

Adjudication D-1: a leader decides over HTTP, on the same ``agent-actions``
face and with the same credential mechanism as ``start-worker-task``. There is
no leader MCP server — all six members are external Bridges with no container
and no mcporter, so an MCP channel would have zero consumers today and a seam
with one adapter is not a seam.

Placed in ``repomesh.api`` rather than under ``task_orchestration`` for the
reason ``round_dispatch`` is: a capability that drives task orchestration is
not owned by a module just because it hangs off that module's ids, and the
alternative here is worse — the credential map is parsed by ``agent_runtime``'s
router, and a business module reaching into a sibling module's API package to
borrow a private function inverts the dependency the packages exist to express.

All three endpoints of the frozen contract live here: the read a leader plans
and reviews from, and the two writes that are its own products.
"""

from __future__ import annotations

import hmac
import json
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from repomesh.api.leader_action_models import PlanDecisionBody, ReviewDecisionBody
from repomesh.modules.task_orchestration.contracts import (
    LeaderActionErrorCode,
    LeaderActionRefused,
)
from repomesh.settings import get_settings

router = APIRouter(tags=["leader-actions"])

_logger = logging.getLogger(__name__)

#: The frozen ``fixtures/error-matrix.json`` mapping, as this producer holds
#: it. Declared in full — including the codes only the write endpoints raise —
#: because the matrix is frozen as one table: a producer that knew half of it
#: could reuse a code under a different status, which the contract forbids and
#: no partial table could catch. ``tests/api/test_leader_actions.py`` checks
#: this dict against the fixture itself, so the two cannot drift.
LEADER_ACTION_ERROR_STATUS: dict[LeaderActionErrorCode, int] = {
    LeaderActionErrorCode.INVALID_TOKEN: 401,
    LeaderActionErrorCode.FORBIDDEN_NOT_ASSIGNEE: 403,
    LeaderActionErrorCode.FORBIDDEN_ROLE: 403,
    LeaderActionErrorCode.ASSIGNMENT_NOT_FOUND: 404,
    LeaderActionErrorCode.PHASE_CONFLICT: 409,
    LeaderActionErrorCode.DECOMPOSITION_MODE_CONFLICT: 409,
    LeaderActionErrorCode.PLAN_INVALID_DAG_CYCLE: 409,
    LeaderActionErrorCode.PLAN_INVALID_DAG_COVERAGE: 409,
    LeaderActionErrorCode.PLAN_INVALID_ASSIGNEE: 409,
    LeaderActionErrorCode.PLAN_INVALID_ALLOWED_PATHS: 409,
    LeaderActionErrorCode.PLAN_INVALID_TESTS_REMOVED: 409,
    LeaderActionErrorCode.PLAN_INVALID_PROVENANCE: 409,
    LeaderActionErrorCode.REVIEW_INVALID_FINDINGS: 409,
}


@router.get("/agent-actions/leader/assignments/{task_id}", response_model=None)
async def leader_assignment_package(task_id: UUID, request: Request) -> dict[str, object]:
    """The ``repository-assignment-package.v1`` document for one leader task.

    Returned as the contract's own dict rather than through a response model,
    so the wire shape is the one ``RepositoryAssignmentPackage.to_wire``
    produces and nothing here re-derives it.

    Nothing in the body names a place on disk (adjudication D-8): a leader
    coordinates over text and structured facts and is never given a repository
    workspace, so there is no path for this endpoint to leak.
    """

    try:
        member_agent_id = _authenticated_member(request)
        reader = request.app.state.container.leader_assignment_reader()
        package = await reader.execute(task_id, caller_agent_id=member_agent_id)
    except LeaderActionRefused as refusal:
        raise _refusal(refusal) from refusal
    return package.to_wire()


@router.post("/agent-actions/leader/assignments/{task_id}/plan", response_model=None)
async def submit_repository_plan(
    task_id: UUID, body: PlanDecisionBody, request: Request
) -> dict[str, object]:
    """The leader's Engineering Spec, DAG and worker tasks (``plan-receipt.v1``).

    The path id is the idempotency key and the body does not repeat it, so
    there is one place a plan's identity lives. A resubmission of the same plan
    returns the same receipt with 200 — the leader may have lost the first
    answer, and making it 409 would leave a Bridge unable to find out what
    happened to work that had already been dispatched. A *different* plan under
    that key is 409 ``phase_conflict``, never a silent replacement.

    The body model checks shape and the use case renders the verdict; see
    ``leader_action_models`` for why a malformed body is the framework's 422
    rather than one of the frozen 409 codes.
    """

    try:
        member_agent_id = _authenticated_member(request)
        receipt = await request.app.state.container.leader_plan_submitter().execute(
            task_id, body.to_decision(), caller_agent_id=member_agent_id
        )
    except LeaderActionRefused as refusal:
        raise _refusal(refusal) from refusal
    return receipt.to_wire()


@router.post("/agent-actions/leader/assignments/{task_id}/review", response_model=None)
async def submit_repository_review(
    task_id: UUID, body: ReviewDecisionBody, request: Request
) -> dict[str, object]:
    """The leader's evidence-based verdict (``review-receipt.v1``).

    Idempotent per review round rather than per task: the key is the pair
    (leader task id, review revision), because ``request_rework`` opens a
    second round and round 1's receipt has to stay replayable after it does.
    """

    try:
        member_agent_id = _authenticated_member(request)
        receipt = await request.app.state.container.leader_review_submitter().execute(
            task_id, body.to_decision(), caller_agent_id=member_agent_id
        )
    except LeaderActionRefused as refusal:
        raise _refusal(refusal) from refusal
    return receipt.to_wire()


def _refusal(refusal: LeaderActionRefused) -> HTTPException:
    """One structured error, at the status the frozen matrix gives its code.

    FastAPI serialises ``detail`` verbatim, so a dict detail is exactly the
    frozen ``{"detail": {"code", "message"}}`` envelope. The message is this
    server's own sentence and carries no identifiers of anything the caller
    was refused access to.
    """

    return HTTPException(
        status_code=LEADER_ACTION_ERROR_STATUS[refusal.code],
        detail={"code": refusal.code.value, "message": refusal.message},
    )


def _authenticated_member(request: Request) -> UUID:
    """Which external member is calling, according to its own token.

    ``REPOMESH_RUNNER_WORKER_TOKENS`` under adjudication D-6: the historical
    name is kept (renaming it would break every deployed Bridge) and the
    semantics are widened from "worker agent id → token" to "external member
    id → token". A Repository Leader Bridge holds one entry, exactly as a
    Worker Bridge does.

    The token *names* the subject. No path or body field is consulted to
    decide who is calling, which is what makes forging another leader's task
    id a 403 rather than a successful read.

    Parsed here rather than imported from ``agent_runtime``'s router because
    that function answers a different question — it validates a claim
    (``is this the worker the body names?``) and never yields a subject — and
    because it is that module's private helper. The duplication is the same
    bounded, deliberate kind as the local-administrator guard next door.

    Two deployment faults are folded into ``invalid_token``: no map configured
    and a malformed map. The frozen error enum has no code for "this server is
    misconfigured", and inventing a status outside the matrix would break the
    contract for a case that is not the caller's. So the caller is told the
    truth it can act on — its credential did not authenticate — and the
    operator is told the rest through the log.
    """

    presented = request.headers.get("Authorization", "").encode()
    if not presented:
        raise LeaderActionRefused(
            LeaderActionErrorCode.INVALID_TOKEN, "invalid agent action token"
        )
    for member_agent_id, expected in _member_credentials():
        if hmac.compare_digest(presented, expected):
            return member_agent_id
    raise LeaderActionRefused(LeaderActionErrorCode.INVALID_TOKEN, "invalid agent action token")


def _member_credentials() -> tuple[tuple[UUID, bytes], ...]:
    """The token map as (member agent id, expected header) pairs.

    A sequence rather than a dict so the scan above compares every entry with
    ``compare_digest``; a dict lookup would decide membership by hashing the
    presented secret instead.
    """

    raw = get_settings().runner_worker_tokens
    if not raw:
        return ()
    try:
        document = json.loads(raw)
    except ValueError:
        # No traceback: it would carry the malformed document, tokens included.
        _logger.error(
            "REPOMESH_RUNNER_WORKER_TOKENS is not valid JSON; every external member "
            "token will be refused as invalid until it is repaired"
        )
        return ()
    if not isinstance(document, dict):
        _logger.error(
            "REPOMESH_RUNNER_WORKER_TOKENS is not a JSON object; every external member "
            "token will be refused as invalid until it is repaired"
        )
        return ()
    credentials: list[tuple[UUID, bytes]] = []
    for member_agent_id, token in document.items():
        if not isinstance(token, str):
            _logger.error(
                "REPOMESH_RUNNER_WORKER_TOKENS entry %s does not carry a string token; "
                "that member's token will be refused as invalid",
                member_agent_id,
            )
            continue
        try:
            credentials.append((UUID(member_agent_id), f"Bearer {token}".encode()))
        except ValueError:
            _logger.error(
                "REPOMESH_RUNNER_WORKER_TOKENS is keyed by %r, which is not an agent id; "
                "that entry will never authenticate anybody",
                member_agent_id,
            )
    return tuple(credentials)
