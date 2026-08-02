from collections.abc import Callable
from dataclasses import dataclass

from repomesh.modules.agent_runtime.ports.coding_agent import CodingAgent
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Process-level dependencies assembled outside business modules."""

    repository_catalog: RepositoryCatalog
    mock_coding_agent_factory: Callable[[str], CodingAgent]
