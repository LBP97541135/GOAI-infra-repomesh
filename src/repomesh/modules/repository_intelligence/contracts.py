from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RepositorySelected:
    """Published after a human confirms a repository for a project."""

    project_id: UUID
    repository_id: UUID
    classification: str


@dataclass(frozen=True, slots=True)
class IssueIntakeCommand:
    """Contract v0.3 §1: create an issue by materialising its first draft snapshot.

    The intake owns no new entity: an issue *is* a project_id, and creating one
    means persisting the earliest PlanSnapshot (plan_version=1, no execution
    plan). ``organization_id`` and ``title`` are intentionally absent — both
    derive from the actor and the requirement text (single source of truth).
    """

    requirement_text: str
    created_by_agent_id: UUID
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class IssueIntakeReceipt:
    """``created`` is False when the idempotency key replayed an existing issue."""

    project_id: UUID
    created: bool


class CreateIssueIntake(Protocol):
    async def execute(self, command: IssueIntakeCommand) -> IssueIntakeReceipt: ...
