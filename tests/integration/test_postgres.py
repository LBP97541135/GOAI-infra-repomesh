import os
from uuid import uuid4

import pytest

from repomesh.modules.repository_intelligence.application import RegisterRepository
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import PostgresRepositoryCatalog
from repomesh.persistence import Database
from repomesh.persistence.outbox import OutboxStore

POSTGRES_URL = os.getenv("REPOMESH_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="REPOMESH_TEST_POSTGRES_URL is not configured"),
]


@pytest.mark.asyncio
async def test_postgres_repository_transaction_and_outbox() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    catalog = PostgresRepositoryCatalog(database)
    outbox = OutboxStore(database)
    unique = uuid4().hex
    profile = RepositoryProfile(
        name=f"integration-{unique}",
        url=f"https://github.com/example/{unique}",
        description="PostgreSQL integration test",
    )
    try:
        await RegisterRepository(catalog).execute(profile)

        assert await catalog.get(profile.id) == profile
        assert any(message.payload["url"] == profile.url for message in await outbox.pending())
    finally:
        await database.dispose()
