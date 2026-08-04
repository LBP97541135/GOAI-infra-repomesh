import asyncio
from collections.abc import Iterator

import pytest

from repomesh.bootstrap.container import ApplicationContainer
from repomesh.integrations.coding_agents.mock import MockCodingAgent, MockScenario
from repomesh.modules.agent_directory.infrastructure import PostgresAgentDirectory
from repomesh.modules.collaboration import PostgresCollaborationMessageStore
from repomesh.modules.context.infrastructure import PostgresContextStore
from repomesh.modules.project.infrastructure import PostgresProjectTopologyStore
from repomesh.modules.repository_intelligence.infrastructure import PostgresRepositoryCatalog
from repomesh.modules.specification import PostgresSpecificationStore
from repomesh.modules.task_orchestration import PostgresTaskStore
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
        agent_directory=PostgresAgentDirectory(database),
        project_topology_store=PostgresProjectTopologyStore(database),
        repository_catalog=PostgresRepositoryCatalog(database),
        outbox_store=OutboxStore(database),
        task_store=PostgresTaskStore(database),
        collaboration_message_store=PostgresCollaborationMessageStore(database),
        context_store=PostgresContextStore(database),
        specification_store=PostgresSpecificationStore(database),
        mock_coding_agent_factory=lambda scenario: MockCodingAgent(MockScenario(scenario)),
    )
