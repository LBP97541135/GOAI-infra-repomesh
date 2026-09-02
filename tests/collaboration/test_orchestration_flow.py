import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from repomesh.integrations.agentteams.project_topology import ReconcileProjectAgentTopology
from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.infrastructure import InMemoryAgentDirectory
from repomesh.modules.agent_runtime.ports import TeamRuntimeRef
from repomesh.modules.collaboration import (
    CollaborationDenied,
    CollaborationMessageKind,
    InboundMatrixMessage,
    InMemoryCollaborationAuditLedger,
    InMemoryCollaborationMessageStore,
    InMemoryProcessedMatrixEventStore,
    MatrixInboundResult,
    ProcessMatrixTaskReport,
    SendCollaborationMessage,
    SendCollaborationMessageCommand,
)
from repomesh.modules.collaboration.contracts import (
    CollaborationDeliveryDeferred,
    CollaborationRouteUnavailable,
)
from repomesh.modules.identity_access import PolicyAuthorizationGateway
from repomesh.modules.project import (
    CheckpointGateDecision,
    CreateProjectAgentTopology,
    CreateProjectAgentTopologyRequest,
    ProjectCheckpoint,
    RepositoryTeamAssignment,
)
from repomesh.modules.project.infrastructure import InMemoryProjectTopologyStore
from repomesh.modules.task_orchestration import (
    AssignTaskCommand,
    InMemoryTaskStore,
    ReportTaskCommand,
    TaskBlocked,
    TaskOrchestrator,
    TaskStatus,
)
from repomesh.modules.task_orchestration.contracts import (
    DatabaseChangeKind,
    DatabaseChangeRequirement,
    PublishedTaskPackage,
)
from repomesh.modules.task_orchestration.database_automation import (
    DatabaseAutomationDisposition,
)


class ReadyControlPlane:
    async def get_worker(self, name: str):
        # Nothing to adopt: the reconcile asks where this repository's Team
        # already lives (A-8) and this controller has never seen the leader,
        # so it falls through to the canonical repository name.
        return None

    async def ensure_team(self, projection, *, idempotency_key: str):
        return TeamRuntimeRef(
            name=projection.name,
            phase="Ready",
            team_room_id=f"!team-{projection.name}:matrix.local",
            leader_room_id=f"!leader-{projection.name}:matrix.local",
            leader_name=projection.members[0].name,
            ready_workers=len(projection.members),
            total_workers=len(projection.members),
        )


class RecordingMessenger:
    def __init__(self) -> None:
        self.deliveries: list[tuple[str, dict, str]] = []

    async def send_task(
        self, room_id: str, body: str, *, transaction_id: str, **kwargs
    ) -> str:
        self.deliveries.append((room_id, json.loads(body), transaction_id))
        return f"$event-{len(self.deliveries)}"


class FailOnceMessenger(RecordingMessenger):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def send_task(
        self, room_id: str, body: str, *, transaction_id: str, **kwargs
    ) -> str:
        if not self.failed:
            self.failed = True
            raise RuntimeError("temporary Matrix outage")
        return await super().send_task(
            room_id, body, transaction_id=transaction_id, **kwargs
        )


class EmptyReceiptMessenger(RecordingMessenger):
    async def send_task(
        self, room_id: str, body: str, *, transaction_id: str, **kwargs
    ) -> str:
        self.deliveries.append((room_id, json.loads(body), transaction_id))
        return ""


class UnavailableTaskReportMessenger(RecordingMessenger):
    """Accept assignments, but defer the upward report delivery."""

    async def send_task(
        self, room_id: str, body: str, *, transaction_id: str, **kwargs
    ) -> str:
        if json.loads(body)["kind"] == CollaborationMessageKind.TASK_REPORT.value:
            raise CollaborationRouteUnavailable("manager Matrix identity is unavailable")
        return await super().send_task(
            room_id, body, transaction_id=transaction_id, **kwargs
        )


