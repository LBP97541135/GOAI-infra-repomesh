from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from repomesh.integrations.scm.contracts import SCMAdapter
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
from repomesh.modules.task_orchestration import TaskOrchestrator
from repomesh.modules.task_orchestration.contracts import TaskReportGateway
from repomesh.modules.task_orchestration.ports import TaskStore
from repomesh.persistence import Database
from repomesh.persistence.outbox import OutboxStore
from repomesh.settings import get_settings


class ReadinessProbe(Protocol):
    async def health(self) -> bool: ...


class AsyncCloseable(Protocol):
    async def close(self) -> None: ...


class BackgroundService(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Process-level dependencies assembled outside business modules."""

    database: Database
    agent_directory: AgentDirectory
    project_topology_store: ProjectTopologyStore
    repository_catalog: RepositoryCatalog
    outbox_store: OutboxStore
    task_store: TaskStore
    collaboration_message_store: CollaborationMessageStore
    context_store: ContextStore
    specification_store: SpecificationStore
    mock_coding_agent_factory: Callable[[str], CodingAgent]
    agent_team_control_plane: AgentTeamControlPlane | None = None
    agent_team_messenger: AgentTeamMessenger | None = None
    agentteams_probe: ReadinessProbe | None = None
    agentteams_required: bool = False
    external_resources: tuple[AsyncCloseable, ...] = ()
    background_services: tuple[BackgroundService, ...] = ()
    task_report_gateway: TaskReportGateway | None = None
    task_assigner: TaskOrchestrator | None = None
    scm_adapter: SCMAdapter | None = None

    def capability_assembler(self) -> PresetCapabilityAssembler:
        return PresetCapabilityAssembler()

    def agent_capabilities(self) -> ResolveAgentCapabilities:
        return ResolveAgentCapabilities(self.agent_directory, self.capability_assembler())

    def native_agent_registration(self):
        from repomesh.integrations.agentteams import RegisterNativeAgent

        if self.agent_team_control_plane is None:
            raise RuntimeError("AgentTeams control plane is not configured")
        return RegisterNativeAgent(
            self.agent_team_control_plane,
            self.agent_directory,
            worker_task_control_url=get_settings().worker_task_control_url,
        )

    async def start(self) -> None:
        for service in self.background_services:
            await service.start()

    async def is_agentteams_ready(self) -> bool:
        if not self.agentteams_required:
            return True
        return self.agentteams_probe is not None and await self.agentteams_probe.health()

    def specification_service(self) -> SpecificationService:
        return SpecificationService(
            self.agent_directory,
            self.project_topology_store,
            self.specification_store,
            ContextPublicationGateway(self.context_store),
            PolicyAuthorizationGateway(),
        )

    def coding_agent_package_builder(self) -> BuildCodingAgentPackage:
        return BuildCodingAgentPackage(
            self.agent_directory,
            self.project_topology_store,
            self.task_store,
            self.specification_store,
            PolicyAuthorizationGateway(),
        )

    def topology_reader(self) -> ProjectTopologyReader:
        """Adapt ProjectTopologyStore to ProjectTopologyReader."""

        store = self.project_topology_store

        class _Adapter:
            async def get_view(self, project_id: UUID) -> ProjectAgentTopologyView | None:
                topology = await store.get(project_id)
                return topology.to_view() if topology else None

        return _Adapter()

    async def confirmation_service(self, llm_client: LLMClient) -> ConfirmationService:
        profiles = await self.repository_catalog.list()
        by_name = {p.name: p for p in profiles}
        return ConfirmationService(llm_client, by_name)

    def plan_integration_service(self, llm_client: LLMClient) -> PlanIntegrationService:
        return PlanIntegrationService(llm_client)

    def delivery_service(self):
        from repomesh.modules.delivery import DeliveryService, PostgresChangeSetStore

        return DeliveryService(PostgresChangeSetStore(self.database))

    def scm_webhook_event_store(self):
        from repomesh.integrations.scm.webhook_store import (
            PostgresSCMWebhookEventStore,
        )

        return PostgresSCMWebhookEventStore(self.database)

    def changeset_scm_coordinator(self):
        from repomesh.integrations.scm import (
            ChangeSetSCMCoordinator,
            GitBranchPublisher,
        )

        if self.scm_adapter is None:
            raise RuntimeError("SCM adapter is not configured")
        return ChangeSetSCMCoordinator(
            self.delivery_service(),
            self.repository_catalog,
            self.scm_adapter,
            GitBranchPublisher(get_settings().runner_workspace_root),
        )

    def plan_execution_bridge(self) -> PlanExecutionBridge:
        if self.task_assigner is None:
            raise RuntimeError(
                "Task orchestration is unavailable; configure AgentTeams messaging first"
            )
        return PlanExecutionBridge(
            specifications=self.specification_service(),
            tasks=self.task_assigner,
            topologies=self.topology_reader(),
            catalog=self.repository_catalog,
        )

    def governed_worker_task_service(self):
        from repomesh.integrations.agentteams.governed_assignment import (
            CreateGovernedWorkerTask,
        )

        if self.task_assigner is None:
            raise RuntimeError(
                "Task orchestration is unavailable; configure AgentTeams messaging first"
            )
        return CreateGovernedWorkerTask(
            self.task_assigner,
            self.specification_service(),
        )

    def runner_gateway(self):
        from repomesh.integrations.runner.gateway import RunnerControlGateway

        return RunnerControlGateway(PostgresRunnerGatewayStore(self.database), self.task_store)

    def worker_task_dispatcher(self):
        from repomesh.integrations.runner import DispatchWorkerTask, RunnerContextMaterializer
        from repomesh.integrations.workspace import GitWorktreeManager

        settings = get_settings()
        return DispatchWorkerTask(
            self.coding_agent_package_builder(),
            GetExecutionContextGrant(self.context_store),
            self.agent_capabilities(),
            self.repository_catalog,
            GitWorktreeManager(settings.runner_workspace_root),
            RunnerContextMaterializer(settings.capability_root),
            self.runner_gateway(),
        )

    def worker_execution_service(self):
        from repomesh.integrations.runner import (
            StartAssignedWorkerTask,
            StartWorkerTaskExecution,
        )
        from repomesh.integrations.workspace import GitWorktreeManager
        from repomesh.modules.agent_runtime.preflight_store import PostgresWorkerPreflightStore
        from repomesh.modules.context.application import PublishContextBundle
        from repomesh.modules.task_orchestration import TaskExecutionState

        states = TaskExecutionState(self.agent_directory, self.task_store)
        execution = StartWorkerTaskExecution(
            states,
            self.worker_task_dispatcher(),
            self.task_report_gateway,
        )
        settings = get_settings()
        return StartAssignedWorkerTask(
            self.agent_directory,
            self.task_store,
            self.coding_agent_package_builder(),
            self.agent_capabilities(),
            self.repository_catalog,
            GitWorktreeManager(settings.runner_workspace_root),
            PublishContextBundle(self.context_store),
            execution,
            states,
            PostgresWorkerPreflightStore(self.database),
            self.task_report_gateway,
        )

    def worker_preflight_service(self):
        from repomesh.integrations.runner import AssessAssignedWorkerTask
        from repomesh.modules.agent_runtime.preflight_store import PostgresWorkerPreflightStore
        from repomesh.modules.task_orchestration import TaskExecutionState

        states = TaskExecutionState(self.agent_directory, self.task_store)
        return AssessAssignedWorkerTask(
            self.agent_directory,
            self.task_store,
            PostgresWorkerPreflightStore(self.database),
            states,
            self.task_report_gateway,
        )

    async def close(self) -> None:
        try:
            for service in reversed(self.background_services):
                await service.close()
            for resource in reversed(self.external_resources):
                await resource.close()
        finally:
            await self.database.dispose()
