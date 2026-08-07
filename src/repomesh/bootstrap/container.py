from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from repomesh.modules.agent_directory.ports import AgentDirectory
from repomesh.modules.agent_runtime.ports.agent_team import (
    AgentTeamControlPlane,
    AgentTeamMessenger,
)
from repomesh.modules.agent_runtime.ports.coding_agent import CodingAgent
from repomesh.modules.agent_runtime.runner_store import PostgresRunnerGatewayStore
from repomesh.modules.capability_management import (
    PresetCapabilityAssembler,
    ResolveAgentCapabilities,
)
from repomesh.modules.collaboration.ports import CollaborationMessageStore
from repomesh.modules.context.application import ContextPublicationGateway, GetExecutionContextGrant
from repomesh.modules.context.ports import ContextStore
from repomesh.modules.identity_access import PolicyAuthorizationGateway
from repomesh.modules.project.contracts import ProjectAgentTopologyView, ProjectTopologyReader
from repomesh.modules.project.ports import ProjectTopologyStore
from repomesh.modules.repository_intelligence.application import (
    PlanExecutionBridge,
    PlanIntegrationService,
)
from repomesh.modules.repository_intelligence.application.confirmation import ConfirmationService
from repomesh.modules.repository_intelligence.application.discovery import LLMClient
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog
from repomesh.modules.specification import BuildCodingAgentPackage, SpecificationService
from repomesh.modules.specification.ports import SpecificationStore
from repomesh.modules.tas