class FailedDeliveryStatusStore(InMemoryCollaborationMessageStore):
    async def update(self, message) -> None:
        raise RuntimeError("collaboration delivery status could not be persisted")


T0 = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)


class StaticIdentityVerifier:
    def __init__(self, agent_id, matrix_user_id: str) -> None:
        self.agent_id = agent_id
        self.matrix_user_id = matrix_user_id

    async def verify(self, profile, matrix_user_id: str) -> bool:
        return profile.id == self.agent_id and matrix_user_id == self.matrix_user_id


class AcceptEveryRoomReport:
    """The D-7 gate held open, so tests of the *other* checks reach them."""

    async def accepts_room_report(self, task_id) -> bool:
        return True


class RecordingTaskPublisher:
    def __init__(self) -> None:
        self.publications = []

    async def publish(self, task, **kwargs):
        self.publications.append((task, kwargs))
        return PublishedTaskPackage(
            kwargs["team_name"],
            f"teams/{kwargs['team_name']}/shared/tasks/{task.id}",
            "sha256:verified",
        )


class RecordingCheckpointGateway:
    def __init__(self) -> None:
        self.calls = []

    async def operational_gate(self, project_id) -> CheckpointGateDecision:
        return CheckpointGateDecision(True, "project_active")

    async def evaluate(
        self,
        project_id,
        checkpoint,
        evidence_version,
        *,
        repository_id=None,
        requested_by_agent_id=None,
        title=None,
        summary=None,
    ) -> CheckpointGateDecision:
        self.calls.append(
            (
                project_id,
                checkpoint,
                evidence_version,
                repository_id,
                requested_by_agent_id,
                title,
                summary,
            )
        )
        return CheckpointGateDecision(False, "human_checkpoint_pending")


async def build_flow(messenger=None, checkpoints=None, database_automation=None):
    directory = InMemoryAgentDirectory()
    create = CreateAgent(directory)
    organization_id = uuid4()
    repository_id = uuid4()
    project_id = uuid4()
    organization_leader = await create.execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="native-org-leader",
        ),
        idempotency_key="org-leader",
    )
    repository_team = await CreateRepositoryAgentTeam(directory).execute(
        CreateRepositoryAgentTeamRequest(
            organization_id=organization_id,
            organization_leader_id=organization_leader.principal.id,
            repository_id=repository_id,
            leader_agentteams_resource_name="native-repo-leader",
            worker_agentteams_resource_names=("native-worker-1", "native-worker-2"),
            worker_responsibility_paths=("src/pricing/**", "tests/pricing/**"),
        ),
        idempotency_key="repository-team",
    )
    topologies = InMemoryProjectTopologyStore()
    await CreateProjectAgentTopology(directory, topologies).execute(
        CreateProjectAgentTopologyRequest(
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=organization_leader.principal.id,
            repository_teams=(
                RepositoryTeamAssignment(
                    repository_id=repository_id,
                    leader_agent_id=repository_team.leader.id,
                    worker_agent_ids=tuple(worker.id for worker in repository_team.workers),
                ),
            ),
        ),
        idempotency_key="project-topology",
    )
    await ReconcileProjectAgentTopology(
        directory, topologies, ReadyControlPlane()  # type: ignore[arg-type]
    ).execute(project_id)
    messenger = messenger or RecordingMessenger()
    collaboration_store = InMemoryCollaborationMessageStore()
    collaboration = SendCollaborationMessage(
        directory,
        topologies,
        PolicyAuthorizationGateway(),
        collaboration_store,
        messenger,
    )
    tasks = InMemoryTaskStore()
    orchestrator = TaskOrchestrator(
        directory,
        topologies,
        tasks,
        collaboration,
        RecordingTaskPublisher(),
        checkpoints,
        database_automation=database_automation,
    )
    return (
        organization_id,
        repository_id,
        project_id,
        organization_leader.principal,
        repository_team,
        messenger,
        collaboration,
        orchestrator,
        directory,
        topologies,
    )


