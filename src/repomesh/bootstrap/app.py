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
from repomesh.modules.agent_directory.infrastructure import PostgresAgentDirectory
from repomesh.modules.collaboration import (
    PostgresCollaborationMessageStore,
    PostgresProcessedMatrixEventStore,
    ProcessMatrixTaskReport,
    SendCollaborationMessage,
)
from repomesh.modules.context.infrastructure import PostgresContextStore
from repomesh.modules.identity_access import PolicyAuthorizationGateway
from repomesh.modules.observability.infrastructure.alerting import (
    AlertingEvaluator,
    AlertingStore,
)
from repomesh.modules.observability.infrastructure.log_recorder import LogRecorder
from repomesh.modules.observability.infrastructure.trace_ingest import (
    LocalTraceSource,
    MinioTraceSource,
    TraceIngester,
    TraceSource,
    TraceStore,
)
from repomesh.modules.observability.infrastructure.trace_query import TraceQueryStore
from repomesh.modules.observability.infrastructure.usage_query import UsageQueryStore
from repomesh.modules.observability.infrastructure.usage_recorder import QueuedUsageRecorder
from repomesh.modules.project.infrastructure import PostgresProjectTopologyStore
from repomesh.modules.repository_intelligence.infrastructure import PostgresRepositoryCatalog
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


def _trace_source(settings) -> TraceSource:
    """Session objects come from MinIO when object storage is configured.

    In a local/dev deployment the CoPaw side pre-syncs the bucket root
    (``agents/...``) into ``agentteams_storage_root`` and the poller reads it
    straight from disk — the same root the local task publisher writes to.
    """
    if (
        settings.agentteams_storage_endpoint
        and settings.agentteams_storage_access_key
        and settings.agentteams_storage_secret_key
    ):
        return MinioTraceSource(
            settings.agentteams_storage_endpoint,
            settings.agentteams_storage_access_key,
            settings.agentteams_storage_secret_key,
            settings.agentteams_storage_bucket,
        )
    return LocalTraceSource(settings.agentteams_storage_root)


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
    agent_directory = PostgresAgentDirectory(database)
    topology_store = PostgresProjectTopologyStore(database)
    task_store = PostgresTaskStore(database)
    collaboration_store = PostgresCollaborationMessageStore(database)
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
    # LLM usage observability: the sink is written from asyncio.to_thread
    # workers, so it only enqueues; the background task flushes on the loop.
    usage_recorder = QueuedUsageRecorder(database)
    background_services = (*background_services, usage_recorder)
    # Process-log capture: the handler sits on the root logger, the flush task
    # drains the bounded queue into observability.log_entries on the loop.
    log_recorder = LogRecorder(database)
    background_services = (*background_services, log_recorder)
    # Alert evaluation runs on an interval; the console endpoints evaluate on
    # demand. Both share the same database-backed alert state machine, so the
    # background loop and "evaluate now" agree even though they are separate
    # instances.
    background_services = (
        *background_services,
        AlertingEvaluator(
            AlertingStore(database),
            UsageQueryStore(database),
            trace_query=TraceQueryStore(database),
        ),
    )
    # Trace ingest projects CoPaw session files into the observability schema;
    # the poller skips unchanged objects, so it is safe to run continuously.
    background_services = (
        *background_services,
        TraceIngester(TraceStore(database), _trace_source(settings)),
    )
    return ApplicationContainer(
        database=database,
        agent_directory=agent_directory,
        project_topology_store=topology_store,
        repository_catalog=PostgresRepositoryCatalog(database),
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
            usage_sink=usage_recorder.record,
        ),
        agent_team_control_plane=control_plane,
        agent_team_messenger=messenger,
        agentteams_probe=control_plane,
        agentteams_required=settings.agentteams_required,
        external_resources=resources,
        background_services=background_services,
        task_report_gateway=task_report_gateway,
        usage_recorder=usage_recorder,
        log_recorder=log_recorder,
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
