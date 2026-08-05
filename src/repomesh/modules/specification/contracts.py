from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class SpecificationKind(StrEnum):
    ENGINEERING = "engineering"
    REPOSITORY = "repository"
    CONTRACT = "contract"
    TASK = "task"


class SpecificationStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    FROZEN = "frozen"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class CreateSpecificationCommand:
    organization_id: UUID
    project_id: UUID
    kind: SpecificationKind
    title: str
    created_by_agent_id: UUID
    goal: str
    acceptance: tuple[str, ...]
    scope: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    interface_changes: tuple[str, ...] = ()
    repository_id: UUID | None = None
    task_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SubmitSpecificationCommand:
    specification_id: UUID
    actor_agent_id: UUID
    expected_revision: int


@dataclass(frozen=True, slots=True)
class ReviseSpecificationCommand:
    specification_id: UUID
    actor_agent_id: UUID
    expected_revision: int
    goal: str
    acceptance: tuple[str, ...]
    scope: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    interface_changes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ApproveSpecificationCommand:
    specification_id: UUID
    actor_agent_id: UUID
    expected_revision: int
    freeze: bool = False


@dataclass(frozen=True, slots=True)
class PublishSpecificationContextCommand:
    specification_id: UUID
    actor_agent_id: UUID


@dataclass(frozen=True, slots=True)
class SpecificationVersionView:
    id: UUID
    specification_id: UUID
    version: int
    content_hash: str
    created_by_agent_id: UUID


@dataclass(frozen=True, slots=True)
class SpecificationView:
    id: UUID
    organization_id: UUID
    project_id: UUID
    kind: SpecificationKind
    status: SpecificationStatus
    title: str
    repository_id: UUID | None
    task_id: UUID | None
    owner_agent_id: UUID
    revision: int
    current_version: SpecificationVersionView


@dataclass(frozen=True, slots=True)
class SpecificationDiff:
    specification_id: UUID
    from_version: int
    to_version: int
    changed_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BuildCodingAgentPackageCommand:
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    task_id: UUID
    worker_agent_id: UUID


@dataclass(frozen=True, slots=True)
class RenderedSpecification:
    specification_id: UUID
    version_id: UUID
    version: int
    source_content_hash: str
    rendered_content_hash: str
    mount_path: str
    mime_type: str
    content: str


@dataclass(frozen=True, slots=True)
class CodingAgentPackage:
    project_id: UUID
    repository_id: UUID
    task_id: UUID
    worker_agent_id: UUID
    instruction: str
    acceptance: tuple[str, ...]
    constraints: tuple[str, ...]
    dependencies: tuple[str, ...]
    interface_changes: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    test_commands: tuple[str, ...]
    context_files: tuple[RenderedSpecification, ...]
    content_hash: str


class CodingAgentPackageBuilder(Protocol):
    async def execute(
        self, command: BuildCodingAgentPackageCommand
    ) -> CodingAgentPackage: ...
