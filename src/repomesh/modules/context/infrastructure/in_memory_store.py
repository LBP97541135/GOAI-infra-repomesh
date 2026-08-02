from collections.abc import Sequence
from uuid import UUID

from repomesh.modules.context.domain import (
    ContextAccessEvent,
    ContextAlreadyExists,
    ContextBundle,
    ContextConflict,
    ContextDelta,
    ContextNotFound,
    ContextObject,
    ContextObjectVersion,
    ContextSequenceConflict,
)
from repomesh.shared.events import EventEnvelope


class InMemoryContextStore:
    def __init__(self) -> None:
        self.objects: dict[UUID, ContextObject] = {}
        self.versions: dict[UUID, ContextObjectVersion] = {}
        self.bundles: dict[UUID, ContextBundle] = {}
        self.deltas: dict[UUID, list[ContextDelta]] = {}
        self.access_events: list[ContextAccessEvent] = []
        self.events: list[EventEnvelope] = []

    async def create_object(
        self,
        context_object: ContextObject,
        version: ContextObjectVersion,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None:
        if context_object.id in self.objects or version.id in self.versions:
            raise ContextAlreadyExists("context object or version already exists")
        self.objects[context_object.id] = context_object
        self.versions[version.id] = version
        self.events.extend(events)

    async def append_version(
        self,
        version: ContextObjectVersion,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None:
        if version.context_object_id not in self.objects:
            raise ContextNotFound(f"context object not found: {version.context_object_id}")
        if version.id in self.versions:
            raise ContextAlreadyExists(f"context version already exists: {version.id}")
        latest = await self.latest_version(version.context_object_id)
        if latest is None or version.version != latest.version + 1:
            raise ContextConflict("context version sequence conflict")
        if version.supersedes_version_id != latest.id:
            raise ContextConflict("context version must supersede the current version")
        self.versions[version.id] = version
        self.events.extend(events)

    async def get_object(self, context_object_id: UUID) -> ContextObject | None:
        return self.objects.get(context_object_id)

    async def get_version(self, version_id: UUID) -> ContextObjectVersion | None:
        return self.versions.get(version_id)

    async def latest_version(self, context_object_id: UUID) -> ContextObjectVersion | None:
        versions = [
            version
            for version in self.versions.values()
            if version.context_object_id == context_object_id
        ]
        return max(versions, key=lambda version: version.version, default=None)

    async def publish_bundle(
        self,
        bundle: ContextBundle,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None:
        if bundle.id in self.bundles or any(
            existing.run_id == bundle.run_id for existing in self.bundles.values()
        ):
            raise ContextAlreadyExists("a context bundle already exists for this run")
        self.bundles[bundle.id] = bundle
        self.deltas[bundle.id] = []
        self.events.extend(events)

    async def get_bundle(self, bundle_id: UUID) -> ContextBundle | None:
        return self.bundles.get(bundle_id)

    async def append_delta(
        self,
        delta: ContextDelta,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None:
        if delta.bundle_id not in self.bundles:
            raise ContextNotFound(f"context bundle not found: {delta.bundle_id}")
        deltas = self.deltas[delta.bundle_id]
        if delta.sequence != len(deltas) + 1:
            raise ContextSequenceConflict("context delta sequence conflict")
        if any(existing.id == delta.id for existing in deltas):
            raise ContextAlreadyExists(f"context delta already exists: {delta.id}")
        deltas.append(delta)
        self.events.extend(events)

    async def list_deltas(self, bundle_id: UUID) -> list[ContextDelta]:
        return list(self.deltas.get(bundle_id, ()))

    async def record_access(
        self,
        access: ContextAccessEvent,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None:
        self.access_events.append(access)
        self.events.extend(events)

    async def list_access_events(self, run_id: UUID) -> list[ContextAccessEvent]:
        return [event for event in self.access_events if event.run_id == run_id]
