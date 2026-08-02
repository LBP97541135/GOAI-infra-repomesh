from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from repomesh.modules.context.domain import (
    ContextAccessEvent,
    ContextBundle,
    ContextDelta,
    ContextObject,
    ContextObjectVersion,
)
from repomesh.shared.events import EventEnvelope


class ContextStore(Protocol):
    async def create_object(
        self,
        context_object: ContextObject,
        version: ContextObjectVersion,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None: ...

    async def append_version(
        self,
        version: ContextObjectVersion,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None: ...

    async def get_object(self, context_object_id: UUID) -> ContextObject | None: ...

    async def get_version(self, version_id: UUID) -> ContextObjectVersion | None: ...

    async def latest_version(self, context_object_id: UUID) -> ContextObjectVersion | None: ...

    async def publish_bundle(
        self,
        bundle: ContextBundle,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None: ...

    async def get_bundle(self, bundle_id: UUID) -> ContextBundle | None: ...

    async def append_delta(
        self,
        delta: ContextDelta,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None: ...

    async def list_deltas(self, bundle_id: UUID) -> list[ContextDelta]: ...

    async def record_access(
        self,
        access: ContextAccessEvent,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None: ...

    async def list_access_events(self, run_id: UUID) -> list[ContextAccessEvent]: ...
