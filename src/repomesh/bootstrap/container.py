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
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.ports import AgentDirectory
from repomesh.modules.agent_runtime.contracts import ExternalWorkerRefused
from repomesh.modules.agent_runtime.ports.agent_team import (
    AgentTeamControlPlane,
    ExternalMemberProvisioner,
    ExternalMemberRole,
    ExternalWorkerProvisioner,
    WorkerBindingReader,
    WorkerControlPlaneUnavailable,
    WorkerRuntimeRef,
)
from repomesh.modules.agent_runtime.ports.coding_agent import CodingAgent
from repomesh.modules.agent_runtime.runner_store import PostgresRunnerGatewayStore
from repomesh.modules.capability_management import (
    PresetCapabilityAssembler,
    ResolveAgentCapabilities,
)
from repomesh.modules.change_orchestration import PlanExecutionBridge, TaskSupersederGateway
from repomesh.modules.collaboration.contracts import AuthorizedRoom, CollaborationGateway
from repomesh.modules.collaboration.ports import (
    AuthorizedRoomReader,
    CollaborationMessageStore,
    CollaborationMessenger,
)
from repomesh.modules.context.application import ContextPublicationGateway, GetExecutionContextGrant
from repomesh.modules.context.ports import ContextStore
from repomesh.modules.identity_access import LocalAccountService, PolicyAuthorizationGateway
from repomesh.modules.identity_access.infrastructure import PostgresLocalAccountStore
from repomesh.modules.observability.infrastructure.alerting import (
    AlertingEvaluator,
    AlertingStore,
)
from repomesh.modules.observability.infrastructure.log_query import LogQueryStore
from repomesh.modules.observability.infrastructure.log_recorder import LogRecorder
from repomesh.modules.observability.infrastructure.trace_ingest import TraceStore
from repomesh.modules.observability.infrastructure.trace_query import TraceQueryStore
from repomesh.modules.observability.infrastructure.usage_query import UsageQueryStore
from repomesh.modules.observability.infrastructure.usage_recorder import QueuedUsageRecorder
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectTopologyReader,
    TeamDecompositionMode,
    TeamDecompositionModeReader,
)
from repomesh.modules.project.infrastructure import PersistedTeamDecompositionModeReader
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
    LeaderDecisionLane,
    ObserveExecutionPlan,
    PostgresExecutionPlanStore,
    PostgresLeaderAssignmentStore,
    ReadLeaderAssignment,
    SubmitRepositoryPlan,
    SubmitRepositoryReview,
)
from repomesh.modules.task_orchestration.contracts import (
    PublishedTaskPackage,
    TaskAssignmentGateway,
    TaskAssignmentPublisher,
    TaskReportGateway,
    TaskView,
)
from repomesh.modules.task_orchestration.ports import (
    ExecutionPlanStore,
    LeaderAssignmentStore,
    TaskStore,
)
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


#: How many recorded room messages one stream read pulls from the timeline.
#:
#: The room stream merges every source and *then* pages, so this is a ceiling
#: on what one room's transcript contributes to that merge, not the page the
#: console sees. A room with more recorded messages than this shows its oldest
#: ones; the outbound side has no equivalent ceiling because that table is
#: written only by this server and stays small. Raising the number is a
#: one-line change; making the merge itself cursor-based across sources is the
#: real fix, and is not what a repository team's room volume calls for.
_ROOM_TIMELINE_PAGE = 1000


def project_topology_reader(store: ProjectTopologyStore) -> ProjectTopologyReader:
    """Adapt the topology store to the read port modules depend on."""

    class _Adapter:
        async def get_view(self, project_id: UUID) -> ProjectAgentTopologyView | None:
            topology = await store.get(project_id)
            return topology.to_view() if topology else None

    return _Adapter()


def authorized_room_reader(store: ProjectTopologyStore) -> AuthorizedRoomReader:
    """The room-ingest whitelist, derived from the topology and nothing else.

    A room is authorized exactly when some repository team names it as its
    team room or its leader DM — there is no separate list to maintain and no
    way for one to drift from the teams that actually exist. Everything else,
    including any room RepoMesh's account is merely invited to, resolves to
    None and is never recorded.

    The lookup returns the *team's* project and repository so the recorder can
    attribute the message without a second query; a room id that matches two
    teams is impossible (each id appears in one team's row).
    """

    class _Adapter:
        async def authorized_room(self, room_id: str) -> AuthorizedRoom | None:
            topology = await store.find_view_by_room(room_id)
            if topology is None:
                return None
            for team in topology.repository_teams:
                if room_id in {team.room_id, team.leader_room_id}:
                    return AuthorizedRoom(
                        room_id=room_id,
                        project_id=topology.project_id,
                        repository_id=team.repository_id,
                    )
            return None

    return _Adapter()


class StaticTeamDecompositionModeReader:
    """One answer for every team, whatever the topology says.

    Was the production ``TeamDecompositionModeReader`` while the project module
    had nowhere to persist a per-team mode; since PR 5.5B that job belongs to
    ``PersistedTeamDecompositionModeReader``, which reads the adopted mode off
    the topology row (adjudication D-2, revision 0037), and the wiring below
    uses it.

    Kept because it is the honest reader for a container assembled without a
    topology store to read through, and because "every team decomposes
    server-side" is worth being able to state in one line in a test. It is no
    longer wired anywhere by default, and a deployment that reached for it
    again would be turning off adoption for every project at once.
    """

    def __init__(self, mode: TeamDecompositionMode = TeamDecompositionMode.SERVER) -> None:
        self._mode = mode

    async def decomposition_mode(
        self, project_id: UUID, repository_id: UUID
    ) -> TeamDecompositionMode:
        return self._mode


