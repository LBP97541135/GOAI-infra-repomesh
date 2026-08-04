from typing import Protocol
from uuid import UUID

from repomesh.modules.specification.domain import Specification, SpecificationVersion


class SpecificationStore(Protocol):
    async def add(
        self,
        specification: Specification,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None: ...

    async def get(self, specification_id: UUID) -> Specification | None: ...

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[Specification, str] | None: ...

    async def update(self, specification: Specification, *, expected_revision: int) -> None: ...

    async def list_by_project(self, project_id: UUID) -> tuple[Specification, ...]: ...

    async def get_version(
        self, specification_id: UUID, version: int
    ) -> SpecificationVersion | None: ...
