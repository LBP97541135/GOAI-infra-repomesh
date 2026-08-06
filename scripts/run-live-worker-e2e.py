import asyncio
import hashlib
import json
import os
from dataclasses import asdict
from uuid import uuid4

from repomesh.bootstrap.app import build_default_container
from repomesh.integrations.agentteams.task_publishing import AgentTeamsTaskPublisher
from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import AgentCreated, AgentRole
from repomesh.modules.collaboration import SendCollaborationMessage
from repomesh.modules.identity_access import PolicyAuthorizationGateway
from repomesh.modules.project.contracts import ProjectTeamRuntimeStatus
from repomesh.modules.project.domain import ProjectAgentTopology, RepositoryTeam
from repomesh.modules.repository_intelligence.application import RegisterRepository
from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile
from repomesh.modules.specification import (
    ApproveSpecificationCommand,
    CreateSpecificationCommand,
    PublishSpecificationContextCommand,
    SpecificationKind,
    SubmitSpecificationCommand,
)
from repomesh.modules.task_orchestration import AssignTaskCommand, TaskOrchestrator
from repomesh.modules.task_orchestration.domain import Task
from repomesh.settings import get_settings


def fingerprint(command: AssignTaskCommand) -> str:
    payload = json.dumps(asdict(command), sort_keys=True, default=str, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


async def main() -> None:
    run_key = os.environ["LIVE_E2E_RUN_KEY"]
    identity_key = os.environ.get("LIVE_E2E_IDENTITY_KEY", run_key)
    project_key = os.environ.get("LIVE_E2E_PROJECT_KEY", run_key)
    repository_url = os.environ["LIVE_E2E_REPOSITORY_URL"]
    base_revision = os.environ["LIVE_E2E_BASE_REVISION"]
    team_name = os.environ["LIVE_E2E_TEAM_NAME"]
    team_room_id = os.environ["LIVE_E2E_TEAM_ROOM_ID"]
    leader_room_id = os.environ["LIVE_E2E_LEADER_ROOM_ID"]
    container = build_default_container()
    if container.agent_team_messenger is None:
        raise RuntimeError("Matrix messenger is not configured")
    try:
        organization_id = uuid4()
        project_id = uuid4()
        repository = next(
            (
                item
                for item in await container.repository_catalog.list()
                if item.url == repository_url
            ),
            None,
        ) or RepositoryProfile(
            name=f"pricing-live-e2e-{run_key}",
            url=repository_url,
            description="Checkout pricing fixture for governed Worker execution",
            topics=("pricing", "checkout"),
            languages=("Python",),
            auto_card=AutoCard(
                top_dirs=("src/checkout_fixture", "tests"),
                exposed_apis=("calculate_total",),
            ),
        )
        if not any(
            item.url == repository_url for item in await container.repository_catalog.list()
        ):
            await RegisterRepository(container.repository_catalog).execute(repository)
        create_agent = CreateAgent(container.agent_directory)
        leader_key = f"{identity_key}:organization-leader"
        existing_leader = await container.agent_directory.get_by_idempotency_key(leader_key)
        if existing_leader:
            organization_id = existing_leader[0].organization_id
            organization_leader = AgentCreated(existing_leader[0].to_view(), leader_key)
        else:
            organization_leader = await create_agent.execute(
                CreateAgentRequest(
                    organization_id=organization_id,
                    role=AgentRole.ORGANIZATION_LEADER,
                    agentteams_resource_name="default",
                ),
                idempotency_key=leader_key,
            )
        team = await CreateRepositoryAgentTeam(container.agent_directory).execute(
            CreateRepositoryAgentTeamRequest(
                organization_id=organization_id,
                organization_leader_id=organization_leader.principal.id,
                repository_id=repository.id,
                leader_agentteams_resource_name="repomesh-pricing-leader",
                worker_agentteams_resource_names=("repomesh-pricing-worker-01",),
                worker_responsibility_paths=(
                    "src/checkout_fixture/pricing.py",
                    "tests/**",
                    "scripts/run_tests.py",
                ),
            ),
            idempotency_key=f"{identity_key}:repository-team",
        )
        topology_key = f"{project_key}:topology"
        existing_topology = await container.project_topology_store.get_by_idempotency_key(
            topology_key
        )
        if existing_topology:
            topology = existing_topology[0]
            project_id = topology.project_id
        else:
            topology = ProjectAgentTopology(
                organization_id=organization_id,
                project_id=project_id,
                organization_leader_id=organization_leader.principal.id,
                repository_teams=(
                    RepositoryTeam(
                        project_id=project_id,
                        repository_id=repository.id,
                        leader_agent_id=team.leader.id,
                        worker_agent_ids=(team.workers[0].id,),
                        agentteams_team_name=team_name,
                        runtime_status=ProjectTeamRuntimeStatus.READY,
                        room_id=team_room_id,
                        leader_room_id=leader_room_id,
                    ),
                ),
            )
            await container.project_topology_store.add(
                topology,
                idempotency_key=topology_key,
                request_fingerprint=f"sha256:{'0' * 64}",
            )
        collaboration = SendCollaborationMessage(
            container.agent_directory,
            container.project_topology_store,
            PolicyAuthorizationGateway(),
            container.collaboration_message_store,
            container.agent_team_messenger,
        )
        orchestrator = TaskOrchestrator(
            container.agent_directory,
            container.project_topology_store,
            container.task_store,
            collaboration,
            AgentTeamsTaskPublisher(get_settings().agentteams_storage_root),
        )
        parent_key = f"{run_key}:parent-task"
        existing_parent = await container.task_store.get_by_idempotency_key(parent_key)
        parent = existing_parent[0] if existing_parent else Task(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository.id,
            assigned_by_agent_id=organization_leader.principal.id,
            assignee_agent_id=team.leader.id,
            title="Coordinate checkout pricing correction",
            instruction="Own the approved repository change and review Runner evidence.",
            acceptance=("Worker task succeeds", "Repository tests pass"),
        )
        if not existing_parent:
            await container.task_store.add(
                parent,
                idempotency_key=parent_key,
                request_fingerprint=f"sha256:{'1' * 64}",
            )
        command = AssignTaskCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository.id,
            parent_task_id=parent.id,
            assigned_by_agent_id=team.leader.id,
            assignee_agent_id=team.workers[0].id,
            title="Fix shipping discount and tax bases",
            instruction=(
                "Fix calculate_total so discount and tax apply only to merchandise subtotal. "
                "Shipping must be added unchanged after tax."
            ),
            acceptance=(
                "Only src/checkout_fixture/pricing.py changes",
                "python scripts/run_tests.py passes",
            ),
        )
        assignment_key = f"{run_key}:worker-task"
        existing_worker_task = await container.task_store.get_by_idempotency_key(assignment_key)
        worker_task = existing_worker_task[0] if existing_worker_task else Task(
            organization_id=command.organization_id,
            project_id=command.project_id,
            repository_id=command.repository_id,
            parent_task_id=command.parent_task_id,
            assigned_by_agent_id=command.assigned_by_agent_id,
            assignee_agent_id=command.assignee_agent_id,
            title=command.title,
            instruction=command.instruction,
            acceptance=command.acceptance,
        )
        if not existing_worker_task:
            await container.task_store.add(
                worker_task,
                idempotency_key=assignment_key,
                request_fingerprint=fingerprint(command),
            )
        specs = container.specification_service()
        created = await specs.create(
            CreateSpecificationCommand(
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repository.id,
                task_id=worker_task.id,
                kind=SpecificationKind.TASK,
                title="Checkout pricing implementation spec",
                created_by_agent_id=team.leader.id,
                goal=command.instruction,
                acceptance=command.acceptance,
                scope=("calculate_total",),
                constraints=("Do not change tests", "Do not push from the coding agent"),
                tests=("python scripts/run_tests.py",),
                allowed_paths=("src/checkout_fixture/pricing.py",),
            ),
            idempotency_key=f"{run_key}:task-spec",
        )
        current = created
        if current.status.value == "draft":
            current = await specs.submit(
                SubmitSpecificationCommand(current.id, team.leader.id, current.revision)
            )
        if current.status.value == "in_review":
            current = await specs.approve(
                ApproveSpecificationCommand(
                    current.id,
                    team.leader.id,
                    current.revision,
                    freeze=True,
                )
            )
        approved = current
        await specs.publish_to_context(
            PublishSpecificationContextCommand(approved.id, team.leader.id)
        )
        assigned = await orchestrator.assign(command, idempotency_key=assignment_key)
        print(
            json.dumps(
                {
                    "run_key": run_key,
                    "organization_id": str(organization_id),
                    "project_id": str(project_id),
                    "repository_id": str(repository.id),
                    "parent_task_id": str(parent.id),
                    "task_id": str(assigned.id),
                    "worker_agent_id": str(team.workers[0].id),
                    "specification_id": str(approved.id),
                    "base_revision": base_revision,
                },
                indent=2,
            )
        )
    finally:
        await container.close()


if __name__ == "__main__":
    asyncio.run(main())
