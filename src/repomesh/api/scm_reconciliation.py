from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from repomesh.integrations.scm.contracts import SCMError
from repomesh.modules.delivery import DeliveryConflict
from repomesh.settings import get_settings

router = APIRouter(prefix="/delivery", tags=["delivery"])


@router.post("/change-sets/{change_set_id}/reconcile")
async def reconcile_change_set(change_set_id: UUID, request: Request) -> dict:
    expected = get_settings().agent_action_token
    if not expected:
        raise HTTPException(
            status_code=503, detail="delivery API authentication is not configured"
        )
    if request.headers.get("Authorization") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid delivery API credentials")
    try:
        coordinator = request.app.state.container.changeset_scm_coordinator()
        return asdict(await coordinator.reconcile_and_merge(change_set_id))
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (DeliveryConflict, SCMError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
