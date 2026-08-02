
from uuid import UUID

from repomesh.modules.context.contracts import ContextAccessResult, ContextAction
from repomesh.modules.context.domain import (
    ContextAccessDenied,
    ContextAccessEvent,
    ContextBundle,
    ContextBundleItem,
    ContextChangeRequestRequired,
    ContextConflict,
    ContextDelta,
    ContextNotFound,
    ContextObject,
    ContextObjectVersion,
    ContextPermissionDenied,
    ContextSequenceConflict,
    DeltaKind,
    PermissionLayer,
    PermissionRequest,
    evaluate_permission,
)
from repomesh.modules.context.ports import ContextStore
from repomesh.shared.domain import new_id
from repomesh.shared.events import ActorType, EventEnvelope


def _event(
    *,
    event_type: str,
    actor_type: ActorType,
    actor_id: str,
    aggregate_type: str,
    aggregate_id: UUID,
    aggregate_version: int,
    project_id: UUID | None,
    run_id: UUID | None,
    payload: dict[str, object],
) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        project_id=project_id,
        run_id=run_id,
        correlation_id=new_id(),
        payload=payload,
    )


class PublishContextObject:
    def __init__(self, store: ContextStore) -> None:
        self._store = store

    async def execute(
        self,
        context_object: ContextObject,
        version: ContextObjectVersion,
        *,
        actor_type: ActorType = ActorType.SERVICE,
        actor_id: str = "repomesh-api",
    ) -> None:
        if version.context_object_id != context_object.id or version.version != 1:
            raise ContextConflict("a new context object requires its own first version")
        event = _event(
            event_type="ContextObjectVersionPublished",
            actor_type=actor_type,
            actor_id=actor_id,
            aggregate_type="ContextObject",
            aggregate_id=context_object.id,
            aggregate_version=1,
            project_id=context_object.project_id,
            run_id=None,
            payload={
                "version_id": str(version.id),
                "object_type": context_object.object_type.value,
                "scope": context_object.scope.value,
                "content_hash": version.content_hash,
            },
        )
        await self._store.create_object(context_object, version, events=(event,))


class PublishContextVersion:
    def __init__(self, store: ContextStore) -> None:
        self._store = store

    async def execute(
        self,
        version: ContextObjectVersion,
        *,
        actor_type: ActorType = ActorType.SERVICE,
        actor_id: str = "repomesh-api",
    ) -> None:
        context_object = await self._store.get_object(version.context_object_id)
        if context_object is None:
            raise ContextNotFound(f"context object not found: {version.context_object_id}")
        latest = await self._store.latest_version(context_object.id)
        if latest is None:
            raise ContextConflict("context object has no current version")
        if version.version != latest.version + 1 or version.supersedes_version_id != latest.id:
            raise ContextConflict("new version must directly supersede the current version")
        event = _event(
            event_type="ContextObjectVersionPublished",
            actor_type=actor_type,
            actor_id=actor_id,
            aggregate_type="ContextObject",
            aggregate_id=context_object.id,
            aggregate_version=version.version,
            project_id=context_object.project_id,
            run_id=None,
            payload={"version_id": str(version.id), "content_hash": version.content_hash},
        )
        await self._store.append_version(version, events=(event,))


async def _validate_items(
    store: ContextStore,
    items: tuple[ContextBundleItem, ...],
    *,
    repository_id: UUID,
    permission_layers: tuple[PermissionLayer, ...],
    explicit_denies: tuple[PermissionLayer, ...],
) -> None:
    for item in items:
        version = await store.get_version(item.version_id)
        if version is None:
            raise ContextNotFound(f"context version not found: {item.version_id}")
        if version.context_object_id != item.context_object_id:
            raise ContextConflict("bundle item object and version do not match")
        if version.content_hash != item.content_hash:
            raise ContextConflict("bundle item content hash does not match the stored version")
        context_object = await store.get_object(item.context_object_id)
        if context_object is None:
            raise ContextNotFound(f"context object not found: {item.context_object_id}")
        decision = evaluate_permission(
            PermissionRequest(
                action=ContextAction.MOUNT,
                scope=context_object.scope,
                context_object_id=context_object.id,
                repository_id=repository_id,
                path=item.mount_path,
            ),
            layers=permission_layers,
            explicit_denies=explicit_denies,
        )
        if not decision.allowed:
            raise ContextPermissionDenied(decision.reason)


class PublishContextBundle:
    def __init__(self, store: ContextStore) -> None:
        self._store = store

    async def execute(
        self,
        bundle: ContextBundle,
        *,
        permission_layers: tuple[PermissionLayer, ...],
        explicit_denies: tuple[PermissionLayer, ...] = (),
        actor_type: ActorType = ActorType.SERVICE,
        actor_id: str = "repomesh-runtime",
    ) -> None:
        await _validate_items(
            self._store,
            bundle.items,
            repository_id=bundle.repository_id,
            permission_layers=permission_layers,
            explicit_denies=explicit_denies,
        )
        event = _event(
            event_type="ContextBundlePublished",
            actor_type=actor_type,
            actor_id=actor_id,
            aggregate_type="ContextBundle",
            aggregate_id=bundle.id,
            aggregate_version=1,
            project_id=bundle.project_id,
            run_id=bundle.run_id,
            payload={
                "content_hash": bundle.content_hash,
                "item_version_ids": [str(item.version_id) for item in bundle.items],
            },
        )
        await self._store.publish_bundle(bundle, events=(event,))


