from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RepositorySelected:
    """Published after a human confirms a repository for a project."""

    project_id: UUID
    repository_id: UUID
    classification: str
