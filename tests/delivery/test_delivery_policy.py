from uuid import uuid4

import pytest
import pytest_asyncio

from repomesh.modules.delivery.policy import DeliveryPolicy, PostgresDeliveryPolicyStore
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


@pytest_asyncio.fixture
async def database(tmp_path) -> Database:
    instance = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'delivery-policy.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await instance.create_all_for_tests()
    yield instance
    await instance.dispose()


@pytest.mark.asyncio
async def test_repository_policy_overrides_organization_policy(database) -> None:
    organization_id = uuid4()
    repository_id = uuid4()
    store = PostgresDeliveryPolicyStore(database)
    await store.put(
        DeliveryPolicy(
            organization_id=organization_id,
            auto_merge=False,
            required_checks=("unit",),
        )
    )
    await store.put(
        DeliveryPolicy(
            organization_id=organization_id,
            repository_id=repository_id,
            auto_merge=True,
            required_checks=("unit", "integration"),
            required_approvals=2,
        )
    )

    organization = await store.resolve(organization_id)
    repository = await store.resolve(organization_id, repository_id)

    assert not organization.auto_merge
    assert repository.auto_merge
    assert repository.required_checks == ("unit", "integration")
    assert repository.required_approvals == 2


@pytest.mark.asyncio
async def test_policy_uses_compatible_fallback_when_database_is_empty(database) -> None:
    organization_id = uuid4()
    fallback = DeliveryPolicy(
        organization_id=organization_id,
        base_branch="develop",
        required_checks=("legacy-check",),
    )

    resolved = await PostgresDeliveryPolicyStore(database).resolve(
        organization_id,
        fallback=fallback,
    )

    assert resolved == fallback
