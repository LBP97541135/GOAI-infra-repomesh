from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from repomesh.settings import get_settings

router = APIRouter(prefix="/deliveries", tags=["deliveries"])


def _service(request: Request):
    expected = get_settings().agent_action_token
    if not expected:
        raise HTTPException(status_code=503, detail="delivery API authentication is not configured")
    if request.headers.get("Authorization") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid delivery API credentials")
    return request.app.state.container.delivery_read_model_service()


@router.get("")
async def list_deliveries(request: Request, include_archived: bool = False) -> dict:
    return await _service(request).list_deliveries(include_archived=include_archived)


@router.get("/{delivery_id}")
async def get_delivery(delivery_id: UUID, request: Request) -> dict:
    service = _service(request)
    payload = await service.get_delivery(delivery_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"delivery not found: {delivery_id}")
    return await service.attach_merge_gates(payload)


@router.get("/{delivery_id}/decisions")
async def list_delivery_decisions(delivery_id: UUID, request: Request) -> dict:
    payload = await _service(request).list_decisions(delivery_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"delivery not found: {delivery_id}")
    return payload


_EVENT_KINDS = {"runner", "matrix", "gate", "plan", "deny"}


@router.get("/{delivery_id}/events")
async def list_delivery_events(
    delivery_id: UUID,
    request: Request,
    kind: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> dict:
    if kind is not None and kind not in _EVENT_KINDS:
        raise HTTPException(status_code=422, detail=f"unknown event kind: {kind}")
    offset = 0
    if cursor is not None:
        try:
            offset = int(cursor)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"invalid cursor: {cursor}") from None
        if offset < 0:
            raise HTTPException(status_code=422, detail=f"invalid cursor: {cursor}")
    payload = await _service(request).list_events(
        delivery_id, kind=kind, offset=offset, limit=max(1, min(limit, 500))
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"delivery not found: {delivery_id}")
    return payload


@router.get("/{delivery_id}/messages")
async def list_delivery_messages(delivery_id: UUID, request: Request) -> dict:
    payload = await _service(request).list_messages(delivery_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"delivery not found: {delivery_id}")
    return payload
