import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from repomesh.shared.git import normalize_full_sha

from .contracts import (
    CreateValidationSnapshotCommand,
    DatabaseBranchValidationView,
    DatabaseValidationStage,
    DatabaseValidationStatus,
    StartDatabaseBranchValidation,
    ValidationDecision,
    ValidationSnapshotView,
    ValidationStatus,
)
from .domain import DatabaseBranchValidation, ValidationSnapshot
from .ports import (
    DatabaseBranchProvider,
    DatabaseBranchValidationStore,
    DatabaseValidationEvidenceReader,
    ValidationSnapshotStore,
)


class ValidationSnapshotService:
    def __init__(
        self,
        store: ValidationSnapshotStore,
        database_validations: DatabaseValidationEvidenceReader | None = None,
    ) -> None:
        self._store = store
        self._database_validations = database_validations

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
        database_validation_ids = tuple(dict.fromkeys(command.database_validation_ids))
        await self._validate_database_evidence(
            command.organization_id,
            command.project_id,
            heads,
            database_validation_ids,
        )
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
            database_validation_ids=database_validation_ids,
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

    async def _validate_database_evidence(
        self,
        organization_id: UUID,
        project_id: UUID,
        candidate_heads: dict[UUID, str],
        run_ids: tuple[UUID, ...],
    ) -> None:
        if not run_ids:
            return
        if self._database_validations is None:
            raise ValueError("database validation evidence reader is unavailable")
        for run_id in run_ids:
            view = await self._database_validations.get(run_id)
            if view is None:
                raise ValueError("database validation evidence was not found")
            if view.organization_id != organization_id or view.project_id != project_id:
                raise ValueError("database validation evidence belongs to another scope")
            if candidate_heads.get(view.repository_id) != view.candidate_sha:
                raise ValueError("database validation evidence does not match candidate head")
            if not DatabaseBranchValidationService.is_delivery_evidence(view):
                raise ValueError("database validation evidence is not passed and cleaned")


class DatabaseBranchValidationConflict(ValueError):
    pass


