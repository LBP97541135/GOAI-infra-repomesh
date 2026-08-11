from datetime import datetime
from typing import Protocol
from uuid import UUID

from .contracts import ValidationEvidenceDecision
from .domain import ChangeSet, SCMCommand, SCMObservation, SCMPollCursor


class ChangeSetStore(Protocol):
    async def add(
        self, change_set: ChangeSet, *, idempotency_key: str, fingerprint: str
    ) -> None: ...

    async def get(self, change_set_id: UUID) -> ChangeSet | None: ...

    async def get_by_idempotency_key(self, key: str) -> tuple[ChangeSet, str] | None: ...

    async def update(self, change_set: ChangeSet, *, expected_version: int) -> None: ...

    async def find_by_candidate(
        self, repository_id: UUID, head_sha: str
    ) -> tuple[ChangeSet, ...]: ...

    async def find_by_project(self, project_id: UUID) -> tuple[ChangeSet, ...]: ...

    async def list_active(self) -> tuple[ChangeSet, ...]: ...


class SCMObservationStore(Protocol):
    async def add(self, observation: SCMObservation) -> None: ...

    async def get(self, observation_id: UUID) -> SCMObservation | None: ...

    async def get_by_identity(
        self, provider: str, source: str, external_id: str
    ) -> SCMObservation | None: ...

    async def update(self, observation: SCMObservation, *, expected_version: int) -> None: ...

    async def list_replayable(
        self,
        *,
        stale_before: datetime,
        max_attempts: int,
        limit: int,
    ) -> tuple[SCMObservation, ...]: ...


class SCMPollCursorStore(Protocol):
    async def get(self, change_set_id: UUID, repository_id: UUID) -> SCMPollCursor | None: ...

    async def upsert(self, cursor: SCMPollCursor, *, expected_version: int | None) -> None: ...


class SCMCommandStore(Protocol):
    async def add(self, command: SCMCommand) -> None: ...

    async def get(self, command_id: UUID) -> SCMCommand | None: ...

    async def get_by_idempotency_key(self, key: str) -> SCMCommand | None: ...

    async def update(self, command: SCMCommand, *, expected_version: int) -> None: ...

    async def list_dispatchable(
        self, *, stale_before: datetime, max_attempts: int, limit: int
    ) -> tuple[SCMCommand, ...]: ...


class ValidationSnapshotReader(Protocol):
    async def validate_for_delivery(
        self,
        snapshot_id: UUID,
        project_id: UUID,
        candidate_heads: dict[UUID, str],
    ) -> ValidationEvidenceDecision: ...
