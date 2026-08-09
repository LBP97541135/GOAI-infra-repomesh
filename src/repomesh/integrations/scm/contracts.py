from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class SCMProvider(StrEnum):
    GITHUB = "github"
    GITLAB = "gitlab"


class PullRequestState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


@dataclass(frozen=True, slots=True)
class RepositoryRef:
    provider: SCMProvider
    owner: str
    name: str
    api_base: str = "https://api.github.com"

    @classmethod
    def from_github(cls, owner: str, name: str) -> "RepositoryRef":
        return cls(SCMProvider.GITHUB, owner, name)


@dataclass(frozen=True, slots=True)
class CreateDraftPullRequestCommand:
    repository: RepositoryRef
    head_branch: str
    base_branch: str
    expected_head_sha: str
    title: str
    body: str
    idempotency_key: str
    draft: bool = True


@dataclass(frozen=True, slots=True)
class PullRequestObservation:
    provider: SCMProvider
    repository: RepositoryRef
    number: int
    url: str
    state: PullRequestState
    draft: bool
    head_branch: str
    head_sha: str
    base_branch: str
    base_sha: str
    mergeable: bool | None
    merge_sha: str | None = None


class SCMReviewState(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    DISMISSED = "dismissed"


@dataclass(frozen=True, slots=True)
class CheckRunObservation:
    check_run_id: str
    check_name: str
    head_sha: str
    terminal: bool
    passed: bool
    summary: str


@dataclass(frozen=True, slots=True)
class PullRequestReviewObservation:
    review_id: str
    reviewer: str
    head_sha: str
    state: SCMReviewState
    summary: str = ""


@dataclass(frozen=True, slots=True)
class PublishBranchCommand:
    workspace: Path
    branch_name: str
    expected_head_sha: str
    expected_remote_sha: str | None = None
    repository: RepositoryRef | None = None


@dataclass(frozen=True, slots=True)
class PublishedBranch:
    branch_name: str
    head_sha: str
    remote_sha: str


@dataclass(frozen=True, slots=True)
class MergePullRequestCommand:
    repository: RepositoryRef
    number: int
    expected_head_sha: str
    commit_title: str


@dataclass(frozen=True, slots=True)
class MergePullRequestResult:
    merged: bool
    merge_sha: str
    message: str


class BranchPublisher(Protocol):
    async def publish(self, command: PublishBranchCommand) -> PublishedBranch: ...


class SCMAdapter(Protocol):
    async def create_draft_pull_request(
        self, command: CreateDraftPullRequestCommand
    ) -> PullRequestObservation: ...

    async def get_pull_request(
        self, repository: RepositoryRef, number: int
    ) -> PullRequestObservation: ...

    async def list_check_runs(
        self, repository: RepositoryRef, head_sha: str
    ) -> tuple[CheckRunObservation, ...]: ...

    async def list_pull_request_reviews(
        self, repository: RepositoryRef, number: int
    ) -> tuple[PullRequestReviewObservation, ...]: ...

    async def close_pull_request(
        self, repository: RepositoryRef, number: int, *, idempotency_key: str
    ) -> PullRequestObservation: ...

    async def merge_pull_request(
        self, command: MergePullRequestCommand
    ) -> MergePullRequestResult: ...


class SCMError(RuntimeError):
    pass


class SCMAuthenticationError(SCMError):
    pass


class SCMConflict(SCMError):
    pass


class SCMRateLimited(SCMError):
    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class SCMNotFound(SCMError):
    pass
