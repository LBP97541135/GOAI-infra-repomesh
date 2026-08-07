import hashlib
import hmac
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .contracts import (
    CreateDraftPullRequestCommand,
    MergePullRequestCommand,
    MergePullRequestResult,
    PullRequestObservation,
    PullRequestState,
    RepositoryRef,
    SCMAuthenticationError,
    SCMConflict,
    SCMNotFound,
    SCMProvider,
    SCMRateLimited,
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
                "draft": True,
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

    async def merge_pull_request(
        self, command: MergePullRequestCommand
    ) -> MergePullRequestResult:
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
                f"merge:{command.repository.owner}:{command.repository.name}:"
                f"{command.number}:{sha}"
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
        )

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
