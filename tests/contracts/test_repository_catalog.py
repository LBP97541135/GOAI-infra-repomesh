from uuid import uuid4

import pytest

from repomesh.modules.repository_intelligence.domain import (
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

    updated = await catalog.update_verification(
        profile.id,
        test_commands=("python scripts/run_tests.py",),
        test_paths=("tests/**",),
    )

    assert updated is not None
    assert updated.id == profile.id
    assert updated.name == profile.name
    assert updated.test_commands == ("python scripts/run_tests.py",)
    assert updated.test_paths == ("tests/**",)
    assert await catalog.get(profile.id) == updated
    assert (
        await catalog.update_verification(
            uuid4(),
            test_commands=("python scripts/run_tests.py",),
            test_paths=("tests/**",),
        )
        is None
    )
