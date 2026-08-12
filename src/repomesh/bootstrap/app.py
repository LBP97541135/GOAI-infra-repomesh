from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from repomesh.api.router import api_router
from repomesh.bootstrap.container import (
    ApplicationContainer,
    AsyncCloseable,
    collaboration_routed_messenger,
    project_topology_reader,
)
from repomesh.integrations.agentteams import (
    AgentTeamsControlPlaneClient,
    AgentTeamsMatrixClient,
    AgentTeamsMatrixIdentityVerifier,
    AgentTeamsMatrixInboundPoller,
)
from repomesh.integrations.agentteams.human_decisions import (
    HumanDecisionCollaborationNotifier,
)
from repomesh.integrations.agentteams.task_publishing import (
    AgentTeamsObjectTaskPublisher,
    AgentTeamsTaskPublisher,
)
from repomesh.integrations.coding_agents.mock import MockCodingAgent, MockScenario
from repomesh.integrations.llm import make_llm_client
from repomesh.integrations.scm import (
    ChangeSetSCMCoordinator,
    CIReworkTaskCreator,
    DeliveryReconciler,
    GitHubAdapter,
    GitHubObservationPoller,
    GitHubObservationProcessor,
    GitHubRevertDeliveryGateway,
    GovernedRecoveryActionHandler,
    MirrorGitReverter,
    RecoveryConflictTaskCreator,
    RecoverySagaExecutor,
    SCMCommandDispatcher,
    SCMObservationReplayWorker,
)
from repomesh.integrations.scm.github_auth import (
    GitHubAppTokenProvider,
    StaticTokenProvider,
    private_key_file_loader,
)
from repomesh.modules.agent_directory.infrastructure import PostgresAgentDirectory
from repomesh.modules.collaboration import (
    CollaborationDeliveryRetryWorker,
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
from repomesh.modules.project import ProjectCheckpointService
from repomesh.modules.project.infrastructure import (
    PostgresHumanReviewRequestStore,
    PostgresProjectCheckpointDecisionStore,
    PostgresProjectTopologyStore,
)
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
    matrix_client = (
        AgentTeamsMatrixClient(
            settings.agentteams_matrix_url,
            settings.agentteams_matrix_access_token,
            control_plane=control_plane,
        )
        if settings.agentteams_matrix_access_token
        else None
    )
    # Wrapped once, here, so that every consumer below — the collaboration
    # sender, the checkpoint notifier, the container's messenger field — reads
    # the same retryable refusal instead of the integration's own taxonomy.
    messenger = (
        collaboration_routed_messenger(matrix_client) if matrix_client is not None else None
    )
    resources: tuple[AsyncCloseable, ...] = (
        (control_plane, matrix_client) if matrix_client is not None else (control_plane,)
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
    elif settings.delivery_github_token:
        # Local-dev seam: one personal token for every repository. The App
        # pair above wins when both are configured (short-lived per-repo
        # tokens beat a static credential).
        scm_token_provider = StaticTokenProvider(settings.delivery_github_token)
        scm_adapter = GitHubAdapter(scm_token_provider)
        resources = (*resources, scm_adapter, scm_token_provider)
    agent_directory = PostgresAgentDirectory(database)
    topology_store = PostgresProjectTopologyStore(database)
    checkpoint_service = ProjectCheckpointService(
        topology_store,
        PostgresProjectCheckpointDecisionStore(database),
        PostgresHumanReviewRequestStore(database),
    )
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
        checkpoint_service = ProjectCheckpointService(
            topology_store,
            PostgresProjectCheckpointDecisionStore(database),
            PostgresHumanReviewRequestStore(database),
            HumanDecisionCollaborationNotifier(collaboration),
        )
        tasks = TaskOrchestrator(
            agent_directory,
            topology_store,
            task_store,
            collaboration,
            task_publisher,
            checkpoint_service,
        )
        task_report_gateway = tasks
        inbound = ProcessMatrixTaskReport(
            agent_directory,
            topology_store,
            AgentTeamsMatrixIdentityVerifier(control_plane),
            PostgresProcessedMatrixEventStore(database),
            tasks,
        )
        background_services = (
            # Inbound polling is not delivery, so it reads the Matrix gateway
            # directly rather than through the delivery-only wrapper.
            AgentTeamsMatrixInboundPoller(matrix_client, inbound),
            CollaborationDeliveryRetryWorker(collaboration_store, collaboration),
        )
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
        # A failed candidate needs a Worker to repair it; without AgentTeams the
        # CI failure only changes delivery state and waits to be noticed.
        rework_tasks = (
            CIReworkTaskCreator(task_report_gateway, project_topology_reader(topology_store))
            if task_report_gateway is not None
            else None
        )
        processor = GitHubObservationProcessor(
            observations,
            delivery,
            repository_catalog,
            coordinator,
            auto_merge=settings.delivery_auto_enabled,
            rework_tasks=rework_tasks,
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
        # Revert conflicts need a Worker to repair them; without AgentTeams the
        # Saga records the failure instead of parking the action.
        conflict_tasks = (
            RecoveryConflictTaskCreator(
                task_report_gateway, project_topology_reader(topology_store)
            )
            if task_report_gateway is not None
            else None
        )
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
            RecoverySagaExecutor(
                delivery,
                GovernedRecoveryActionHandler(
                    GitHubRevertDeliveryGateway(
                        repository_catalog,
                        scm_adapter,
                        MirrorGitReverter(
                            settings.runner_workspace_root / "revert-mirrors",
                            token_provider=scm_token_provider,
                        ),
                        base_branch=settings.delivery_base_branch,
                    )
                ),
                conflict_tasks,
                interval_seconds=settings.delivery_recovery_interval_seconds,
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
        project_checkpoint_service_instance=checkpoint_service,
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
