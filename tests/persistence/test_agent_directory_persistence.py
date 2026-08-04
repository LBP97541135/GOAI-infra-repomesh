from uuid import uuid4

import pytest
import pytest_asyncio

from repomesh.modules.agent_directory.application import CreateAgent, CreateAgentRequest
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.infrastructure import PostgresAgentDirectory
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS
from repomesh.persistence.outbox import OutboxStore


@pytest_asyncio.fixture
async def database(tmp_path: object) -> Database:
    database_path = tmp_path.joinpath("repomesh-agent-directory.db")
    instance = Database(
        f"sqlite+aiosqlite:///{database_path}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await instance.create_all_for_tests()
    yield instance
    await instance.dispose()


@pytest.mark.asyncio
async def test_agent_principal_and_registration_event_commit_atomically(
    database: Database,
) -> None:
    directory = PostgresAgentDirectory(database)
    create = CreateAgent(directory)
    created = await create.execute(
        CreateAgentRequest(
            organization_id=uuid4(),
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="native-persistence-manager",
        ),
        idempotency_key="persistent-org-manager-v1",
    )

    stored = await directory.get(created.principal.id)
    replayed = await create.execute(
        CreateAgentRequest(
            organization_id=created.principal.organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="native-persistence-manager",
        ),
        idempotency_key="persistent-org-manager-v1",
    )
    events = await OutboxStore(database).pending(limit=10)

    assert stored is not None
    assert stored.to_view() == created.principal
    assert replayed.principal.id == created.principal.id
    assert [event.event_type for event in events] == ["AgentPrincipalRegistered"]
