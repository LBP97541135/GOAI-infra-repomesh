from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from repomesh.modules.context.application import (
    AppendContextDelta,
    PublishContextBundle,
    PublishContextObject,
    PublishContextVersion,
    RecordContextAccess,
)
from repomesh.modules.context.contracts import (
    ContextAccessResult,
    ContextAction,
    ContextObjectType,
    ContextScope,
)
from repomesh.modules.context.domain import (
    ContextAccessDenied,
    ContextBundle,
    ContextBundleItem,
    ContextChangeRequestRequired,
    ContextDelta,
    ContextObject,
    ContextObjectVersion,
    ContextPermissionDenied,
    ContextSequenceConflict,
    DeltaKind,
    PermissionLayer,
)
from repomesh.modules.context.infrastructure import InMemoryContextStore


def content_hash(character: str) -> str:
    return f"sha256:{character * 64}"


def permission_layers(
    repository_id: UUID,
    *,
    actions: frozenset[ContextAction] = frozenset(
        {ContextAction.MOUNT, ContextAction.READ}
    ),
) -> tuple[PermissionLayer, ...]:
    common = {
        "actions": actions,
        "scopes": frozenset({ContextScope.PROJECT_SHARED}),
        "repository_ids": frozenset({repository_id}),
        "path_patterns": ("context/**",),
    }
    return tuple(
        PermissionLayer(name=name, **common)
        for name in ("agent_policy", "project_membership", "task_spec", "run_delegation")
    )


async def publish_object(
    store: InMemoryContextStore,
    *,
    project_id: UUID,
    character: str = "a",
    title: str = "Engineering specification",
) -> tuple[ContextObject, ContextObjectVersion]:
    context_object = ContextObject(
        organization_id=uuid4(),
        project_id=project_id,
        object_type=ContextObjectType.ENGINEERING_SPEC,
        scope=ContextScope.PROJECT_SHARED,
        owner_subject="project-manager",
        title=title,
    )
    version = ContextObjectVersion(
        context_object_id=context_object.id,
        version=1,
        content_uri=f"s3://contexts/{context_object.id}/1.md",
        content_hash=content_hash(character),
        mime_type="text/markdown",
        size_bytes=100,
        created_by="project-manager",
    )
    await PublishContextObject(store).execute(context_object, version)
    return context_object, version


