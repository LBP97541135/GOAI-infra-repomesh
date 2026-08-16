from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from repomesh.settings import get_settings

router = APIRouter(prefix="/deliveries", tags=["deliveries"])
issues_router = APIRouter(prefix="/issues", tags=["issues"])
rooms_router = APIRouter(prefix="/rooms", tags=["rooms"])
grid_router = APIRouter(prefix="/console", tags=["console-grid"])
"""§4's three list endpoints.

Namespaced because `GET /api/v1/repositories` is already owned by
repository_intelligence (its catalog view, registered earlier and therefore the
one that wins at runtime). Mounting the grid at the bare path would have left it
permanently shadowed while OpenAPI advertised it — a collision that reads as a
working endpoint returning the wrong shape.
"""

_ISSUE_STATES = {"open", "closed", "all"}


def _offset(cursor: str | None) -> int:
    """§4.1 cursor semantics: an opaque string that is an offset underneath."""

    if cursor is None:
        return 0
    try:
        offset = int(cursor)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid cursor: {cursor}") from None
    if offset < 0:
        raise HTTPException(status_code=422, detail=f"invalid cursor: {cursor}")
    return offset


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


@router.get("/{delivery_id}/rollback-scope")
async def get_rollback_scope(delivery_id: UUID, request: Request) -> dict:
    """Contract v0.1 §4.6 read side: what the rollback dialog puts in its table.

    A delivery with no ChangeSet answers 200 with `available: false`, not 404 —
    "there is nothing published to roll back" is a state the dialog explains,
    the same call §5.1's rooms endpoint makes for an issue with no team.
    """

    payload = await _service(request).rollback_scope(delivery_id)
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
    payload = await _service(request).list_events(
        delivery_id,
        kind=kind,
        offset=_offset(cursor),
        limit=max(1, min(limit, 500)),
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


@issues_router.get("")
async def list_issues(
    request: Request,
    state: str = "open",
    organization_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> dict:
    """Contract v0.2 §2; `state` defaults to open like a GitHub issue list."""

    if state not in _ISSUE_STATES:
        raise HTTPException(status_code=422, detail=f"unknown issue state: {state}")
    return await _service(request).list_issues(
        state=state,
        organization_id=organization_id,
        offset=_offset(cursor),
        limit=max(1, min(limit, 500)),
    )


@issues_router.get("/{issue_id}")
async def get_issue(issue_id: UUID, request: Request) -> dict:
    payload = await _service(request).get_issue(issue_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"issue not found: {issue_id}")
    return payload


@issues_router.get("/{issue_id}/rooms")
async def list_issue_rooms(issue_id: UUID, request: Request) -> dict:
    """Contract v0.2 §5.1. An issue with no team returns an empty list, not 404."""

    payload = await _service(request).list_rooms(issue_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"issue not found: {issue_id}")
    return payload


@issues_router.get("/{issue_id}/discovery")
async def get_issue_discovery(issue_id: UUID, request: Request) -> dict:
    """Contract v0.4 §3.1. An issue that never started a chain returns 200.

    Same call as §5.1's rooms endpoint: "nothing has happened yet" is a state
    the panel renders, not an error. Only an issue with no snapshot at all —
    nothing evidencing it exists — is a 404.
    """

    payload = await _service(request).discovery(issue_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"issue not found: {issue_id}")
    return payload


@issues_router.get("/{issue_id}/repositories/{repository_id}/plan")
async def get_repository_plan(
    issue_id: UUID, repository_id: UUID, request: Request
) -> dict:
    payload = await _service(request).repository_plan(issue_id, repository_id)
    if payload is None:
        raise HTTPException(
            status_code=404, detail=f"no plan for repository {repository_id}"
        )
    return payload


@grid_router.get("/repositories")
async def list_repositories(request: Request) -> dict:
    """Contract v0.2 §4.1."""

    return await _service(request).list_repositories()


@grid_router.get("/teams")
async def list_teams(request: Request, with_runtime: bool = True) -> dict:
    """Contract v0.2 §4.2; the runtime block is proxied unless opted out."""

    return await _service(request).list_teams(with_runtime=with_runtime)


@grid_router.get("/agents")
async def list_agents(request: Request, with_runtime: bool = True) -> dict:
    """Contract v0.2 §4.3."""

    return await _service(request).list_agents(with_runtime=with_runtime)


@rooms_router.get("/{room_id}/stream")
async def get_room_stream(
    room_id: str, request: Request, cursor: str | None = None, limit: int = 100
) -> dict:
    payload = await _service(request).room_stream(
        room_id, offset=_offset(cursor), limit=max(1, min(limit, 500))
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"room not found: {room_id}")
    return payload
