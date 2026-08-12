from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from repomesh.modules.repository_intelligence.application import RegisterRepository
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import (
    PostgresRepositoryCatalog,
    RepositoryAlreadyExists,
)
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS
from repomesh.persistence.models import AuditEventRecord, StateEventRecord
from repomesh.persistence.outbox import OutboxStore


@pytest_asyncio.fixture
async def database(tmp_path: object) -> Database:
    database_path = tmp_path.joinpath("repomesh-persistence.db")
    instance = Database(
        f"sqlite+aiosqlite:///{database_path}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await instance.create_all_for_tests()
    yield instance
    await instance.dispose()


@pytest.mark.asyncio
async def test_repository_and_events_commit_atomically(database: Database) -> None:
    catalog = PostgresRepositoryCatalog(database)
    outbox = OutboxStore(database)
    profile = RepositoryProfile(
        name="orders",
        url="https://github.com/example/orders",
        description="Order service",
        topics=("orders",),
        languages=("python",),
    )

    await RegisterRepository(catalog).execute(profile)

    assert await catalog.get(profile.id) == profile
    assert await outbox.pending_count() == 1
    message = (await outbox.pending())[0]
    assert message.event_type == "RepositoryRegistered"
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(StateEventRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(AuditEventRecord)) == 1

    assert await outbox.mark_published(message.event_id, datetime.now(UTC))
    assert await outbox.pending_count() == 0


@pytest.mark.asyncio
async def test_duplicate_repository_rolls_back_its_events(database: Database) -> None:
    catalog = PostgresRepositoryCatalog(database)
    outbox = OutboxStore(database)
    first = RepositoryProfile(
        name="orders",
        url="https://github.com/example/orders",
        description="Order service",
    )
    duplicate = RepositoryProfile(
        name="orders-copy",
        url=first.url,
        description="Duplicate",
    )
    await RegisterRepository(catalog).execute(first)

    with pytest.raises(RepositoryAlreadyExists):
        await RegisterRepository(catalog).execute(duplicate)

    assert len(await catalog.list()) == 1
    assert await outbox.pending_count() == 1


@pytest.mark.asyncio
async def test_a_repository_stores_and_returns_its_verification_commands(
    database: Database,
) -> None:
    """Defect A-19: the catalog is the source of truth for how a repo is tested.

    Round-tripped through the real store rather than asserted on the dataclass,
    because the whole point of the column is that a *later* round — a different
    process, days later — reads back what an operator wrote once.
    """

    catalog = PostgresRepositoryCatalog(database)
    verified = RepositoryProfile(
        name="checkout",
        url="https://github.com/example/checkout",
        test_commands=("python scripts/run_tests.py",),
        test_paths=("tests/**",),
    )
    silent = RepositoryProfile(
        name="legacy",
        url="https://github.com/example/legacy",
    )

    await RegisterRepository(catalog).execute(verified)
    await RegisterRepository(catalog).execute(silent)

    assert (await catalog.get(verified.id)).test_commands == ("python scripts/run_tests.py",)
    # Defect A-21: the command and the directory it reads are one fact stored
    # in two columns, and both have to survive the round trip. Supplying the
    # command without the path is what voided a whole live run.
    assert (await catalog.get(verified.id)).test_paths == ("tests/**",)
    assert (await catalog.get(silent.id)).test_paths == ()
    # Empty stays empty. A default invented here would put a command that does
    # not exist into a real Runner dispatch.
    assert (await catalog.get(silent.id)).test_commands == ()
    assert {profile.name: profile.test_commands for profile in await catalog.list()} == {
        "checkout": ("python scripts/run_tests.py",),
        "legacy": (),
    }
