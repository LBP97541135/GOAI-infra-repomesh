"""Workspace registry endpoints — contract v0.3 §2.2 / §2.3.

Owned by identity_access (module map: organizations belong here). The path
sits in the ``console`` namespace per the v0.2 §4.5 ruling: generic nouns get
the shared prefix so later additions cannot shadow or be shadowed.
"""

import hashlib
from dataclasses import asdict
from hmac import compare_digest
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from repomesh.modules.identity_access.contracts import CreateOrganizationCommand
from repomesh.modules.identity_access.local_accounts import LocalAuthenticationError
from repomesh.modules.identity_access.organizations import (
    OrganizationLeaderConflict,
    OrganizationNameConflict,
)
from repomesh.shared.events import ActorType

router = APIRouter(prefix="/console", tags=["console-organizations"])

# Mirrors repomesh.api.human_control.SESSION_COOKIE. Not imported: that router
# pulls in modules.project at import time and a modules→api dependency would
# invert the layering for one constant.
_SESSION_COOKIE = "repomesh_session"


class OrganizationCreate(BaseModel):
    """v0.3 §2.3. The idempotency key is client-generated: a fresh random key
    per logical create, the same key on retry (organization_id derives from
    it — low-entropy keys would merge unrelated workspaces). Key minimum is 8
    (v0.3 §6, same hardening as issue intake)."""

    name: str = Field(min_length=1, max_length=200)
    leader_resource_name: str | None = Field(default=None, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=200)


def _authorize(request: Request) -> None:
    from repomesh.settings import get_settings

    expected = get_settings().agent_action_token
    if not expected:
        raise HTTPException(
            status_code=503, detail="organization API authentication is not configured"
        )
    presented = request.headers.get("Authorization") or ""
    if not compare_digest(presented, f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="invalid organization API credentials")


async def _audit_actor(request: Request) -> tuple[ActorType, str]:
    """Audit attribution for workspace creation (v0.3 §6 S-6).

    The action token is a shared secret with no subject, so "who created
    this" can at best be pinned to (a) the logged-in human session when the
    request carries one, else (b) a fingerprint of the presented token —
    enough to tell credentials apart once tokens rotate, and honest about
    not knowing more. Subject-carrying credentials are the adopted backlog
    item; this is the pre-push floor."""

    session_token = request.cookies.get(_SESSION_COOKIE)
    if session_token:
        try:
            account = await request.app.state.container.local_account_service().authenticate(
                session_token
            )
            return ActorType.HUMAN, f"human:{account.id}"
        except LocalAuthenticationError:
            pass  # expired/invalid cookie → fall back to token attribution
    presented = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    digest = hashlib.sha256(presented.encode("utf-8")).hexdigest()[:12]
    return ActorType.SERVICE, f"action-token:{digest}"


@router.get("/organizations")
async def list_organizations(request: Request, organization_id: UUID | None = None) -> dict:
    """v0.3 §2.2; the optional ``organization_id`` filter is the §6 S-6
    caller-scoping knob (the workspace switcher still lists all — it exists
    to pick one)."""

    _authorize(request)
    registry = request.app.state.container.organization_registry_service()
    return {
        "organizations": [
            asdict(view) for view in await registry.list_views(organization_id)
        ]
    }


@router.post("/organizations")
async def create_organization(body: OrganizationCreate, request: Request) -> JSONResponse:
    """201 first creation / 200 idempotent replay; 409 when the name is taken
    under a different key or the leader resource name is held by another
    workspace (repairable: same idempotency_key + a different
    leader_resource_name completes the registration, v0.3 §6 S-8). The
    created leader is a desired-state directory row, not a running agent
    (§2.3 honesty note) — the response deliberately carries no runtime
    claim."""

    _authorize(request)
    actor_type, actor_id = await _audit_actor(request)
    registry = request.app.state.container.organization_registry_service()
    try:
        receipt = await registry.create(
            CreateOrganizationCommand(
                name=body.name,
                idempotency_key=body.idempotency_key,
                actor_type=actor_type,
                actor_id=actor_id,
                leader_resource_name=body.leader_resource_name,
            )
        )
    except OrganizationNameConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OrganizationLeaderConflict as error:
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
