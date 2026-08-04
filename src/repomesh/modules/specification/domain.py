import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from repomesh.modules.specification.contracts import (
    SpecificationKind,
    SpecificationStatus,
    SpecificationVersionView,
    SpecificationView,
)
from repomesh.shared.domain import new_id


class SpecificationError(Exception):
    pass


class SpecificationConflict(SpecificationError):
    pass


class SpecificationDenied(SpecificationError):
    pass


class SpecificationNotFound(SpecificationError):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SpecificationContent:
    goal: str
    acceptance: tuple[str, ...]
    scope: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    interface_changes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("specification goal is required")
        if not self.acceptance or any(not item.strip() for item in self.acceptance):
            raise ValueError("at least one non-empty acceptance criterion is required")
        for values in (
            self.scope,
            self.constraints,
            self.tests,
            self.dependencies,
            self.allowed_paths,
            self.interface_changes,
        ):
            if any(not item.strip() for item in values):
                raise ValueError("specification lists cannot contain empty values")

    def canonical_payload(self) -> dict[str, object]:
        return {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in asdict(self).items()
        }


@dataclass(frozen=True, slots=True)
class SpecificationVersion:
    specification_id: UUID
    version: int
    content: SpecificationContent
    created_by_agent_id: UUID
    id: UUID = field(default_factory=new_id)
    supersedes_version_id: UUID | None = None
    created_at: datetime = field(default_factory=_utc_now)
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("specification version must be positive")
        if self.version == 1 and self.supersedes_version_id is not None:
            raise ValueError("first specification version cannot supersede another version")
        if self.version > 1 and self.supersedes_version_id is None:
            raise ValueError("later specification versions must supersede a version")
        calculated = self.calculate_content_hash()
        if self.content_hash and self.content_hash != calculated:
            raise ValueError("specification content hash does not match content")
        if not self.content_hash:
            object.__setattr__(self, "content_hash", calculated)

    def calculate_content_hash(self) -> str:
        encoded = json.dumps(
            self.content.canonical_payload(), sort_keys=True, separators=(",", ":")
        ).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def to_view(self) -> SpecificationVersionView:
        return SpecificationVersionView(
            id=self.id,
            specification_id=self.specification_id,
            version=self.version,
            content_hash=self.content_hash,
            created_by_agent_id=self.created_by_agent_id,
        )


@dataclass(frozen=True, slots=True)
class Specification:
    organization_id: UUID
    project_id: UUID
    kind: SpecificationKind
    title: str
    owner_agent_id: UUID
    current_version: SpecificationVersion
    repository_id: UUID | None = None
    task_id: UUID | None = None
    id: UUID = field(default_factory=new_id)
    status: SpecificationStatus = SpecificationStatus.DRAFT
    revision: int = 1
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("specification title is required")
        if self.current_version.specification_id != self.id:
            raise ValueError("current version must belong to specification")
        if (
            self.kind in {SpecificationKind.REPOSITORY, SpecificationKind.TASK}
            and self.repository_id is None
        ):
            raise ValueError("repository and task specifications require repository_id")
        if self.kind is SpecificationKind.TASK and self.task_id is None:
            raise ValueError("task specifications require task_id")
        if (
            self.kind in {SpecificationKind.ENGINEERING, SpecificationKind.CONTRACT}
            and self.task_id is not None
        ):
            raise ValueError("project specifications cannot bind a task")

    def submit(self) -> "Specification":
        if self.status is not SpecificationStatus.DRAFT:
            raise SpecificationConflict("only draft specifications can be submitted")
        return replace(self, status=SpecificationStatus.IN_REVIEW, revision=self.revision + 1)

    def revise(self, version: SpecificationVersion) -> "Specification":
        if self.status not in {SpecificationStatus.DRAFT, SpecificationStatus.APPROVED}:
            raise SpecificationConflict("only draft or approved specifications can be revised")
        if version.specification_id != self.id:
            raise SpecificationConflict("new version belongs to another specification")
        if version.version != self.current_version.version + 1:
            raise SpecificationConflict("new version must follow the current version")
        if version.supersedes_version_id != self.current_version.id:
            raise SpecificationConflict("new version must supersede the current version")
        return replace(
            self,
            current_version=version,
            status=SpecificationStatus.DRAFT,
            revision=self.revision + 1,
        )

    def approve(self, *, freeze: bool) -> "Specification":
        if self.status is not SpecificationStatus.IN_REVIEW:
            raise SpecificationConflict("only in-review specifications can be approved")
        status = SpecificationStatus.FROZEN if freeze else SpecificationStatus.APPROVED
        return replace(self, status=status, revision=self.revision + 1)

    def to_view(self) -> SpecificationView:
        return SpecificationView(
            id=self.id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            kind=self.kind,
            status=self.status,
            title=self.title,
            repository_id=self.repository_id,
            task_id=self.task_id,
            owner_agent_id=self.owner_agent_id,
            revision=self.revision,
            current_version=self.current_version.to_view(),
        )