def collaboration_routed_messenger(messenger: CollaborationMessenger) -> CollaborationMessenger:
    """Adapt the AgentTeams messenger to the refusal the collaboration port owns.

    ``AgentTeamsUnavailable`` is the integration's word for "the execution
    plane cannot take this message *yet*" — the recipient's Matrix identity has
    not appeared, or Matrix itself did not answer. The collaboration port
    already has a name for exactly that, ``CollaborationRouteUnavailable``, and
    every caller up the chain is written against it: the API layer turns it
    into a retryable 503 and the round stays materialisable.

    Untranslated, it escaped the whole stack as a bare 500 with no body — the
    console told the operator to file a bug about a plan that only needed the
    button pressed again (defect A-6, found live 2026-08-12). This is the same
    move ``topology_runtime_projector`` makes for the projection's taxonomy,
    made in the same place and for the same reason: the business module must
    not import the integration, so the composition root is where the two
    vocabularies are allowed to meet.

    Only ``AgentTeamsUnavailable`` is translated. Its siblings
    ``AgentTeamsResponseError`` and ``AgentTeamsConflict`` mean the plane
    answered and the answer was wrong, which is a fault to report rather than a
    wait to retry, and dressing them as 503 would tell the operator to keep
    pressing a button that cannot work.
    """

    from repomesh.integrations.agentteams import AgentTeamsUnavailable
    from repomesh.modules.collaboration.contracts import CollaborationRouteUnavailable

    class _Messenger:
        async def send_task(
            self,
            room_id: str,
            body: str,
            *,
            transaction_id: str,
            recipient_resource_name: str | None = None,
            recipient_role: AgentRole | None = None,
        ) -> str:
            try:
                return await messenger.send_task(
                    room_id,
                    body,
                    transaction_id=transaction_id,
                    recipient_resource_name=recipient_resource_name,
                    recipient_role=recipient_role,
                )
            except AgentTeamsUnavailable as error:
                raise CollaborationRouteUnavailable(str(error)) from error

        def __getattr__(self, name: str):
            # The concrete client is also a Matrix gateway (whoami, sync_once,
            # close). Only delivery is being retold, so everything else is the
            # object itself.
            return getattr(messenger, name)

    return _Messenger()


