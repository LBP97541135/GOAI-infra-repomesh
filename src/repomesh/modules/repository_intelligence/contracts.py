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
    plan). ``title`` is intentionally absent — it derives from the requirement
    text (single source of truth). ``organization_id`` is an optional
    cross-check only (v0.3 §6 S-4): the workspace of record still derives from
    the actor; when the field is present and disagrees with the actor's
    organization the request is rejected instead of silently trusting either.
    """

    requirement_text: str
    created_by_agent_id: UUID
    idempotency_key: str
    organization_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class IssueIntakeReceipt:
    """``created`` is False when the idempotency key replayed an existing issue."""

    project_id: UUID
    created: bool


class CreateIssueIntake(Protocol):
    async def execute(self, command: IssueIntakeCommand) -> IssueIntakeReceipt: ...
