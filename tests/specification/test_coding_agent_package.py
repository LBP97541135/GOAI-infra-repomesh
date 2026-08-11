from uuid import uuid4

import pytest

from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.infrastructure import InMemoryAgentDirectory
from repomesh.modules.context.application import ContextPublicationGateway
from repomesh.modules.context.infrastructure import InMemoryContextStore
from repomesh.modules.identity_access import PolicyAuthorizationGateway
from repomesh.modules.project import (
    CreateProjectAgentTopology,
    CreateProjectAgentTopologyRequest,
    RepositoryTeamAssignment,
)
from repomesh.modules.project.infrastructure import InMemoryProjectTopologyStore
from repomesh.modules.specification import (
    ApproveSpecificationCommand,
    BuildCodingAgentPackage,
    BuildCodingAgentPackageCommand,
    CreateSpecificationCommand,
    InMemorySpecificationStore,
    SpecificationDenied,
    SpecificationKind,
    SpecificationService,
    SubmitSpecificationCommand,
)
from repomesh.modules.task_orchestration.domain import Task
from repomesh.modules.task_orchestration.infrastructure import InMemoryTaskStore


async def build_package_scenario():
    organization_id = uuid4()
    project_id = uuid4()
    repository_id = uuid4()
    directory = InMemoryAgentDirectory()
    organization_leader = await CreateAgent(directory).execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="package-org-leader",
        ),
        idempotency_key="package-org-leader",
    )
    team = await CreateRepositoryAgentTeam(directory).execute(
        CreateRepositoryAgentTeamRequest(
            organization_id=organization_id,
            organization_leader_id=organization_leader.principal.id,
            repository_id=repository_id,
            leader_agentteams_resource_name="package-repo-leader",
            worker_agentteams_resource_names=("package-worker-a", "package-worker-b"),
            worker_responsibility_paths=("src/pricing/**", "tests/pricing/**"),
        ),
        idempotency_key="package-team",
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
                    leader_agent_id=team.leader.id,
                    worker_agent_ids=tuple(worker.id for worker in team.workers),
                ),
            ),
        ),
        idempotency_key="package-topology",
    )
    tasks = InMemoryTaskStore()
    task = Task(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        assigned_by_agent_id=team.leader.id,
        assignee_agent_id=team.workers[0].id,
        title="Update pricing API",
        instruction="Implement the approved pricing change",
        acceptance=("Old clients remain compatible",),
    )
    await tasks.add(
        task,
        idempotency_key="package-task",
        request_fingerprint=f"sha256:{'a' * 64}",
    )
    specifications = InMemorySpecificationStore()
    authorizer = PolicyAuthorizationGateway()
    lifecycle = SpecificationService(
        directory,
        topologies,
        specifications,
        ContextPublicationGateway(InMemoryContextStore()),
        authorizer,
    )
    created = await lifecycle.create(
        CreateSpecificationCommand(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            task_id=task.id,
            kind=SpecificationKind.TASK,
            title="BE-01 Pricing API",
            created_by_agent_id=team.leader.id,
            goal="Add the new pricing field without breaking old clients",
            acceptance=("Old clients remain compatible", "Pricing tests pass"),
            constraints=("Do not remove the old field",),
            tests=("pytest tests/pricing",),
            dependencies=("pricing-contract v2.3",),
            allowed_paths=("src/pricing/**", "tests/pricing/**"),
            forbidden_paths=("src/pricing/legacy/**",),
            interface_changes=("Expose the new field as nullable",),
        ),
        idempotency_key="package-task-spec",
    )
    submitted = await lifecycle.submit(
        SubmitSpecificationCommand(created.id, team.leader.id, created.revision)
    )
    await lifecycle.approve(
        ApproveSpecificationCommand(
            submitted.id,
            team.leader.id,
            submitted.revision,
            freeze=True,
        )
    )
    builder = BuildCodingAgentPackage(
        directory,
        topologies,
        tasks,
        specifications,
        authorizer,
    )
    command = BuildCodingAgentPackageCommand(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        task_id=task.id,
        worker_agent_id=team.workers[0].id,
    )
    return builder, command, team.workers[1].id


@pytest.mark.asyncio
async def test_package_contains_only_current_task_execution_context() -> None:
    builder, command, _ = await build_package_scenario()
    package = await builder.execute(command)

    assert package.instruction == "Add the new pricing field without breaking old clients"
    assert package.allowed_paths == ("src/pricing/**", "tests/pricing/**")
    assert package.forbidden_paths == ("src/pricing/legacy/**",)
    assert package.test_commands == ("pytest tests/pricing",)
    assert len(package.context_files) == 1
    rendered = package.context_files[0]
    assert rendered.mount_path == ".repomesh/context/current-task.md"
    assert "BE-01 Pricing API" in rendered.content
    assert "src/pricing/legacy/**" in rendered.content
    assert "pricing-contract v2.3" in rendered.content
    assert "Organization Leader" not in rendered.content
    assert package.content_hash.startswith("sha256:")


@pytest.mark.asyncio
async def test_unassigned_worker_cannot_build_another_workers_package() -> None:
    builder, command, other_worker_id = await build_package_scenario()

    with pytest.raises(SpecificationDenied, match="not assigned"):
        await builder.execute(
            BuildCodingAgentPackageCommand(
                organization_id=command.organization_id,
                project_id=command.project_id,
                repository_id=command.repository_id,
                task_id=command.task_id,
                worker_agent_id=other_worker_id,
            )
        )