def storage_backed_task_publisher(
    publisher: TaskAssignmentPublisher,
) -> TaskAssignmentPublisher:
    """Adapt a task-package store to the refusal the publisher port owns.

    A Worker is handed its work as files. ``AgentTeamsTaskPublisher`` writes
    the package to a directory and ``AgentTeamsObjectTaskPublisher`` writes the
    same package through AgentTeams' S3 API, and both speak their store's
    native failure vocabulary — ``minio.error.S3Error`` and its
    ``MinioException`` siblings, ``urllib3``'s connection errors, and plain
    ``OSError`` from the filesystem. None of those meant anything to the
    materialize path, so they escaped it: an S3 ``InvalidAccessKeyId`` reached
    the console as ``text/plain`` "Internal Server Error" for a round that
    only needed the button again once the credentials were right (defect A-10,
    found live 2026-08-12).

    So the composition root wraps the publisher once, where the port meets the
    adapter, and retells them as ``TaskPublicationUnavailable`` with the
    store's own sentence preserved — the same move
    ``collaboration_routed_messenger`` makes for Matrix identities (A-6) and
    ``topology_runtime_projector`` makes for the projection taxonomy, in the
    same place and for the same reason: the business module must not import
    the integration, so the composition root is where the two vocabularies are
    allowed to meet.

    Both variants are wrapped by one function because the port is what is
    being wrapped, not the adapter. An unreachable store and a misconfigured
    one are the same reading — the execution plane cannot take this *yet* — and
    the live evidence was the misconfigured half.

    ``ValueError`` is deliberately not translated. The file channel raises it
    when the task path already holds a *different* package, which means the
    store answered and the answer was no; pressing materialize again cannot
    change it, and a 503 there would tell the operator to keep pressing a
    button that cannot work.
    """

    from repomesh.modules.task_orchestration.contracts import TaskPublicationUnavailable

    # ``minio`` is an optional import in the object publisher, so the file
    # channel must keep working in a deployment that never installed it.
    try:
        from minio.error import MinioException
    except ImportError:  # pragma: no cover - minio is a declared dependency
        MinioException = ()
    try:
        from urllib3.exceptions import HTTPError as Urllib3Error
    except ImportError:  # pragma: no cover - urllib3 arrives with minio
        Urllib3Error = ()
    # OSError already covers ConnectionError, TimeoutError and every
    # filesystem failure the file channel can raise, including its own
    # "publication verification failed".
    unavailable: tuple[type[BaseException], ...] = tuple(
        family
        for family in (OSError, MinioException, Urllib3Error)
        if isinstance(family, type)
    )

    class _Publisher:
        async def publish(
            self,
            task: TaskView,
            *,
            team_name: str,
            room_id: str,
            assignee_resource_name: str,
            idempotency_key: str,
        ) -> PublishedTaskPackage:
            try:
                return await publisher.publish(
                    task,
                    team_name=team_name,
                    room_id=room_id,
                    assignee_resource_name=assignee_resource_name,
                    idempotency_key=idempotency_key,
                )
            except unavailable as error:
                raise TaskPublicationUnavailable(str(error)) from error

        def __getattr__(self, name: str):
            return getattr(publisher, name)

    return _Publisher()


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
    # Thread-safe usage sink + background flush service for planning LLM calls.
    usage_recorder: QueuedUsageRecorder | None = None
    # Process-log capture + background flush service for the unified log page.
    log_recorder: LogRecorder | None = None
    agent_team_control_plane: AgentTeamControlPlane | None = None
    agent_team_messenger: CollaborationMessenger | None = None
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

    def project_topology_provisioner(self):
        """Contract v0.4 §8: the topology a console round makes on its way to work.

        Composed of the two capabilities the modules already publish rather
        than a third path into the same tables — ``scripts/run_pipeline.py``
        writes the topology straight to the store, and repeating that here
        would put a fourth spelling of "what a team is" in the codebase.
        """

        from repomesh.modules.agent_directory.application import (
            ProvisionRepositoryAgentTeam,
        )
        from repomesh.modules.project import EnsureProjectAgentTopology

        return EnsureProjectAgentTopology(
            self.project_topology_store,
            ProvisionRepositoryAgentTeam(self.agent_directory),
            self.project_topology_creator(),
            self.topology_policy_draft_store(),
        )

    @cached_service
    def topology_policy_draft_store(self):
        """Where the admin face leaves a supervision policy for materialization.

        One instance for both directions on purpose: the endpoints write
        through it over an admin session, ``EnsureProjectAgentTopology`` reads
        through it in-process. That asymmetry is the whole design — the shared
        console action token gains no way to set a policy, only to trigger one
        an admin already set.
        """

        from repomesh.modules.project.infrastructure import (
            PostgresTopologyPolicyDraftStore,
        )

        return PostgresTopologyPolicyDraftStore(self.database)

    def topology_runtime_projector(self):
        """Contract v0.4 §8: the step that makes a round's rooms exist.

        Wired here for the reason the bridge is: the capability is composed
        out of an integration (``RegisterNativeAgent`` +
        ``ReconcileProjectAgentTopology``) and the module that needs it
        declares only a Protocol, so the two can meet nowhere else.

        The adapter's whole body is a translation. The integration raises its
        own taxonomy — unreachable controller, refused request, teams whose
        rooms have not appeared — and the port publishes two refusals, split on
        the only question the caller can act on: *is pressing the button again
        a plan?* Unreachable and rooms-not-yet are yes (503). A controller that
        answered and said no — ``AgentTeamsConflict``, or any 4xx it spelled
        out — is not (409, §8.7.1's second ruling, A-8). Folding the second
        into the first is what made a permanent deadlock wear "materialize
        again once AgentTeams answers".

        A container with no control plane gets a projector that refuses rather
        than one that quietly does nothing: "the rooms were never made" is the
        defect this exists to end, and a silent skip is how it hid. Production
        always has one (``build_default_container`` constructs the client
        unconditionally); a test that wants past this stubs it, exactly as it
        already stubs ``execution_plan_starter``.
        """

        from repomesh.modules.repository_intelligence.ports import (
            RuntimeProjectionConflict,
            RuntimeProjectionUnavailable,
        )

        control_plane = self.agent_team_control_plane
        store = self.project_topology_store
        directory = self.agent_directory
        settings = get_settings()

        class _RuntimeProjection:
            async def project(self, project_id: UUID) -> None:
                from repomesh.integrations.agentteams import (  # noqa: PLC0415
                    AgentTeamsConflict,
                    AgentTeamsError,
                    AgentTeamsResponseError,
                    AgentTeamsRoomsPending,
                    ProjectRuntimeProjection,
                )

                if control_plane is None:
                    raise RuntimeProjectionUnavailable(
                        "the AgentTeams control plane is not configured, so this "
                        "project's teams can have no rooms"
                    )
                try:
                    await ProjectRuntimeProjection(
                        directory,
                        store,
                        control_plane,
                        model=settings.deepseek_model,
                        manager_runtime=settings.agentteams_manager_runtime,
                        worker_runtime=settings.agentteams_worker_runtime,
                        worker_task_control_url=settings.worker_task_control_url,
                    ).project(project_id)
                except AgentTeamsRoomsPending as error:
                    # A subclass of AgentTeamsError and *not* a conflict: the
                    # controller took the Teams and simply has not published
                    # their rooms yet. Caught first because the clauses below
                    # would not otherwise see past it.
                    raise RuntimeProjectionUnavailable(str(error)) from error
                except AgentTeamsConflict as error:
                    raise RuntimeProjectionConflict(str(error)) from error
                except AgentTeamsResponseError as error:
                    # The controller answered with a status. 4xx is a verdict
                    # on what we asked for — the already-a-member 400 A-8 lived
                    # under is exactly this — and 5xx is the plane having a bad
                    # day, which a retry may well outlast.
                    if 400 <= error.status_code < 500:
                        raise RuntimeProjectionConflict(str(error)) from error
                    raise RuntimeProjectionUnavailable(str(error)) from error
                except AgentTeamsError as error:
                    raise RuntimeProjectionUnavailable(str(error)) from error

        return _RuntimeProjection()

    def automatic_project_topology_creator(self):
        from repomesh.modules.project import CreateAutomaticProjectTopology

        return CreateAutomaticProjectTopology(
            self.agent_directory,
            self.project_topology_creator(),
        )

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

    def external_worker_binding_control_plane(self) -> WorkerBindingReader | None:
        """The control plane the bridge preflight (ADR 0004) reads through.

        Wraps ``agent_team_control_plane`` in ``ExternalWorkerProjection`` so
        that an unreachable controller reaches ``ResolveExternalWorkerBinding``
        as ``WorkerControlPlaneUnavailable`` rather than the integration's own
        ``AgentTeamsUnavailable`` — the router maps the former to 503 without
        importing ``repomesh.integrations.*``, which module code may not do.

        Typed as the narrow ``WorkerBindingReader`` because that is what this
        returns and what the preflight may have: the adapter implements two
        reads and a provisioning method, never the whole
        ``AgentTeamControlPlane``, and an annotation claiming otherwise invites
        a caller to reach for an ``ensure_*`` that is not there.

        Scoped to this one call site: every other reader of
        ``agent_team_control_plane`` (``ProjectRuntimeProjection``,
        ``RegisterNativeAgent``, the readiness probe) keeps talking to the raw
        client and is unaffected by this translation.
        """

        if self.agent_team_control_plane is None:
            return None
        from repomesh.integrations.agentteams.runtime_projection import (  # noqa: PLC0415
            ExternalWorkerProjection,
        )

        settings = get_settings()
        return ExternalWorkerProjection(
            self.agent_team_control_plane,
            model=settings.deepseek_model,
            worker_runtime=settings.agentteams_worker_runtime,
            worker_task_control_url=settings.worker_task_control_url,
        )

    def external_worker_provisioner(self) -> ExternalWorkerProvisioner | None:
        """The v1 name for the adapter below, kept for the v1 route that asks by it.

        One adapter, two names, because there is one AgentTeams resource per
        principal and provisioning it twice under two spellings is exactly what
        a second adapter would make possible. The narrower type is the honest
        one for this caller: the v1 route has no role to pass and must not
        acquire the ability to.
        """

        return self.external_member_provisioner()

    def external_member_provisioner(self) -> ExternalMemberProvisioner | None:
        """The provisioning half of ADR 0004, with the integration's errors translated.

        ``ExternalWorkerProjection``, the same class the preflight reads
        through — one projection, so the worker this writes is field-for-field
        the worker that endpoint later confirms — wrapped in the translation the
        port's contract
        asks its callers for: ``ExternalWorkerProvisioner`` says an adapter
        conflict is a *refusal*, not an internal error, and the admin route
        cannot enforce that itself because module code may not import
        ``repomesh.integrations.*`` to catch ``AgentTeamsConflict``.

        So the split is the same one ``project_runtime_projector`` makes, on the
        same question — *is pressing the button again a plan?* A controller that
        answered and said no (a conflict, or any 4xx it spelled out) is a 409
        that no retry clears; a controller that did not answer, or answered 5xx,
        is a 503. Reads stay unwrapped: ``ExternalWorkerProjection.get_worker``
        and ``get_team`` already translate their own transport failures.

        ``None`` when no control plane is configured, exactly as
        ``external_worker_binding_control_plane`` answers — the route turns that
        into 503 rather than provisioning nothing and reporting success.

        The adapter is built here rather than taken from that method precisely
        because that method's return type is narrowed to two reads on purpose:
        casting past a narrowing to reach the write it was narrowed to hide is
        the move it exists to prevent. The three settings below are the only
        duplication, and they are the same three both call sites would read.
        """

        if self.agent_team_control_plane is None:
            return None
        from repomesh.integrations.agentteams.runtime_projection import (  # noqa: PLC0415
            ExternalWorkerProjection,
        )

        settings = get_settings()
        projection = ExternalWorkerProjection(
            self.agent_team_control_plane,
            model=settings.deepseek_model,
            worker_runtime=settings.agentteams_worker_runtime,
            worker_task_control_url=settings.worker_task_control_url,
        )

        class _ExternalMemberProvisioner:
            async def provision(
                self,
                name: str,
                *,
                idempotency_key: str,
                role: ExternalMemberRole = ExternalMemberRole.WORKER,
            ) -> WorkerRuntimeRef:
                from repomesh.integrations.agentteams import (  # noqa: PLC0415
                    AgentTeamsConflict,
                    AgentTeamsError,
                    AgentTeamsResponseError,
                )

                try:
                    return await projection.provision(
                        name, idempotency_key=idempotency_key, role=role
                    )
                except AgentTeamsConflict as error:
                    raise ExternalWorkerRefused(str(error)) from error
                except AgentTeamsResponseError as error:
                    if 400 <= error.status_code < 500:
                        raise ExternalWorkerRefused(str(error)) from error
                    raise WorkerControlPlaneUnavailable(str(error)) from error
                except AgentTeamsError as error:
                    raise WorkerControlPlaneUnavailable(str(error)) from error

        return _ExternalMemberProvisioner()

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

        return project_topology_reader(self.project_topology_store)

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

    def usage_query_store(self) -> UsageQueryStore:
        return UsageQueryStore(self.database)

    @cached_service
    def alerting_store(self) -> AlertingStore:
        return AlertingStore(self.database)

    @cached_service
    def alerting_evaluator(self) -> AlertingEvaluator:
        return AlertingEvaluator(
            self.alerting_store(),
            self.usage_query_store(),
            trace_query=self.trace_query_store(),
        )

    @cached_service
    def trace_store(self) -> TraceStore:
        return TraceStore(self.database)

    @cached_service
    def trace_query_store(self) -> TraceQueryStore:
        return TraceQueryStore(self.database)

    @cached_service
    def log_query_store(self) -> LogQueryStore:
        return LogQueryStore(self.database)

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
                self.contract_catalog() if get_settings().delivery_contract_gate else None
            ),
        )

    @cached_service
    def delivery_policy_store(self):
        from repomesh.modules.delivery import PostgresDeliveryPolicyStore

        return PostgresDeliveryPolicyStore(self.database)

    @staticmethod
    def default_delivery_policy(organization_id):
        from repomesh.modules.delivery import DeliveryPolicy

        settings = get_settings()
        return DeliveryPolicy(
            organization_id=organization_id,
            auto_merge=settings.delivery_auto_enabled,
            base_branch=settings.delivery_base_branch,
            required_checks=settings.delivery_required_checks,
            required_approvals=settings.delivery_required_approvals,
            contract_gate=settings.delivery_contract_gate,
            add_label=settings.delivery_pr_label,
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
                    interface = content.interface_changes[0] if content.interface_changes else ""
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
            RepositoryProfileData,
            RepositorySpecData,
            RunnerEventData,
            RuntimeSnapshot,
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
                        execution_batches=tuple(tuple(batch) for batch in record.execution_batches),
                        task_dag=tuple(record.task_dag),
                        execution_plan_id=record.execution_plan_id,
                        created_by_agent_id=record.created_by_agent_id,
                        contracts=tuple(record.contracts or ()),
                        discovery=record.discovery,
                    )
                    for record in await snapshot_store.list_all(project_id)
                )

        class _Tasks:
            async def list_by_project(self, project_id: UUID):
                return tuple(
                    task.to_view()
                    for task in await container.task_store.list_by_project(project_id)
                )

            async def list_all(self):
                return tuple(task.to_view() for task in await container.task_store.list_all())

        class _ChangeSets:
            async def for_delivery(self, delivery_id: UUID):
                return await delivery.get_by_idempotency_key(delivery_change_set_key(delivery_id))

            async def merge_gate(self, change_set_id: UUID, repository_id: UUID):
                return await delivery.evaluate_merge_gate(change_set_id, repository_id)

            async def recovery_preview(self, change_set_id: UUID):
                from repomesh.modules.delivery.contracts import (
                    PlanRecoveryCommand,
                    RecoveryTrigger,
                )

                return await delivery.preview_recovery(
                    PlanRecoveryCommand(
                        change_set_id=change_set_id,
                        trigger=RecoveryTrigger.OPERATOR_REQUESTED,
                        reason="rollback scope preview",
                    )
                )

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
                frozen = [item for item in candidates if item.status is SpecificationStatus.FROZEN]
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

            async def repository_spec(self, project_id: UUID, repository_id: UUID):
                candidates = [
                    specification
                    for specification in await container.specification_store.list_by_project(
                        project_id
                    )
                    if specification.repository_id == repository_id
                    and specification.kind in {SpecificationKind.REPOSITORY, SpecificationKind.TASK}
                ]
                if not candidates:
                    return None
                # Contract v0.2 §5.4: FROZEN wins, then APPROVED, then the
                # highest revision within the winning status.
                for status in (SpecificationStatus.FROZEN, SpecificationStatus.APPROVED):
                    ranked = [item for item in candidates if item.status is status]
                    if ranked:
                        chosen = max(ranked, key=lambda item: item.revision)
                        break
                else:
                    return None
                content = chosen.current_version.content
                return RepositorySpecData(
                    specification_id=chosen.id,
                    kind=chosen.kind.value,
                    status=chosen.status.value,
                    revision=chosen.revision,
                    goal=content.goal,
                    acceptance=content.acceptance,
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

            async def profiles(self):
                return tuple(
                    RepositoryProfileData(
                        id=profile.id,
                        name=profile.name,
                        url=profile.url,
                        description=profile.description,
                        topics=tuple(profile.topics),
                        languages=tuple(profile.languages),
                        test_commands=tuple(profile.test_commands),
                        test_paths=tuple(profile.test_paths),
                        profiled_at=profile.profiled_at,
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

            async def list_all(self):
                return tuple(
                    principal.to_view() for principal in await container.agent_directory.list()
                )

        class _Topology:
            async def get_view(self, project_id: UUID):
                return await container.topology_reader().get_view(project_id)

            async def find_by_room(self, room_id: str):
                return await container.project_topology_store.find_view_by_room(room_id)

            async def list_views(self):
                return tuple(await container.project_topology_store.list_views())

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
                    message.to_view() for message in await message_store.list_by_project(project_id)
                )

            async def for_room(self, room_id: str):
                return tuple(
                    message.to_view() for message in await message_store.list_by_room(room_id)
                )

            async def last_assignment_at(self, project_id: UUID):
                return await message_store.last_assignment_at(project_id)

        class _RoomTimeline:
            """Read through ``collaboration``'s own read use case (gate #9).

            Not a query against ``collaboration.room_timeline_messages``: the
            API layer holds a Database handle and could join that table
            directly, and doing so would make the ordering rule and the
            whitelist two independent opinions the moment either changed. The
            use case is one line thick on purpose — the point is which module
            owns the answer, not how much code stands between.
            """

            async def for_room(self, room_id: str):
                return await container.room_timeline_reader().list_room(
                    room_id, limit=_ROOM_TIMELINE_PAGE
                )

        class _Observations:
            async def for_change_set(self, change_set_id: UUID):
                return tuple(
                    observation.to_view()
                    for observation in await observation_store.list_by_change_set(change_set_id)
                )

        class _Runtime:
            """Live AgentTeams proxy for §4.4.

            Translates the integration's error taxonomy into the read model's
            two outcomes: None for 404 (no such resource) and a raised error for
            anything else, which the read model degrades to reachable:false.
            """

            def __init__(self, control_plane) -> None:
                self._control_plane = control_plane

            async def worker(self, name: str):
                ref = await self._not_found_as_none(self._control_plane.get_worker(name))
                if ref is None:
                    return None
                return RuntimeSnapshot(
                    phase=ref.phase,
                    runtime_kind=ref.runtime,
                    matrix_user_id=ref.matrix_user_id,
                    room_id=ref.room_id,
                    message=ref.message,
                    container_managed=ref.container_managed,
                )

            async def manager(self, name: str):
                ref = await self._not_found_as_none(self._control_plane.get_manager(name))
                if ref is None:
                    return None
                return RuntimeSnapshot(
                    phase=ref.phase,
                    matrix_user_id=ref.matrix_user_id,
                    room_id=ref.room_id,
                )

            async def team(self, name: str):
                ref = await self._not_found_as_none(self._control_plane.get_team(name))
                if ref is None:
                    return None
                return RuntimeSnapshot(
                    phase=ref.phase,
                    ready_workers=ref.ready_workers,
                    total_workers=ref.total_workers,
                )

            @staticmethod
            async def _not_found_as_none(awaitable):
                from repomesh.integrations.agentteams import AgentTeamsResponseError

                try:
                    return await awaitable
                except AgentTeamsResponseError as error:
                    if error.status_code == 404:
                        return None
                    raise

        class _DiscoveryTasks:
            def running(self, issue_id: UUID):
                from repomesh.modules.repository_intelligence.api.discovery_chain import (  # noqa: PLC0415
                    running_task,
                )

                return running_task(issue_id)

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
            room_timeline=_RoomTimeline(),
            runtime=(
                _Runtime(self.agent_team_control_plane)
                if self.agent_team_control_plane is not None
                else None
            ),
            # v0.4 §3.1: "is a step in flight" is process memory owned by the
            # writing module. Adapted here rather than imported there, the same
            # way the AgentTeams runtime probe is — the read model depends on
            # the protocol, not on where the dict lives.
            discovery_tasks=_DiscoveryTasks(),
            probe_timeout=get_settings().runtime_probe_timeout_seconds,
            probe_concurrency=get_settings().runtime_probe_concurrency,
        )

    def organization_registry_service(self):
        from repomesh.modules.agent_directory.application import (
            CreateAgent,
            CreateAgentRequest,
        )
        from repomesh.modules.agent_directory.contracts import (
            AgentPrincipalStatus,
            AgentRole,
        )
        from repomesh.modules.agent_directory.domain import AgentAlreadyExists
        from repomesh.modules.delivery import PostgresDeliveryAuditLog
        from repomesh.modules.identity_access.infrastructure import (
            PostgresOrganizationStore,
        )
        from repomesh.modules.identity_access.organizations import (
            OrganizationLeaderConflict,
            OrganizationRegistryService,
        )

        directory = self.agent_directory

        # Composition-root adapters: identity_access only knows its own ports;
        # the agent_directory coupling lives here (same pattern as the read
        # model's inline sources).
        class _LeaderRegistrar:
            async def ensure_leader(
                self, organization_id: UUID, resource_name: str, idempotency_key: str
            ) -> tuple[UUID, bool]:
                # Key already used → that registration stands, whatever
                # resource name this call derived (the naming scheme may have
                # changed since); replaying must not trip CreateAgent's
                # fingerprint comparison.
                existing = await directory.get_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing[0].id, False
                try:
                    created = await CreateAgent(directory).execute(
                        CreateAgentRequest(
                            organization_id=organization_id,
                            role=AgentRole.ORGANIZATION_LEADER,
                            agentteams_resource_name=resource_name,
                        ),
                        idempotency_key=idempotency_key,
                    )
                    return created.principal.id, True
                except AgentAlreadyExists as error:
                    # Converge on the workspace's existing leader when there is
                    # one: the directory's singleton key guarantees at most one
                    # ORGANIZATION_LEADER per organization, so any conflict here
                    # (idempotency-fingerprint drift after the naming scheme
                    # changed, crash-gap replay, singleton race) resolves to
                    # that row instead of stranding the workspace.
                    for principal in await directory.list():
                        if (
                            principal.organization_id == organization_id
                            and principal.role is AgentRole.ORGANIZATION_LEADER
                            and principal.status is AgentPrincipalStatus.ACTIVE
                        ):
                            return principal.id, False
                    # No leader in this workspace → the resource name is held
                    # by another workspace (v0.3 §6 S-8). Typed so the API
                    # answers 409 with repair guidance instead of a 500, and
                    # the organization row stays repairable by replay.
                    raise OrganizationLeaderConflict(
                        f"leader resource name is not available: {resource_name}; "
                        "replay the same idempotency_key with a different "
                        "leader_resource_name"
                    ) from error

        class _AgentCounter:
            async def count_active(self, organization_id: UUID) -> int:
                principals = await directory.list()
                return sum(
                    1
                    for principal in principals
                    if principal.organization_id == organization_id
                    and principal.status is AgentPrincipalStatus.ACTIVE
                )

        return OrganizationRegistryService(
            PostgresOrganizationStore(self.database),
            _LeaderRegistrar(),
            _AgentCounter(),
            PostgresDeliveryAuditLog(self.database),
        )

    def discovery_chain_service(self):
        """Contract v0.4 §4: the discovery chain's write side.

        Same audit sink and the same directory the issue intake uses — the
        acting subject rule is one rule (an active organization leader of the
        issue's workspace), so it is satisfied from one place.
        """

        from repomesh.modules.delivery import PostgresDeliveryAuditLog
        from repomesh.modules.repository_intelligence.application.discovery_chain import (
            DiscoveryChainService,
            DiscoveryPipeline,
        )

        return DiscoveryChainService(
            self.plan_snapshot_store(),
            self.agent_directory,
            PostgresDeliveryAuditLog(self.database),
            DiscoveryPipeline(
                self.repository_catalog,
                self.llm_client,
                self.requirement_analyzer(),
            ),
        )

    def discovery_materialization_service(self):
        """Contract v0.4 §8: the write that ends a round and starts the work.

        The bridge is handed in as ``PlanMaterializer``, the port
        repository_intelligence declares for it. The import direction is the
        reason: change_orchestration already depends on repository_intelligence
        for ``IntegratedPlan``, so the service cannot name the bridge without
        closing a cycle — the composition root is where the two meet.
        """

        from repomesh.modules.repository_intelligence.application.discovery_materialization import (  # noqa: E501
            DiscoveryMaterializationService,
        )

        return DiscoveryMaterializationService(
            self.plan_snapshot_store(),
            self.agent_directory,
            self.repository_catalog,
            self.topology_reader(),
            self.project_topology_provisioner(),
            self.plan_execution_bridge(),
            self.topology_runtime_projector(),
            self.external_member_readiness_gate(),
        )

    def issue_intake_service(self):
        from repomesh.modules.delivery import PostgresDeliveryAuditLog
        from repomesh.modules.repository_intelligence.application import (
            IssueIntakeService,
        )

        # The audit sink is the platform-generic 6-line writer that happens to
        # live in delivery; only the composition root couples to it (module
        # code depends on the IssueIntakeAuditLog protocol).
        return IssueIntakeService(
            self.plan_snapshot_store(),
            self.agent_directory,
            PostgresDeliveryAuditLog(self.database),
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

    def delivery_rollback_service(self):
        from repomesh.modules.delivery import (
            DeliveryRollbackService,
            PostgresDeliveryAuditLog,
        )

        return DeliveryRollbackService(
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
                base_branch=get_settings().delivery_base_branch,
            ),
            auto_merge=get_settings().delivery_auto_enabled,
            on_observed=on_observed,
            rework_tasks=self.ci_rework_task_gateway(),
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
            base_branch=get_settings().delivery_base_branch,
        )

    def ci_rework_task_gateway(self):
        """Route a failed delivery candidate back to the repository Worker.

        Same shape as ``recovery_conflict_task_gateway``: task assignment
        travels over AgentTeams, so without a messenger a CI failure can only
        change state and wait to be noticed.
        """

        from repomesh.integrations.scm.rework import CIReworkTaskCreator

        tasks = self.task_assignment_gateway()
        if tasks is None:
            return None
        return CIReworkTaskCreator(tasks, self.topology_reader())

    def recovery_conflict_task_gateway(self):
        """Route revert conflicts to the repository Worker, when there is one.

        Task assignment travels over AgentTeams, so without a messenger the
        Saga has nobody to hand a conflict to and records the failed action
        instead of parking it in ``waiting_worker``.
        """

        from repomesh.integrations.scm.rework import RecoveryConflictTaskCreator

        tasks = self.task_assignment_gateway()
        if tasks is None:
            return None
        return RecoveryConflictTaskCreator(tasks, self.topology_reader())

    def revert_delivery_gateway(self):
        """Choose the GitHub rollback provider for the recovery Saga."""

        from repomesh.integrations.scm import GitHubRevertDeliveryGateway, MirrorGitReverter
        from repomesh.integrations.scm.github import GitHubAdapter

        if self.scm_adapter is None:
            raise RuntimeError("SCM adapter is not configured")
        settings = get_settings()
        return GitHubRevertDeliveryGateway(
            self.repository_catalog,
            cast(GitHubAdapter, self.scm_adapter),
            MirrorGitReverter(
                settings.runner_workspace_root / "revert-mirrors",
                token_provider=self.scm_token_provider,
            ),
            base_branch=settings.delivery_base_branch,
        )

    def recovery_saga_executor(self):
        """Assemble the durable rollback Saga over the GitHub revert gateway."""

        from repomesh.integrations.scm import (
            GovernedRecoveryActionHandler,
            RecoverySagaExecutor,
        )

        return RecoverySagaExecutor(
            self.delivery_service(),
            GovernedRecoveryActionHandler(self.revert_delivery_gateway()),
            self.recovery_conflict_task_gateway(),
            interval_seconds=get_settings().delivery_recovery_interval_seconds,
        )

    def plan_delivery_finalizer(self):
        from repomesh.integrations.scm import PlanDeliveryFinalizer, PlanDeliveryPolicy

        settings = get_settings()
        if not settings.delivery_required_checks:
            raise RuntimeError("automatic delivery requires at least one named CI check")
        if settings.delivery_required_approvals < 1:
            raise RuntimeError("automatic delivery requires at least one PR approval")
        async def resolve_policy(organization_id, repository_id=None):
            policy = await self.delivery_policy_store().resolve(
                organization_id,
                repository_id,
                fallback=self.default_delivery_policy(organization_id),
            )
            return PlanDeliveryPolicy(
                base_branch=policy.base_branch,
                required_checks=policy.required_checks,
                required_approvals=policy.required_approvals,
                add_label=policy.add_label,
            )

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
            policy_resolver=resolve_policy,
            validation=self.validation_snapshot_service(),
            checkpoints=self.project_checkpoint_service(),
            contracts=(self.contract_catalog() if settings.delivery_contract_gate else None),
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

    def task_redispatch_gateway(self):
        """The same composed TaskOrchestrator, in its re-dispatch role (§8.7.4).

        ``TaskOrchestrator.redispatch`` is the fourth capability on the
        instance that already backs assignment, reports and supersession, so
        this is a cast rather than a construction — and it stays optional for
        the same reason they do: without the AgentTeams messenger there is no
        orchestrator, and a round with nowhere to dispatch to cannot be
        dispatched again.
        """

        if self.task_report_gateway is None:
            return None
        from repomesh.modules.task_orchestration.contracts import TaskRedispatchGateway

        return cast(TaskRedispatchGateway, self.task_report_gateway)

    def round_redispatch_service(self):
        """Contract v0.4 §8.7.4: the operator's handle on a stalled round."""

        dispatcher = self.task_redispatch_gateway()
        if dispatcher is None:
            return None
        from repomesh.modules.task_orchestration.application import RedispatchRound

        return RedispatchRound(
            self.execution_plan_store(),
            self.task_store,
            dispatcher,
            self.leader_assignment_store(),
        )

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
    def leader_assignment_store(self) -> LeaderAssignmentStore:
        return PostgresLeaderAssignmentStore(self.database)

    @cached_service
    def team_decomposition_mode_reader(self) -> TeamDecompositionModeReader:
        """Who decomposes each team's repository tasks (adjudication D-2).

        The project module's own projection over the persisted topology, as of
        PR 5.5B: the mode is a row, written by the adoption pass and read here
        without asking the controller anything. Teams nobody adopted answer
        ``SERVER``, which is every team in an installation that has not
        provisioned an external Repository Leader — so switching the placeholder
        out for this changes no behavior anywhere that has not opted in through
        adoption, which is the whole of D-2.
        """

        return PersistedTeamDecompositionModeReader(self.project_topology_store)

    @cached_service
    def leader_assignment_reader(self) -> ReadLeaderAssignment:
        """The read behind ``GET /agent-actions/leader/assignments/{taskId}``."""

        return ReadLeaderAssignment(
            self.leader_assignment_store(),
            self.task_store,
            self.agent_directory,
            self.team_decomposition_mode_reader(),
        )

    @cached_service
    def leader_plan_submitter(self) -> SubmitRepositoryPlan:
        """The write behind ``POST .../plan``.

        The assigner and the spec author are the *same* two collaborators
        ``DecomposeRepositoryTask`` uses, and that is the point of the slice: a
        leader-planned worker task is created, published, told about and
        permitted by exactly the machinery that creates a server-planned one.
        A second path would be a second set of bugs.

        Raises rather than answering None when the orchestrator is absent. The
        optional services above are optional because a *round* can exist
        without a messenger; this endpoint cannot — a plan that could not
        dispatch its worker tasks would be accepted and then do nothing, which
        is worse than refusing to answer at all.
        """

        assigner = self.task_assignment_gateway()
        if assigner is None:
            raise RuntimeError(
                "the AgentTeams messenger is not configured, so a leader plan could not "
                "dispatch the worker tasks it creates"
            )
        return SubmitRepositoryPlan(
            self.leader_assignment_store(),
            self.task_store,
            self.agent_directory,
            self.team_decomposition_mode_reader(),
            assigner,
            spec_author=ApprovedTaskSpecificationAuthor(
                self.specification_service(), self.specification_store
            ),
        )

    @cached_service
    def leader_review_submitter(self) -> SubmitRepositoryReview:
        """The write behind ``POST .../review``.

        ``on_leader_task_terminal`` is the execution plan's own advance hook,
        the same callable the Runner gateway holds. In leader mode an approved
        review is the *only* way a leader task reaches a terminal status, so
        without it an approved round would settle the task and leave its plan
        parked one batch short of finished.
        """

        assigner = self.task_assignment_gateway()
        if assigner is None or self.task_report_gateway is None:
            raise RuntimeError(
                "the AgentTeams messenger is not configured, so a leader review could not "
                "report its verdict"
            )
        advancer = self.execution_plan_advancer()
        return SubmitRepositoryReview(
            self.leader_assignment_store(),
            self.task_store,
            self.agent_directory,
            self.team_decomposition_mode_reader(),
            assigner,
            self.task_report_gateway,
            spec_author=ApprovedTaskSpecificationAuthor(
                self.specification_service(), self.specification_store
            ),
            on_leader_task_terminal=(
                advancer.on_task_terminal if advancer is not None else None
            ),
        )

    @cached_service
    def collaboration_gateway(self) -> CollaborationGateway | None:
        """The composed collaboration sender, or None without a messenger.

        Built here the way ``project_checkpoint_service`` builds its own: the
        gateway is assembled in ``bootstrap.app`` for the TaskOrchestrator and
        never handed to the container, so a container-side caller composes it
        from the same five collaborators rather than reaching for a global.
        """

        if self.agent_team_messenger is None:
            return None
        from repomesh.modules.collaboration import SendCollaborationMessage

        return SendCollaborationMessage(
            self.agent_directory,
            self.project_topology_store,
            PolicyAuthorizationGateway(),
            self.collaboration_message_store,
            self.agent_team_messenger,
        )

    @cached_service
    def room_timeline_store(self):
        from repomesh.modules.collaboration import PostgresRoomTimelineStore

        return PostgresRoomTimelineStore(self.database)

    @cached_service
    def room_timeline_reader(self):
        """The read use case the console's room stream goes through.

        Cached like the other process-level services: it holds a store and no
        request state, and a fresh one per request would buy nothing.
        """

        from repomesh.modules.collaboration import ReadRoomTimeline

        return ReadRoomTimeline(self.room_timeline_store())

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
        # Wiring the lane does not switch any team into leader mode: the reader
        # above answers per team from the persisted topology, and only a team
        # whose external Repository Leader the adoption pass adopted says
        # LEADER. Every other team -- every team at all, in an installation
        # that has provisioned no external leader -- takes the path it always
        # has.
        collaboration = self.collaboration_gateway()
        leader_lane = (
            LeaderDecisionLane(
                modes=self.team_decomposition_mode_reader(),
                assignments=self.leader_assignment_store(),
                collaboration=collaboration,
            )
            if collaboration is not None
            else None
        )
        return AdvanceExecutionPlan(
            self.execution_plan_store(),
            self.task_store,
            assigner,
            decomposer,
            on_plan_completed=completion_handler,
            delivery_state=delivery_state,
            on_batch_deliver=on_batch_deliver,
            leader_lane=leader_lane,
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
            snapshot_reader=self.plan_snapshot_store(),
            superseder=self.task_superseder(),
            task_reader=self.project_task_reader(),
            handoff_docs=self.handoff_doc_service(),
            checkpoints=self.project_checkpoint_service(),
        )

    @cached_service
    def external_member_readiness_store(self):
        """The readiness lease table, and there is exactly one of it.

        Cached for the process lifetime because the store *is* the state: a
        factory that built a new one per request would answer an empty table to
        every reader, and a per-request lock guards nothing. Being a process
        singleton is also the whole extent of its durability — the leases are
        held in memory on purpose (see ``application/readiness``), so a restart
        empties the table and the next round of renews refills it.
        """

        from repomesh.modules.agent_runtime.application.readiness import (
            ExternalMemberReadinessStore,
        )

        return ExternalMemberReadinessStore(
            ttl_seconds=get_settings().external_readiness_ttl_seconds
        )

    def external_member_readiness_gate(self):
        """The join materialize refuses on, and the console's precheck reads.

        Three dependencies, one per layer of "is this member there": the
        directory says who it is, ``external_worker_binding_control_plane`` says
        whether RepoMesh runs its body, and the lease store says whether the
        body RepoMesh does not run is running now. Only the last holds state,
        and it is the cached one above; this is rebuilt per call because it has
        none of its own.

        The control plane is passed exactly as it comes, ``None`` included. A
        deployment without one cannot tell an external member from a managed
        one, and the gate is written to refuse rather than guess — substituting
        anything here would be the composition root deciding a question the
        module already answered.
        """

        from repomesh.modules.agent_runtime.application.readiness import (
            RequireExternalMembersReady,
        )

        return RequireExternalMembersReady(
            self.agent_directory,
            self.external_worker_binding_control_plane(),
            self.external_member_readiness_store(),
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
