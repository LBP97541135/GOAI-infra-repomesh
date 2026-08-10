from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from repomesh.api.router import api_router
from repomesh.bootstrap.container import ApplicationContainer, AsyncCloseable
from repomesh.integrations.agentteams import (
    AgentTeamsControlPlaneClient,
    AgentTeamsMatrixClient,
    AgentTeamsMatrixIdentityVerifier,
    AgentTeamsMatrixInboundPoller,
)
from repomesh.integrations.agentteams.task_publishing import (
    AgentTeamsObjectTaskPublisher,
    AgentTeamsTaskPublisher,
)
from repomesh.integrations.coding_agents.mock import MockCodingAgent, MockScenario
from repomesh.integrations.llm import make_llm_client
from repomesh.integrations.scm import (
    ChangeSetSCMCoordinator,
    DeliveryReconciler,
    GitHubAdapter,
    GitHubObservationPoller,
    GitHubObservationProcessor,
    SCMCommandDispatcher,
    SCMObservationReplayWorker,
)
from repomesh.integrations.scm.github_auth import (
    GitHubAppTokenProvider,
    private_key_file_loader,
)
from repomesh.modules.agent_directory.infrastructure import PostgresAgentDirectory
from repomesh.modules.collaboration import (
    PostgresCollaborationMessageStore,
    PostgresProcessedMatrixEventStore,
    ProcessMatrixTaskReport,
    SendCollaborationMessage,
)
from repomesh.modules.context.infrastructure import PostgresContextStore
from repomesh.modules.delivery import (
    DeliveryService,
    PostgresChangeSetStore,
    PostgresSCMCommandStore,
    PostgresSCMObservationStore,
    PostgresSCMPollCursorStore,
    SCMCommandService,
    SCMObservationService,
    SCMPollCursorService,
)
from repomesh.modules.identity_access import PolicyAuthorizationGateway
from repomesh.modules.project.infrastructure import PostgresProjectTopologyStore
from repomesh.modules.repository_intelligence.infrastructure import PostgresRepositoryCatalog
from repomesh.modules.review_validation import (
    PostgresValidationSnapshotStore,
    ValidationSnapshotService,
)
from repomesh.modules.specification import PostgresSpecificationStore
from repomesh.modules.task_orchestration import PostgresTaskStore, TaskOrchestrator
from repomesh.persistence import Database
from repomesh.persistence.outbox import OutboxStore
from repomesh.settings import get_settings
from repomesh_runner.telemetry import setup_tracing


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    try:
        await application.state.container.start()
        yield
    finally:
        await application.state.container.close()


