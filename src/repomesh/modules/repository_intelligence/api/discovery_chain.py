"""Write triggers for an issue's discovery chain (contract v0.4 §4 and §5).

Native paths — ``POST /api/v1/issues/{id}/discovery/*`` — rather than a
``/console`` face (Q10). The codebase has both styles: ``POST /issues`` is
native, ``POST /console/repositories/scan-org`` is not. The console face exists
to stop credentials being passed through a browser, and nothing in these bodies
is a credential, so that reason does not apply and the native style wins. These
routes live with ``plan_snapshots``, the table they write.

**202, and a thread.** All four triggers accept and return a task to poll.
Step 2 is one model call per candidate and cannot be a request anyone holds
open; making the other three synchronous would buy nothing and cost the panel
two waiting paths and two error paths. The important half is the thread: every
LLM call in this pipeline is a blocking ``chat()``, so wrapping one in
``asyncio.create_task`` and calling it asynchronous would stop the event loop
for the whole process — the browser would poll an endpoint that cannot answer
because the request before it is holding the loop. The work runs in a worker
thread (``asyncio.to_thread``, inside the application service) and the loop
stays free to serve the polls.

**The task registry is an in-process dict**, the same trade the console's scans
make, and a safer one here: a scan's task record is the only account of what
happened, while a discovery step's result is in the snapshot before the task is
marked done. Losing the registry costs a re-read, not a re-run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from repomesh.modules.agent_directory.contracts import AgentHierarchyViolation
from repomesh.modules.change_orchestration.contracts import ExecutionPlaneUnavailable
from repomesh.modules.collaboration.contracts import CollaborationRouteUnavailable
from repomesh.modules.repository_intelligence.application.discovery_chain import (
    DiscoveryActorNotFound,
    DiscoveryChainService,
    DiscoveryDenied,
    DiscoveryEvidenceStale,
    DiscoveryIssueNotFound,
    DiscoveryLLMUnavailable,
    DiscoveryPreconditionFailed,
)
from repomesh.modules.repository_intelligence.application.discovery_materialization import (
    MaterializeIssueCommand,
)
from repomesh.modules.repository_intelligence.contracts import (
    GUI_STEP_OF,
    DiscoveryApprovalCommand,
    DiscoveryStepCommand,
)
from repomesh.modules.repository_intelligence.ports import RuntimeProjectionUnavailable
from repomesh.shared.workflow import WorkflowBlocked

from .models import (
    DiscoveryAnalysisRequest,
    DiscoveryApprovalRequest,
    DiscoveryCandidatesRequest,
    DiscoveryClassificationRequest,
    DiscoveryMaterializeRequest,
    DiscoveryMaterializeView,
    DiscoveryPlanRequest,
    DiscoveryTaskProgress,
    DiscoveryTaskView,
    DiscoveryTriggerView,
)
from .router import ACTION_TOKEN

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["issue-discovery"])

#: How many finished steps to remember. The panel never looks further back
#: than the step it just started.
_MAX_REMEMBERED_TASKS = 100


@dataclass(slots=True)
class DiscoveryTaskRecord:
    """One discovery step in flight, alive only in this process."""

    id: UUID
    issue_id: UUID
    step: int
    status: str = "running"
    done: int = 0
    total: int = 1
    label: str | None = None
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    #: Strong reference to the running coroutine. Without it the loop may
    #: collect a task nobody awaits, leaving the record reading "running"
    #: while the step has actually stopped.
    runner: asyncio.Task | None = None


_DISCOVERY_TASKS: dict[UUID, DiscoveryTaskRecord] = {}


def _forget_old_tasks() -> None:
    excess = len(_DISCOVERY_TASKS) - _MAX_REMEMBERED_TASKS
    if excess <= 0:
        return
    finished = [r for r in _DISCOVERY_TASKS.values() if r.status != "running"]
    finished.sort(key=lambda r: r.finished_at or r.started_at)
    for record in finished[:excess]:
        _DISCOVERY_TASKS.pop(record.id, None)


def running_task(issue_id: UUID) -> tuple[UUID, int] | None:
    """The step this issue currently has in flight, if any.

    Read by the composition root so the discovery projection can report
    ``running`` without the read model reaching into this module.
    """

    for record in _DISCOVERY_TASKS.values():
        if record.issue_id == issue_id and record.status == "running":
            return record.id, record.step
    return None


def _as_view(record: DiscoveryTaskRecord) -> DiscoveryTaskView:
    return DiscoveryTaskView(
        task_id=record.id,
        issue_id=record.issue_id,
        step=record.step,
        status=record.status,
        progress=DiscoveryTaskProgress(
            done=record.done, total=record.total, label=record.label
        ),
        error=record.error,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def _progress_reporter(record: DiscoveryTaskRecord) -> Callable[[int, int, str], None]:
    """Feed per-candidate progress into the record (§4.5 / Q17).

    ``confirm()`` already takes this callback, so N model calls stop being N
    minutes of silence. Called from the worker thread; these are plain
    attribute writes on one object with a single reader.
    """

    def report(done: int, total: int, label: str) -> None:
        record.done = done
        record.total = total
        record.label = label

    return report


async def _drive(record: DiscoveryTaskRecord, run: Callable[[], Awaitable[None]]) -> None:
    """Run one step to completion and record the outcome, whatever it is.

    Nothing may escape: there is no caller to catch for this coroutine, and an
    exception that got out would leave the record reading "running" forever
    while the panel polled a step that had already died.
    """

    try:
        await run()
    except Exception as error:  # noqa: BLE001 - the record is the only reporter
        _logger.warning(
            "discovery step %d failed for issue %s", record.step, record.issue_id,
            exc_info=error,
        )
        record.status = "failed"
        # Our own pipeline's message, not an echo of an outbound response: the
        # panel shows it verbatim because a generic failure leaves an operator
        # with nothing to act on.
        record.error = str(error)[:500] or error.__class__.__name__
    else:
        record.status = "succeeded"
        record.done = record.total
    finally:
        record.finished_at = datetime.now(UTC)


def _start(
    *,
    issue_id: UUID,
    step: int,
    total: int,
    run: Callable[[DiscoveryTaskRecord], Awaitable[None]],
) -> DiscoveryTaskRecord:
    record = DiscoveryTaskRecord(id=uuid4(), issue_id=issue_id, step=step, total=total)
    _DISCOVERY_TASKS[record.id] = record
    _forget_old_tasks()
    record.runner = asyncio.create_task(_drive(record, lambda: run(record)))
    return record


def _service(request: Request) -> DiscoveryChainService:
    return request.app.state.container.discovery_chain_service()


def _guard_single_flight(issue_id: UUID) -> None:
    """One step at a time per issue (§4.4).

    Two people pressing "re-analyse" together must not produce two competing
    writes to the same block, with whichever finishes last silently winning.
    """

    in_flight = running_task(issue_id)
    if in_flight is None:
        return
    task_id, step = in_flight
    raise HTTPException(
        status_code=409,
        detail=(
            f"this issue already has a discovery step running (step {step}, "
            f"task {task_id}); wait for it to finish or read "
            f"GET /api/v1/issues/{issue_id}/discovery"
        ),
    )


def _translate(error: Exception) -> HTTPException:
    """One place where the chain's refusals become status codes."""

    if isinstance(error, DiscoveryIssueNotFound | DiscoveryActorNotFound):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, DiscoveryDenied):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, DiscoveryLLMUnavailable):
        return HTTPException(status_code=503, detail=str(error))
    if isinstance(error, CollaborationRouteUnavailable):
        # The execution plane has no Matrix room for the team the plan wants to
        # assign to. Nothing about the request is wrong and nothing about the
        # server is broken — the runtime has not caught up with the topology —
        # so this reads like the ExecutionPlaneUnavailable 503 above: press the
        # button again once the rooms exist. It used to leak out as a 500,
        # which told the operator to file a bug instead of to retry (found live
        # by the final acceptance walk, 2026-08-12).
        return HTTPException(
            status_code=503,
            detail=(
                f"the execution plane is not ready to take this plan ({error}); "
                "materialize again once the repository teams have their rooms"
            ),
        )
    if isinstance(error, DiscoveryPreconditionFailed | DiscoveryEvidenceStale):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, AgentHierarchyViolation):
        # The repository-team provisioner's honest refusal: the repository's
        # leader singleton is global, so a repository already staffed by
        # another organization cannot be converged on. A legitimate business
        # rejection, not a server fault — it used to leak out as a 500 (found
        # live by the final acceptance walk, 2026-08-12).
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail=str(error))
    raise error


