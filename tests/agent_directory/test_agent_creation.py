from dataclasses import replace
from uuid import uuid4

import pytest

from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import (
    AgentRole,
)
from repomesh.modules.agent_directory.domain import (
    AgentAlreadyExists,
    AgentHierarchyViolation,
)
from repomesh.modules.agent_directory.infrastructure import InMemoryAgentDirectory
from repomesh.modules.context.application import agent_permission_layer
from repomesh.modules.context.contracts import ContextAction, ContextScope
from repomesh.modules.context.domain import PermissionRequest, evaluate_permission


async def create_hierarchy():
    directory = InMemoryAgentDirectory()
    create = CreateAgent(directory)
    organization_id = uuid4()
    repository_id = uuid4()
    organization_leader = await create.execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="native-manager-main",
        ),
        idempotency_key="organization-leader-v1",
    )
    repository_leader = await create.execute(
        CreateAgentRequest(
            organization_id=organization_id,
            repository_id=repository_id,
            leader_agent_id=organization_leader.principal.id,
            responsibility_paths=("src/**", "tests/**"),
            role=AgentRole.REPOSITORY_LEADER,
            agentteams_resource_name="native-worker-repository-leader",
        ),
        idempotency_key="repository-leader-v1",
    )
    return directory, create, organization_id, repository_id, repository_leader


@pytest.mark.asyncio
async def test_repository_team_registers_existing_agentteams_resources() -> None:
    directory = InMemoryAgentDirectory()
    create = CreateAgent(directory)
    organization_id = uuid4()
    repository_id = uuid4()
    organization_leader = await create.execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="native-manager-main",
        ),
        idempotency_key="org-leader",
    )
    request = CreateRepositoryAgentTeamRequest(
        organization_id=organization_id,
        organization_leader_id=organization_leader.principal.id,
        repository_id=repository_id,
        leader_agentteams_resource_name="native-saleor-leader",
        worker_agentteams_resource_names=(
            "native-saleor-worker-01",
            "native-saleor-worker-02",
            "native-saleor-worker-03",
        ),
    )

    first = await CreateRepositoryAgentTeam(directory).execute(
        request, idempotency_key=f"repository-team:{repository_id}:v1"
    )
    replayed = await CreateRepositoryAgentTeam(directory).execute(
        request, idempotency_key=f"repository-team:{repository_id}:v1"
    )

    assert first.leader.agentteams_resource_name == "native-saleor-leader"
    assert len(first.workers) == 3
    assert replayed == first
    assert len(directory.events) == 5


def test_repository_team_requires_unique_native_resource_names() -> None:
    with pytest.raises(ValueError, match="must be unique"):
        CreateRepositoryAgentTeamRequest(
            organization_id=uuid4(),
            organization_leader_id=uuid4(),
            repository_id=uuid4(),
            leader_agentteams_resource_name="native-duplicate",
            worker_agentteams_resource_names=("native-duplicate",),
        )


@pytest.mark.asyncio
async def test_worker_role_ceiling_and_context_visibility() -> None:
    directory, create, organization_id, repository_id, leader = await create_hierarchy()
    worker = await create.execute(
        CreateAgentRequest(
            organization_id=organization_id,
            repository_id=repository_id,
            leader_agent_id=leader.principal.id,
            responsibility_paths=("src/pricing/**",),
            role=AgentRole.WORKER,
            agentteams_resource_name="native-pricing-worker",
        ),
        idempotency_key="pricing-worker-v1",
    )

    layer = agent_permission_layer(worker.principal)
    allowed = PermissionRequest(
        action=ContextAction.READ,
        scope=ContextScope.TASK_PRIVATE,
        context_object_id=uuid4(),
        repository_id=repository_id,
    )
    assert evaluate_permission(allowed, layers=(layer,)).allowed
    assert directory.events[-1].event_type == "AgentPrincipalRegistered"


@pytest.mark.asyncio
async def test_worker_must_match_repository_leader_scope() -> None:
    _, create, organization_id, _, leader = await create_hierarchy()
    with pytest.raises(AgentHierarchyViolation, match="repository must match"):
        await create.execute(
            CreateAgentRequest(
                organization_id=organization_id,
                repository_id=uuid4(),
                leader_agent_id=leader.principal.id,
                responsibility_paths=("src/**",),
                role=AgentRole.WORKER,
                agentteams_resource_name="native-wrong-repository",
            ),
            idempotency_key="wrong-repository-worker",
        )


@pytest.mark.asyncio
async def test_only_one_leader_and_one_native_binding_are_allowed() -> None:
    directory, create, organization_id, repository_id, repository_leader = (
        await create_hierarchy()
    )
    with pytest.raises(AgentAlreadyExists, match="leader already exists"):
        await create.execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="native-second-manager",
            ),
            idempotency_key="second-organization-leader",
        )
    with pytest.raises(AgentAlreadyExists, match="leader already exists"):
        await create.execute(
            CreateAgentRequest(
                organization_id=organization_id,
                repository_id=repository_id,
                leader_agent_id=repository_leader.principal.leader_agent_id,
                responsibility_paths=("**",),
                role=AgentRole.REPOSITORY_LEADER,
                agentteams_resource_name="native-second-repository-leader",
            ),
            idempotency_key="second-repository-leader",
        )
    with pytest.raises(AgentAlreadyExists, match="principal already exists"):
        await create.execute(
            CreateAgentRequest(
                organization_id=uuid4(),
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="native-manager-main",
            ),
            idempotency_key="reused-native-binding",
        )


@pytest.mark.asyncio
async def test_registration_is_idempotent_and_rejects_key_reuse() -> None:
    _, create, organization_id, repository_id, leader = await create_hierarchy()
    request = CreateAgentRequest(
        organization_id=organization_id,
        repository_id=repository_id,
        leader_agent_id=leader.principal.id,
        responsibility_paths=("src/**",),
        role=AgentRole.WORKER,
        agentteams_resource_name="native-idempotent-worker",
    )
    first = await create.execute(request, idempotency_key="worker-idempotent")
    second = await create.execute(request, idempotency_key="worker-idempotent")
    assert second.principal.id == first.principal.id

    with pytest.raises(AgentAlreadyExists, match="different agent request"):
        await create.execute(
            replace(request, agentteams_resource_name="native-different-worker"),
            idempotency_key="worker-idempotent",
        )
