from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

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
    PlanExecutionBridge,
    PlanIntegrationService,
)
from repomesh.modules.repository_intelligence.application.confirmation import ConfirmationService
from repomesh.modules.repository_intelligence.application.discovery import LLMClient
from repomesh.modules.repository_intelligence.application.plan_execution_bridge import (
    StartedExecutionPlan,
    TaskSupersederGateway,
)
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
from repomesh.modules.specification import (
    ApproveSpecificationCommand,
    BuildCodingAgentPackage,
    CreateSpecificationCommand,
    PublishSpecificationContextCommand,
    SpecificationKind,
    SpecificationService,
    SpecificationStatus,
    SubmitSpecificationCommand,
)
from repomesh.modules.specification.ports import SpecificationStore
from repomesh.modules.task_orchestration import (
    AdvanceExecutionPlan,
    DecomposeRepositoryTask,
    ExecutionPlan,
    PlannedRepositoryTask,
    PostgresExecutionPlanStore,
)
from repomesh.modules.task_orchestration.contracts import (
    ExecutionPlanView,
    PlannedRepositoryTaskView,
    TaskAssignmentGateway,
    TaskReportGateway,
    TaskView,
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


class AdvanceExecutionPlanStarter:
    """Adapt ``AdvanceExecutionPlan`` to the plan bridge's starter port.

    The bridge owns repository-intelligence concerns and may only speak the
    published task-orchestration contracts, so building the execution plan
    aggregate belongs to the composition root.
    """

    def __init__(self, advancer: AdvanceExecutionPlan, tasks: TaskStore) -> None:
        self._advancer = advancer
        self._tasks = tasks

    async def start_plan(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        created_by_agent_id: UUID,
        batches: Sequence[Sequence[PlannedRepositoryTaskView]],
        idempotency_key: str,
    ) -> StartedExecutionPlan:
        plan = ExecutionPlan(
            organization_id=organization_id,
            project_id=project_id,
            created_by_agent_id=created_by_agent_id,
            batches=tuple(
                tuple(
                    PlannedRepositoryTask(
                        repository_id=planned.repository_id,
                        title=planned.title,
                        instruction=planned.instruction,
                        acceptance=planned.acceptance,
                        tests=planned.tests,
                    )
                    for planned in batch
                )
                for batch in batches
            ),
        )
        view = await self._advancer.start(plan, idempotency_key=idempotency_key)
        return StartedExecutionPlan(plan=view, tasks=await self._assigned_tasks(view))

    async def _assigned_tasks(self, view: ExecutionPlanView) -> tuple[TaskView, ...]:
        """Read back the leader tasks the plan assigned and their Worker children."""

        assigned: list[TaskView] = []
        for batch in view.batches:
            for planned in batch:
                if planned.leader_task_id is None:
                    continue
                leader = await self._tasks.get(planned.leader_task_id)
                if leader is None:
                    continue
                assigned.append(leader.to_view())
                for child in await self._tasks.list_by_parent(leader.id):
                    assigned.append(child.to_view())
        return tuple(assigned)


class ApprovedTaskSpecificationAuthor:
    """Create, approve, freeze and publish the Task Spec a Worker task executes under.

    ``start_assigned_task`` refuses to run a Worker task without an approved
    (or frozen) ``SpecificationKind.TASK`` bound to that task, and it reads the
    Runner's test commands out of the spec.  Producing that permit needs the
    specification service, which *task_orchestration* may not import, so the
    composition root adapts it to the published ``TaskSpecificationAuthor`` port.

    The four steps mirror the manual ritual proven by
    ``scripts/run-live-worker-e2e.py``: create → submit → approve(freeze) →
    publish.  Steps the stored specification already went through are skipped,
    and a task that already holds a permit is left alone entirely -- a second
    approved spec for the same task would make ``start_assigned_task`` fail with
    "multiple approved task specifications found", so the guard is keyed on the
    task rather than only on the caller's idempotency key.
    """

    def __init__(self, specifications: SpecificationService, stored: SpecificationStore) -> None:
        self._specifications = specifications
        self._stored = stored

    async def ensure_approved(
        self,
        task: TaskView,
        *,
        allowed_paths: tuple[str, ...],
        tests: tuple[str, ...],
        idempotency_key: str,
    ) -> None:
        if await self._has_permit(task):
            return
        # The repository leader that assigned the Worker task owns its spec.
        owner_agent_id = task.assigned_by_agent_id
        current = await self._specifications.create(
            CreateSpecificationCommand(
                organization_id=task.organization_id,
                project_id=task.project_id,
                repository_id=task.repository_id,
                task_id=task.id,
                kind=SpecificationKind.TASK,
                title=self._title(task),
                created_by_agent_id=owner_agent_id,
                goal=task.instruction,
                acceptance=task.acceptance,
                tests=self._clean(tests),
                allowed_paths=self._clean(allowed_paths),
            ),
            idempotency_key=idempotency_key,
        )
        if current.status is SpecificationStatus.DRAFT:
            current = await self._specifications.submit(
                SubmitSpecificationCommand(current.id, owner_agent_id, current.revision)
            )
        if current.status is SpecificationStatus.IN_REVIEW:
            current = await self._specifications.approve(
                ApproveSpecificationCommand(
                    current.id, owner_agent_id, current.revision, freeze=True
                )
            )
            # Publishing is the last step, and it has no idempotency key of its
            # own: only publish the approval this call produced.
            await self._specifications.publish_to_context(
                PublishSpecificationContextCommand(current.id, owner_agent_id)
            )

    async def _has_permit(self, task: TaskView) -> bool:
        """Report whether *task* already has the permit the Runner path requires."""

        return any(
            specification.kind is SpecificationKind.TASK
            and specification.task_id == task.id
            and specification.repository_id == task.repository_id
            and specification.status in {SpecificationStatus.APPROVED, SpecificationStatus.FROZEN}
            for specification in await self._stored.list_by_project(task.project_id)
        )

    @staticmethod
    def _title(task: TaskView) -> str:
        return f"Task spec: {task.title}".strip()[:500]

    @staticmethod
    def _clean(values: Sequence[str]) -> tuple[str, ...]:
        """Drop blanks: specification content rejects empty list entries."""

        return tuple(value.strip() for value in values if value.strip())


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

    def capability_assembler(self) -> PresetCapabilityAssembler:
        return PresetCapabilityAssembler()

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

    def delivery_service(self):
        from repomesh.modules.delivery import DeliveryService, PostgresChangeSetStore

        validation = self.validation_snapshot_service()
        return DeliveryService(
            PostgresChangeSetStore(self.database),
            require_governance=get_settings().delivery_auto_enabled,
            require_validation=get_settings().delivery_auto_enabled,
            validation_reader=validation,
        )

    def validation_snapshot_service(self):
        from repomesh.modules.review_validation import (
            PostgresValidationSnapshotStore,
            ValidationSnapshotService,
        )

        return ValidationSnapshotService(PostgresValidationSnapshotStore(self.database))

    def scm_webhook_event_store(self):
        return self.scm_observation_service()

    def scm_observation_service(self):
        from repomesh.modules.delivery import (
            PostgresSCMObservationStore,
            SCMObservationService,
        )

        return SCMObservationService(PostgresSCMObservationStore(self.database))

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
            ChangeSetSCMCoordinator(delivery, self.repository_catalog, self.scm_adapter),
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
            ),
            validation=self.validation_snapshot_service(),
            checkpoints=self.project_checkpoint_service(),
        )

    def project_checkpoint_service(self):
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

    def human_review_request_store(self):
        from repomesh.modules.project.infrastructure import PostgresHumanReviewRequestStore

        return PostgresHumanReviewRequestStore(self.database)

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

    def execution_plan_store(self) -> ExecutionPlanStore:
        return PostgresExecutionPlanStore(self.database)

    def handoff_doc_store(self) -> PostgresHandoffDocStore:
        return PostgresHandoffDocStore(self.database)

    def handoff_doc_service(self) -> HandoffDocService:
        return HandoffDocService(self.handoff_doc_store())

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

        from repomesh.modules.delivery.contracts import RepositoryDeliveryStatus
        from repomesh.modules.task_orchestration.contracts import (
            DeliveryGatedRepositoryView,
        )

        class _DeliveryStatePort:
            def __init__(self, delivery) -> None:
                self._delivery = delivery

            async def repository_states(self, project_id):
                by_repository: dict[UUID, DeliveryGatedRepositoryView] = {}
                for change_set in await self._delivery.find_by_project(project_id):
                    for repository in change_set.repositories:
                        merged = repository.status is RepositoryDeliveryStatus.MERGED
                        previous = by_repository.get(repository.repository_id)
                        if previous is None or (merged and not previous.merged):
                            by_repository[repository.repository_id] = (
                                DeliveryGatedRepositoryView(
                                    repository_id=repository.repository_id,
                                    merged=merged,
                                )
                            )
                return tuple(by_repository.values())

        return _DeliveryStatePort(self.delivery_service())

    def execution_plan_starter(self) -> AdvanceExecutionPlanStarter | None:
        advancer = self.execution_plan_advancer()
        if advancer is None:
            return None
        return AdvanceExecutionPlanStarter(advancer, self.task_store)

    def plan_execution_bridge(self) -> PlanExecutionBridge:
        return PlanExecutionBridge(
            specifications=self.specification_service(),
            plans=self.execution_plan_starter(),
            topologies=self.topology_reader(),
            catalog=self.repository_catalog,
            snapshot_store=self.plan_snapshot_store(),
            superseder=self.task_superseder(),
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