def _accepted(record: DiscoveryTaskRecord) -> JSONResponse:
    view = DiscoveryTriggerView(task_id=record.id, step=record.step, status="accepted")
    return JSONResponse(status_code=202, content=view.model_dump(mode="json"))


def _replay(step: int) -> JSONResponse:
    view = DiscoveryTriggerView(task_id=None, step=step, status="replayed")
    return JSONResponse(status_code=200, content=view.model_dump(mode="json"))


@router.post("/issues/{issue_id}/discovery/analysis", dependencies=[ACTION_TOKEN])
async def trigger_analysis(
    issue_id: UUID, body: DiscoveryAnalysisRequest, request: Request
) -> JSONResponse:
    """Step 0 — requirement sufficiency, and the override for its questions.

    ``force_continue`` is a field here rather than an endpoint of its own
    because it is a statement about the analysis already on file: it records
    "N questions ignored, continuing" and does not call the model. It answers
    200, not 202 — there is no work to wait for.
    """

    service = _service(request)
    command = DiscoveryStepCommand(
        issue_id=issue_id,
        created_by_agent_id=body.created_by_agent_id,
        idempotency_key=body.idempotency_key,
        answers=tuple((a.question, a.answer) for a in body.answers),
        force_continue=body.force_continue,
    )
    _guard_single_flight(issue_id)
    try:
        target, needs_run = await service.analysis(command)
    except Exception as error:
        raise _translate(error) from error
    if not needs_run:
        return _replay(GUI_STEP_OF["analysis"])

    record = _start(
        issue_id=issue_id,
        step=GUI_STEP_OF["analysis"],
        total=1,
        run=lambda _task: service.run_analysis(target, command),
    )
    return _accepted(record)


