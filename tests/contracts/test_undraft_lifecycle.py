import json
from uuid import uuid4

import httpx
import pytest

from repomesh.integrations.scm.command_dispatcher import SCMCommandDispatcher
from repomesh.integrations.scm.contracts import (
    PullRequestObservation,
    PullRequestState,
    RepositoryRef,
    SCMProvider,
)
from repomesh.integrations.scm.delivery import ChangeSetSCMCoordinator
from repomesh.integrations.scm.github import GitHubAdapter
from repomesh.modules.delivery import DeliveryService, InMemoryChangeSetStore, SCMCommandService
from repomesh.modules.delivery.contracts import (
    CIObservationCommand,
    EnqueueSCMCommand,
    MergeObservationCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RepositoryCandidateInput,
    SCMCommandKind,
)
from repomesh.modules.delivery.infrastructure import InMemorySCMCommandStore
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import InMemoryRepositoryCatalog


def repository() -> RepositoryRef:
    return RepositoryRef(SCMProvider.GITHUB, "acme", "pricing")


def pull_payload(*, draft: bool = True, state: str = "open") -> dict:
    return {
        "number": 42,
        "html_url": "https://github.com/acme/pricing/pull/42",
        "state": state,
        "draft": draft,
        "merged_at": None,
        "head": {"ref": "repomesh/pricing", "sha": "a" * 40},
        "base": {"ref": "main", "sha": "b" * 40},
        "mergeable": True,
    }


async def prepare_change_set(delivery: DeliveryService, candidates) -> object:
    return await delivery.prepare(
        PrepareChangeSetCommand(
            organization_id=uuid4(),
            project_id=uuid4(),
            created_by_agent_id=uuid4(),
            title="Draft lifecycle",
            validation_snapshot_id=uuid4(),
            candidates=tuple(candidates),
        ),
        idempotency_key=str(uuid4()),
    )


def candidate(
    repository_id, *, depends_on=(), branch="repomesh/candidate"
) -> RepositoryCandidateInput:
    return RepositoryCandidateInput(
        repository_id=repository_id,
        task_id=uuid4(),
        commit_sha="a" * 40,
        base_sha="b" * 40,
        branch_name=branch,
        depends_on=tuple(depends_on),
    )


class FakeCommandService:
    def __init__(self) -> None:
        self.commands: list[EnqueueSCMCommand] = []

    async def enqueue(self, command: EnqueueSCMCommand) -> None:
        self.commands.append(command)


@pytest.mark.asyncio
async def test_ready_for_review_patches_draft_false() -> None:
    requests: list[httpx.Request] = []
    responses = iter([pull_payload(draft=True), pull_payload(draft=False)])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=next(responses))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    result = await adapter.ready_for_review(repository(), 42, idempotency_key="undraft:1")

    assert [request.method for request in requests] == ["GET", "PATCH"]
    assert json.loads(requests[1].content) == {"draft": False}
    assert result.draft is False
    await client.aclose()


@pytest.mark.asyncio
async def test_ready_for_review_is_noop_when_pr_not_draft() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=pull_payload(draft=False))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    result = await adapter.ready_for_review(repository(), 42, idempotency_key="undraft:1")

    assert [request.method for request in requests] == ["GET"]
    assert result.draft is False
    await client.aclose()


@pytest.mark.asyncio
async def test_undraft_issues_command_for_open_pr_with_merged_dependencies() -> None:
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
    change_set = await prepare_change_set(delivery, [candidate(repository_id)])
    await delivery.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id, repository_id, 42, "https://github.com/acme/pricing/pull/42", "a" * 40
        )
    )
    commands = FakeCommandService()
    coordinator = ChangeSetSCMCoordinator(
        delivery, catalog, None, command_service=commands
    )

    await coordinator.undraft_when_allowed(change_set.id)

    assert len(commands.commands) == 1
    command = commands.commands[0]
    assert command.kind is SCMCommandKind.UNDRAFT_PULL_REQUEST
    assert command.payload["pull_request_number"] == 42
    assert command.payload["expected_head_sha"] == "a" * 40


