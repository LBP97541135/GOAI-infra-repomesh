import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from repomesh.integrations.scm.command_dispatcher import SCMCommandDispatcher
from repomesh.integrations.scm.contracts import (
    MergePullRequestResult,
    PullRequestObservation,
    PullRequestState,
    SCMProvider,
)
from repomesh.modules.delivery import (
    DeliveryConflict,
    DeliveryService,
    InMemoryChangeSetStore,
    InMemorySCMCommandStore,
    SCMCommandService,
)
from repomesh.modules.delivery.contracts import (
    EnqueueSCMCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RepositoryCandidateInput,
    SCMCommandKind,
    SCMCommandStatus,
)
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import InMemoryRepositoryCatalog


class MergeAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def get_pull_request(self, repository, number):
        return PullRequestObservation(
            SCMProvider.GITHUB,
            repository,
            number,
            f"https://github.com/acme/pricing/pull/{number}",
            PullRequestState.OPEN,
            False,
            "repomesh/pricing",
            "a" * 40,
            "main",
            "b" * 40,
            True,
        )

    async def merge_pull_request(self, command):
        self.calls += 1
        return MergePullRequestResult(True, "d" * 40, "accepted")


async def setup_dispatcher():
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
            uuid4(),
            uuid4(),
            uuid4(),
            "Merge pricing",
            uuid4(),
            (
                RepositoryCandidateInput(
                    repository_id,
                    uuid4(),
                    "a" * 40,
                    "b" * 40,
                    "repomesh/pricing",
                ),
            ),
        ),
        idempotency_key="dispatch-merge",
    )
    await delivery.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id,
            repository_id,
            42,
            "https://github.com/acme/pricing/pull/42",
            "a" * 40,
        )
    )
    # No configured checks or reviews: one successful check makes the candidate ready.
    from repomesh.modules.delivery.contracts import CIObservationCommand

    await delivery.observe_ci(
        CIObservationCommand(change_set.id, repository_id, True, "1", "passed", "unit")
    )
    store = InMemorySCMCommandStore()
    commands = SCMCommandService(store)
    queued = await commands.enqueue(
        EnqueueSCMCommand(
            change_set.id,
            repository_id,
            SCMCommandKind.MERGE_PULL_REQUEST,
            f"merge:{change_set.id}:{repository_id}:{'a' * 40}",
            {
                "pull_request_number": 42,
                "expected_head_sha": "a" * 40,
                "commit_title": "Merge pricing",
            },
        )
    )
    adapter = MergeAdapter()
    dispatcher = SCMCommandDispatcher(commands, delivery, catalog, adapter)
    return dispatcher, commands, delivery, change_set.id, queued.id, adapter


@pytest.mark.asyncio
async def test_dispatcher_accepts_command_then_waits_for_remote_observation() -> None:
    dispatcher, commands, delivery, change_set_id, command_id, adapter = await setup_dispatcher()

    await dispatcher.run_once()

    assert adapter.calls == 1
    assert (await commands._required(command_id)).status is SCMCommandStatus.ACCEPTED
    result = await delivery.get(change_set_id)
    assert result.repositories[0].status.value == "merge_requested"
    assert result.status.value != "delivered"


@pytest.mark.asyncio
async def test_command_enqueue_is_idempotent_and_rejects_changed_meaning() -> None:
    _, commands, _, change_set_id, command_id, _ = await setup_dispatcher()
    original = await commands._required(command_id)

    duplicate = await commands.enqueue(
        EnqueueSCMCommand(
            original.change_set_id,
            original.repository_id,
            original.kind,
            original.idempotency_key,
            original.payload,
        )
    )
    assert duplicate.id == command_id

    with pytest.raises(Exception, match="changed meaning"):
        await commands.enqueue(
            EnqueueSCMCommand(
                original.change_set_id,
                original.repository_id,
                original.kind,
                original.idempotency_key,
                {**original.payload, "pull_request_number": 99},
            )
        )


@pytest.mark.asyncio
async def test_atomic_claim_fences_stale_owner() -> None:
    _, commands, _, _, command_id, _ = await setup_dispatcher()

    first = (await commands.claim_batch("dispatcher-a", limit=1))[0]
    assert first.lease_owner == "dispatcher-a"
    assert await commands.claim_batch("dispatcher-b", limit=1) == ()

    renewed = await commands.renew(first.id, "dispatcher-a", first.version)
    assert renewed.version == first.version
    assert renewed.lease_expires_at is not None

    with pytest.raises(DeliveryConflict, match="ownership"):
        await commands.accept(command_id, "dispatcher-b", first.version)

    accepted = await commands.accept(command_id, "dispatcher-a", first.version)
    assert accepted.status is SCMCommandStatus.ACCEPTED
    assert accepted.lease_owner is None
    assert accepted.lease_expires_at is None


@pytest.mark.asyncio
async def test_two_dispatchers_mutate_provider_once() -> None:
    dispatcher, commands, _, _, command_id, adapter = await setup_dispatcher()
    second = SCMCommandDispatcher(
        commands,
        dispatcher._delivery,
        dispatcher._catalog,
        adapter,
        lease_owner="dispatcher-b",
    )
    dispatcher._lease_owner = "dispatcher-a"

    await asyncio.gather(dispatcher.run_once(), second.run_once())

    assert adapter.calls == 1
    assert (await commands._required(command_id)).status is SCMCommandStatus.ACCEPTED


@pytest.mark.asyncio
async def test_attempt_limit_prevents_automatic_reclaim() -> None:
    _, commands, _, _, command_id, _ = await setup_dispatcher()
    current = await commands._required(command_id)
    commands._store.items[command_id] = replace(current, attempts=8)

    assert await commands.claim_batch("dispatcher-a", limit=1) == ()