@router.post("/issues/{issue_id}/discovery/candidates", dependencies=[ACTION_TOKEN])
async def trigger_candidates(
    issue_id: UUID, body: DiscoveryCandidatesRequest, request: Request
) -> JSONResponse:
    """Step 1 — score the catalog against the analysed requirement."""

    service = _service(request)
    command = DiscoveryStepCommand(
        issue_id=issue_id,
        created_by_agent_id=body.created_by_agent_id,
        idempotency_key=body.idempotency_key,
        limit=body.limit,
        entry_point=body.entry_point,
    )
    _guard_single_flight(issue_id)
    try:
        target, needs_run = await service.candidates(command)
    except Exception as error:
        raise _translate(error) from error
    if not needs_run:
        return _replay(GUI_STEP_OF["candidates"])

    record = _start(
        issue_id=issue_id,
        step=GUI_STEP_OF["candidates"],
        total=1,
        run=lambda _task: service.run_candidates(target, command),
    )
    return _accepted(record)


@router.post("/issues/{issue_id}/discovery/classification", dependencies=[ACTION_TOKEN])
async def trigger_classification(
    issue_id: UUID, body: DiscoveryClassificationRequest, request: Request
) -> JSONResponse:
    """Step 2 — one model call per candidate, with per-candidate progress."""

    service = _service(request)
    command = DiscoveryStepCommand(
        issue_id=issue_id,
        created_by_agent_id=body.created_by_agent_id,
        idempotency_key=body.idempotency_key,
    )
    _guard_single_flight(issue_id)
    try:
        target, needs_run = await service.classification(command)
    except Exception as error:
        raise _translate(error) from error
    if not needs_run:
        return _replay(GUI_STEP_OF["classification"])

    candidate_count = len((target.discovery.get("candidates") or {}).get("items") or ())
    record = _start(
        issue_id=issue_id,
        step=GUI_STEP_OF["classification"],
        total=max(candidate_count, 1),
        run=lambda task: service.run_classification(
            target, command, on_progress=_progress_reporter(task)
        ),
    )
    return _accepted(record)


@router.post("/issues/{issue_id}/discovery/plan", dependencies=[ACTION_TOKEN])
async def trigger_plan(
    issue_id: UUID, body: DiscoveryPlanRequest, request: Request
) -> JSONResponse:
    """Step 3 — integrate the released tiering into a plan.

    Refuses with 409 unless the classification has been approved. That refusal
    is where "approval is mandatory in v1" actually lives; everything else
    about it is display.
    """

    service = _service(request)
    command = DiscoveryStepCommand(
        issue_id=issue_id,
        created_by_agent_id=body.created_by_agent_id,
        idempotency_key=body.idempotency_key,
    )
    _guard_single_flight(issue_id)
    try:
        target, needs_run = await service.plan(command)
    except Exception as error:
        raise _translate(error) from error
    if not needs_run:
        return _replay(GUI_STEP_OF["plan"])

    record = _start(
        issue_id=issue_id,
        step=GUI_STEP_OF["plan"],
        total=1,
        run=lambda _task: service.run_plan(target, command),
    )
    return _accepted(record)


