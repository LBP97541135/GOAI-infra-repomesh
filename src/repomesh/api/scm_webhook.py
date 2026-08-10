import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request

from repomesh.integrations.scm import (
    verify_github_webhook,
)
from repomesh.modules.delivery import DeliveryConflict, DeliveryNotFound
from repomesh.modules.delivery.contracts import (
    RecordSCMObservationCommand,
    SCMObservationSource,
)
from repomesh.settings import get_settings

router = APIRouter(tags=["delivery"])


@router.post("/delivery/github-webhook")
async def receive_routed_github_webhook(
    request: Request,
    x_github_delivery: str = Header(alias="X-GitHub-Delivery"),
    x_github_event: str = Header(alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(alias="X-Hub-Signature-256"),
) -> dict:
    return await _receive(
        request,
        x_github_delivery=x_github_delivery,
        x_github_event=x_github_event,
        x_hub_signature_256=x_hub_signature_256,
    )


@router.post(
    "/delivery/change-sets/{change_set_id}/repositories/{repository_id}/github-webhook"
)
async def receive_github_webhook(
    change_set_id: UUID,
    repository_id: UUID,
    request: Request,
    x_github_delivery: str = Header(alias="X-GitHub-Delivery"),
    x_github_event: str = Header(alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(alias="X-Hub-Signature-256"),
) -> dict:
    return await _receive(
        request,
        x_github_delivery=x_github_delivery,
        x_github_event=x_github_event,
        x_hub_signature_256=x_hub_signature_256,
        change_set_id=change_set_id,
        repository_id=repository_id,
    )


async def _receive(
    request: Request,
    *,
    x_github_delivery: str,
    x_github_event: str,
    x_hub_signature_256: str,
    change_set_id: UUID | None = None,
    repository_id: UUID | None = None,
) -> dict:
    secret = get_settings().github_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="GitHub webhook is not configured")
    body = await request.body()
    if not verify_github_webhook(secret, body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid GitHub webhook signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="invalid JSON payload") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="GitHub payload must be an object")
    container = request.app.state.container
    try:
        recorded = await container.scm_observation_service().record(
            RecordSCMObservationCommand(
                provider="github",
                source=SCMObservationSource.WEBHOOK,
                external_id=x_github_delivery,
                event_type=x_github_event,
                payload=payload,
                payload_hash=hashlib.sha256(body).hexdigest(),
                observed_at=datetime.now(UTC),
                change_set_id=change_set_id,
                repository_id=repository_id,
            )
        )
        result = await container.github_observation_processor().process(
            recorded.observation.id
        )
    except DeliveryNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DeliveryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    response = {
        "accepted": True,
        "duplicate": not recorded.created,
        "ignored": result.ignored,
        "observation_id": str(recorded.observation.id),
    }
    if result.change_set is not None:
        response["change_set"] = asdict(result.change_set)
    return response
