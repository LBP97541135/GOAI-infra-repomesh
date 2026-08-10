from dataclasses import dataclass
from typing import Any

from repomesh.modules.delivery.contracts import ReviewState

from .contracts import (
    PullRequestObservation,
    PullRequestState,
    RepositoryRef,
    SCMConflict,
    SCMProvider,
)

_PASSING = {"success", "neutral", "skipped"}
_FAILING = {
    "failure",
    "timed_out",
    "cancelled",
    "action_required",
    "startup_failure",
    "stale",
}


@dataclass(frozen=True, slots=True)
class GitHubCIObservation:
    repository: RepositoryRef
    check_run_id: str
    check_name: str
    head_sha: str
    status: str
    conclusion: str | None
    summary: str

    @property
    def terminal(self) -> bool:
        return self.status == "completed" and self.conclusion in (_PASSING | _FAILING)

    @property
    def passed(self) -> bool:
        if not self.terminal:
            raise ValueError("CI observation is not terminal")
        return self.conclusion in _PASSING


@dataclass(frozen=True, slots=True)
class GitHubReviewObservation:
    repository: RepositoryRef
    review_id: str
    reviewer: str
    head_sha: str
    state: ReviewState
    summary: str


def parse_github_pull_request(payload: dict[str, Any]) -> PullRequestObservation:
    pull_request = payload.get("pull_request")
    repository = payload.get("repository")
    if not isinstance(pull_request, dict) or not isinstance(repository, dict):
        raise ValueError("GitHub pull_request payload is incomplete")
    owner = (repository.get("owner") or {}).get("login")
    name = repository.get("name")
    head = pull_request.get("head") or {}
    base = pull_request.get("base") or {}
    if not owner or not name or not head.get("sha") or not base.get("sha"):
        raise ValueError("GitHub pull_request binding is incomplete")
    merged = bool(pull_request.get("merged_at") or pull_request.get("merged"))
    state = PullRequestState.MERGED if merged else PullRequestState(str(pull_request["state"]))
    return PullRequestObservation(
        provider=SCMProvider.GITHUB,
        repository=RepositoryRef.from_github(str(owner), str(name)),
        number=int(pull_request["number"]),
        url=str(pull_request.get("html_url") or ""),
        state=state,
        draft=bool(pull_request.get("draft")),
        head_branch=str(head.get("ref") or ""),
        head_sha=str(head["sha"]).lower(),
        base_branch=str(base.get("ref") or ""),
        base_sha=str(base["sha"]).lower(),
        mergeable=pull_request.get("mergeable"),
        merge_sha=(
            str(pull_request.get("merge_commit_sha")).lower()
            if merged and pull_request.get("merge_commit_sha")
            else None
        ),
    )


def parse_github_check_run(payload: dict[str, Any]) -> GitHubCIObservation:
    check = payload.get("check_run")
    repository = payload.get("repository")
    if not isinstance(check, dict) or not isinstance(repository, dict):
        raise ValueError("GitHub check_run payload is incomplete")
    owner = repository.get("owner", {}).get("login")
    name = repository.get("name")
    if not owner or not name:
        raise ValueError("GitHub check_run repository identity is missing")
    output = check.get("output") or {}
    summary = str(output.get("summary") or check.get("name") or "GitHub check run")
    return GitHubCIObservation(
        repository=RepositoryRef.from_github(str(owner), str(name)),
        check_run_id=str(check["id"]),
        check_name=str(check.get("name") or check["id"]).strip().lower(),
        head_sha=str(check["head_sha"]).lower(),
        status=str(check["status"]).lower(),
        conclusion=(str(check["conclusion"]).lower() if check.get("conclusion") else None),
        summary=summary,
    )


def parse_github_pull_request_review(
    payload: dict[str, Any],
) -> GitHubReviewObservation | None:
    review = payload.get("review")
    repository = payload.get("repository")
    pull_request = payload.get("pull_request")
    if not isinstance(review, dict) or not isinstance(repository, dict):
        raise ValueError("GitHub pull_request_review payload is incomplete")
    if not isinstance(pull_request, dict):
        raise ValueError("GitHub pull request identity is missing")
    owner = repository.get("owner", {}).get("login")
    name = repository.get("name")
    reviewer = review.get("user", {}).get("login")
    state_value = str(review.get("state") or "").lower()
    if str(payload.get("action") or "").lower() == "dismissed":
        state_value = ReviewState.DISMISSED.value
    if state_value not in {state.value for state in ReviewState}:
        return None
    head = pull_request.get("head") or {}
    head_sha = str(review.get("commit_id") or head.get("sha") or "").lower()
    if not owner or not name or not reviewer or len(head_sha) != 40:
        raise ValueError("GitHub review binding is incomplete")
    return GitHubReviewObservation(
        repository=RepositoryRef.from_github(str(owner), str(name)),
        review_id=str(review["id"]),
        reviewer=str(reviewer),
        head_sha=head_sha,
        state=ReviewState(state_value),
        summary=str(review.get("body") or state_value),
    )


def validate_ci_observation(
    observation: GitHubCIObservation,
    *,
    expected_repository: RepositoryRef,
    expected_head_sha: str,
) -> None:
    if observation.repository != expected_repository:
        raise SCMConflict("CI event belongs to another repository")
    if observation.head_sha != expected_head_sha.lower():
        raise SCMConflict("CI event head SHA differs from the frozen ChangeSet candidate")