@pytest.mark.asyncio
async def test_successful_database_task_automatically_requests_validation() -> None:
    decisions = []

    async def capture(decision) -> None:
        decisions.append(decision)

    (
        organization_id,
        repository_id,
        project_id,
        organization_leader,
        repository_team,
        _,
        _,
        orchestrator,
        _,
        _,
    ) = await build_flow(database_automation=capture)
    leader_task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            assigned_by_agent_id=organization_leader.id,
            assignee_agent_id=repository_team.leader.id,
            title="Plan database change",
            instruction="Coordinate the database change",
            acceptance=("worker evidence reviewed",),
        ),
        idempotency_key="database-leader-task",
    )
    worker = repository_team.workers[0]
    worker_task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            assigned_by_agent_id=repository_team.leader.id,
            assignee_agent_id=worker.id,
            parent_task_id=leader_task.id,
            title="Add users migration",
            instruction="Add and validate the migration",
            acceptance=("migration_apply passes",),
            database_change=DatabaseChangeRequirement(
                declared=True,
                required=True,
                change_kinds=(DatabaseChangeKind.MIGRATION,),
                affected_tables=("users",),
                migration_required=True,
                required_checks=("migration_apply",),
            ),
        ),
        idempotency_key="database-worker-task",
    )
    await orchestrator.start(worker_task.id, agent_id=worker.id)
    with pytest.raises(TaskBlocked, match="worker_database_evidence_missing"):
        await orchestrator.report(
            ReportTaskCommand(
                task_id=worker_task.id,
                reporter_agent_id=worker.id,
                status=TaskStatus.SUCCEEDED,
                summary=json.dumps(
                    {"commitSha": "a" * 40, "changedFiles": ["migrations/0053_users.py"]}
                ),
            ),
            idempotency_key="database-worker-report-missing",
        )
    await orchestrator.report(
        ReportTaskCommand(
            task_id=worker_task.id,
            reporter_agent_id=worker.id,
            status=TaskStatus.SUCCEEDED,
            summary=json.dumps(
                {
                    "commitSha": "a" * 40,
                    "changedFiles": ["migrations/0053_users.py"],
                    "databaseChange": {
                        "migrationFiles": ["migrations/0053_users.py"],
                        "backfillFiles": [],
                        "affectedTables": ["users"],
                        "checks": [{"name": "migration_apply", "exitCode": 0}],
                    },
                }
            ),
        ),
        idempotency_key="database-worker-report",
    )

    assert len(decisions) == 1
    assert decisions[0].disposition is DatabaseAutomationDisposition.REQUEST_VALIDATION
    assert decisions[0].intent.candidate_sha == "a" * 40
@pytest.mark.asyncio
async def test_manager_leader_worker_assignment_and_report_flow() -> None:
    (
        organization_id,
        repository_id,
        project_id,
        organization_leader,
        repository_team,
        messenger,
        _,
        orchestrator,
        _,
        _,
    ) = await build_flow()
    repository_task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            assigned_by_agent_id=organization_leader.id,
            assignee_agent_id=repository_team.leader.id,
            title="Implement pricing API",
            instruction="Own the repository-level pricing change.",
            acceptance=("Old API remains compatible", "Integration tests pass"),
        ),
        idempotency_key="assign-repository-task",
    )
    worker = repository_team.workers[0]
    worker_task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            parent_task_id=repository_task.id,
            assigned_by_agent_id=repository_team.leader.id,
            assignee_agent_id=worker.id,
            title="Change pricing resolver",
            instruction="Implement the approved pricing spec.",
            acceptance=("Pricing unit tests pass",),
        ),
        idempotency_key="assign-worker-task",
    )
    await orchestrator.start(worker_task.id, agent_id=worker.id)
    await orchestrator.report(
        ReportTaskCommand(
            task_id=worker_task.id,
            reporter_agent_id=worker.id,
            status=TaskStatus.SUCCEEDED,
            summary="Resolver changed and pricing tests pass.",
        ),
        idempotency_key="report-worker-task",
    )
    await orchestrator.start(repository_task.id, agent_id=repository_team.leader.id)
    await orchestrator.report(
        ReportTaskCommand(
            task_id=repository_task.id,
            reporter_agent_id=repository_team.leader.id,
            status=TaskStatus.SUCCEEDED,
            summary="Repository change integrated and verified.",
        ),
        idempotency_key="report-repository-task",
    )

    progress = await orchestrator.progress(project_id)
    assert progress.total == 2
    assert progress.succeeded == 2
    assert len(messenger.deliveries) == 4
    assert messenger.deliveries[0][0].startswith("!leader-")
    assert messenger.deliveries[1][0].startswith("!team-")
    worker_body = messenger.deliveries[1][1]["body"]
    assert "repomesh-task-control.start_assigned_task" in worker_body
    assert "Do not edit code directly" in worker_body
    assert "sha256:verified" in worker_body
    assert messenger.deliveries[2][0].startswith("!team-")
    assert messenger.deliveries[3][0].startswith("!leader-")
    assert all(
        delivery[1]["schema"] == "repomesh.collaboration.v1"
        for delivery in messenger.deliveries
    )


