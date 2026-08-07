import hashlib
import hmac
import json
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from repomesh.api.scm_webhook import router
from repomesh.integrations.scm.webhook_store import InMemorySCMWebhookEventStore
from repomesh.modules.delivery import DeliveryService, InMemoryChangeSetStore
from repomesh.modules.delivery.contracts import (
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RepositoryCandidateInput,
)
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import InMemoryRepositoryCatalog
from repomesh.settings import get_settings


class Container:
    def __init__(self, delivery, catalog, events) -> None:
        self._delivery = delivery
        self.repository_catalog = catalog
        self._events = events

    def delivery_service(self):
        return self._delivery

    def scm_webhook_event_store(self):
        return self._events


@pytest.mark.asyncio
async def test_signed_check_run_routes_without_internal_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REPOMESH_GITHUB_WEBHOOK_SECRET", "webhook-secret")
    get_settings.cache_clear()
    repository_id = uuid4()
    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(
            id=repository_id,
            name="pricing",
            url="https://github.com/acme/pricing",
        )
    )
    delivery = DeliveryService(InMemoryChangeSetStore())
    change_set = await delivery.prepare(
        PrepareChangeSetCommand(
            organization_id=uuid4(),
            project_id=uuid4(),
            created_by_agent_id=uuid4(),
            title="Routed delivery",
            validation_snapshot_id=uuid4(),
            candidates=(
                RepositoryCandidateInput(
                    repository_id=repository_id,
                    task_id=uuid4(),
                    commit_sha="a" * 40,
                    base_sha="b" * 40,
                    branch_name="repomesh/routed",
                    required_checks=("unit-test",),
                ),
            ),
        ),
        idempotency_key="routed-delivery",
    )
    await delivery.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id,
            repository_id,
            17,
            "https://github.com/acme/pricing/pull/17",
            "a" * 40,
        )
    )
    payload = {
        "repository": {"name": "pricing", "owner": {"login": "acme"}},
        "check_run": {
            "id": 91,
            "name": "unit-test",
            "head_sha": "a" * 40,
            "status": "completed",
            "conclusion": "success",
            "output": {"summary": "all tests passed"},
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
    app = FastAPI()
    app.state.container = Container(
        delivery, catalog, InMemorySCMWebhookEventStore()
    )
    app.include_router(router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/delivery/github-webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Delivery": "delivery-91",
                "X-GitHub-Event": "check_run",
                "X-Hub-Signature-256": f"sha256={signature}",
            },
        )

    get_settings.cache_clear()
    assert response.status_code == 200
    assert response.json()["change_set"]["id"] == str(change_set.id)
    current = await delivery.get(change_set.id)
    assert current.repositories[0].status.value == "ready_to_merge"
