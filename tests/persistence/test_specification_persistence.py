import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio

from repomesh.modules.specification import (
    PostgresSpecificationStore,
    Specification,
    SpecificationContent,
    SpecificationVersion,
)
from repomesh.modules.specification.contracts import SpecificationKind
from repomesh.modules.specification.infrastructure import SpecificationVersionRecord
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


@pytest_asyncio.fixture
async def database(tmp_path: object) -> Database:
    database_path = tmp_path.joinpath("repomesh-specification.db")
    instance = Database(
        f"sqlite+aiosqlite:///{database_path}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await instance.create_all_for_tests()
    yield instance
    await instance.dispose()


@pytest.mark.asyncio
async def test_specification_versions_survive_database_round_trip(
    database: Database,
) -> None:
    store = PostgresSpecificationStore(database)
    specification_id = uuid4()
    owner_id = uuid4()
    first_version = SpecificationVersion(
        specification_id=specification_id,
        version=1,
        created_by_agent_id=owner_id,
        content=SpecificationContent(
            goal="Update pricing API",
            acceptance=("Old clients remain compatible",),
            allowed_paths=("src/pricing/**",),
        ),
    )
    specification = Specification(
        id=specification_id,
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        kind=SpecificationKind.REPOSITORY,
        title="Pricing repository specification",
        owner_agent_id=owner_id,
        current_version=first_version,
    )
    await store.add(
        specification,
        idempotency_key="persistent-spec",
        request_fingerprint=f"sha256:{'a' * 64}",
    )
    second_version = SpecificationVersion(
        specification_id=specification.id,
        version=2,
        supersedes_version_id=first_version.id,
        created_by_agent_id=owner_id,
        content=SpecificationContent(
            goal="Update pricing and tax APIs",
            acceptance=("Old clients remain compatible", "Tax tests pass"),
            allowed_paths=("src/pricing/**", "src/tax/**"),
        ),
    )
    revised = specification.revise(second_version)
    await store.update(revised, expected_revision=specification.revision)

    restored = await store.get(specification.id)
    original = await store.get_version(specification.id, 1)
    replay = await store.get_by_idempotency_key("persistent-spec")

    assert restored == revised
    assert original == first_version
    assert replay is not None and replay[0] == revised


@pytest.mark.asyncio
async def test_versions_persisted_before_forbidden_paths_load_with_empty_set(
    database: Database,
) -> None:
    store = PostgresSpecificationStore(database)
    specification_id = uuid4()
    legacy_content = {
        "goal": "Update pricing API",
        "acceptance": ["Old clients remain compatible"],
        "scope": [],
        "constraints": [],
        "tests": [],
        "dependencies": [],
        "allowed_paths": ["src/pricing/**"],
        "interface_changes": [],
    }
    encoded = json.dumps(legacy_content, sort_keys=True, separators=(",", ":")).encode()
    legacy_hash = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    async with database.transaction() as session:
        session.add(
            SpecificationVersionRecord(
                id=uuid4(),
                specification_id=specification_id,
                version=1,
                content=legacy_content,
                content_hash=legacy_hash,
                created_by_agent_id=uuid4(),
                supersedes_version_id=None,
                created_at=datetime.now(UTC),
            )
        )

    restored = await store.get_version(specification_id, 1)

    assert restored is not None
    assert restored.content.forbidden_paths == ()
    assert restored.content_hash == legacy_hash