@pytest.mark.asyncio
async def test_repository_leader_exception_requests_human_review_without_worker_bypass() -> None:
    checkpoints = RecordingCheckpointGateway()
    (
        organization_id,
        repository_id,
        project_id,
        organization_leader,
        repository_team,
        _,
        _,
        orchestrator,
        _,
        _,
    ) = await build_flow(checkpoints=checkpoints)
    repository_task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            assigned_by_agent_id=organization_leader.id,
            assignee_agent_id=repository_team.leader.id,
            title="Integrate pricing change",
            instruction="Coordinate the repository change.",
            acceptance=("Integration succeeds",),
        ),
        idempotency_key="assign-exception-repository-task",
    )
    worker = repository_team.workers[0]
    worker_task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            parent_task_id=repository_task.id,
            assigned_by_agent_id=repository_team.leader.id,
            assignee_agent_id=worker.id,
            title="Change pricing code",
            instruction="Implement the pricing change.",
            acceptance=("Unit tests pass",),
        ),
        idempotency_key="assign-exception-worker-task",
    )
    await orchestrator.report(
        ReportTaskCommand(
            task_id=worker_task.id,
            reporter_agent_id=worker.id,
            status=TaskStatus.BLOCKED,
            summary="API contract is ambiguous.",
        ),
        idempotency_key="worker-blocked",
    )
    assert checkpoints.calls == []

    reported = await orchestrator.report(
        ReportTaskCommand(
            task_id=repository_task.id,
            reporter_agent_id=repository_team.leader.id,
            status=TaskStatus.BLOCKED,
            summary="The contract conflict requires a project decision.",
        ),
        idempotency_key="repository-leader-blocked",
    )
    assert checkpoints.calls == [
        (
            project_id,
            ProjectCheckpoint.EXCEPTION_ESCALATION,
            f"task:{repository_task.id}:v{reported.version}",
            repository_id,
            repository_team.leader.id,
            "Integrate pricing change：仓库异常升级",
            "The contract conflict requires a project decision.",
        )
    ]


@pytest.mark.asyncio
async def test_worker_cannot_message_another_worker() -> None:
    (
        organization_id,
        repository_id,
        project_id,
        _,
        repository_team,
        _,
        collaboration,
        _,
        _,
        _,
    ) = await build_flow()
    with pytest.raises(CollaborationDenied, match="agent_reachability_denied"):
        await collaboration.send(
            SendCollaborationMessageCommand(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repository_id,
                sender_agent_id=repository_team.workers[0].id,
                recipient_agent_id=repository_team.workers[1].id,
                kind=CollaborationMessageKind.QUESTION,
                subject="Bypass leader",
                body="Can we coordinate directly?",
            ),
            idempotency_key="worker-peer-message",
        )