def build_default_container() -> ApplicationContainer:
    settings = get_settings()
    task_publisher = AgentTeamsTaskPublisher(settings.agentteams_storage_root)
    if (
        settings.agentteams_storage_endpoint
        and settings.agentteams_storage_access_key
        and settings.agentteams_storage_secret_key
    ):
        task_publisher = AgentTeamsObjectTaskPublisher(
            settings.agentteams_storage_endpoint,
            settings.agentteams_storage_access_key,
            settings.agentteams_storage_secret_key,
            settings.agentteams_storage_bucket,
        )
    database = Database(settings.database_url)
    control_plane = AgentTeamsControlPlaneClient(
        settings.agentteams_controller_url,
        token=settings.agentteams_controller_token,
    )
    messenger = (
        AgentTeamsMatrixClient(
            settings.agentteams_matrix_url,
            settings.agentteams_matrix_access_token,
            control_plane=control_plane,
        )
        if settings.agentteams_matrix_access_token
        else None
    )
    resources: tuple[AsyncCloseable, ...] = (
        (control_plane, messenger) if messenger is not None else (control_plane,)
    )
    scm_adapter = None
    scm_token_provider = None
    if settings.github_app_id and settings.github_app_private_key_file:
        scm_token_provider = GitHubAppTokenProvider(
            settings.github_app_id,
            private_key_file_loader(settings.github_app_private_key_file),
        )
        scm_adapter = GitHubAdapter(scm_token_provider)
        resources = (*resources, scm_adapter, scm_token_provider)
    agent_directory = PostgresAgentDirectory(database)
    topology_store = PostgresProjectTopologyStore(database)
    task_store = PostgresTaskStore(database)
    collaboration_store = PostgresCollaborationMessageStore(database)
    repository_catalog = PostgresRepositoryCatalog(database)
    background_services = ()
    task_report_gateway = None
    if messenger is not None:
        collaboration = SendCollaborationMessage(
            agent_directory,
            topology_store,
            PolicyAuthorizationGateway(),
            collaboration_store,
            messenger,
        )
        tasks = TaskOrchestrator(
            agent_directory,
            topology_store,
            task_store,
            collaboration,
            task_publisher,
        )
        task_report_gateway = tasks
        inbound = ProcessMatrixTaskReport(
            agent_directory,
            topology_store,
            AgentTeamsMatrixIdentityVerifier(control_plane),
            PostgresProcessedMatrixEventStore(database),
            tasks,
        )
        background_services = (AgentTeamsMatrixInboundPoller(messenger, inbound),)
    if settings.github_webhook_secret or scm_adapter is not None:
        validation = ValidationSnapshotService(PostgresValidationSnapshotStore(database))
        delivery = DeliveryService(
            PostgresChangeSetStore(database),
            require_governance=settings.delivery_auto_enabled,
            require_validation=settings.delivery_auto_enabled,
            validation_reader=validation,
        )
        observations = SCMObservationService(PostgresSCMObservationStore(database))
        commands = SCMCommandService(PostgresSCMCommandStore(database))
        coordinator = ChangeSetSCMCoordinator(
            delivery,
            repository_catalog,
            scm_adapter,
            command_service=commands,
        )
        processor = GitHubObservationProcessor(
            observations,
            delivery,
            repository_catalog,
            coordinator,
            auto_merge=settings.delivery_auto_enabled,
        )
        if settings.github_webhook_secret:
            background_services = (
                *background_services,
                SCMObservationReplayWorker(
                    observations,
                    processor,
                    interval_seconds=settings.scm_observation_replay_interval_seconds,
                ),
            )
        if scm_adapter is not None:
            background_services = (
                *background_services,
                SCMCommandDispatcher(
                    commands,
                    delivery,
                    repository_catalog,
                    scm_adapter,
                    interval_seconds=settings.scm_command_dispatch_interval_seconds,
                ),
                GitHubObservationPoller(
                    delivery,
                    observations,
                    SCMPollCursorService(
                        PostgresSCMPollCursorStore(database),
                        interval_seconds=settings.scm_poll_interval_seconds,
                    ),
                    repository_catalog,
                    scm_adapter,
                    processor,
                    scan_interval_seconds=settings.scm_poll_scan_interval_seconds,
                ),
            )
    if scm_adapter is not None and settings.delivery_auto_enabled:
        validation = ValidationSnapshotService(PostgresValidationSnapshotStore(database))
        delivery = DeliveryService(
            PostgresChangeSetStore(database),
            require_governance=True,
            require_validation=True,
            validation_reader=validation,
        )
        commands = SCMCommandService(PostgresSCMCommandStore(database))
        background_services = (
            *background_services,
            DeliveryReconciler(
                delivery,
                ChangeSetSCMCoordinator(
                    delivery,
                    repository_catalog,
                    scm_adapter,
                    command_service=commands,
                ),
                interval_seconds=settings.delivery_reconcile_interval_seconds,
            ),
        )
    return ApplicationContainer(
        database=database,
        agent_directory=agent_directory,
        project_topology_store=topology_store,
        repository_catalog=repository_catalog,
        outbox_store=OutboxStore(database),
        task_store=task_store,
        collaboration_message_store=collaboration_store,
        context_store=PostgresContextStore(database),
        specification_store=PostgresSpecificationStore(database),
        mock_coding_agent_factory=lambda scenario: MockCodingAgent(MockScenario(scenario)),
        llm_client=make_llm_client(
            settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        ),
        agent_team_control_plane=control_plane,
        agent_team_messenger=messenger,
        agentteams_probe=control_plane,
        agentteams_required=settings.agentteams_required,
        external_resources=resources,
        background_services=background_services,
        task_report_gateway=task_report_gateway,
        scm_adapter=scm_adapter,
        scm_token_provider=scm_token_provider,
    )


def create_app(container: ApplicationContainer | None = None) -> FastAPI:
    settings = get_settings()
    setup_tracing(settings.otlp_endpoint, service_name=settings.otlp_service_name)
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Multi-repository coding-agent orchestration infrastructure",
        lifespan=lifespan,
    )
    application.state.container = container or build_default_container()
    application.include_router(api_router)
    return application