@pytest.mark.asyncio
async def test_undraft_waits_for_unmerged_dependency() -> None:
    producer = uuid4()
    consumer = uuid4()
    catalog = InMemoryRepositoryCatalog()
    for profile in (
        RepositoryProfile(id=producer, name="api", url="https://github.com/acme/api"),
        RepositoryProfile(
            id=consumer,
            name="pricing",
            url="https://github.com/acme/pricing",
        ),
    ):
        await catalog.add(profile)
    delivery = DeliveryService(InMemoryChangeSetStore())
    change_set = await prepare_change_set(
        delivery,
        [
            candidate(producer, branch="repomesh/api"),
            candidate(consumer, depends_on=(producer,)),
        ],
    )
    await delivery.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id, consumer, 43, "https://github.com/acme/pricing/pull/43", "a" * 40
        )
    )
    commands = FakeCommandService()
    coordinator = ChangeSetSCMCoordinator(
        delivery, catalog, None, command_service=commands
    )

    await coordinator.undraft_when_allowed(change_set.id)
    assert commands.commands == []

    await delivery.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id, producer, 42, "https://github.com/acme/api/pull/42", "a" * 40
        )
    )
    await delivery.observe_ci(
        CIObservationCommand(change_set.id, producer, True, "ci-producer", "passed")
    )
    await delivery.observe_merge(
        MergeObservationCommand(change_set.id, producer, "c" * 40)
    )
    await coordinator.undraft_when_allowed(change_set.id)

    assert len(commands.commands) == 1
    assert commands.commands[0].kind is SCMCommandKind.UNDRAFT_PULL_REQUEST
    assert commands.commands[0].repository_id == consumer


class DispatchAdapter:
    def __init__(self, *, draft: bool = True) -> None:
        self.draft = draft
        self.ready_calls: list[tuple[RepositoryRef, int, str]] = []

    async def get_pull_request(
        self, repository: RepositoryRef, number: int
    ) -> PullRequestObservation:
        return PullRequestObservation(
            provider=SCMProvider.GITHUB,
            repository=repository,
            number=number,
            url=f"https://github.com/acme/pricing/pull/{number}",
            state=PullRequestState.OPEN,
            draft=self.draft,
            head_branch="repomesh/pricing",
            head_sha="a" * 40,
            base_branch="main",
            base_sha="b" * 40,
            mergeable=True,
        )

    async def ready_for_review(
        self, repository: RepositoryRef, number: int, *, idempotency_key: str
    ) -> PullRequestObservation:
        self.ready_calls.append((repository, number, idempotency_key))
        return await self.get_pull_request(repository, number)


async def enqueue_undraft_command(delivery: DeliveryService, repository_id) -> object:
    store = InMemorySCMCommandStore()
    commands = SCMCommandService(store)
    await commands.enqueue(
        EnqueueSCMCommand(
            change_set_id=uuid4(),
            repository_id=repository_id,
            kind=SCMCommandKind.UNDRAFT_PULL_REQUEST,
            idempotency_key="undraft:dispatch:1",
            payload={
                "pull_request_number": 42,
                "expected_head_sha": "a" * 40,
            },
        )
    )
    return commands


@pytest.mark.asyncio
async def test_dispatcher_calls_ready_for_review_for_undraft_command() -> None:
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
    commands = await enqueue_undraft_command(delivery, repository_id)
    adapter = DispatchAdapter(draft=True)
    dispatcher = SCMCommandDispatcher(commands, delivery, catalog, adapter)

    await dispatcher.run_once()

    assert len(adapter.ready_calls) == 1
    assert adapter.ready_calls[0][1] == 42


@pytest.mark.asyncio
async def test_dispatcher_skips_ready_for_review_when_already_undrafted() -> None:
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
    commands = await enqueue_undraft_command(delivery, repository_id)
    adapter = DispatchAdapter(draft=False)
    dispatcher = SCMCommandDispatcher(commands, delivery, catalog, adapter)

    await dispatcher.run_once()

    assert adapter.ready_calls == []
