from collections.abc import Sequence
from datetime import UTC
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from repomesh.modules.context.contracts import (
    ContextAccessResult,
    ContextObjectType,
    ContextScope,
    ContextStatus,
)
from repomesh.modules.context.domain import (
    ContextAccessEvent,
    ContextAlreadyExists,
    ContextBundle,
    ContextBundleItem,
    ContextConflict,
    ContextDelta,
    ContextNotFound,
    ContextObject,
    ContextObjectVersion,
    ContextSequenceConflict,
    DeltaKind,
)
from repomesh.persistence import Database
from repomesh.persistence.models import AuditEventRecord, OutboxEventRecord, StateEventRecord
from repomesh.shared.events import EventEnvelope

from .models import (
    ContextAccessEventRecord,
    ContextBundleItemRecord,
    ContextBundleRecord,
    ContextDeltaItemRecord,
    ContextDeltaRecord,
    ContextObjectRecord,
    ContextObjectVersionRecord,
)


def _aware(value: object) -> object:
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=UTC)  # type: ignore[union-attr]
    return value


def _add_events(session: object, events: Sequence[EventEnvelope]) -> None:
    for event in events:
        session.add(StateEventRecord.from_envelope(event))  # type: ignore[attr-defined]
        session.add(AuditEventRecord.from_envelope(event))  # type: ignore[attr-defined]
        session.add(OutboxEventRecord.from_envelope(event))  # type: ignore[attr-defined]


class PostgresContextStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_object(
        self,
        context_object: ContextObject,
        version: ContextObjectVersion,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(self._object_record(context_object))
                session.add(self._version_record(version))
                _add_events(session, events)
        except IntegrityError as exc:
            raise ContextAlreadyExists("context object or first version already exists") from exc

    async def append_version(
        self,
        version: ContextObjectVersion,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None:
        try:
            async with self._database.transaction() as session:
                current = await session.scalar(
                    select(ContextObjectVersionRecord)
                    .where(
                        ContextObjectVersionRecord.context_object_id
                        == version.context_object_id
                    )
                    .order_by(ContextObjectVersionRecord.version.desc())
                    .limit(1)
                    .with_for_update()
                )
                if current is None:
                    raise ContextNotFound(
                        f"context object not found: {version.context_object_id}"
                    )
                if (
                    version.version != current.version + 1
                    or version.supersedes_version_id != current.id
                ):
                    raise ContextConflict("context version must supersede the current version")
                session.add(self._version_record(version))
                _add_events(session, events)
        except IntegrityError as exc:
            raise ContextConflict("context version sequence conflict") from exc

    async def get_object(self, context_object_id: UUID) -> ContextObject | None:
        async with self._database.transaction() as session:
            record = await session.get(ContextObjectRecord, context_object_id)
        return self._to_object(record) if record is not None else None

    async def get_version(self, version_id: UUID) -> ContextObjectVersion | None:
        async with self._database.transaction() as session:
            record = await session.get(ContextObjectVersionRecord, version_id)
        return self._to_version(record) if record is not None else None

    async def latest_version(self, context_object_id: UUID) -> ContextObjectVersion | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(ContextObjectVersionRecord)
                .where(ContextObjectVersionRecord.context_object_id == context_object_id)
                .order_by(ContextObjectVersionRecord.version.desc())
                .limit(1)
            )
        return self._to_version(record) if record is not None else None

    async def publish_bundle(
        self,
        bundle: ContextBundle,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(self._bundle_record(bundle))
                for position, item in enumerate(bundle.items):
                    session.add(self._bundle_item_record(bundle.id, item, position))
                _add_events(session, events)
        except IntegrityError as exc:
            raise ContextAlreadyExists("a context bundle already exists for this run") from exc

    async def get_bundle(self, bundle_id: UUID) -> ContextBundle | None:
        async with self._database.transaction() as session:
            record = await session.get(ContextBundleRecord, bundle_id)
            if record is None:
                return None
            item_records = (
                await session.scalars(
                    select(ContextBundleItemRecord)
                    .where(ContextBundleItemRecord.bundle_id == bundle_id)
                    .order_by(ContextBundleItemRecord.position)
                )
            ).all()
        return self._to_bundle(record, item_records)

    async def append_delta(
        self,
        delta: ContextDelta,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None:
        try:
            async with self._database.transaction() as session:
                existing = (
                    await session.scalars(
                        select(ContextDeltaRecord)
                        .where(ContextDeltaRecord.bundle_id == delta.bundle_id)
                        .order_by(ContextDeltaRecord.sequence)
                        .with_for_update()
                    )
                ).all()
                if await session.get(ContextBundleRecord, delta.bundle_id) is None:
                    raise ContextNotFound(f"context bundle not found: {delta.bundle_id}")
                if delta.sequence != len(existing) + 1:
                    raise ContextSequenceConflict("context delta sequence conflict")
                session.add(self._delta_record(delta))
                for position, item in enumerate(delta.items):
                    session.add(self._delta_item_record(delta.id, item, position))
                _add_events(session, events)
        except IntegrityError as exc:
            raise ContextSequenceConflict("context delta sequence conflict") from exc

    async def list_deltas(self, bundle_id: UUID) -> list[ContextDelta]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(ContextDeltaRecord)
                    .where(ContextDeltaRecord.bundle_id == bundle_id)
                    .order_by(ContextDeltaRecord.sequence)
                )
            ).all()
            result: list[ContextDelta] = []
            for record in records:
                item_records = (
                    await session.scalars(
                        select(ContextDeltaItemRecord)
                        .where(ContextDeltaItemRecord.delta_id == record.id)
                        .order_by(ContextDeltaItemRecord.position)
                    )
                ).all()
                result.append(self._to_delta(record, item_records))
        return result

    async def record_access(
        self,
        access: ContextAccessEvent,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(
                    ContextAccessEventRecord(
                        id=access.id,
                        project_id=access.project_id,
                        bundle_id=access.bundle_id,
                        run_id=access.run_id,
                        agent_id=access.agent_id,
                        version_id=access.version_id,
                        path=access.path,
                        content_hash=access.content_hash,
                        result=access.result.value,
                        accessed_at=access.accessed_at,
                        metadata_payload={},
                    )
                )
                _add_events(session, events)
        except IntegrityError as exc:
            raise ContextConflict("context access event could not be recorded") from exc

    async def list_access_events(self, run_id: UUID) -> list[ContextAccessEvent]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(ContextAccessEventRecord)
                    .where(ContextAccessEventRecord.run_id == run_id)
                    .order_by(ContextAccessEventRecord.accessed_at)
                )
            ).all()
        return [
            ContextAccessEvent(
                id=record.id,
                project_id=record.project_id,
                bundle_id=record.bundle_id,
                run_id=record.run_id,
                agent_id=record.agent_id,
                version_id=record.version_id,
                path=record.path,
                content_hash=record.content_hash,
                result=ContextAccessResult(record.result),
                accessed_at=_aware(record.accessed_at),
            )
            for record in records
        ]

    @staticmethod
    def _object_record(context_object: ContextObject) -> ContextObjectRecord:
        return ContextObjectRecord(
            id=context_object.id,
            organization_id=context_object.organization_id,
            project_id=context_object.project_id,
            object_type=context_object.object_type.value,
            scope=context_object.scope.value,
            owner_subject=context_object.owner_subject,
            title=context_object.title,
            status=context_object.status.value,
            created_at=context_object.created_at,
        )

    @staticmethod
    def _version_record(version: ContextObjectVersion) -> ContextObjectVersionRecord:
        return ContextObjectVersionRecord(
            id=version.id,
            context_object_id=version.context_object_id,
            version=version.version,
            content_uri=version.content_uri,
            content_hash=version.content_hash,
            mime_type=version.mime_type,
            size_bytes=version.size_bytes,
            created_by=version.created_by,
            created_at=version.created_at,
            supersedes_version_id=version.supersedes_version_id,
        )

    @staticmethod
    def _to_object(record: ContextObjectRecord) -> ContextObject:
        return ContextObject(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            object_type=ContextObjectType(record.object_type),
            scope=ContextScope(record.scope),
            owner_subject=record.owner_subject,
            title=record.title,
            status=ContextStatus(record.status),
            created_at=_aware(record.created_at),
        )

    @staticmethod
    def _to_version(record: ContextObjectVersionRecord) -> ContextObjectVersion:
        return ContextObjectVersion(
            id=record.id,
            context_object_id=record.context_object_id,
            version=record.version,
            content_uri=record.content_uri,
            content_hash=record.content_hash,
            mime_type=record.mime_type,
            size_bytes=record.size_bytes,
            created_by=record.created_by,
            created_at=_aware(record.created_at),
            supersedes_version_id=record.supersedes_version_id,
        )

    @staticmethod
    def _bundle_record(bundle: ContextBundle) -> ContextBundleRecord:
        return ContextBundleRecord(
            id=bundle.id,
            project_id=bundle.project_id,
            run_id=bundle.run_id,
            task_spec_version_id=bundle.task_spec_version_id,
            agent_id=bundle.agent_id,
            role=bundle.role,
            repository_id=bundle.repository_id,
            base_sha=bundle.base_sha,
            workspace_id=bundle.workspace_id,
            allowed_tools=list(bundle.allowed_tools),
            allowed_paths=list(bundle.allowed_paths),
            denied_paths=list(bundle.denied_paths),
            network_policy=list(bundle.network_policy),
            expires_at=bundle.expires_at,
            content_hash=bundle.content_hash,
            created_at=bundle.created_at,
        )

    @staticmethod
    def _bundle_item_record(
        bundle_id: UUID, item: ContextBundleItem, position: int
    ) -> ContextBundleItemRecord:
        return ContextBundleItemRecord(
            bundle_id=bundle_id,
            version_id=item.version_id,
            context_object_id=item.context_object_id,
            content_hash=item.content_hash,
            mount_path=item.mount_path,
            required_read=item.required_read,
            position=position,
        )

    @staticmethod
    def _to_item(record: object) -> ContextBundleItem:
        return ContextBundleItem(
            context_object_id=record.context_object_id,  # type: ignore[attr-defined]
            version_id=record.version_id,  # type: ignore[attr-defined]
            content_hash=record.content_hash,  # type: ignore[attr-defined]
            mount_path=record.mount_path,  # type: ignore[attr-defined]
            required_read=record.required_read,  # type: ignore[attr-defined]
        )

    @classmethod
    def _to_bundle(
        cls,
        record: ContextBundleRecord,
        item_records: Sequence[ContextBundleItemRecord],
    ) -> ContextBundle:
        return ContextBundle(
            id=record.id,
            project_id=record.project_id,
            run_id=record.run_id,
            task_spec_version_id=record.task_spec_version_id,
            agent_id=record.agent_id,
            role=record.role,
            repository_id=record.repository_id,
            base_sha=record.base_sha,
            workspace_id=record.workspace_id,
            items=tuple(cls._to_item(item) for item in item_records),
            allowed_tools=tuple(record.allowed_tools),
            allowed_paths=tuple(record.allowed_paths),
            denied_paths=tuple(record.denied_paths),
            network_policy=tuple(record.network_policy),
            expires_at=_aware(record.expires_at),
            content_hash=record.content_hash,
            created_at=_aware(record.created_at),
        )

    @staticmethod
    def _delta_record(delta: ContextDelta) -> ContextDeltaRecord:
        return ContextDeltaRecord(
            id=delta.id,
            bundle_id=delta.bundle_id,
            sequence=delta.sequence,
            summary=delta.summary,
            kind=delta.kind.value,
            content_hash=delta.content_hash,
            created_at=delta.created_at,
        )

    @staticmethod
    def _delta_item_record(
        delta_id: UUID, item: ContextBundleItem, position: int
    ) -> ContextDeltaItemRecord:
        return ContextDeltaItemRecord(
            delta_id=delta_id,
            version_id=item.version_id,
            context_object_id=item.context_object_id,
            content_hash=item.content_hash,
            mount_path=item.mount_path,
            required_read=item.required_read,
            position=position,
        )

    @classmethod
    def _to_delta(
        cls,
        record: ContextDeltaRecord,
        item_records: Sequence[ContextDeltaItemRecord],
    ) -> ContextDelta:
        return ContextDelta(
            id=record.id,
            bundle_id=record.bundle_id,
            sequence=record.sequence,
            summary=record.summary,
            items=tuple(cls._to_item(item) for item in item_records),
            kind=DeltaKind(record.kind),
            content_hash=record.content_hash,
            created_at=_aware(record.created_at),
        )