@pytest.mark.asyncio
async def test_empty_matrix_receipt_is_persisted_as_failed_delivery() -> None:
    messenger = EmptyReceiptMessenger()
    (
        organization_id,
        repository_id,
        project_id,
        organization_leader,
        repository_team,
        _,
        collaboration,
        _,
        _,
        _,
    ) = await build_flow(messenger=messenger)
    with pytest.raises(ValueError, match="event_id"):
        await collaboration.send(
            SendCollaborationMessageCommand(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repository_id,
                sender_agent_id=organization_leader.id,
                recipient_agent_id=repository_team.leader.id,
                kind=CollaborationMessageKind.TASK_ASSIGNMENT,
                subject="Assign repository work",
                body="Implement the approved repository specification.",
            ),
            idempotency_key="empty-matrix-receipt",
        )

    failed = await collaboration._store.list_failed()  # noqa: SLF001
    assert len(failed) == 1
    assert failed[0][1] == "empty-matrix-receipt"


@pytest.mark.asyncio
async def test_replayed_assignment_does_not_send_twice() -> None:
    (
        organization_id,
        repository_id,
        project_id,
        organization_leader,
        repository_team,
        messenger,
        _,
        orchestrator,
        _,
        _,
    ) = await build_flow()
    command = AssignTaskCommand(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        assigned_by_agent_id=organization_leader.id,
        assignee_agent_id=repository_team.leader.id,
        title="Stable assignment",
        instruction="Execute once.",
        acceptance=("One message is delivered",),
    )
    first = await orchestrator.assign(command, idempotency_key="stable-assignment")
    replay = await orchestrator.assign(command, idempotency_key="stable-assignment")
    assert replay == first
    assert len(messenger.deliveries) == 1


@pytest.mark.asyncio
async def test_assignment_retry_recovers_failed_matrix_delivery() -> None:
    messenger = FailOnceMessenger()
    (
        organization_id,
        repository_id,
        project_id,
        organization_leader,
        repository_team,
        _,
        _,
        orchestrator,
        _,
        _,
    ) = await build_flow(messenger)
    command = AssignTaskCommand(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        assigned_by_agent_id=organization_leader.id,
        assignee_agent_id=repository_team.leader.id,
        title="Recoverable assignment",
        instruction="Deliver after transport recovery.",
        acceptance=("Exactly one Matrix event exists",),
    )
    with pytest.raises(RuntimeError, match="Matrix outage"):
        await orchestrator.assign(command, idempotency_key="recoverable-assignment")
    recovered = await orchestrator.assign(
        command, idempotency_key="recoverable-assignment"
    )
    assert recovered.title == "Recoverable assignment"
    assert len(messenger.deliveries) == 1


@pytest.mark.asyncio
async def test_report_accepts_the_fact_when_delivery_is_deferred() -> None:
    """D-M7-1: persisted success and the caller's answer cannot disagree.

    Collaboration has already stored the failed message for its retry worker.
    Once the Task terminal fact is committed, a temporarily unavailable
    Matrix recipient must not turn that accepted report into an exception.
    """

    messenger = UnavailableTaskReportMessenger()
    (
        organization_id,
        repository_id,
        project_id,
        organization_leader,
        repository_team,
        _,
        collaboration,
        orchestrator,
        _,
        _,
    ) = await build_flow(messenger)
    task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            assigned_by_agent_id=organization_leader.id,
            assignee_agent_id=repository_team.leader.id,
            title="Review repository evidence",
            instruction="Review the completed worker round.",
            acceptance=("Report the evidence summary",),
        ),
        idempotency_key="d-m7-1-assignment",
    )
    await orchestrator.start(task.id, agent_id=repository_team.leader.id)

    reported = await orchestrator.report(
        ReportTaskCommand(
            task_id=task.id,
            reporter_agent_id=repository_team.leader.id,
            status=TaskStatus.SUCCEEDED,
            summary="All worker evidence passed review.",
        ),
        idempotency_key="d-m7-1-report",
    )

    assert reported.status is TaskStatus.SUCCEEDED
    failed = await collaboration._store.list_failed()  # noqa: SLF001
    assert len(failed) == 1
    assert failed[0][0].task_id == task.id
    assert failed[0][1] == "d-m7-1-report:message"


