#!/usr/bin/env python3
"""Run RepoMesh locally with SQLite when Docker/PostgreSQL is unavailable."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from repomesh.bootstrap import create_app  # noqa: E402
from repomesh.bootstrap.container import ApplicationContainer  # noqa: E402
from repomesh.integrations.coding_agents.mock import MockCodingAgent, MockScenario  # noqa: E402
from repomesh.modules.agent_directory.infrastructure import PostgresAgentDirectory  # noqa: E402
from repomesh.modules.collaboration import PostgresCollaborationMessageStore  # noqa: E402
from repomesh.modules.context.infrastructure import PostgresContextStore  # noqa: E402
from repomesh.modules.project.infrastructure import PostgresProjectTopologyStore  # noqa: E402
from repomesh.modules.repository_intelligence.infrastructure import (  # noqa: E402
    PostgresRepositoryCatalog,
)
from repomesh.modules.specification import PostgresSpecificationStore  # noqa: E402
from repomesh.modules.task_orchestration import PostgresTaskStore  # noqa: E402
from repomesh.persistence import Database  # noqa: E402
from repomesh.persistence.base import ALL_SCHEMAS  # noqa: E402
from repomesh.persistence.outbox import OutboxStore  # noqa: E402


async def build_container(database_path: Path) -> ApplicationContainer:
    database = Database(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    return ApplicationContainer(
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database", type=Path, default=Path(".repomesh-local.db"))
    args = parser.parse_args()
    container = asyncio.run(build_container(args.database.resolve()))
    uvicorn.run(create_app(container), host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