class DatabaseBranchValidationService:
    def __init__(
        self, store: DatabaseBranchValidationStore, provider: DatabaseBranchProvider
    ) -> None:
        self._store = store
        self._provider = provider

    async def start(
        self, command: StartDatabaseBranchValidation
    ) -> DatabaseBranchValidationView:
        candidate_sha = normalize_full_sha(command.candidate_sha, field="candidate sha")
        self._validate(command)
        request_hash = self._request_hash(command, candidate_sha)
        now = datetime.now(UTC)
        proposed = DatabaseBranchValidation(
            organization_id=command.organization_id,
            project_id=command.project_id,
            repository_id=command.repository_id,
            candidate_sha=candidate_sha,
            source_database_ref=command.source_database_ref.strip(),
            provider=self._provider.name,
            request_hash=request_hash,
            idempotency_key=command.idempotency_key.strip(),
            created_at=now,
            updated_at=now,
        )
        run, created = await self._store.reserve(proposed)
        if run.request_hash != request_hash:
            raise DatabaseBranchValidationConflict(
                "idempotency key is already bound to another database validation request"
            )
        if not created:
            return run.to_view()
        return (await self._execute(run, command)).to_view()

    async def get(self, run_id: UUID) -> DatabaseBranchValidationView | None:
        run = await self._store.get(run_id)
        return run.to_view() if run else None

    async def retry_cleanup(self, run_id: UUID) -> DatabaseBranchValidationView:
        run = await self._store.get(run_id)
        if run is None:
            raise ValueError("database validation run was not found")
        if not run.cleanup_pending or not run.provider_branch_ref:
            return run.to_view()
        return (await self._cleanup(run)).to_view()

    @staticmethod
    def is_delivery_evidence(view: DatabaseBranchValidationView) -> bool:
        return (
            view.status is DatabaseValidationStatus.CLEANED
            and not view.cleanup_pending
            and view.failure_code is None
            and bool(view.results)
            and all(result.exit_code == 0 for result in view.results)
        )

    async def _execute(
        self, run: DatabaseBranchValidation, command: StartDatabaseBranchValidation
    ) -> DatabaseBranchValidation:
        run = await self._save(run, status=DatabaseValidationStatus.PROVISIONING)
        try:
            branch = await self._provider.create_branch(
                source_database_ref=run.source_database_ref,
                idempotency_key=run.idempotency_key,
            )
        except Exception:  # provider details must not enter durable evidence
            return await self._save(
                run,
                status=DatabaseValidationStatus.FAILED,
                failure_code="branch_provision_failed",
            )
        run = await self._save(
            run,
            status=DatabaseValidationStatus.READY,
            provider_branch_ref=branch.branch_ref,
            engine_version=branch.engine_version,
            cleanup_pending=True,
        )
        run = await self._save(run, status=DatabaseValidationStatus.VALIDATING)
        results = []
        failure_code = None
        for item in command.commands:
            try:
                result = await self._provider.execute(branch, item)
            except Exception:
                failure_code = f"{item.stage.value}_execution_failed"
                break
            results.append(result)
            if result.exit_code != 0:
                failure_code = f"{item.stage.value}_command_failed"
                break
        run = await self._save(
            run,
            status=(
                DatabaseValidationStatus.FAILED
                if failure_code
                else DatabaseValidationStatus.PASSED
            ),
            results=tuple(results),
            failure_code=failure_code,
        )
        return await self._cleanup(run)

    async def _cleanup(self, run: DatabaseBranchValidation) -> DatabaseBranchValidation:
        run = await self._save(run, status=DatabaseValidationStatus.CLEANING)
        try:
            assert run.provider_branch_ref is not None
            await self._provider.delete_branch(run.provider_branch_ref)
        except Exception:
            return await self._save(
                run, cleanup_pending=True, failure_code=run.failure_code or "cleanup_failed"
            )
        return await self._save(
            run,
            status=DatabaseValidationStatus.CLEANED,
            cleanup_pending=False,
            failure_code=None if run.failure_code == "cleanup_failed" else run.failure_code,
        )

    async def _save(
        self, run: DatabaseBranchValidation, **changes: object
    ) -> DatabaseBranchValidation:
        updated = replace(run, **changes, updated_at=datetime.now(UTC))
        await self._store.update(updated)
        return updated

    @staticmethod
    def _validate(command: StartDatabaseBranchValidation) -> None:
        if not command.source_database_ref.strip():
            raise ValueError("source database reference is required")
        unsafe_reference = (
            "://" in command.source_database_ref
            or "password=" in command.source_database_ref.lower()
        )
        if unsafe_reference:
            raise ValueError("source database reference must be an opaque policy name, not a URL")
        if not command.idempotency_key.strip() or len(command.idempotency_key) > 200:
            raise ValueError("idempotency key must contain 1-200 characters")
        if not command.commands:
            raise ValueError("database validation requires commands")
        stages = [item.stage for item in command.commands]
        if DatabaseValidationStage.MIGRATION not in stages:
            raise ValueError("database validation requires a migration command")
        if DatabaseValidationStage.VERIFICATION not in stages:
            raise ValueError("database validation requires a verification command")
        order = {stage: index for index, stage in enumerate(DatabaseValidationStage)}
        if stages != sorted(stages, key=order.__getitem__):
            raise ValueError("database validation commands must follow stage order")
        for item in command.commands:
            if not item.name.strip() or not item.command_ref.strip():
                raise ValueError("database validation command name and reference are required")

    @staticmethod
    def _request_hash(command: StartDatabaseBranchValidation, candidate_sha: str) -> str:
        document = {
            "organization_id": str(command.organization_id),
            "project_id": str(command.project_id),
            "repository_id": str(command.repository_id),
            "candidate_sha": candidate_sha,
            "source_database_ref": command.source_database_ref.strip(),
            "commands": [
                {"stage": item.stage.value, "name": item.name, "ref": item.command_ref}
                for item in command.commands
            ],
        }
        return hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
