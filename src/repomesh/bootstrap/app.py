import base64
import logging
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from repomesh.api.router import api_router
from repomesh.bootstrap.container import (
    ApplicationContainer,
    AsyncCloseable,
    authorized_room_reader,
    collaboration_routed_messenger,
    project_topology_reader,
    storage_backed_task_publisher,
)
from repomesh.integrations.agentteams import (
    AgentTeamsControlPlaneClient,
    AgentTeamsMatrixClient,
    AgentTeamsMatrixIdentityResolver,
    AgentTeamsMatrixIdentityVerifier,
    AgentTeamsMatrixInboundPoller,
    AgentTeamsRecipientMatrixIdentityResolver,
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
from repomesh.integrations.recovery import (
    RecoverySourceProjector,
    UnifiedRecoveryActionHandlers,
)
from repomesh.integrations.scm import (
    ChangeSetSCMCoordinator,
    CIReworkTaskCreator,
    DeliveryConflictTaskCreator,
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
from repomesh.modules.agent_runtime import PostgresWorkerRecoveryStore
from repomesh.modules.collaboration import (
    CollaborationDeliveryRetryWorker,
    PostgresCollaborationAuditLedger,
    PostgresCollaborationMessageStore,
    PostgresProcessedMatrixEventStore,
    PostgresRoomTimelineStore,
    ProcessMatrixTaskReport,
    RecordRoomTimeline,
    SendCollaborationMessage,
)
from repomesh.modules.context.infrastructure import PostgresContextStore
from repomesh.modules.decision_chain import (
    DecisionChainProjectionService,
    DecisionChainProjector,
    PostgresDecisionChainStore,
    PostgresDecisionEventSource,
)
from repomesh.modules.delivery import (
    DeliveryService,
    PostgresChangeSetStore,
    PostgresDeliveryAuditLog,
    PostgresDeliveryConflictCaseStore,
    PostgresSCMCommandStore,
    PostgresSCMObservationStore,
    PostgresSCMPollCursorStore,
    SCMCommandService,
    SCMObservationService,
    SCMPollCursorService,
)
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
from repomesh.modules.platform_config import (
    GITHUB_APP_ID,
    GITHUB_PRIVATE_KEY,
    GITHUB_WEBHOOK_SECRET,
    MODEL_API_KEY,
    MODEL_BASE_URL,
    MODEL_NAME,
    PostgresPlatformCredentialStore,
)
from repomesh.modules.project import ProjectCheckpointService
from repomesh.modules.project.contracts import ProjectCheckpoint
from repomesh.modules.project.domain import HumanReviewRequest
from repomesh.modules.project.infrastructure import (
    PostgresHumanReviewRequestStore,
    PostgresProjectCheckpointDecisionStore,
    PostgresProjectTopologyStore,
)
from repomesh.modules.recovery_management import (
    PostgresRecoveryCaseStore,
    RecoveryActionExecutor,
)
from repomesh.modules.repository_intelligence.infrastructure import PostgresRepositoryCatalog
from repomesh.modules.review_validation import (
    PostgresValidationSnapshotStore,
    ValidationSnapshotService,
)
from repomesh.modules.specification import PostgresSpecificationStore
from repomesh.modules.task_orchestration import (
    DispatchedWorkerTaskReader,
    PostgresTaskStore,
    TaskOrchestrator,
)
from repomesh.modules.task_orchestration.contracts import TaskAssignmentPublisher
from repomesh.persistence import Database
from repomesh.persistence.outbox import OutboxStore
from repomesh.settings import Settings, get_settings
from repomesh_runner.telemetry import setup_logs, setup_metrics, setup_tracing

_API_LOGGER = logging.getLogger("repomesh.api")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    if application.state.container is None:
        application.state.container = await build_default_container_from_database()
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


@dataclass(frozen=True, slots=True)
class CredentialOverrides:
    model_api_key: str | None = None
    model_base_url: str | None = None
    model_name: str | None = None
    github_app_id: int | None = None
    github_private_key: str | None = None
    github_webhook_secret: str | None = None


async def build_default_container_from_database() -> ApplicationContainer:
    settings = get_settings()
    database = Database(settings.database_url)
    try:
        values = await PostgresPlatformCredentialStore(database).get_many(
            {
                MODEL_API_KEY,
                MODEL_BASE_URL,
                MODEL_NAME,
                GITHUB_APP_ID,
                GITHUB_PRIVATE_KEY,
                GITHUB_WEBHOOK_SECRET,
            }
        )
        overrides = CredentialOverrides(
            model_api_key=values.get(MODEL_API_KEY).value if MODEL_API_KEY in values else None,
            model_base_url=values.get(MODEL_BASE_URL).value if MODEL_BASE_URL in values else None,
            model_name=values.get(MODEL_NAME).value if MODEL_NAME in values else None,
            github_app_id=(
                int(values[GITHUB_APP_ID].value) if GITHUB_APP_ID in values else None
            ),
            github_private_key=(
                values[GITHUB_PRIVATE_KEY].value if GITHUB_PRIVATE_KEY in values else None
            ),
            github_webhook_secret=(
                values[GITHUB_WEBHOOK_SECRET].value
                if GITHUB_WEBHOOK_SECRET in values
                else None
            ),
        )
        return build_default_container(overrides=overrides, database=database)
    except Exception:
        await database.dispose()
        raise


def build_default_container(
    *,
    overrides: CredentialOverrides | None = None,
    database: Database | None = None,
) -> ApplicationContainer:
    settings = get_settings()
    overrides = overrides or CredentialOverrides()
    model_api_key = overrides.model_api_key or settings.deepseek_api_key
    model_base_url = overrides.model_base_url or settings.deepseek_base_url
    model_name = overrides.model_name or settings.deepseek_model
    github_app_id = overrides.github_app_id or settings.github_app_id
    github_webhook_secret = overrides.github_webhook_secret or settings.github_webhook_secret
    selected_publisher: TaskAssignmentPublisher = AgentTeamsTaskPublisher(
        settings.agentteams_storage_root
    )
    if (
        settings.agentteams_storage_endpoint
        and settings.agentteams_storage_access_key
        and settings.agentteams_storage_secret_key
    ):
        selected_publisher = AgentTeamsObjectTaskPublisher(
            settings.agentteams_storage_endpoint,
            settings.agentteams_storage_access_key,
            settings.agentteams_storage_secret_key,
            settings.agentteams_storage_bucket,
        )
    # Wrapped after the choice, not inside either branch: both channels fail in
    # their store's own vocabulary and both mean the same thing to the round
    # (A-10). One wrapper over the port covers them.
    task_publisher = storage_backed_task_publisher(selected_publisher)
    database = database or Database(settings.database_url)
    control_plane = AgentTeamsControlPlaneClient(
        settings.agentteams_controller_url,
        token=settings.agentteams_controller_token,
    )
    matrix_client = (
        AgentTeamsMatrixClient(
            settings.agentteams_matrix_url,
            settings.agentteams_matrix_access_token,
            recipient_identity_resolver=AgentTeamsRecipientMatrixIdentityResolver(
                control_plane
            ),
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
    github_private_key_loader: Callable[[], bytes] | None = None
    if overrides.github_private_key is not None:
        private_key = overrides.github_private_key.encode("utf-8")

        def load_stored_github_private_key() -> bytes:
            return private_key

        github_private_key_loader = load_stored_github_private_key
    elif settings.github_app_private_key_base64 is not None:
        encoded_key = settings.github_app_private_key_base64.get_secret_value()

        def load_encoded_github_private_key() -> bytes:
            return base64.b64decode(encoded_key, validate=True)

        github_private_key_loader = load_encoded_github_private_key
    elif settings.github_app_private_key_file:
        github_private_key_loader = private_key_file_loader(
            settings.github_app_private_key_file
        )
    if github_app_id and github_private_key_loader is not None:
        scm_token_provider = GitHubAppTokenProvider(
            github_app_id,
            github_private_key_loader,
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
    human_review_store = PostgresHumanReviewRequestStore(database)
    checkpoint_service = ProjectCheckpointService(
        topology_store,
        PostgresProjectCheckpointDecisionStore(database),
        human_review_store,
    )
    task_store = PostgresTaskStore(database)
    collaboration_store = PostgresCollaborationMessageStore(database)
    repository_catalog = PostgresRepositoryCatalog(database)
    recovery_case_store = PostgresRecoveryCaseStore(database)
    worker_recovery_projection_store = PostgresWorkerRecoveryStore(database)
    delivery_conflict_projection_store = PostgresDeliveryConflictCaseStore(database)
    background_services = (
        RecoverySourceProjector(
            recovery_case_store,
            worker_recovery_projection_store,
            delivery_conflict_projection_store,
            task_store,
            delivery=DeliveryService(PostgresChangeSetStore(database)),
            reviews=human_review_store,
            topologies=topology_store,
        ),
        RecoveryActionExecutor(
            recovery_case_store,
            UnifiedRecoveryActionHandlers(
                recovery_case_store,
                worker_recovery_projection_store,
                delivery_conflict_projection_store,
            ).handlers(),
            owner=f"{socket.gethostname()}:{uuid4()}",
        ),
    )
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
            human_review_store,
            HumanDecisionCollaborationNotifier(collaboration),
        )
        tasks = TaskOrchestrator(
            agent_directory,
            topology_store,
            task_store,
            collaboration,
            task_publisher,
            checkpoint_service,
            PostgresDeliveryAuditLog(database),
        )
        task_report_gateway = tasks
        inbound = ProcessMatrixTaskReport(
            agent_directory,
            topology_store,
            AgentTeamsMatrixIdentityVerifier(control_plane),
            PostgresProcessedMatrixEventStore(database),
            tasks,
            DispatchedWorkerTaskReader(task_store, agent_directory),
            PostgresCollaborationAuditLedger(database),
        )
        # Recording what a room said and acting on what it said are two
        # consumers of one sync loop, composed separately: the recorder never
        # touches the task store and the report consumer never writes the
        # transcript, so neither can grow into the other's authority.
        room_timeline = RecordRoomTimeline(
            authorized_room_reader(topology_store),
            AgentTeamsMatrixIdentityResolver(agent_directory, control_plane),
            PostgresRoomTimelineStore(database),
        )
        background_services = (
            # Inbound polling is not delivery, so it reads the Matrix gateway
            # directly rather than through the delivery-only wrapper.
            AgentTeamsMatrixInboundPoller(matrix_client, inbound, room_timeline),
            CollaborationDeliveryRetryWorker(collaboration_store, collaboration),
        )
    if github_webhook_secret or scm_adapter is not None:
        validation = ValidationSnapshotService(PostgresValidationSnapshotStore(database))
        conflict_cases = PostgresDeliveryConflictCaseStore(database)
        delivery = DeliveryService(
            PostgresChangeSetStore(database),
            require_governance=settings.delivery_auto_enabled,
            require_validation=settings.delivery_auto_enabled,
            validation_reader=validation,
            audit=PostgresDeliveryAuditLog(database),
            conflict_cases=conflict_cases,
        )
        observations = SCMObservationService(PostgresSCMObservationStore(database))
        commands = SCMCommandService(
            PostgresSCMCommandStore(database),
            lease_seconds=settings.scm_command_lease_seconds,
        )
        conflict_tasks = (
            DeliveryConflictTaskCreator(
                task_report_gateway, project_topology_reader(topology_store)
            )
            if task_report_gateway is not None
            else None
        )

        async def escalate_delivery_conflict(case, reason):
            await human_review_store.ensure(
                HumanReviewRequest(
                    project_id=case.project_id,
                    checkpoint=ProjectCheckpoint.EXCEPTION_ESCALATION,
                    repository_id=case.repository_id,
                    evidence_version=f"delivery-conflict:{case.id}:v{case.version}",
                    title="多仓交付冲突需要人工处理",
                    summary=f"自动冲突修复无法派发，错误代码：{reason}",
                )
            )

        coordinator = ChangeSetSCMCoordinator(
            delivery,
            repository_catalog,
            scm_adapter,
            command_service=commands,
            base_branch=settings.delivery_base_branch,
            conflict_cases=conflict_cases,
            conflict_tasks=conflict_tasks,
            conflict_escalate=escalate_delivery_conflict,
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
        if github_webhook_secret:
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
                    lease_renew_interval_seconds=(
                        settings.scm_command_lease_renew_interval_seconds
                    ),
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
        forward_conflict_cases = PostgresDeliveryConflictCaseStore(database)
        delivery = DeliveryService(
            PostgresChangeSetStore(database),
            require_governance=True,
            require_validation=True,
            validation_reader=validation,
            audit=PostgresDeliveryAuditLog(database),
            conflict_cases=forward_conflict_cases,
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
                    base_branch=settings.delivery_base_branch,
                    conflict_cases=forward_conflict_cases,
                    conflict_tasks=(
                        DeliveryConflictTaskCreator(
                            task_report_gateway,
                            project_topology_reader(topology_store),
                        )
                        if task_report_gateway is not None
                        else None
                    ),
                    conflict_escalate=escalate_delivery_conflict,
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
    # Decision-chain projection subscribes to the five chain events; drain is
    # idempotent (event_id unique) and incremental (the source skips projected
    # ids), so the interval loop is safe to run continuously.
    background_services = (
        *background_services,
        DecisionChainProjector(
            DecisionChainProjectionService(
                PostgresDecisionChainStore(database),
                PostgresDecisionEventSource(database),
            )
        ),
    )
    container_holder: dict[str, ApplicationContainer] = {}
    if settings.worker_recovery_enabled:
        from repomesh.integrations.runner.recovery import WorkerRecoveryCoordinator
        from repomesh.modules.agent_runtime import (
            PostgresWorkerExecutionReservationStore,
            WorkerRecoveryDecision,
            WorkerRecoveryReconciler,
        )
        from repomesh.modules.agent_runtime.contracts import StartAssignedWorkerTaskCommand
        from repomesh.modules.agent_runtime.runner_store import PostgresRunnerGatewayStore
        from repomesh.modules.task_orchestration import PostgresTaskAssignmentStore

        reservations = PostgresWorkerExecutionReservationStore(database)
        runner_store = PostgresRunnerGatewayStore(database)
        recovery_store = PostgresWorkerRecoveryStore(database)
        assignments = PostgresTaskAssignmentStore(database)
        review_store = PostgresHumanReviewRequestStore(database)

        class _WorkerHealth:
            async def healthy(self, worker_agent_id):
                principal = await agent_directory.get_view(worker_agent_id)
                if principal is None:
                    return False
                try:
                    runtime = await control_plane.get_worker(
                        principal.agentteams_resource_name
                    )
                except Exception:
                    return False
                return runtime is not None and runtime.phase.lower() in {
                    "ready", "running", "healthy"
                }

        async def _start(operation, worker_id, *, resume_session_id=None):
            execution = await reservations.get(operation.execution_id)
            payload = dict(execution.task_payload or {}) if execution else {}
            repository = dict(payload.get("repository") or {})
            await container_holder["container"].worker_execution_service().execute(
                StartAssignedWorkerTaskCommand(
                    task_id=operation.task_id,
                    worker_agent_id=worker_id,
                    adapter_id=str(payload.get("adapterId") or "mock"),
                    base_revision=str(repository.get("baseRevision") or "main"),
                    resume_session_id=resume_session_id,
                )
            )

        async def _resume(operation):
            await _start(
                operation,
                operation.failed_worker_id,
                resume_session_id=operation.native_session_id,
            )

        async def _escalate(operation, reason):
            task = await task_store.get(operation.task_id)
            if task is None:
                return
            await review_store.ensure(
                HumanReviewRequest(
                    project_id=task.project_id,
                    checkpoint=ProjectCheckpoint.EXCEPTION_ESCALATION,
                    repository_id=task.repository_id,
                    evidence_version=(
                        f"worker-recovery:{operation.execution_id}:"
                        f"g{operation.assignment_generation or 0}"
                    ),
                    title="Worker 自动恢复需要人工处理",
                    summary=f"恢复无法安全继续，错误代码：{reason}",
                    requested_by_agent_id=task.assigned_by_agent_id,
                )
            )

        # Replacement callback needs the operation payload; keep it in a scoped
        # holder only for the duration of one leased decision.
        active_operation: dict[str, object] = {}

        async def _replacement(task_id, worker_id):
            operation = active_operation.get("operation")
            if operation is None or operation.task_id != task_id:
                raise RuntimeError("replacement recovery context unavailable")
            await _start(operation, worker_id)

        coordinator = WorkerRecoveryCoordinator(
            task_store,
            assignments,
            agent_directory,
            topology_store,
            reservations,
            _WorkerHealth(),
            resume=_resume,
            start_replacement=_replacement,
            escalate=_escalate,
            max_reassignments=settings.worker_recovery_max_reassignments,
            recent_failures=recovery_store.recent_failures,
        )

        async def _decide(operation):
            active_operation["operation"] = operation
            try:
                if operation.attempts > settings.worker_recovery_max_execution_attempts:
                    await _escalate(operation, "recovery_attempts_exhausted")
                    return WorkerRecoveryDecision.ESCALATE
                return await coordinator.decide(operation)
            finally:
                active_operation.clear()

        async def _discover():
            for execution in await reservations.list_expired_active(
                grace_seconds=settings.worker_recovery_grace_seconds
            ):
                dispatch = await runner_store.get_dispatch(execution.run_id)
                if dispatch is not None and dispatch.status in {
                    "queued", "leased", "accepted"
                }:
                    await reservations.reconcile_live_dispatch(
                        execution.id,
                        lease_seconds=settings.worker_execution_reservation_lease_seconds,
                    )
                    continue
                await recovery_store.ensure(
                    execution_id=execution.id,
                    task_id=execution.task_id,
                    assignment_attempt_id=execution.assignment_attempt_id,
                    assignment_generation=execution.assignment_generation,
                    failed_worker_id=execution.worker_agent_id,
                    reason="lease_expired",
                    native_session_id=None,
                )

        background_services = (
            *background_services,
            WorkerRecoveryReconciler(
                recovery_store,
                _decide,
                owner=f"{socket.gethostname()}:{uuid4()}",
                interval_seconds=settings.worker_recovery_scan_interval_seconds,
                discover=_discover,
            ),
        )

    container = ApplicationContainer(
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
            model_api_key,
            base_url=model_base_url,
            model=model_name,
            usage_sink=usage_recorder.record,
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
        usage_recorder=usage_recorder,
        log_recorder=log_recorder,
    )
    container_holder["container"] = container
    return container


async def _unhandled_exception_envelope(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort JSON envelope for otherwise-unhandled errors (M-10).

    Endpoints that translate a domain failure raise ``HTTPException`` with a
    named status and keep their own body — those are dispatched by FastAPI's
    own handler and never reach here. This catch-all exists so that a *new*
    endpoint which forgets to translate (or a driver-level fault such as
    ``StringDataRightTruncation``) still returns a structured 500 instead of a
    bare text body. The real cause is logged with a traceback, not leaked.
    """
    _API_LOGGER.exception(
        "unhandled error on %s %s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error", "error": type(exc).__name__},
    )


_PUBLIC_DEV_ACTION_TOKEN = "console-dev-token"


def _guard_deployment_defaults(settings: Settings) -> None:
    """Fail fast when a non-development environment keeps the public default
    agent action token.

    ``console-dev-token`` is the shared default across compose.yaml, the
    frontend build args and the dev scripts, and it is embedded in the built
    frontend bundle. Fine as a development convenience; in staging or
    production it means every write endpoint is protected by a publicly-known
    credential, so the process refuses to start rather than serve with it.
    """

    if settings.environment not in {"staging", "production"}:
        return
    if settings.agent_action_token == _PUBLIC_DEV_ACTION_TOKEN:
        raise RuntimeError(
            "REPOMESH_AGENT_ACTION_TOKEN is still the public default "
            f"'{_PUBLIC_DEV_ACTION_TOKEN}'; set a real token for the "
            f"{settings.environment} environment"
        )


def create_app(container: ApplicationContainer | None = None) -> FastAPI:
    settings = get_settings()
    _guard_deployment_defaults(settings)
    setup_tracing(
        settings.otlp_endpoint,
        service_name=settings.otlp_service_name,
        headers=settings.otlp_headers,
    )
    if settings.otlp_metrics_enabled:
        setup_metrics(
            settings.otlp_endpoint,
            service_name=settings.otlp_service_name,
            headers=settings.otlp_headers,
        )
    if settings.otlp_logs_enabled:
        setup_logs(
            settings.otlp_endpoint,
            service_name=settings.otlp_service_name,
            headers=settings.otlp_headers,
            level=getattr(logging, settings.otlp_log_level.upper(), logging.WARNING),
        )
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Multi-repository coding-agent orchestration infrastructure",
        lifespan=lifespan,
    )
    application.state.container = container
    application.include_router(api_router)
    application.add_exception_handler(Exception, _unhandled_exception_envelope)
    return application
