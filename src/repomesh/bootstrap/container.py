from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Protocol, cast
from uuid import UUID

from repomesh.integrations.orchestration import (
    AdvanceExecutionPlanStarter,
    ApprovedTaskSpecificationAuthor,
    DeliveryStateAdapter,
)
from repomesh.integrations.scm.contracts import RepositoryRef, SCMAdapter
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
from repomesh.modules.change_orchestration import PlanExecutionBridge, TaskSupersederGateway
from repomesh.modules.collaboration.ports import CollaborationMessageStore
from repomesh.modules.context.application import ContextPublicationGateway, GetExecutionContextGrant
from repomesh.modules.context.ports import ContextStore
from repomesh.modules.identity_access import LocalAccountService, PolicyAuthorizationGateway
from repomesh.modules.identity_access.infrastructure import PostgresLocalAccountStore
from repomesh.modules.project.contracts import ProjectAgentTopologyView, ProjectTopologyReader
from repomesh.modules.project.ports import ProjectTopologyStore
from repomesh.modules.repository_intelligence.application import (
    DependencyGraphService,
    HandoffDocService,
    PlanIntegrationService,
)
from repomesh.modules.repository_intelligence.application.confirmation import ConfirmationService
from repomesh.modules.repository_intelligence.application.discovery import LLMClient
from repomesh.modules.repository_intelligence.application.requirement_analysis import (
    RequirementAnalyzer,
)
from repomesh.modules.repository_intelligence.infrastructure.handoff_doc_store import (
    PostgresHandoffDocStore,
)
from repomesh.modules.repository_intelligence.infrastructure.plan_snapshot_store import (
    PlanSnapshotStore,
)
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog
from repomesh.modules.specification import BuildCodingAgentPackage, SpecificationService
from repomesh.modules.specification.ports import SpecificationStore
from repomesh.modules.task_orchestration import (
    AdvanceExecutionPlan,
    DecomposeRepositoryTask,
    ObserveExecutionPlan,
    PostgresExecutionPlanStore,
)
from repomesh.modules.task_orchestration.contracts import (
    TaskAssignmentGateway,
    TaskReportGateway,
)
from repomesh.modules.task_orchestration.ports import ExecutionPlanStore, TaskStore
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


def cached_service(factory):
    """Cache a zero-argument container factory for the process lifetime."""

    @wraps(factory)
    def resolve(self, *args, **kwargs):
        if args or kwargs:
            return factory(self, *args, **kwargs)
        key = factory.__name__
        if key not in self._service_cache:
            self._service_cache[key] = factory(self)
        return self._service_cache[key]

    return resolve


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
    # Planning LLM adapter; None selects the deterministic keyword fallback paths.
    llm_client: LLMClient | None = None
    agent_team_control_plane: AgentTeamControlPlane | None = None
    agent_team_messenger: AgentTeamMessenger | None = None
    agentteams_probe: ReadinessProbe | None = None
    agentteams_required: bool = False
    external_resources: tuple[AsyncCloseable, ...] = ()
    background_services: tuple[BackgroundService, ...] = ()
    task_report_gateway: TaskReportGateway | None = None
    scm_adapter: SCMAdapter | None = None
    scm_token_provider: Callable[[RepositoryRef], Awaitable[str]] | None = None
    project_checkpoint_service_instance: object | None = None
    _service_cache: dict[str, object] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def capability_assembler(self) -> PresetCapabilityAssembler:
        return PresetCapabilityAssembler()

    @cached_service
    def local_account_service(self):
        return LocalAccountService(
            PostgresLocalAccountStore(self.database),
            session_ttl_seconds=get_settings().local_session_ttl_seconds,
        )

    def project_topology_creator(self):
        from repomesh.modules.project import CreateProjectAgentTopology

        return CreateProjectAgentTopology(self.agent_directory, self.project_topology_store)

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

    @cached_service
    def specification_service(self) -> SpecificationService:
        return SpecificationService(
            self.agent_directory,
            self.project_topology_store,
            self.specification_store,
            ContextPublicationGateway(self.context_store),
            PolicyAuthorizationGateway(),
            self.project_checkpoint_service(),
        )

    def coding_agent_package_builder(self) -> BuildCodingAgentPackage:
        return BuildCodingAgentPackage(
            self.agent_directory,
            self.project_topology_store,
            self.task_store,
            self.specification_store,
            PolicyAuthorizationGateway(),
        )

    @cached_service
    def topology_reader(self) -> ProjectTopologyReader:
        """Adapt ProjectTopologyStore to ProjectTopologyReader."""

        store = self.project_topology_store

        class _Adapter:
            async def get_view(self, project_id: UUID) -> ProjectAgentTopologyView | None:
                topology = await store.get(project_id)
                return topology.to_view() if topology else None

        return _Adapter()

    def requirement_analyzer(self) -> RequirementAnalyzer | None:
        """Build a RequirementAnalyzer when an LLM client is configured.

        Requirement sufficiency analysis is only meaningful with an LLM; when
        none is wired the endpoint returns 503 rather than degrading silently.
        """

        if self.llm_client is None:
            return None
        return RequirementAnalyzer(self.llm_client)

    async def confirmation_service(self, llm_client: LLMClient) -> ConfirmationService:
        profiles = await self.repository_catalog.list()
        by_name = {p.name: p for p in profiles}
        graph = DependencyGraphService(profiles) if profiles else None
        return ConfirmationService(llm_client, by_name, graph=graph)

    async def plan_integration_service(self, llm_client: LLMClient) -> PlanIntegrationService:
        profiles = await self.repository_catalog.list()
        graph = DependencyGraphService(profiles) if profiles else None
        return PlanIntegrationService(llm_client, graph=graph)

    def plan_snapshot_store(self) -> PlanSnapshotStore:
        return PlanSnapshotStore(self.database)

    @cached_service
    def delivery_service(self):
        from repomesh.modules.delivery import DeliveryService, PostgresChangeSetStore

        validation = self.validation_snapshot_service()
        return DeliveryService(
            PostgresChangeSetStore(self.database),
            require_governance=get_settings().delivery_auto_enabled,
            require_validation=get_settings().delivery_auto_enabled,
            validation_reader=validation,
            contract_catalog=(
                self.contract_catalog()
                if get_settings().delivery_contract_gate
                else None
            ),
        )

    def contract_catalog(self):
        """Adapt CONTRACT specifications into the delivery contract gate.

        CONTRACT specs store repository names in ``scope=(producer,
        consumer)``; this composition-root adapter projects the names onto
        repository ids and marks consumers that already have a planned
        adapter task. The merge gate then distinguishes a genuinely missing
        consumer candidate from one that arrives with a later batch.
        """

        from repomesh.modules.delivery.contracts import ContractView
        from repomesh.modules.specification.contracts import (
            SpecificationKind,
            SpecificationStatus,
        )

        class _ContractCatalogPort:
            def __init__(self, store, repository_catalog, task_store) -> None:
                self._specs = store
                self._catalog = repository_catalog
                self._tasks = task_store

            async def contracts_for_project(self, project_id):
                spec_rows = await self._specs.list_by_project(project_id)
                if not spec_rows:
                    return ()
                profiles = await self._catalog.list()
                by_name = {profile.name: profile for profile in profiles}
                task_rows = await self._tasks.list_by_project(project_id)
                task_repository_ids = {task.repository_id for task in task_rows}
                contracts = []
                for spec in spec_rows:
                    if spec.kind is not SpecificationKind.CONTRACT:
                        continue
                    if spec.status not in {
                        SpecificationStatus.APPROVED,
                        SpecificationStatus.FROZEN,
                    }:
                        continue
                    content = spec.current_version.content
                    if len(content.scope) < 2:
                        continue
                    producer = by_name.get(content.scope[0])
                    consumer = by_name.get(content.scope[1])
                    if producer is None or consumer is None:
                        continue
                    interface = (
                        content.interface_changes[0]
                        if content.interface_changes
                        else ""
                    )
                    contracts.append(
                        ContractView(
                            producer=producer.id,
                            consumer=consumer.id,
                            interface=interface,
                            status=spec.status.value,
                            consumer_planned=consumer.id in task_repository_ids,
                        )
                    )
                return tuple(contracts)

        return _ContractCatalogPort(
            self.specification_store,
            self.repository_catalog,
            self.task_store,
        )

    def delivery_read_model_service(self):
        from repomesh.api.read_models import DeliveryReadModelService
        from repomesh.api.read_models.sources import (
            PlanSnapshotData,
            RepositoryData,
            RunnerEventData,
            SpecificationContractData,
        )
        from repomesh.modules.agent_runtime.runner_store import PostgresRunnerGatewayStore
        from repomesh.modules.collaboration import PostgresCollaborationMessageStore
        from repomesh.modules.delivery import (
            PostgresDeliveryArchiveStore,
            PostgresSCMObservationStore,
            delivery_change_set_key,
        )
        from repomesh.modules.review_validation import PostgresValidationSnapshotStore
        from repomesh.modules.specification.contracts import (
            SpecificationKind,
            SpecificationStatus,
        )

        container = self
        delivery = self.delivery_service()
        plan_store = self.execution_plan_store()
        snapshot_store = self.plan_snapshot_store()
        archive_store = PostgresDeliveryArchiveStore(self.database)
        validation_store = PostgresValidationSnapshotStore(self.database)
        runner_store = PostgresRunnerGatewayStore(self.database)
        message_store = PostgresCollaborationMessageStore(self.database)
        observation_store = PostgresSCMObservationStore(self.database)

        class _Plans:
            async def list_all(self):
                return tuple(plan.to_view() for plan in await plan_store.list_all())

            async def get(self, plan_id: UUID):
                plan = await plan_store.get(plan_id)
                return plan.to_view() if plan is not None else None

        class _Snapshots:
            async def project_ids(self):
                return await snapshot_store.list_project_ids()

            async def for_project(self, project_id: UUID):
                return tuple(
                    PlanSnapshotData(
                        id=record.id,
                        project_id=record.project_id,
                        plan_version=record.plan_version,
                        created_at=record.created_at,
                        engineering_spec=record.engineering_spec,
                        requirement_text=record.requirement_text,
                        execution_batches=tuple(
                            tuple(batch) for batch in record.execution_batches
                        ),
                        task_dag=tuple(record.task_dag),
                        execution_plan_id=record.execution_plan_id,
                        created_by_agent_id=record.created_by_agent_id,
                    )
                    for record in await snapshot_store.list_all(project_id)
                )

        class _Tasks:
            async def list_by_project(self, project_id: UUID):
                return tuple(
                    task.to_view()
                    for task in await container.task_store.list_by_project(project_id)
                )

        class _ChangeSets:
            async def for_delivery(self, delivery_id: UUID):
                return await delivery.get_by_idempotency_key(
                    delivery_change_set_key(delivery_id)
                )

            async def merge_gate(self, change_set_id: UUID, repository_id: UUID):
                return await delivery.evaluate_merge_gate(change_set_id, repository_id)

        class _Validations:
            async def for_project(self, project_id: UUID):
                return tuple(
                    snapshot.to_view()
                    for snapshot in await validation_store.list_by_project(project_id)
                )

        class _Specifications:
            async def engineering_contract(self, project_id: UUID):
                candidates = [
                    specification
                    for specification in await container.specification_store.list_by_project(
                        project_id
                    )
                    if specification.kind is SpecificationKind.ENGINEERING
                ]
                if not candidates:
                    return None
                frozen = [
                    item
                    for item in candidates
                    if item.status is SpecificationStatus.FROZEN
                ]
                chosen = (frozen or candidates)[-1]
                content = chosen.current_version.content
                return SpecificationContractData(
                    specification_id=chosen.id,
                    version=chosen.current_version.version,
                    status=chosen.status.value,
                    goal=content.goal,
                    acceptance=content.acceptance,
                    constraints=content.constraints,
                    allowed_paths=content.allowed_paths,
                    forbidden_paths=content.forbidden_paths,
                    tests=content.tests,
                )

        class _Repositories:
            async def list(self):
                return tuple(
                    RepositoryData(
                        id=profile.id, name=profile.name, description=profile.description
                    )
                    for profile in await container.repository_catalog.list()
                )

        class _Agents:
            async def name(self, agent_id: UUID):
                principal = await container.agent_directory.get_view(agent_id)
                return principal.agentteams_resource_name if principal else None

            async def organization_id(self, agent_id: UUID):
                principal = await container.agent_directory.get_view(agent_id)
                return principal.organization_id if principal else None

        class _Topology:
            async def get_view(self, project_id: UUID):
                return await container.topology_reader().get_view(project_id)

            async def matrix_room_id(self, project_id: UUID):
                topology = await self.get_view(project_id)
                if topology is None or len(topology.repository_teams) != 1:
                    return None
                return topology.repository_teams[0].room_id

        class _RunnerEvents:
            async def for_project(self, project_id: UUID):
                return tuple(
                    RunnerEventData(
                        event_id=row["event_id"],
                        run_id=row["run_id"],
                        sequence=row["sequence"],
                        event_type=row["event_type"],
                        occurred_at=row["occurred_at"],
                        task_id=row["task_id"],
                        repository_id=row["repository_id"],
                    )
                    for row in await runner_store.list_events_for_project(project_id)
                )

        class _Messages:
            async def for_project(self, project_id: UUID):
                return tuple(
                    message.to_view()
                    for message in await message_store.list_by_project(project_id)
                )

        class _Observations:
            async def for_change_set(self, change_set_id: UUID):
                return tuple(
                    observation.to_view()
                    for observation in await observation_store.list_by_change_set(
                        change_set_id
                    )
                )

        return DeliveryReadModelService(
            plans=_Plans(),
            snapshots=_Snapshots(),
            tasks=_Tasks(),
            change_sets=_ChangeSets(),
            archives=archive_store,
            validations=_Validations(),
            specifications=_Specifications(),
            repositories=_Repositories(),
            agents=_Agents(),
            topology=_Topology(),
            runner_events=_RunnerEvents(),
            messages=_Messages(),
            observations=_Observations(),
        )

    def delivery_governance_service(self):
        from repomesh.modules.delivery import (
            DeliveryGovernanceService,
            PostgresDeliveryAuditLog,
        )

        return DeliveryGovernanceService(
            self.delivery_service(),
            self.agent_directory,
            PostgresDeliveryAuditLog(self.database),
        )

    def delivery_archive_service(self):
        from repomesh.modules.delivery import (
            DeliveryArchiveService,
            PostgresDeliveryArchiveStore,
            PostgresDeliveryAuditLog,
        )

        plans = self.execution_plan_store()

        class _PlanViewReader:
            async def get_view(self, plan_id: UUID):
                plan = await plans.get(plan_id)
                return plan.to_view() if plan is not None else None

        return DeliveryArchiveService(
            PostgresDeliveryArchiveStore(self.database),
            self.delivery_service(),
            _PlanViewReader(),
            PostgresDeliveryAuditLog(self.database),
        )

    @cached_service
    def validation_snapshot_service(self):
        from repomesh.modules.review_validation import (
            PostgresValidationSnapshotStore,
            ValidationSnapshotService,
        )

        return ValidationSnapshotService(PostgresValidationSnapshotStore(self.database))

    def scm_webhook_event_store(self):
        return self.scm_observation_service()

    @cached_service
    def scm_observation_service(self):
        from repomesh.modules.delivery import (
            PostgresSCMObservationStore,
            SCMObservationService,
        )

        return SCMObservationService(PostgresSCMObservationStore(self.database))

    @cached_service
    def scm_command_service(self):
        from repomesh.modules.delivery import PostgresSCMCommandStore, SCMCommandService

        return SCMCommandService(PostgresSCMCommandStore(self.database))

    def github_observation_processor(self):
        from repomesh.integrations.scm import (
            ChangeSetSCMCoordinator,
            GitHubObservationProcessor,
        )

        delivery = self.delivery_service()
        advancer = self.execution_plan_advancer()
        on_observed = None
        if advancer is not None and get_settings().delivery_auto_enabled:

            async def _on_observed(change_set):
                # After a delivery observation (e.g. a merge) re-evaluate the
                # affected plan so a waiting batch can advance.
                for repository in change_set.repositories:
                    await advancer.reconsider_task(repository.task_id)

            on_observed = _on_observed
        return GitHubObservationProcessor(
            self.scm_observation_service(),
            delivery,
            self.repository_catalog,
            ChangeSetSCMCoordinator(
                delivery,
                self.repository_catalog,
                self.scm_adapter,
                command_service=self.scm_command_service(),
            ),
            auto_merge=get_settings().delivery_auto_enabled,
            on_observed=on_observed,
        )

    def changeset_scm_coordinator(self):
        from repomesh.integrations.scm import ChangeSetSCMCoordinator, GitBranchPublisher

        if self.scm_adapter is None:
            raise RuntimeError("SCM adapter is not configured")
        return ChangeSetSCMCoordinator(
            self.delivery_service(),
            self.repository_catalog,
            self.scm_adapter,
            GitBranchPublisher(
                get_settings().runner_workspace_root,
                token_provider=self.scm_token_provider,
            ),
            command_service=self.scm_command_service(),
        )

    def plan_delivery_finalizer(self):
        from repomesh.integrations.scm import PlanDeliveryFinalizer, PlanDeliveryPolicy

        settings = get_settings()
        if not settings.delivery_required_checks:
            raise RuntimeError("automatic delivery requires at least one named CI check")
        if settings.delivery_required_approvals < 1:
            raise RuntimeError("automatic delivery requires at least one PR approval")
        return PlanDeliveryFinalizer(
            self.delivery_service(),
            self.changeset_scm_coordinator(),
            self.task_store,
            PlanDeliveryPolicy(
                base_branch=settings.delivery_base_branch,
                required_checks=settings.delivery_required_checks,
                required_approvals=settings.delivery_required_approvals,
                add_label=settings.delivery_pr_label,
            ),
            validation=self.validation_snapshot_service(),
            checkpoints=self.project_checkpoint_service(),
            contracts=(
                self.contract_catalog()
                if settings.delivery_contract_gate
                else None
            ),
        )

    @cached_service
    def project_checkpoint_service(self):
        if self.project_checkpoint_service_instance is not None:
            return self.project_checkpoint_service_instance
        from repomesh.modules.project import ProjectCheckpointService
        from repomesh.modules.project.infrastructure import (
            PostgresHumanReviewRequestStore,
            PostgresProjectCheckpointDecisionStore,
        )

        notifier = None
        if self.agent_team_messenger is not None:
            from repomesh.integrations.agentteams.human_decisions import (
                HumanDecisionCollaborationNotifier,
            )
            from repomesh.modules.collaboration import SendCollaborationMessage

            notifier = HumanDecisionCollaborationNotifier(
                SendCollaborationMessage(
                    self.agent_directory,
                    self.project_topology_store,
                    PolicyAuthorizationGateway(),
                    self.collaboration_message_store,
                    self.agent_team_messenger,
                )
            )
        return ProjectCheckpointService(
            self.topology_reader(),
            PostgresProjectCheckpointDecisionStore(self.database),
            PostgresHumanReviewRequestStore(self.database),
            notifier,
        )

    @cached_service
    def human_review_request_store(self):
        from repomesh.modules.project.infrastructure import PostgresHumanReviewRequestStore

        return PostgresHumanReviewRequestStore(self.database)

    @cached_service
    def project_lifecycle_service(self):
        from repomesh.modules.project import ProjectLifecycleService

        return ProjectLifecycleService(self.project_topology_store)

    def task_assignment_gateway(self) -> TaskAssignmentGateway | None:
        """The composed TaskOrchestrator assigns and receives task reports.

        It only exists once the AgentTeams messenger is configured, so every
        execution-plane service derived from it stays optional.
        """

        if self.task_report_gateway is None:
            return None
        return cast(TaskAssignmentGateway, self.task_report_gateway)

    def task_superseder(self) -> TaskSupersederGateway | None:
        """The composed TaskOrchestrator also superseds tasks on replan.

        ``TaskOrchestrator`` implements ``assign``, ``report`` and
        ``supersede``, so the same instance that backs the assignment and
        report gateways also satisfies the superseder protocol. It only
        exists once the AgentTeams messenger is configured.
        """

        if self.task_report_gateway is None:
            return None
        return cast(TaskSupersederGateway, self.task_report_gateway)

    def project_task_reader(self):
        """Expose task views through the TaskOrchestrator public read port."""

        if self.task_report_gateway is None:
            return None
        from repomesh.modules.task_orchestration.contracts import ProjectTaskReader

        return cast(ProjectTaskReader, self.task_report_gateway)

    @cached_service
    def execution_plan_store(self) -> ExecutionPlanStore:
        return PostgresExecutionPlanStore(self.database)

    @cached_service
    def execution_plan_observer(self) -> ObserveExecutionPlan:
        return ObserveExecutionPlan(self.execution_plan_store(), self.task_store)

    def handoff_doc_store(self) -> PostgresHandoffDocStore:
        return PostgresHandoffDocStore(self.database)

    def handoff_doc_service(self) -> HandoffDocService:
        return HandoffDocService(self.handoff_doc_store())

    @cached_service
    def execution_plan_advancer(self) -> AdvanceExecutionPlan | None:
        assigner = self.task_assignment_gateway()
        if assigner is None:
            return None
        decomposer = DecomposeRepositoryTask(
            self.agent_directory,
            self.topology_reader(),
            self.task_store,
            assigner,
            spec_author=ApprovedTaskSpecificationAuthor(
                self.specification_service(), self.specification_store
            ),
        )
        completion_handler = None
        on_batch_deliver = None
        delivery_state = None
        if get_settings().delivery_auto_enabled:
            # Batch-by-batch delivery: each successful batch is delivered and
            # the plan advances only once that batch's pull requests merged.
            # The one-shot ``handle`` is superseded by ``handle_batch``.
            on_batch_deliver = self.plan_delivery_finalizer().handle_batch
            delivery_state = self.delivery_state_adapter()
        return AdvanceExecutionPlan(
            self.execution_plan_store(),
            self.task_store,
            assigner,
            decomposer,
            on_plan_completed=completion_handler,
            delivery_state=delivery_state,
            on_batch_deliver=on_batch_deliver,
        )

    def delivery_state_adapter(self):
        """Adapt DeliveryService into the task orchestration delivery gate.

        The gate only needs a merged flag per repository; ChangeSet delivery
        status is projected here in the composition root.
        """

        return DeliveryStateAdapter(self.delivery_service())

    def execution_plan_starter(self) -> AdvanceExecutionPlanStarter | None:
        advancer = self.execution_plan_advancer()
        if advancer is None:
            return None
        return AdvanceExecutionPlanStarter(advancer, self.task_store)

    @cached_service
    def plan_execution_bridge(self) -> PlanExecutionBridge:
        return PlanExecutionBridge(
            specifications=self.specification_service(),
            plans=self.execution_plan_starter(),
            topologies=self.topology_reader(),
            catalog=self.repository_catalog,
            snapshot_store=self.plan_snapshot_store(),
            superseder=self.task_superseder(),
            task_reader=self.project_task_reader(),
            handoff_docs=self.handoff_doc_service(),
            checkpoints=self.project_checkpoint_service(),
        )

    def runner_gateway(self):
        from repomesh.integrations.runner.gateway import RunnerControlGateway

        advancer = self.execution_plan_advancer()
        return RunnerControlGateway(
            PostgresRunnerGatewayStore(self.database),
            self.task_store,
            advancer.on_task_terminal if advancer is not None else None,
        )

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
            self.task_report_gateway,
            dispatches=PostgresRunnerGatewayStore(self.database),
        )

    async def close(self) -> None:
        try:
            for service in reversed(self.background_services):
                await service.close()
            for resource in reversed(self.external_resources):
                await resource.close()
        finally:
            await self.database.dispose()