@router.post(
    "/issues/{issue_id}/discovery/approval",
    response_model=DiscoveryTriggerView,
    dependencies=[ACTION_TOKEN],
)
async def decide_classification(
    issue_id: UUID, body: DiscoveryApprovalRequest, request: Request
) -> DiscoveryTriggerView:
    """§5.2 — the organization leader releases (or returns) a classification.

    Synchronous: no model is involved, so there is nothing to poll for.

    This is a second approval face in the console, and it is not the delivery
    decision folder. That folder decides a round of changes and needs a
    change set, a repository and a head sha; none of those exist while an
    issue is still being scoped. What is approved here is a tiering.

    The body is a receipt in the same shape the four triggers use — never the
    approval block itself, which the discovery projection already owns. The
    caller's next move is to re-read that projection, exactly as after a step.
    ``status`` distinguishes a decision that was recorded now from one an
    earlier request with this key already recorded.
    """

    service = _service(request)
    command = DiscoveryApprovalCommand(
        issue_id=issue_id,
        decided_by_agent_id=body.decided_by_agent_id,
        idempotency_key=body.idempotency_key,
        decision=body.decision,
        reason=body.reason,
        evidence_version=body.evidence_version,
        adjustments=tuple((a.repository, a.tier) for a in body.adjustments),
    )
    try:
        _target, replayed = await service.approve(command)
    except Exception as error:
        raise _translate(error) from error
    return DiscoveryTriggerView(
        task_id=None,
        step=GUI_STEP_OF["approval"],
        status="replayed" if replayed else "accepted",
    )


@router.post(
    "/issues/{issue_id}/discovery/materialize",
    response_model=DiscoveryMaterializeView,
    dependencies=[ACTION_TOKEN],
)
async def materialize_discovery_plan(
    issue_id: UUID, body: DiscoveryMaterializeRequest, request: Request
) -> DiscoveryMaterializeView:
    """§8 — build the teams, create the tasks, start the round.

    Synchronous, and deliberately not one of the ``_start``/``_accepted`` pair
    above: nothing here calls a model, so there is no blocking work to move off
    the loop and no task worth polling. The panel's next move is a re-read of
    the issue, exactly as after an approval.

    Both 409s below are passed through with the server's own words. The
    checkpoint one in particular is not ours to reword — it names the gate and
    the evidence version, which is the entire actionable content, and a panel
    that renders "blocked" instead has thrown that away.
    """

    service = request.app.state.container.discovery_materialization_service()
    command = MaterializeIssueCommand(
        issue_id=issue_id,
        created_by_agent_id=body.created_by_agent_id,
        idempotency_key=body.idempotency_key,
    )
    try:
        result = await service.materialize(command)
    except WorkflowBlocked as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ExecutionPlaneUnavailable as error:
        # The bridge refuses before any side effect, so this is a 503 the
        # caller may simply retry once the plane is configured — same reading
        # as `POST /bridge/materialize` gives it.
        raise HTTPException(status_code=503, detail=str(error)) from error
    except RuntimeProjectionUnavailable as error:
        # The runtime projection could not give the teams their rooms, so the
        # plan was never started. Read it exactly as the 503 above: the round
        # is still open and the same key will finish it once the controller
        # answers. Saying it here beats letting the round start and die on its
        # first dispatch, which is what defect B-11 actually looked like.
        raise HTTPException(
            status_code=503,
            detail=(
                f"the execution plane has no rooms for this project's teams ({error}); "
                "nothing was started — materialize again once AgentTeams answers"
            ),
        ) from error
    except Exception as error:
        raise _translate(error) from error

    return DiscoveryMaterializeView(
        plan_id=result.plan_id,
        task_ids=list(result.task_ids),
        team_count=result.team_count,
        repositories=list(result.repositories),
        status=result.status,
    )


@router.get(
    "/issues/{issue_id}/discovery/tasks/{task_id}",
    response_model=DiscoveryTaskView,
    dependencies=[ACTION_TOKEN],
)
async def get_discovery_task(issue_id: UUID, task_id: UUID) -> DiscoveryTaskView:
    """Report a step's progress, never its result.

    A 404 is ambiguous on purpose-free terms — the task never existed, or this
    process restarted and forgot it — and the detail says so. Unlike a lost
    scan, a lost discovery task does not mean the work must be redone: the
    result was written to the snapshot before the task was marked done, so
    reading the projection answers "did this land" without re-running anything.
    """

    record = _DISCOVERY_TASKS.get(task_id)
    if record is None or record.issue_id != issue_id:
        raise HTTPException(
            404,
            "discovery task not found — task state lives in this process only and "
            "is lost on restart; read GET /api/v1/issues/"
            f"{issue_id}/discovery to see whether the step landed, rather than "
            "re-running it",
        )
    return _as_view(record)
