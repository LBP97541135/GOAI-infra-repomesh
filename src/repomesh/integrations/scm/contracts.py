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
class BranchProtectionObservation:
    required_checks: tuple[str, ...]
    required_approvals: int
    dismisses_stale_reviews: bool
    requires_conversation_resolution: bool
    protected: bool = True
    """False when the base branch has no protection rule configured at all.

    A branch with no protection and a branch protected by a rule that happens
    to demand nothing both read as empty requirements, so the two need a flag
    to stay apart: only the first means "the remote was never asked to enforce
    anything", which the delivery preflight must not read as a misconfigured
    gate.
    """

    @classmethod
    def unprotected(cls) -> "BranchProtectionObservation":
        return cls(
            required_checks=(),
            required_approvals=0,
            dismisses_stale_reviews=False,
            requires_conversation_resolution=False,
            protected=False,
        )


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
    async def get_branch_protection(
        self, repository: RepositoryRef, branch: str
    ) -> BranchProtectionObservation: ...

    async def get_branch_head(self, repository: RepositoryRef, branch: str) -> str | None:
        """The commit a remote branch points at, or None when it does not exist.

        Delivery needs to tell "the branch was pushed" from "the branch was
        never pushed" without a workspace: the reconciler holds a ChangeSet,
        not a checkout, so ``BranchPublisher`` -- which reads the remote through
        a local clone -- is out of reach there.
        """
        ...

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

    async def ready_for_review(
        self, repository: RepositoryRef, number: int, *, idempotency_key: str
    ) -> PullRequestObservation: ...

    async def update_pull_request(
        self, repository: RepositoryRef, number: int, *, body: str, idempotency_key: str
    ) -> PullRequestObservation: ...

    async def add_label(
        self, repository: RepositoryRef, number: int, label: str, *, idempotency_key: str
    ) -> None: ...

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
    """A 404 from the provider.

    ``detail`` carries the provider's own message for the 404, because the
    status code alone cannot say *what* was not found: GitHub answers 404 both
    for a repository that does not exist and for a branch that merely has no
    protection rule, and only the body tells the two apart.
    """

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail
