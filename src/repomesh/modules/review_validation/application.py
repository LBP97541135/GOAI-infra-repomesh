import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from repomesh.shared.git import normalize_full_sha

from .contracts import (
    CreateValidationSnapshotCommand,
    ValidationDecision,
    ValidationSnapshotView,
    ValidationStatus,
)
from .domain import ValidationSnapshot
from .ports import ValidationSnapshotStore


class ValidationSnapshotService:
    def __init__(self, store: ValidationSnapshotStore) -> None:
        self._store = store

    async def create(self, command: CreateValidationSnapshotCommand) -> ValidationSnapshotView:
        if command.ttl_seconds <= 0:
            raise ValueError("validation snapshot ttl must be positive")
        heads = {
            repository_id: self._full_sha(head)
            for repository_id, head in command.candidate_heads.items()
        }
        if not heads or not command.tests:
            raise ValueError("validation snapshot requires candidates and test evidence")
        environment = {
            str(key).strip(): str(value).strip()
            for key, value in command.environment.items()
            if str(key).strip()
        }
        if not environment:
            raise ValueError("validation environment identity is required")
        encoded = json.dumps(environment, sort_keys=True, separators=(",", ":")).encode()
        now = datetime.now(UTC)
        snapshot = ValidationSnapshot(
            organization_id=command.organization_id,
            project_id=command.project_id,
            specification_version_id=command.specification_version_id,
            candidate_heads=heads,
            tests=command.tests,
            environment=environment,
            environment_hash=hashlib.sha256(encoded).hexdigest(),
            review_evidence_ids=tuple(dict.fromkeys(command.review_evidence_ids)),
            status=(
                ValidationStatus.PASSED
                if all(item.exit_code == 0 for item in command.tests)
                else ValidationStatus.FAILED
            ),
            created_at=now,
            expires_at=now + timedelta(seconds=command.ttl_seconds),
        )
        await self._store.add(snapshot)
        return snapshot.to_view()

    async def get(self, snapshot_id: UUID) -> ValidationSnapshotView | None:
        item = await self._store.get(snapshot_id)
        return item.to_view() if item else None

    async def validate_for_delivery(
        self,
        snapshot_id: UUID,
        project_id: UUID,
        candidate_heads: dict[UUID, str],
    ) -> ValidationDecision:
        snapshot = await self._store.get(snapshot_id)
        reasons: list[str] = []
        if snapshot is None:
            return ValidationDecision(False, ("validation snapshot was not found",))
        if snapshot.project_id != project_id:
            reasons.append("validation snapshot belongs to another project")
        if snapshot.status is not ValidationStatus.PASSED:
            reasons.append("validation snapshot contains failed tests")
        if snapshot.expires_at <= datetime.now(UTC):
            reasons.append("validation snapshot has expired")
        normalized = {key: value.strip().lower() for key, value in candidate_heads.items()}
        if snapshot.candidate_heads != normalized:
            reasons.append("validation snapshot does not match current candidate heads")
        return ValidationDecision(not reasons, tuple(reasons))

    @staticmethod
    def _full_sha(value: str) -> str:
        return normalize_full_sha(value, field="candidate head")