class AppendContextDelta:
    def __init__(self, store: ContextStore) -> None:
        self._store = store

    async def execute(
        self,
        delta: ContextDelta,
        *,
        permission_layers: tuple[PermissionLayer, ...],
        explicit_denies: tuple[PermissionLayer, ...] = (),
        actor_type: ActorType = ActorType.SERVICE,
        actor_id: str = "repomesh-runtime",
    ) -> None:
        if delta.kind is DeltaKind.EXECUTION_CHANGE:
            raise ContextChangeRequestRequired(
                "execution premise changes require a ChangeRequest and a new bundle"
            )
        bundle = await self._store.get_bundle(delta.bundle_id)
        if bundle is None:
            raise ContextNotFound(f"context bundle not found: {delta.bundle_id}")
        existing = await self._store.list_deltas(bundle.id)
        expected_sequence = len(existing) + 1
        if delta.sequence != expected_sequence:
            raise ContextSequenceConflict(
                f"expected delta sequence {expected_sequence}, got {delta.sequence}"
            )
        existing_version_ids = {
            item.version_id
            for source in (bundle.items, *(current.items for current in existing))
            for item in source
        }
        if any(item.version_id in existing_version_ids for item in delta.items):
            raise ContextConflict("a delta cannot add a version already available to the run")
        await _validate_items(
            self._store,
            delta.items,
            repository_id=bundle.repository_id,
            permission_layers=permission_layers,
            explicit_denies=explicit_denies,
        )
        event = _event(
            event_type="ContextDeltaPublished",
            actor_type=actor_type,
            actor_id=actor_id,
            aggregate_type="ContextBundle",
            aggregate_id=bundle.id,
            aggregate_version=delta.sequence + 1,
            project_id=bundle.project_id,
            run_id=bundle.run_id,
            payload={
                "delta_id": str(delta.id),
                "sequence": delta.sequence,
                "content_hash": delta.content_hash,
            },
        )
        await self._store.append_delta(delta, events=(event,))


class RecordContextAccess:
    def __init__(self, store: ContextStore) -> None:
        self._store = store

    async def execute(
        self,
        *,
        bundle_id: UUID,
        run_id: UUID,
        agent_id: UUID,
        version_id: UUID,
        path: str,
        content_hash: str,
        permission_layers: tuple[PermissionLayer, ...],
        explicit_denies: tuple[PermissionLayer, ...] = (),
        actor_type: ActorType = ActorType.AGENT,
        actor_id: str = "coding-agent",
    ) -> ContextAccessEvent:
        bundle = await self._store.get_bundle(bundle_id)
        if bundle is None:
            raise ContextNotFound(f"context bundle not found: {bundle_id}")
        if bundle.run_id != run_id or bundle.agent_id != agent_id:
            result = ContextAccessResult.DENIED
        else:
            deltas = await self._store.list_deltas(bundle.id)
            items = [*bundle.items, *(item for delta in deltas for item in delta.items)]
            item = next(
                (
                    candidate
                    for candidate in items
                    if candidate.version_id == version_id and candidate.mount_path == path
                ),
                None,
            )
            if item is None:
                result = ContextAccessResult.NOT_FOUND
            elif item.content_hash != content_hash:
                result = ContextAccessResult.HASH_MISMATCH
            else:
                context_object = await self._store.get_object(item.context_object_id)
                if context_object is None:
                    result = ContextAccessResult.NOT_FOUND
                else:
                    decision = evaluate_permission(
                        PermissionRequest(
                            action=ContextAction.READ,
                            scope=context_object.scope,
                            context_object_id=context_object.id,
                            repository_id=bundle.repository_id,
                            path=path,
                        ),
                        layers=permission_layers,
                        explicit_denies=explicit_denies,
                    )
                    result = (
                        ContextAccessResult.ALLOWED
                        if decision.allowed
                        else ContextAccessResult.DENIED
                    )
        access = ContextAccessEvent(
            project_id=bundle.project_id,
            bundle_id=bundle.id,
            run_id=bundle.run_id,
            agent_id=agent_id,
            version_id=version_id,
            path=path,
            content_hash=content_hash,
            result=result,
        )
        event = _event(
            event_type="ContextAccessRecorded",
            actor_type=actor_type,
            actor_id=actor_id,
            aggregate_type="ContextBundle",
            aggregate_id=bundle.id,
            aggregate_version=1 + len(await self._store.list_deltas(bundle.id)),
            project_id=bundle.project_id,
            run_id=bundle.run_id,
            payload={
                "access_id": str(access.id),
                "agent_id": str(agent_id),
                "version_id": str(version_id),
                "path": path,
                "content_hash": content_hash,
                "result": result.value,
            },
        )
        await self._store.record_access(access, events=(event,))
        if result is not ContextAccessResult.ALLOWED:
            raise ContextAccessDenied(result.value)
        return access
