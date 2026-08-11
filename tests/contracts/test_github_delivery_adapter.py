import hashlib
import hmac

import httpx
import pytest

from repomesh.integrations.scm.contracts import (
    CreateDraftPullRequestCommand,
    MergePullRequestCommand,
    PullRequestState,
    RepositoryRef,
    SCMConflict,
    SCMProvider,
    SCMRateLimited,
)
from repomesh.integrations.scm.github import GitHubAdapter, verify_github_webhook


def repository() -> RepositoryRef:
    return RepositoryRef(SCMProvider.GITHUB, "acme", "pricing")


def pull_payload(*, sha: str = "a" * 40, state: str = "open") -> dict:
    return {
        "number": 42,
        "html_url": "https://github.com/acme/pricing/pull/42",
        "state": state,
        "draft": True,
        "merged_at": None,
        "head": {"ref": "repomesh/pricing", "sha": sha},
        "base": {"ref": "main", "sha": "b" * 40},
        "mergeable": True,
    }


def command() -> CreateDraftPullRequestCommand:
    return CreateDraftPullRequestCommand(
        repository=repository(),
        head_branch="repomesh/pricing",
        base_branch="main",
        expected_head_sha="a" * 40,
        title="Update pricing",
        body="RepoMesh ChangeSet",
        idempotency_key="changeset:1:pricing",
    )


@pytest.mark.asyncio
async def test_draft_pr_creation_is_idempotent_by_remote_head() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[pull_payload()])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    result = await adapter.create_draft_pull_request(command())

    assert result.number == 42
    assert result.state is PullRequestState.OPEN
    assert [request.method for request in requests] == ["GET"]
    await client.aclose()


@pytest.mark.asyncio
async def test_created_pr_must_match_frozen_candidate_sha() -> None:
    responses = [httpx.Response(200, json=[]), httpx.Response(201, json=pull_payload(sha="c" * 40))]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    with pytest.raises(SCMConflict, match="head SHA"):
        await adapter.create_draft_pull_request(command())
    await client.aclose()


@pytest.mark.asyncio
async def test_rate_limit_is_classified_with_retry_after() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "30"})
        )
    )
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    with pytest.raises(SCMRateLimited) as captured:
        await adapter.get_pull_request(repository(), 42)
    assert captured.value.retry_after_seconds == 30
    await client.aclose()


def test_webhook_signature_verification() -> None:
    body = b'{"action":"opened"}'
    digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    assert verify_github_webhook("secret", body, f"sha256={digest}")
    assert not verify_github_webhook("secret", body, "sha256=wrong")


@pytest.mark.asyncio
async def test_update_pull_request_patches_body() -> None:
    import json

    requests: list[httpx.Request] = []
    responses = iter(
        [
            httpx.Response(200, json=pull_payload()),
            httpx.Response(200, json=pull_payload()),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return next(responses)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    result = await adapter.update_pull_request(
        repository(), 42, body="## Sibling PRs", idempotency_key="body:1"
    )

    assert [request.method for request in requests] == ["GET", "PATCH"]
    assert json.loads(requests[1].content) == {"body": "## Sibling PRs"}
    assert result.number == 42
    await client.aclose()


@pytest.mark.asyncio
async def test_add_label_posts_labels() -> None:
    import json

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    await adapter.add_label(repository(), 42, "changeset/abc123", idempotency_key="label:1")

    assert [request.method for request in requests] == ["POST"]
    assert requests[0].url.path.endswith("/issues/42/labels")
    assert json.loads(requests[0].content) == {"labels": ["changeset/abc123"]}
    await client.aclose()


@pytest.mark.asyncio
async def test_branch_protection_is_normalized_for_delivery_preflight() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "required_status_checks": {
                        "checks": [{"context": "Unit-Tests"}],
                        "contexts": ["lint"],
                    },
                    "required_pull_request_reviews": {
                        "required_approving_review_count": 2,
                        "dismiss_stale_reviews": True,
                    },
                    "required_conversation_resolution": {"enabled": True},
                },
            )
        )
    )
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    protection = await adapter.get_branch_protection(repository(), "main")

    assert protection.required_checks == ("lint", "unit-tests")
    assert protection.required_approvals == 2
    assert protection.dismisses_stale_reviews
    assert protection.requires_conversation_resolution
    await client.aclose()


@pytest.mark.asyncio
async def test_merge_uses_frozen_head_sha_as_github_guard() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"merged": True, "sha": "d" * 40, "message": "merged"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    result = await adapter.merge_pull_request(
        MergePullRequestCommand(repository(), 42, "a" * 40, "Update pricing")
    )

    assert result.merge_sha == "d" * 40
    assert b'"sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"' in requests[0].content
    await client.aclose()


@pytest.mark.asyncio
async def test_reconciliation_reads_check_runs_and_actionable_reviews() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/check-runs"):
            assert request.url.params["per_page"] == "100"
            assert request.url.params["filter"] == "latest"
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "id": 501,
                            "name": "Unit-Tests",
                            "head_sha": "a" * 40,
                            "status": "completed",
                            "conclusion": "success",
                            "output": {"summary": "204 tests passed"},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json=[
                {
                    "id": 601,
                    "user": {"login": "Reviewer-One"},
                    "commit_id": "a" * 40,
                    "state": "APPROVED",
                    "body": "looks good",
                },
                {
                    "id": 602,
                    "user": {"login": "observer"},
                    "commit_id": "a" * 40,
                    "state": "COMMENTED",
                    "body": "non-gating comment",
                },
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    checks = await adapter.list_check_runs(repository(), "a" * 40)
    reviews = await adapter.list_pull_request_reviews(repository(), 42)

    assert checks[0].check_name == "unit-tests"
    assert checks[0].passed
    assert [review.reviewer for review in reviews] == ["reviewer-one"]
    await client.aclose()


@pytest.mark.asyncio
async def test_merged_pull_request_exposes_remote_merge_sha() -> None:
    payload = pull_payload()
    payload.update(merged_at="2026-08-09T00:00:00Z", merge_commit_sha="d" * 40)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)

    observation = await adapter.get_pull_request(repository(), 42)

    assert observation.state is PullRequestState.MERGED
    assert observation.merge_sha == "d" * 40
    await client.aclose()
