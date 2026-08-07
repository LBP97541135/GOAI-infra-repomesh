from dataclasses import dataclass
from typing import Any

from .contracts import RepositoryRef, SCMConflict

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
        conclusion=(
            str(check["conclusion"]).lower() if check.get("conclusion") else None
        ),
        summary=summary,
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
