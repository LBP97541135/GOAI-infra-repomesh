from collections.abc import Callable
from dataclasses import dataclass

from repomesh.modules.agent_runtime.ports.coding_agent import CodingAgent
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog
from repomesh.persistence import Database
from repomesh.persistence.outbox import OutboxStore


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Process-level dependencies assembled outside business modules."""

    database: Database
    repository_catalog: RepositoryCatalog
    outbox_store: OutboxStore
    mock_coding_agent_factory: Callable[[str], CodingAgent]