@pytest.mark.asyncio
async def test_delivery_is_not_deferred_when_failed_status_cannot_be_persisted() -> None:
    messenger = UnavailableTaskReportMessenger()
    (
        organization_id,
        repository_id,
        project_id,
        organization_leader,
        repository_team,
        _,
        _,
        _,
        directory,
        topologies,
    ) = await build_flow(messenger)
    collaboration = SendCollaborationMessage(
        directory,
        topologies,
        PolicyAuthorizationGateway(),
        FailedDeliveryStatusStore(),
        messenger,
    )

    with pytest.raises(
        RuntimeError,
        match="collaboration delivery status could not be persisted",
    ) as raised:
        await collaboration.send(
            SendCollaborationMessageCommand(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repository_id,
                task_id=uuid4(),
                sender_agent_id=repository_team.leader.id,
                recipient_agent_id=organization_leader.id,
                kind=CollaborationMessageKind.TASK_REPORT,
                subject="Repository review approved",
                body="All worker evidence passed review.",
            ),
            idempotency_key="d-m7-1-failed-status-write",
        )

    assert not isinstance(raised.value, CollaborationDeliveryDeferred)


@pytest.mark.asyncio
async def test_matrix_task_report_is_authenticated_processed_and_deduplicated() -> None:
    """The room-report path that adjudication D-7 leaves open: a leader task.

    Reported through the leader DM by the repository leader, which is not a
    task carrying a published package, so the ruling that closes the coding
    path does not touch it. Everything this test pinned before D-7 —
    authentication against the sender's Matrix identity, replay returning
    DUPLICATE, a spoofed sender being refused — is pinned here unchanged; only
    the task it happens on moved. The closed half is
    ``test_room_report_narrowing.py``.
    """

    (
        organization_id,
        repository_id,
        project_id,
        organization_leader,
        repository_team,
        _,
        _,
        orchestrator,
        directory,
        topologies,
    ) = await build_flow()
    leader = repository_team.leader
    repository_task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            assigned_by_agent_id=organization_leader.id,
            assignee_agent_id=leader.id,
            title="Repository task",
            instruction="Coordinate the pricing change.",
            acceptance=("Worker result is collected",),
        ),
        idempotency_key="inbound-repository-task",
    )
    topology = await topologies.get_view(project_id)
    assert topology is not None
    room_id = topology.repository_teams[0].leader_room_id
    assert room_id is not None
    processor = ProcessMatrixTaskReport(
        directory,
        topologies,
        StaticIdentityVerifier(leader.id, "@leader:matrix.local"),
        InMemoryProcessedMatrixEventStore(),
        orchestrator,
        AcceptEveryRoomReport(),
        InMemoryCollaborationAuditLedger(),
    )
    message = InboundMatrixMessage(
        event_id="$leader-report",
        room_id=room_id,
        sender="@leader:matrix.local",
        body=json.dumps(
            {
                "schema": "repomesh.agent-report.v1",
                "sender_agent_id": str(leader.id),
                "project_id": str(project_id),
                "task_id": str(repository_task.id),
                "status": "succeeded",
                "summary": "Pricing implementation and tests completed.",
            }
        ),
        occurred_at=T0,
    )
    assert await processor.execute(message) is MatrixInboundResult.PROCESSED
    assert await processor.execute(message) is MatrixInboundResult.DUPLICATE
    progress = await orchestrator.progress(project_id)
    assert progress.succeeded == 1

    spoofed = InboundMatrixMessage(
        event_id="$spoofed-report",
        room_id=room_id,
        sender="@attacker:matrix.local",
        body=message.body,
        occurred_at=T0,
    )
    with pytest.raises(CollaborationDenied, match="does not match"):
        await processor.execute(spoofed)
