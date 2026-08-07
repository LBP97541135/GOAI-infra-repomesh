import pytest

from repomesh.integrations.scm.contracts import SCMConflict
from repomesh.integrations.scm.webhook_store import InMemorySCMWebhookEventStore


@pytest.mark.asyncio
async def test_completed_webhook_delivery_is_idempotent() -> None:
    store = InMemorySCMWebhookEventStore()

    assert await store.begin("delivery-1", "a" * 64)
    await store.complete("delivery-1")
    assert not await store.begin("delivery-1", "a" * 64)


@pytest.mark.asyncio
async def test_failed_webhook_delivery_can_be_retried() -> None:
    store = InMemorySCMWebhookEventStore()

    assert await store.begin("delivery-2", "b" * 64)
    await store.release("delivery-2")
    assert await store.begin("delivery-2", "b" * 64)


@pytest.mark.asyncio
async def test_delivery_id_cannot_be_reused_for_another_payload() -> None:
    store = InMemorySCMWebhookEventStore()
    assert await store.begin("delivery-3", "c" * 64)

    with pytest.raises(SCMConflict, match="reused"):
        await store.begin("delivery-3", "d" * 64)
