"""Source-control provider adapters."""

from .contracts import BranchPublisher, SCMAdapter
from .delivery import (
    ChangeSetSCMCoordinator,
    OpenChangeSetPullRequestCommand,
    PublishChangeSetPullRequestCommand,
    parse_repository_ref,
)
from .git_branch import GitBranchPublisher
from .github import GitHubAdapter, verify_github_webhook
from .github_auth import GitHubAppTokenProvider, private_key_file_loader
from .github_events import (
    GitHubCIObservation,
    parse_github_check_run,
    validate_ci_observation,
)

__all__ = [
    "BranchPublisher",
    "ChangeSetSCMCoordinator",
    "GitHubAdapter",
    "GitHubAppTokenProvider",
    "GitHubCIObservation",
    "GitBranchPublisher",
    "OpenChangeSetPullRequestCommand",
    "PublishChangeSetPullRequestCommand",
    "SCMAdapter",
    "parse_repository_ref",
    "private_key_file_loader",
    "parse_github_check_run",
    "validate_ci_observation",
    "verify_github_webhook",
]
