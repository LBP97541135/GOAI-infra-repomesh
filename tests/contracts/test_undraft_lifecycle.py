import json
from uuid import uuid4

import httpx
import pytest

from repomesh.integrations.scm.command_dispatcher import SCMCommandDispatcher
from repomesh.integrations.scm.contracts import (
    PullRequestObservation,
    PullRequestState,
    RepositoryRef,
    SCMAuthenticationError,
    SCMConflict,
    SCMNotFound,
    SCMProvider,
    SCMRateLimited,
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


NODE_ID = "PR_kwDOAbCdEf4AbCdEf"


def pull_payload(*, draft: bool = True, state: str = "open") -> dict:
    return {
        "number": 42,
        "node_id": NODE_ID,
        "html_url": "https://github.com/acme/pricing/pull/42",
        "state": state,
        "draft": draft,
        "merged_at": None,
        "head": {"ref": "repomesh/pricing", "sha": "a" * 40},
        "base": {"ref": "main", "sha": "b" * 40},
        "mergeable": True,
    }


def mark_ready_payload(*, is_draft: bool = False) -> dict:
    return {
        "data": {
            "markPullRequestReadyForReview": {
                "pullRequest": {"number": 42, "isDraft": is_draft}
            }
        }
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


def undraft_transport(
    requests: list[httpx.Request],
    *,
    draft: bool = True,
    state: str = "open",
    graphql: dict | None = None,
) -> httpx.AsyncClient:
    """REST reads answer with the PR, /graphql answers with ``graphql``."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/graphql"):
            return httpx.Response(200, json=graphql if graphql is not None else {})
        return httpx.Response(200, json=pull_payload(draft=draft, state=state))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_ready_for_review_promotes_through_the_graphql_mutation() -> None:
    """REST has no way to undraft; the mutation is the only thing that works."""

    requests: list[httpx.Request] = []
    client = undraft_transport(requests, graphql=mark_ready_payload())
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    result = await adapter.ready_for_review(repository(), 42, idempotency_key="undraft:1")

    assert [request.method for request in requests] == ["GET", "POST"]
    assert requests[0].url.path == "/repos/acme/pricing/pulls/42"
    assert requests[1].url.path == "/graphql"
    body = json.loads(requests[1].content)
    assert "markPullRequestReadyForReview" in body["query"]
    # The node id is a variable, never interpolated into the query text.
    assert body["variables"] == {"pullRequestId": NODE_ID}
    assert NODE_ID not in body["query"]
    assert requests[1].headers["Authorization"] == "Bearer installation-token"
    # The PATCH that silently did nothing is gone.
    assert "PATCH" not in [request.method for request in requests]
    assert result.draft is False
    assert result.number == 42
    await client.aclose()


@pytest.mark.asyncio
async def test_a_mutation_that_leaves_the_pr_draft_is_not_a_success() -> None:
    """The defect this method exists to stop: 200 OK, nothing changed.

    GitHub answered 200 to ``PATCH {"draft": false}`` for as long as RepoMesh
    has had this adapter, and every undraft on every path reported success
    while the pull request stayed draft. A promotion is only promoted when the
    provider says so.
    """

    requests: list[httpx.Request] = []
    client = undraft_transport(requests, graphql=mark_ready_payload(is_draft=True))
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    with pytest.raises(SCMConflict, match="still a draft"):
        await adapter.ready_for_review(repository(), 42, idempotency_key="undraft:1")
    await client.aclose()


@pytest.mark.asyncio
async def test_a_mutation_answering_without_the_pull_request_is_not_a_success() -> None:
    requests: list[httpx.Request] = []
    client = undraft_transport(
        requests, graphql={"data": {"markPullRequestReadyForReview": None}}
    )
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    with pytest.raises(SCMConflict, match="still a draft"):
        await adapter.ready_for_review(repository(), 42, idempotency_key="undraft:1")
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("NOT_FOUND", SCMNotFound),
        ("FORBIDDEN", SCMAuthenticationError),
        ("UNAUTHORIZED", SCMAuthenticationError),
        ("RATE_LIMITED", SCMRateLimited),
        ("UNPROCESSABLE", SCMConflict),
        ("", SCMConflict),
    ],
)
async def test_graphql_errors_arrive_as_200_and_are_mapped_honestly(
    kind: str, expected: type[Exception]
) -> None:
    """GraphQL reports failures inside a 200, so status code alone says nothing."""

    requests: list[httpx.Request] = []
    client = undraft_transport(
        requests,
        graphql={
            "data": None,
            "errors": [{"type": kind, "message": "Could not resolve to a node"}],
        },
    )
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    with pytest.raises(expected) as error:
        await adapter.ready_for_review(repository(), 42, idempotency_key="undraft:1")
    # GitHub's own words survive the mapping -- on SCMNotFound they live in
    # ``detail``, the same place its REST 404s put them.
    carried = f"{error.value} {getattr(error.value, 'detail', '')}"
    assert "Could not resolve to a node" in carried
    await client.aclose()


@pytest.mark.asyncio
async def test_ready_for_review_is_noop_when_pr_not_draft() -> None:
    requests: list[httpx.Request] = []
    client = undraft_transport(requests, draft=False)
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    result = await adapter.ready_for_review(repository(), 42, idempotency_key="undraft:1")

    assert [request.method for request in requests] == ["GET"]
    assert not [request for request in requests if request.url.path.endswith("/graphql")]
    assert result.draft is False
    await client.aclose()


@pytest.mark.asyncio
async def test_ready_for_review_leaves_a_closed_pull_request_alone() -> None:
    requests: list[httpx.Request] = []
    client = undraft_transport(requests, state="closed")
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    result = await adapter.ready_for_review(repository(), 42, idempotency_key="undraft:1")

    assert [request.method for request in requests] == ["GET"]
    assert result.state is PullRequestState.CLOSED
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
