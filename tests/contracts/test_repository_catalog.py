from uuid import uuid4

import pytest

from repomesh.modules.repository_intelligence.domain import (
    AutoCard,
    RepositoryProfile,
)
from repomesh.modules.repository_intelligence.infrastructure import InMemoryRepositoryCatalog


@pytest.mark.asyncio
async def test_repository_catalog_contract() -> None:
    catalog = InMemoryRepositoryCatalog()
    profile = RepositoryProfile(
        name="orders",
        url="https://github.com/example/orders",
        description="Order service",
    )

    await catalog.add(profile)

    assert await catalog.get(profile.id) == profile
    assert await catalog.get(uuid4()) is None
    assert await catalog.list() == [profile]