def make_bundle(
    *,
    project_id: UUID,
    repository_id: UUID,
    context_object: ContextObject,
    version: ContextObjectVersion,
) -> ContextBundle:
    return ContextBundle.create(
        project_id=project_id,
        run_id=uuid4(),
        task_spec_version_id=uuid4(),
        agent_id=uuid4(),
        role="worker",
        repository_id=repository_id,
        base_sha="abc123",
        workspace_id=uuid4(),
        items=(
            ContextBundleItem(
                context_object_id=context_object.id,
                version_id=version.id,
                content_hash=version.content_hash,
                mount_path="context/project/spec.md",
                required_read=True,
            ),
        ),
        allowed_tools=("pytest",),
        allowed_paths=("src/**",),
        denied_paths=(".github/**",),
        network_policy=(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_versions_are_append_only_and_publish_events() -> None:
    store = InMemoryContextStore()
    context_object, first = await publish_object(store, project_id=uuid4())
    second = ContextObjectVersion(
        context_object_id=context_object.id,
        version=2,
        content_uri=f"s3://contexts/{context_object.id}/2.md",
        content_hash=content_hash("b"),
        mime_type="text/markdown",
        size_bytes=120,
        created_by="project-manager",
        supersedes_version_id=first.id,
    )

    await PublishContextVersion(store).execute(second)

    assert await store.latest_version(context_object.id) == second
    assert [event.event_type for event in store.events] == [
        "ContextObjectVersionPublished",
        "ContextObjectVersionPublished",
    ]


@pytest.mark.asyncio
async def test_bundle_requires_mount_permission_and_exact_stored_hash() -> None:
    store = InMemoryContextStore()
    project_id = uuid4()
    repository_id = uuid4()
    context_object, version = await publish_object(store, project_id=project_id)
    bundle = make_bundle(
        project_id=project_id,
        repository_id=repository_id,
        context_object=context_object,
        version=version,
    )

    restricted_layers = list(permission_layers(repository_id))
    restricted_layers[2] = PermissionLayer(
        name="task_spec",
        actions=frozenset({ContextAction.READ}),
        scopes=frozenset({ContextScope.PROJECT_SHARED}),
        repository_ids=frozenset({repository_id}),
        path_patterns=("context/**",),
    )
    with pytest.raises(ContextPermissionDenied, match="task_spec:action"):
        await PublishContextBundle(store).execute(
            bundle,
            permission_layers=tuple(restricted_layers),
        )

    await PublishContextBundle(store).execute(
        bundle, permission_layers=permission_layers(repository_id)
    )
    assert await store.get_bundle(bundle.id) == bundle
    assert store.events[-1].event_type == "ContextBundlePublished"


@pytest.mark.asyncio
async def test_delta_is_ordered_and_execution_changes_require_change_request() -> None:
    store = InMemoryContextStore()
    project_id = uuid4()
    repository_id = uuid4()
    context_object, version = await publish_object(store, project_id=project_id)
    bundle = make_bundle(
        project_id=project_id,
        repository_id=repository_id,
        context_object=context_object,
        version=version,
    )
    await PublishContextBundle(store).execute(
        bundle, permission_layers=permission_layers(repository_id)
    )
    second_object, second_version = await publish_object(
        store, project_id=project_id, character="b", title="Decision"
    )
    item = ContextBundleItem(
        context_object_id=second_object.id,
        version_id=second_version.id,
        content_hash=second_version.content_hash,
        mount_path="context/decisions/decision.md",
    )
    out_of_order = ContextDelta.create(
        bundle_id=bundle.id,
        sequence=2,
        summary="Additional implementation evidence",
        items=(item,),
    )

    with pytest.raises(ContextSequenceConflict):
        await AppendContextDelta(store).execute(
            out_of_order, permission_layers=permission_layers(repository_id)
        )

    execution_change = ContextDelta.create(
        bundle_id=bundle.id,
        sequence=1,
        summary="Acceptance criteria changed",
        items=(item,),
        kind=DeltaKind.EXECUTION_CHANGE,
    )
    with pytest.raises(ContextChangeRequestRequired):
        await AppendContextDelta(store).execute(
            execution_change, permission_layers=permission_layers(repository_id)
        )


@pytest.mark.asyncio
async def test_allowed_and_denied_reads_are_both_audited() -> None:
    store = InMemoryContextStore()
    project_id = uuid4()
    repository_id = uuid4()
    context_object, version = await publish_object(store, project_id=project_id)
    bundle = make_bundle(
        project_id=project_id,
        repository_id=repository_id,
        context_object=context_object,
        version=version,
    )
    layers = permission_layers(repository_id)
    await PublishContextBundle(store).execute(bundle, permission_layers=layers)

    access = await RecordContextAccess(store).execute(
        bundle_id=bundle.id,
        run_id=bundle.run_id,
        agent_id=bundle.agent_id,
        version_id=version.id,
        path="context/project/spec.md",
        content_hash=version.content_hash,
        permission_layers=layers,
    )
    assert access.result is ContextAccessResult.ALLOWED

    with pytest.raises(ContextAccessDenied, match="denied"):
        await RecordContextAccess(store).execute(
            bundle_id=bundle.id,
            run_id=uuid4(),
            agent_id=uuid4(),
            version_id=version.id,
            path="context/project/spec.md",
            content_hash=version.content_hash,
            permission_layers=layers,
        )

    recorded = await store.list_access_events(bundle.run_id)
    assert [event.result for event in recorded] == [
        ContextAccessResult.ALLOWED,
        ContextAccessResult.DENIED,
    ]
    assert [event.event_type for event in store.events[-2:]] == [
        "ContextAccessRecorded",
        "ContextAccessRecorded",
    ]
