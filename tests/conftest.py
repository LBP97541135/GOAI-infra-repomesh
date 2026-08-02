import asyncio
from collections.abc import Iterator

import pytest

from repomesh.bootstrap.container import ApplicationContainer
from repomesh.integrations.coding_agents.mock import MockCodingAgent, MockScenario
from repomesh.modules.repository_intelligence.infrastructure import PostgresRepositoryCatalog
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS
from repomesh.persistence.outbox import OutboxStore


@pytest.fixture
def application_container(tmp_path: object) -> Iterator[ApplicationContainer]:
    database_path = tmp_path.joinpath("repomesh-api.db")
    database = Database(
        f"sqlite+aiosqlite:///{database_path}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    asyncio.run(database.create_all_for_tests())
    yield ApplicationContainer(
        database=database,
        repository_catalog=PostgresRepositoryCatalog(database),
        outbox_store=OutboxStore(database),
        mock_coding_agent_factory=lambda scenario: MockCodingAgent(MockScenario(scenario)),
    )
