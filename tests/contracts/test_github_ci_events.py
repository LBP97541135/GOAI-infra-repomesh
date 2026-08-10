import pytest

from repomesh.integrations.scm.contracts import RepositoryRef, SCMConflict
from repomesh.integrations.scm.github_events import (
    parse_github_check_run,
    parse_github_pull_request_review,
    validate_ci_observation,
)
from repomesh.modules.delivery.contracts import ReviewState


def payload(*, sha: str = "a" * 40, conclusion: str | None = "success") -> dict:
    return {
        "repository": {"name": "pricing", "owner": {"login": "acme"}},
        "check_run": {
            "id": 91,
            "name": "pricing tests",
            "head_sha": sha,
            "status": "completed" if conclusion else "in_progress",
            "conclusion": conclusion,
            "output": {"summary": "128 tests passed"},
        },
    }


def test_check_run_is_normalized() -> None:
    observation = parse_github_check_run(payload())

    assert observation.repository == RepositoryRef.from_github("acme", "pricing")
    assert observation.check_run_id == "91"
    assert observation.terminal
    assert observation.passed


def test_non_terminal_check_does_not_claim_a_result() -> None:
    observation = parse_github_check_run(payload(conclusion=None))

    assert not observation.terminal
    with pytest.raises(ValueError, match="not terminal"):
        _ = observation.passed


def test_ci_sha_drift_is_rejected() -> None:
    observation = parse_github_check_run(payload(sha="c" * 40))

    with pytest.raises(SCMConflict, match="head SHA"):
        validate_ci_observation(
            observation,
            expected_repository=RepositoryRef.from_github("acme", "pricing"),
            expected_head_sha="a" * 40,
        )


def test_github_approval_binds_reviewer_and_candidate_sha() -> None:
    observation = parse_github_pull_request_review(
        {
            "action": "submitted",
            "repository": {"name": "pricing", "owner": {"login": "acme"}},
            "pull_request": {"head": {"sha": "a" * 40}},
            "review": {
                "id": 77,
                "state": "approved",
                "commit_id": "a" * 40,
                "body": "looks good",
                "user": {"login": "reviewer"},
            },
        }
    )

    assert observation is not None
    assert observation.state is ReviewState.APPROVED
    assert observation.reviewer == "reviewer"
    assert observation.head_sha == "a" * 40
