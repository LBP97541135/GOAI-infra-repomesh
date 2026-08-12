from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from repomesh.modules.delivery.contracts import (
    RecordGovernanceDecisionCommand,
    RequestChangeSetRollbackCommand,
)
from repomesh.modules.delivery.domain import (
    DeliveryConflict,
    DeliveryDenied,
    DeliveryNotFound,
)
from repomesh.settings import get_settings

from .models import ChangeSetRollbackCreate, GovernanceDecisionCreate

router = APIRouter(prefix="/deliveries", tags=["deliveries"])


def _authorize(request: Request) -> None:
    expected = get_settings().agent_action_token
    if not expected:
        raise HTTPException(status_code=503, detail="delivery API authentication is not configured")
    if request.headers.get("Authorization") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid delivery API credentials")


@router.post("/{delivery_id}/governance-decisions")
async def record_governance_decision(
    delivery_id: UUID, body: GovernanceDecisionCreate, request: Request
) -> dict:
    _authorize(request)
    service = request.app.state.container.delivery_governance_service()
    try:
        recorded = await service.record(
            delivery_id,
            RecordGovernanceDecisionCommand(
                change_set_id=body.change_set_id,
                repository_id=body.repository_id,
                head_sha=body.head_sha,
                decision=body.decision,
                decided_by_agent_id=body.decided_by_agent_id,
                reason=body.reason,
            ),
            idempotency_key=body.idempotency_key,
        )
    except DeliveryNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DeliveryDenied as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except DeliveryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return asdict(recorded)


@router.post("/{delivery_id}/rollback")
async def request_rollback(
    delivery_id: UUID, body: ChangeSetRollbackCreate, request: Request
) -> dict:
    """Contract v0.1 §4.6: close the merge gate and hand execution to the Saga.

    One call, two writes (ROLLBACK_REQUIRED per candidate + an
    OPERATOR_REQUESTED recovery plan); see ``DeliveryRollbackService`` for why
    they are not separate endpoints.
    """

    _authorize(request)
    service = request.app.state.container.delivery_rollback_service()
    try:
        receipt = await service.request(
            delivery_id,
            RequestChangeSetRollbackCommand(
                change_set_id=body.change_set_id,
                reason=body.reason,
                requested_by_agent_id=body.requested_by_agent_id,
            ),
            idempotency_key=body.idempotency_key,
        )
    except DeliveryNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DeliveryDenied as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except DeliveryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return asdict(receipt)


@router.post("/{delivery_id}/archive")
async def archive_delivery(delivery_id: UUID, request: Request) -> dict:
    _authorize(request)
    service = request.app.state.container.delivery_archive_service()
    try:
        archive = await service.archive(delivery_id)
    except DeliveryNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DeliveryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return asdict(archive)
