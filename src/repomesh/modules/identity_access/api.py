"""Workspace registry endpoints — contract v0.3 §2.2 / §2.3.

Owned by identity_access (module map: organizations belong here). The path
sits in the ``console`` namespace per the v0.2 §4.5 ruling: generic nouns get
the shared prefix so later additions cannot shadow or be shadowed.
"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from repomesh.modules.identity_access.contracts import CreateOrganizationCommand
from repomesh.modules.identity_access.organizations import OrganizationNameConflict

router = APIRouter(prefix="/console", tags=["console-organizations"])


class OrganizationCreate(BaseModel):
    """v0.3 §2.3. The idempotency key is client-generated: a fresh random key
    per logical create, the same key on retry (organization_id derives from
    it — low-entropy keys would merge unrelated workspaces)."""

    name: str = Field(min_length=1, max_length=200)
    leader_resource_name: str | None = Field(default=None, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)


def _authorize(request: Request) -> None:
    from repomesh.settings import get_settings

    expected = get_settings().agent_action_token
    if not expected:
        raise HTTPException(
            status_code=503, detail="organization API authentication is not configured"
        )
    if request.headers.get("Authorization") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid organization API credentials")


@router.get("/organizations")
async def list_organizations(request: Request) -> dict:
    _authorize(request)
    registry = request.app.state.container.organization_registry_service()
    return {"organizations": [asdict(view) for view in await registry.list_views()]}


@router.post("/organizations")
async def create_organization(body: OrganizationCreate, request: Request) -> JSONResponse:
    """201 first creation / 200 idempotent replay; 409 when the name is taken
    under a different key. The created leader is a desired-state directory
    row, not a running agent (§2.3 honesty note) — the response deliberately
    carries no runtime claim."""

    _authorize(request)
    registry = request.app.state.container.organization_registry_service()
    try:
        receipt = await registry.create(
            CreateOrganizationCommand(
                name=body.name,
                idempotency_key=body.idempotency_key,
                leader_resource_name=body.leader_resource_name,
            )
        )
    except OrganizationNameConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return JSONResponse(
        status_code=201 if receipt.created else 200,
        content={
            "organization_id": str(receipt.organization_id),
            "name": receipt.name,
            "created_at": receipt.created_at,
            "leader_agent_id": str(receipt.leader_agent_id),
        },
    )
