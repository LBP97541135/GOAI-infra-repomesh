"""Re-dispatch a round's unfinished work (contract v0.4 §8.7.4, defect A-13).

One write: ``POST /api/v1/deliveries/{round_id}/redispatch``.

**Why here and not in a module's own api package.** The capability reads an
execution plan and re-drives task_orchestration, while the id in the path is
the one §0 declares equal across three vocabularies — ``round_id`` =
``execution_plan_id`` = v0.1's ``delivery_id`` — so it sits on the ``/deliveries``
prefix beside rollback and archive, where every other round-scoped action the
console can press already lives. Putting it in ``delivery/api`` would make the
delivery module import task_orchestration for a capability that is not its own;
``src/repomesh/api`` is the shelf for exactly that, the same way
``human_control`` and ``scm_reconciliation`` sit here rather than inside the
modules they drive.

**The refusals**, and they are the same translation family the materialize
endpoint established:

*404* — no such round. The path id is not an execution plan.

*409* — the round has nothing to dispatch again: either it never got as far as
writing tasks, or every task it has is finished. Both are ``RoundNotDispatchable``
and both carry the server's own sentence, because "which of the two" is the
entire actionable content and a panel that renders "blocked" has thrown it away.
Pressing again will not change either, which is what keeps this off the 503.

*503* — the execution plane could not take it. The storage that carries the task
package refused (``TaskPublicationUnavailable``, A-10's translation, reused
verbatim), or the room the mention goes to is not ready
(``CollaborationRouteUnavailable``). Both mean the same thing this endpoint's
whole existence means: press it again when the plane is back.

The response says what was done rather than what will happen. ``task_ids`` is
what was dispatched, ``settled_task_ids`` what was deliberately left alone, and
the console prints both so the operator can see that a finished task was not
disturbed.
"""

from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from repomesh.modules.collaboration.contracts import CollaborationRouteUnavailable
from repomesh.modules.task_orchestration.contracts import (
    RedispatchScope,
    TaskPublicationUnavailable,
)
from repomesh.modules.task_orchestration.domain import (
    RoundNotDispatchable,
    TaskConflict,
    TaskDenied,
    TaskNotFound,
)
from repomesh.settings import get_settings

router = APIRouter(prefix="/deliveries", tags=["deliveries"])


class RoundRedispatchRequest(BaseModel):
    """The attempt token, and nothing else.

    No repository id and no task list: the granularity is the whole round, for
    the same reason rollback's is the whole change set (GUI ruling #4). An
    operator watching a stalled round is looking at the round, and asking the
    browser which task to re-tell would put "which of these is stuck" in the
    front end, where §3.1 says no judgement may live.
    """

    idempotency_key: str = Field(min_length=1, max_length=180)
    """Doubles as the attempt token that versions the Matrix transaction id.

    The console mints a fresh one per press, so each press is a genuinely new
    room event; re-sending the *same* key — a double-click, a retried fetch —
    replays and posts nothing. See ``_dispatch_message_key`` for the mechanism
    both halves rest on.
    """

    scope: RedispatchScope = RedispatchScope.UNFINISHED
    """Which tasks to reach; ``unfinished`` by default, and the default matters.

    ``unfinished`` writes no task row at all, so the worst a mistaken press can
    do is put one duplicate notification in a room. ``rerun`` additionally
    sends finished tasks back to work — a real write that un-succeeds a batch —
    and asking for it explicitly is the point: it is the answer to "the result
    on file is wrong", not to "this looks slow".
    """


def _authorize(request: Request) -> None:
    expected = get_settings().agent_action_token
    if not expected:
        raise HTTPException(status_code=503, detail="delivery API authentication is not configured")
    if request.headers.get("Authorization") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid delivery API credentials")


@router.post("/{round_id}/redispatch")
async def redispatch_round(
    round_id: UUID, body: RoundRedispatchRequest, request: Request
) -> dict:
    """Publish this round's task packages again and re-post their mentions."""

    _authorize(request)
    service = request.app.state.container.round_redispatch_service()
    if service is None:
        # The execution plane is not composed at all — no AgentTeams messenger,
        # so no orchestrator and nothing to dispatch through. Same reading as
        # every other unconfigured-plane refusal: a 503 the operator fixes in
        # the deployment, not a 500 they file a bug about.
        raise HTTPException(
            status_code=503,
            detail=(
                "the execution plane is not configured, so this round cannot be "
                "dispatched again"
            ),
        )
    try:
        receipt = await service.execute(
            round_id, attempt=body.idempotency_key, scope=body.scope
        )
    except TaskNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RoundNotDispatchable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except TaskPublicationUnavailable as error:
        # A-10's sentence, one press later. The difference from materialize's
        # is the remedy: there the retry *finishes* the round, here it repeats
        # a telling, and both are the same button pressed again.
        raise HTTPException(
            status_code=503,
            detail=(
                f"the execution plane could not store this round's tasks ({error}); "
                "nothing was re-sent — press re-dispatch again once AgentTeams "
                "storage accepts them"
            ),
        ) from error
    except CollaborationRouteUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail=(
                f"the execution plane has no room to dispatch this round through "
                f"({error}); press re-dispatch again once the rooms exist"
            ),
        ) from error
    except TaskConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except TaskDenied as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        # A server fault, so a 500 — but a *named* one, the way the materialize
        # path names ``RoundNotRecorded``. The first live press of re-dispatch
        # failed on an ``asyncpg.StringDataRightTruncationError`` and reached
        # the operator as ``text/plain "Internal Server Error"``: no class, no
        # sentence, nothing to search for, and no way to tell a bug from an
        # outage. The class name and the driver's own words are the whole of
        # what is actionable, so they are what gets said.
        #
        # Deliberately local to this endpoint rather than a global envelope —
        # that is a separate adjudication. This is parity with its siblings.
        raise HTTPException(
            status_code=500,
            detail=(
                f"re-dispatching round {round_id} failed unexpectedly "
                f"({type(error).__name__}: {error}); nothing was re-sent"
            ),
        ) from error
    return asdict(receipt)
