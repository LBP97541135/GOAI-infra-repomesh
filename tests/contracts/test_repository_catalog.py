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


@pytest.mark.asyncio
async def test_capability_profile_is_whole_replacement_and_clearable() -> None:
    catalog = InMemoryRepositoryCatalog()
    profile = RepositoryProfile(
        name="test-assets",
        url="https://github.com/example/test-assets",
        description="Cross-repo scenario library",
    )
    await catalog.add(profile)

    assert (await catalog.get(profile.id)).capability_profile is None

    set_once = await catalog.update_capability_profile(
        profile.id, capability_profile="cross-repo-test-team"
    )
    assert set_once is not None
    assert set_once.capability_profile == "cross-repo-test-team"

    # Retry-safe: the same request again changes nothing, and a null clears it.
    replayed = await catalog.update_capability_profile(
        profile.id, capability_profile="cross-repo-test-team"
    )
    assert replayed == set_once
    cleared = await catalog.update_capability_profile(profile.id, capability_profile=None)
    assert cleared is not None
    assert cleared.capability_profile is None
    assert await catalog.update_capability_profile(uuid4(), capability_profile=None) is None
