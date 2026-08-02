from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio

from repomesh.modules.context.application import (
    PublishContextBundle,
    PublishContextObject,
    RecordContextAccess,
)
from repomesh.modules.context.contracts import (
    ContextAction,
    ContextObjectType,
    ContextScope,
)
from repomesh.modules.context.domain import (
    ContextAlreadyExists,
    ContextBundle,
    ContextBundleItem,
    ContextObject,
    ContextObjectVersion,
    PermissionLayer,
)
from repomesh.modules.context.infrastructure import PostgresContextStore
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS
from repomesh.persistence.outbox import OutboxStore

CONTENT_HASH = f"sha256:{'c' * 64}"


@pytest_asyncio.fixture
async def database(tmp_path: object) -> Database:
    database_path = tmp_path.joinpath("repomesh-context.db")
    instance = Database(
        f"sqlite+aiosqlite:///{database_path}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await instance.create_all_for_tests()
    yield instance
    await instance.dispose()


def permission_layers(repository_id: object) -> tuple[PermissionLayer, ...]:
    common = {
        "actions": frozenset({ContextAction.MOUNT, ContextAction.READ}),
        "scopes": frozenset({ContextScope.PROJECT_SHARED}),
        "repository_ids": frozenset({repository_id}),
        "path_patterns": ("context/**",),
    }
    return tuple(
        PermissionLayer(name=name, **common)
        for name in ("agent_policy", "project_membership", "task_spec", "run_delegation")
    )


@pytest.mark.asyncio
async def test_context_lifecycle_round_trips_with_transactional_events(database: Database) -> None:
    store = PostgresContextStore(database)
    outbox = OutboxStore(database)
    project_id = uuid4()
    repository_id = uuid4()
    context_object = ContextObject(
        organization_id=uuid4(),
        project_id=project_id,
        object_type=ContextObjectType.CONTRACT,
        scope=ContextScope.PROJECT_SHARED,
        owner_subject="repository-manager",
        title="Orders contract",
    )
    version = ContextObjectVersion(
        context_object_id=context_object.id,
        version=1,
        content_uri=f"s3://contexts/{context_object.id}/1.md",
        content_hash=CONTENT_HASH,
        mime_type="text/markdown",
        size_bytes=42,
        created_by="repository-manager",
    )
    await PublishContextObject(store).execute(context_object, version)
    bundle = ContextBundle.create(
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
                mount_path="context/contracts/orders.md",
                required_read=True,
            ),
        ),
        allowed_tools=("pytest",),
        allowed_paths=("src/**",),
        denied_paths=(".github/**",),
        network_policy=(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    layers = permission_layers(repository_id)
    await PublishContextBundle(store).execute(bundle, permission_layers=layers)
    await RecordContextAccess(store).execute(
        bundle_id=bundle.id,
        run_id=bundle.run_id,
        agent_id=bundle.agent_id,
        version_id=version.id,
        path="context/contracts/orders.md",
        content_hash=version.content_hash,
        permission_layers=layers,
    )

    assert await store.get_object(context_object.id) == context_object
    assert await store.get_version(version.id) == version
    assert await store.get_bundle(bundle.id) == bundle
    assert len(await store.list_access_events(bundle.run_id)) == 1
    assert {message.event_type for message in await outbox.pending()} == {
        "ContextObjectVersionPublished",
        "ContextBundlePublished",
        "ContextAccessRecorded",
    }


@pytest.mark.asyncio
async def test_duplicate_context_rolls_back_its_outbox_event(database: Database) -> None:
    store = PostgresContextStore(database)
    outbox = OutboxStore(database)
    context_object = ContextObject(
        organization_id=uuid4(),
        project_id=uuid4(),
        object_type=ContextObjectType.PRD,
        scope=ContextScope.PROJECT_SHARED,
        owner_subject="project-manager",
        title="PRD",
    )
    version = ContextObjectVersion(
        context_object_id=context_object.id,
        version=1,
        content_uri=f"s3://contexts/{context_object.id}/1.md",
        content_hash=CONTENT_HASH,
        mime_type="text/markdown",
        size_bytes=42,
        created_by="project-manager",
    )
    publisher = PublishContextObject(store)
    await publisher.execute(context_object, version)

    with pytest.raises(ContextAlreadyExists):
        await publisher.execute(context_object, version)

    assert await outbox.pending_count() == 1
