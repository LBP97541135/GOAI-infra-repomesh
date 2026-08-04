import json
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
    InMemoryCollaborationMessageStore,
    InMemoryProcessedMatrixEventStore,
    MatrixInboundResult,
    ProcessMatrixTaskReport,
    SendCollaborationMessage,
    SendCollaborationMessageCommand,
)
from repomesh.modules.identity_access import PolicyAuthorizationGateway
from repomesh.modules.project import (
    CreateProjectAgentTopology,
    CreateProjectAgentTopologyRequest,
    RepositoryTeamAssignment,
)
from repomesh.modules.project.infrastructure import InMemoryProjectTopologyStore
from repomesh.modules.task_orchestration import (
    AssignTaskCommand,
    InMemoryTaskStore,
    ReportTaskCommand,
    TaskOrchestrator,
    TaskStatus,
)


class ReadyControlPlane:
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

    async def send_task(self, room_id: str, body: str, *, transaction_id: str) -> str:
        self.deliveries.append((room_id, json.loads(body), transaction_id))
        return f"$event-{len(self.deliveries)}"


class FailOnceMessenger(RecordingMessenger):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def send_task(self, room_id: str, body: str, *, transaction_id: str) -> str:
        if not self.failed:
            self.failed = True
            raise RuntimeError("temporary Matrix outage")
        return await super().send_task(room_id, body, transaction_id=transaction_id)


class StaticIdentityVerifier:
    def __init__(self, agent_id, matrix_user_id: str) -> None:
        self.agent_id = agent_id
        self.matrix_user_id = matrix_user_id

    async def verify(self, profile, matrix_user_id: str) -> bool:
        return profile.id == self.agent_id and matrix_user_id == self.matrix_user_id


async def build_flow(messenger=None):
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
    orchestrator = TaskOrchestrator(directory, topologies, tasks, collaboration)
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
    assert messenger.deliveries[2][0].startswith("!team-")
    assert messenger.deliveries[3][0].startswith("!leader-")
    assert all(
        delivery[1]["schema"] == "repomesh.collaboration.v1"
        for delivery in messenger.deliveries
    )


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
async def test_matrix_task_report_is_authenticated_processed_and_deduplicated() -> None:
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
    repository_task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            assigned_by_agent_id=organization_leader.id,
            assignee_agent_id=repository_team.leader.id,
            title="Repository task",
            instruction="Coordinate the pricing change.",
            acceptance=("Worker result is collected",),
        ),
        idempotency_key="inbound-repository-task",
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
            title="Worker task",
            instruction="Implement pricing.",
            acceptance=("Tests pass",),
        ),
        idempotency_key="inbound-worker-task",
    )
    topology = await topologies.get_view(project_id)
    assert topology is not None
    room_id = topology.repository_teams[0].room_id
    assert room_id is not None
    processor = ProcessMatrixTaskReport(
        directory,
        topologies,
        StaticIdentityVerifier(worker.id, "@worker:matrix.local"),
        InMemoryProcessedMatrixEventStore(),
        orchestrator,
    )
    message = InboundMatrixMessage(
        event_id="$worker-report",
        room_id=room_id,
        sender="@worker:matrix.local",
        body=json.dumps(
            {
                "schema": "repomesh.agent-report.v1",
                "sender_agent_id": str(worker.id),
                "project_id": str(project_id),
                "task_id": str(worker_task.id),
                "status": "succeeded",
                "summary": "Pricing implementation and tests completed.",
            }
        ),
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
    )
    with pytest.raises(CollaborationDenied, match="does not match"):
        await processor.execute(spoofed)
