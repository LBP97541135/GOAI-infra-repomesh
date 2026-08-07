import hashlib
import json
from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request

from repomesh.integrations.scm import (
    ChangeSetSCMCoordinator,
    parse_github_check_run,
    parse_repository_ref,
    verify_github_webhook,
)
from repomesh.modules.delivery import DeliveryConflict, DeliveryNotFound
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
    payload_hash = hashlib.sha256(body).hexdigest()
    container = request.app.state.container
    events = container.scm_webhook_event_store()
    claimed = await events.begin(x_github_delivery, payload_hash)
    if not claimed:
        return {"accepted": True, "duplicate": True}
    try:
        if x_github_event != "check_run":
            await events.complete(x_github_delivery)
            return {"accepted": True, "ignored": True}
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise HTTPException(status_code=400, detail="invalid JSON payload") from error
        observation = parse_github_check_run(payload)
        delivery = container.delivery_service()
        if change_set_id is None or repository_id is None:
            matches = []
            for profile in await container.repository_catalog.list():
                try:
                    repository = parse_repository_ref(profile.url)
                except ValueError:
                    continue
                if repository == observation.repository:
                    matches.append(profile)
            if not matches:
                raise HTTPException(
                    status_code=404,
                    detail="GitHub repository is not registered",
                )
            if len(matches) > 1:
                raise HTTPException(
                    status_code=409,
                    detail="GitHub repository identity is ambiguous",
                )
            try:
                routed, repository_id = await delivery.resolve_candidate(
                    matches[0].id, observation.head_sha
                )
            except DeliveryNotFound as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except DeliveryConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            change_set_id = routed.id
        coordinator = ChangeSetSCMCoordinator(
            delivery, container.repository_catalog, None
        )
        result = await coordinator.record_github_ci(
            change_set_id,
            repository_id,
            observation,
        )
        await events.complete(x_github_delivery)
        return {"accepted": True, "duplicate": False, "change_set": asdict(result)}
    except Exception:
        await events.release(x_github_delivery)
        raise
