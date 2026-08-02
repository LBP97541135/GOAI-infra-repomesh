from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ContextWorkspaceEntry:
    context_object_id: UUID
    version_id: UUID
    content_uri: str
    content_hash: str
    relative_path: str
    required_read: bool


@dataclass(frozen=True, slots=True)
class ContextWorkspacePlan:
    bundle_id: UUID
    run_id: UUID
    entries: tuple[ContextWorkspaceEntry, ...]


@dataclass(frozen=True, slots=True)
class MaterializedContextWorkspace:
    bundle_id: UUID
    root_path: str
    index_path: str
    content_hash: str
    read_only: bool


class ContextWorkspaceMaterializer(Protocol):
    async def materialize(self, plan: ContextWorkspacePlan) -> MaterializedContextWorkspace: ...
