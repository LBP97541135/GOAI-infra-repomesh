import hashlib
import hmac
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .contracts import (
    BranchProtectionObservation,
    CheckRunObservation,
    CreateDraftPullRequestCommand,
    MergePullRequestCommand,
    MergePullRequestResult,
    PullRequestObservation,
    PullRequestReviewObservation,
    PullRequestState,
    RepositoryRef,
    SCMAuthenticationError,
    SCMConflict,
    SCMNotFound,
    SCMProvider,
    SCMRateLimited,
    SCMReviewState,
)


class GitHubAdapter:
    """GitHub delivery adapter restricted to draft PRs and reconciliation."""

    def __init__(
        self,
        token_provider: Callable[[RepositoryRef], str | Awaitable[str]],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=20)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_draft_pull_request(
        self, command: CreateDraftPullRequestCommand
    ) -> PullRequestObservation:
        self._validate(command)
        existing = await self._find_by_head(command.repository, command.head_branch)
        if existing is not None:
            if (
                existing.head_sha != command.expected_head_sha.lower()
                or existing.base_branch != command.base_branch
            ):
                raise SCMConflict("existing PR does not match the frozen ChangeSet candidate")
            return existing
        payload = await self._request(
            "POST",
            command.repository,
            "/pulls",
            body={
                "title": command.title.strip(),
                "head": command.head_branch,
                "base": command.base_branch,
                "body": command.body,
                "draft": command.draft,
            },
            idempotency_key=command.idempotency_key,
        )
        result = self._observation(command.repository, payload)
        if result.head_sha != command.expected_head_sha.lower():
            raise SCMConflict("created PR head SHA differs from the frozen candidate")
        return result

    async def get_pull_request(
        self, repository: RepositoryRef, number: int
    ) -> PullRequestObservation:
        return self._observation(
            repository, await self._request("GET", repository, f"/pulls/{number}")
        )

    async def get_branch_protection(
        self, repository: RepositoryRef, branch: str
    ) -> BranchProtectionObservation:
        payload = await self._request("GET", repository, f"/branches/{branch}/protection")
        checks = payload.get("required_status_checks") or {}
        contexts = tuple(
            sorted(
                {
                    str(item.get("context") or "").strip().lower()
                    for item in checks.get("checks", ())
                    if item.get("context")
                }
                | {
                    str(item).strip().lower()
                    for item in checks.get("contexts", ())
                    if str(item).strip()
                }
            )
        )
        reviews = payload.get("required_pull_request_reviews") or {}
        return BranchProtectionObservation(
            required_checks=contexts,
            required_approvals=int(reviews.get("required_approving_review_count") or 0),
            dismisses_stale_reviews=bool(reviews.get("dismiss_stale_reviews")),
            requires_conversation_resolution=bool(
                (payload.get("required_conversation_resolution") or {}).get("enabled")
            ),
        )

    async def list_check_runs(
        self, repository: RepositoryRef, head_sha: str
    ) -> tuple[CheckRunObservation, ...]:
        normalized = self._full_sha(head_sha)
        observations = []
        page = 1
        while True:
            payload = await self._request(
                "GET",
                repository,
                f"/commits/{normalized}/check-runs",
                params={"filter": "latest", "per_page": "100", "page": str(page)},
            )
            items = payload.get("check_runs", [])
            for item in items:
                conclusion = str(item.get("conclusion") or "").lower()
                output = item.get("output") or {}
                observations.append(
                    CheckRunObservation(
                        check_run_id=str(item["id"]),
                        check_name=str(item["name"]).strip().lower(),
                        head_sha=str(item.get("head_sha") or normalized).lower(),
                        terminal=str(item.get("status") or "").lower() == "completed",
                        passed=conclusion in {"success", "neutral", "skipped"},
                        summary=str(output.get("summary") or output.get("title") or conclusion),
                    )
                )
            if len(items) < 100:
                break
            page += 1
        return tuple(observations)

    async def list_pull_request_reviews(
        self, repository: RepositoryRef, number: int
    ) -> tuple[PullRequestReviewObservation, ...]:
        observations = []
        page = 1
        while True:
            payload = await self._request(
                "GET",
                repository,
                f"/pulls/{number}/reviews",
                params={"per_page": "100", "page": str(page)},
            )
            for item in payload:
                state = str(item.get("state") or "").lower()
                if state not in {value.value for value in SCMReviewState}:
                    continue
                observations.append(
                    PullRequestReviewObservation(
                        review_id=str(item["id"]),
                        reviewer=str((item.get("user") or {}).get("login") or "").strip().lower(),
                        head_sha=str(item.get("commit_id") or "").lower(),
                        state=SCMReviewState(state),
                        summary=str(item.get("body") or ""),
                    )
                )
            if len(payload) < 100:
                break
            page += 1
        return tuple(item for item in observations if item.reviewer and item.head_sha)

    async def close_pull_request(
        self, repository: RepositoryRef, number: int, *, idempotency_key: str
    ) -> PullRequestObservation:
        current = await self.get_pull_request(repository, number)
        if current.state is not PullRequestState.OPEN:
            return current
        payload = await self._request(
            "PATCH",
            repository,
            f"/pulls/{number}",
            body={"state": "closed"},
            idempotency_key=idempotency_key,
        )
        return self._observation(repository, payload)

    async def ready_for_review(
        self, repository: RepositoryRef, number: int, *, idempotency_key: str
    ) -> PullRequestObservation:
        current = await self.get_pull_request(repository, number)
        if not current.draft or current.state is not PullRequestState.OPEN:
            return current
        payload = await self._request(
            "PATCH",
            repository,
            f"/pulls/{number}",
            body={"draft": False},
            idempotency_key=idempotency_key,
        )
        return self._observation(repository, payload)

    async def update_pull_request(
        self, repository: RepositoryRef, number: int, *, body: str, idempotency_key: str
    ) -> PullRequestObservation:
        current = await self.get_pull_request(repository, number)
        if current.state is not PullRequestState.OPEN:
            return current
        payload = await self._request(
            "PATCH",
            repository,
            f"/pulls/{number}",
            body={"body": body},
            idempotency_key=idempotency_key,
        )
        return self._observation(repository, payload)

    async def add_label(
        self, repository: RepositoryRef, number: int, label: str, *, idempotency_key: str
    ) -> None:
        await self._request(
            "POST",
            repository,
            f"/issues/{number}/labels",
            body={"labels": [label]},
            idempotency_key=idempotency_key,
        )

    async def merge_pull_request(self, command: MergePullRequestCommand) -> MergePullRequestResult:
        sha = command.expected_head_sha.strip().lower()
        if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
            raise ValueError("expected_head_sha must be a full Git object id")
        payload = await self._request(
            "PUT",
            command.repository,
            f"/pulls/{command.number}/merge",
            body={
                "sha": sha,
                "commit_title": command.commit_title.strip(),
                "merge_method": "merge",
            },
            idempotency_key=(
                f"merge:{command.repository.owner}:{command.repository.name}:{command.number}:{sha}"
            ),
        )
        merged = bool(payload.get("merged"))
        merge_sha = str(payload.get("sha") or "").lower()
        if not merged or len(merge_sha) != 40:
            raise SCMConflict(str(payload.get("message") or "GitHub did not merge the PR"))
        return MergePullRequestResult(
            merged=True,
            merge_sha=merge_sha,
            message=str(payload.get("message") or "merged"),
        )

    async def _find_by_head(
        self, repository: RepositoryRef, branch: str
    ) -> PullRequestObservation | None:
        payload = await self._request(
            "GET",
            repository,
            "/pulls",
            params={"state": "all", "head": f"{repository.owner}:{branch}"},
        )
        return self._observation(repository, payload[0]) if payload else None

    async def _request(
        self,
        method: str,
        repository: RepositoryRef,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        supplied = self._token_provider(repository)
        token = (await supplied if inspect.isawaitable(supplied) else supplied).strip()
        if not token:
            raise SCMAuthenticationError("GitHub installation token is unavailable")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if idempotency_key:
            headers["X-RepoMesh-Idempotency-Key"] = idempotency_key
        url = f"{repository.api_base.rstrip('/')}/repos/{repository.owner}/{repository.name}{path}"
        try:
            response = await self._client.request(
                method, url, headers=headers, params=params, json=body
            )
        except httpx.HTTPError as error:
            raise SCMConflict(f"GitHub request failed: {type(error).__name__}") from error
        remaining = response.headers.get("X-RateLimit-Remaining")
        if response.status_code in {401, 403} and remaining != "0":
            raise SCMAuthenticationError("GitHub rejected the installation token")
        if response.status_code == 429 or remaining == "0":
            retry = response.headers.get("Retry-After", "")
            raise SCMRateLimited(
                "GitHub rate limit exceeded",
                retry_after_seconds=int(retry) if retry.isdigit() else None,
            )
        if response.status_code == 404:
            raise SCMNotFound("GitHub repository or PR was not found")
        if response.status_code >= 400:
            try:
                message = str(response.json().get("message") or response.status_code)
            except (json.JSONDecodeError, TypeError):
                message = str(response.status_code)
            raise SCMConflict(f"GitHub rejected the operation: {message}")
        return response.json()

    @staticmethod
    def _observation(repository: RepositoryRef, payload: dict[str, Any]) -> PullRequestObservation:
        state = (
            PullRequestState.MERGED
            if payload.get("merged_at")
            else PullRequestState(payload["state"])
        )
        return PullRequestObservation(
            provider=SCMProvider.GITHUB,
            repository=repository,
            number=int(payload["number"]),
            url=str(payload["html_url"]),
            state=state,
            draft=bool(payload.get("draft")),
            head_branch=str(payload["head"]["ref"]),
            head_sha=str(payload["head"]["sha"]).lower(),
            base_branch=str(payload["base"]["ref"]),
            base_sha=str(payload["base"]["sha"]).lower(),
            mergeable=payload.get("mergeable"),
            merge_sha=(
                str(payload.get("merge_commit_sha")).lower()
                if payload.get("merged_at") and payload.get("merge_commit_sha")
                else None
            ),
        )

    @staticmethod
    def _full_sha(value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 40 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("head_sha must be a full Git object id")
        return normalized

    @staticmethod
    def _validate(command: CreateDraftPullRequestCommand) -> None:
        if command.repository.provider is not SCMProvider.GITHUB:
            raise ValueError("GitHubAdapter requires a GitHub repository")
        sha = command.expected_head_sha.strip().lower()
        if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
            raise ValueError("expected_head_sha must be a full Git object id")
        if not command.idempotency_key.strip() or not command.title.strip():
            raise ValueError("idempotency key and title are required")


def verify_github_webhook(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, f"sha256={digest}")
