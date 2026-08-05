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
from repomesh.modules.context.application import (
    agent_permission_layer,
    project_membership_permission_layer,
)
from repomesh.modules.context.contracts import ContextAction, ContextObjectType, ContextScope
from repomesh.modules.context.domain import PermissionRequest, evaluate_permission
from repomesh.modules.identity_access import (
    AuthorizationAction,
    AuthorizationRequest,
    authorize_agent,
)
from repomesh.modules.project import (
    CreateProjectAgentTopology,
    CreateProjectAgentTopologyRequest,
    RepositoryTeamAssignment,
)
from repomesh.modules.project.infrastructure import InMemoryProjectTopologyStore


async def authorization_fixture():
    directory = InMemoryAgentDirectory()
    create = CreateAgent(directory)
    organization_id = uuid4()
    repository_id = uuid4()
    project_id = uuid4()
    organization_leader = await create.execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="native-auth-manager",
        ),
        idempotency_key="auth-organization-leader",
    )
    repository_team = await CreateRepositoryAgentTeam(directory).execute(
        CreateRepositoryAgentTeamRequest(
            organization_id=organization_id,
            organization_leader_id=organization_leader.principal.id,
            repository_id=repository_id,
            leader_agentteams_resource_name="native-auth-repository-leader",
            worker_agentteams_resource_names=(
                "native-auth-worker-01",
                "native-auth-worker-02",
            ),
            worker_responsibility_paths=("src/pricing/**", "tests/pricing/**"),
        ),
        idempotency_key="auth-repository-team",
    )
    topology = await CreateProjectAgentTopology(
        directory, InMemoryProjectTopologyStore()
    ).execute(
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
        idempotency_key="auth-project-topology",
    )
    return (
        organization_id,
        project_id,
        repository_id,
        organization_leader.principal,
        repository_team.leader,
        repository_team.workers,
        topology,
    )


def request(action: AuthorizationAction, organization_id, project_id, **changes):
    return AuthorizationRequest(
        action=action,
        organization_id=organization_id,
        project_id=project_id,
        **changes,
    )


@pytest.mark.asyncio
async def test_organization_leader_manages_project_but_has_no_code_access() -> None:
    organization_id, project_id, repository_id, leader, _, _, topology = (
        await authorization_fixture()
    )
    manage = request(AuthorizationAction.PROJECT_MANAGE, organization_id, project_id)
    code_read = request(
        AuthorizationAction.REPOSITORY_READ,
        organization_id,
        project_id,
        repository_id=repository_id,
        path="src/pricing/service.py",
    )

    assert authorize_agent(leader, manage, topology=topology).allowed
    decision = authorize_agent(leader, code_read, topology=topology)
    assert not decision.allowed
    assert decision.reason == "role_action_denied"


@pytest.mark.asyncio
async def test_repository_leader_reads_repo_and_publishes_spec_but_cannot_write_code() -> None:
    organization_id, project_id, repository_id, _, leader, _, topology = (
        await authorization_fixture()
    )
    repository_read = request(
        AuthorizationAction.REPOSITORY_READ,
        organization_id,
        project_id,
        repository_id=repository_id,
        path="src/pricing/service.py",
    )
    publish_spec = request(
        AuthorizationAction.CONTEXT_PUBLISH,
        organization_id,
        project_id,
        repository_id=repository_id,
        context_scope=ContextScope.TEAM_PRIVATE,
        context_object_type=ContextObjectType.ENGINEERING_SPEC,
    )

    assert authorize_agent(leader, repository_read, topology=topology).allowed
    assert authorize_agent(leader, publish_spec, topology=topology).allowed
    assert not authorize_agent(
        leader,
        request(
            AuthorizationAction.REPOSITORY_WRITE,
            organization_id,
            project_id,
            repository_id=repository_id,
            path="src/pricing/service.py",
        ),
        topology=topology,
    ).allowed
    assert not authorize_agent(
        leader,
        request(AuthorizationAction.MERGE, organization_id, project_id),
        topology=topology,
    ).allowed


@pytest.mark.asyncio
async def test_worker_writes_only_delegated_paths_and_publishes_execution_results() -> None:
    organization_id, project_id, repository_id, _, _, workers, topology = (
        await authorization_fixture()
    )
    worker = workers[0]
    allowed_write = request(
        AuthorizationAction.REPOSITORY_WRITE,
        organization_id,
        project_id,
        repository_id=repository_id,
        path="src/pricing/service.py",
    )
    denied_write = request(
        AuthorizationAction.REPOSITORY_WRITE,
        organization_id,
        project_id,
        repository_id=repository_id,
        path="src/orders/service.py",
    )
    publish_result = request(
        AuthorizationAction.CONTEXT_PUBLISH,
        organization_id,
        project_id,
        repository_id=repository_id,
        context_scope=ContextScope.TASK_PRIVATE,
        context_object_type=ContextObjectType.TASK_RESULT,
    )
    publish_spec = request(
        AuthorizationAction.CONTEXT_PUBLISH,
        organization_id,
        project_id,
        repository_id=repository_id,
        context_scope=ContextScope.PROJECT_SHARED,
        context_object_type=ContextObjectType.ENGINEERING_SPEC,
    )

    assert authorize_agent(worker, allowed_write, topology=topology).allowed
    assert not authorize_agent(worker, denied_write, topology=topology).allowed
    assert authorize_agent(worker, publish_result, topology=topology).allowed
    assert not authorize_agent(worker, publish_spec, topology=topology).allowed


@pytest.mark.asyncio
async def test_communication_reachability_follows_manager_leader_worker_chain() -> None:
    organization_id, project_id, _, organization_leader, repository_leader, workers, topology = (
        await authorization_fixture()
    )
    worker = workers[0]
    peer = workers[1]

    assert authorize_agent(
        organization_leader,
        request(
            AuthorizationAction.COLLABORATION_MESSAGE,
            organization_id,
            project_id,
            target_agent_id=repository_leader.id,
        ),
        topology=topology,
    ).allowed
    assert not authorize_agent(
        organization_leader,
        request(
            AuthorizationAction.COLLABORATION_MESSAGE,
            organization_id,
            project_id,
            target_agent_id=worker.id,
        ),
        topology=topology,
    ).allowed
    assert authorize_agent(
        worker,
        request(
            AuthorizationAction.COLLABORATION_MESSAGE,
            organization_id,
            project_id,
            target_agent_id=repository_leader.id,
        ),
        topology=topology,
    ).allowed
    assert not authorize_agent(
        worker,
        request(
            AuthorizationAction.COLLABORATION_MESSAGE,
            organization_id,
            project_id,
            target_agent_id=peer.id,
        ),
        topology=topology,
    ).allowed


@pytest.mark.asyncio
async def test_worker_project_shared_visibility_is_limited_to_selected_objects() -> None:
    organization_id, project_id, repository_id, _, _, workers, topology = (
        await authorization_fixture()
    )
    worker = workers[0]
    selected_object_id = uuid4()
    other_object_id = uuid4()
    layers = (
        agent_permission_layer(worker),
        project_membership_permission_layer(
            worker,
            topology,
            context_object_ids=frozenset({selected_object_id}),
        ),
    )
    selected = PermissionRequest(
        action=ContextAction.READ,
        scope=ContextScope.PROJECT_SHARED,
        context_object_id=selected_object_id,
        repository_id=repository_id,
        tool="context.read",
    )
    unselected = PermissionRequest(
        action=ContextAction.READ,
        scope=ContextScope.PROJECT_SHARED,
        context_object_id=other_object_id,
        repository_id=repository_id,
        tool="context.read",
    )

    assert evaluate_permission(selected, layers=layers).allowed
    decision = evaluate_permission(unselected, layers=layers)
    assert not decision.allowed
    assert decision.reason == "project_membership:context_object"


@pytest.mark.asyncio
async def test_spec_visibility_isolated_by_role_repository_and_assignment() -> None:
    directory = InMemoryAgentDirectory()
    create = CreateAgent(directory)
    organization_id = uuid4()
    project_id = uuid4()
    repository_a_id = uuid4()
    repository_b_id = uuid4()
    organization_leader = await create.execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="visibility-organization-leader",
        ),
        idempotency_key="visibility-organization-leader",
    )
    team_a = await CreateRepositoryAgentTeam(directory).execute(
        CreateRepositoryAgentTeamRequest(
            organization_id=organization_id,
            organization_leader_id=organization_leader.principal.id,
            repository_id=repository_a_id,
            leader_agentteams_resource_name="visibility-repository-a-leader",
            worker_agentteams_resource_names=("visibility-repository-a-worker",),
            worker_responsibility_paths=("src/pricing/**", "tests/pricing/**"),
        ),
        idempotency_key="visibility-repository-a-team",
    )
    team_b = await CreateRepositoryAgentTeam(directory).execute(
        CreateRepositoryAgentTeamRequest(
            organization_id=organization_id,
            organization_leader_id=organization_leader.principal.id,
            repository_id=repository_b_id,
            leader_agentteams_resource_name="visibility-repository-b-leader",
            worker_agentteams_resource_names=("visibility-repository-b-worker",),
            worker_responsibility_paths=("src/checkout/**", "tests/checkout/**"),
        ),
        idempotency_key="visibility-repository-b-team",
    )
    topology = await CreateProjectAgentTopology(
        directory, InMemoryProjectTopologyStore()
    ).execute(
        CreateProjectAgentTopologyRequest(
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=organization_leader.principal.id,
            repository_teams=(
                RepositoryTeamAssignment(
                    repository_id=repository_a_id,
                    leader_agent_id=team_a.leader.id,
                    worker_agent_ids=(team_a.workers[0].id,),
                ),
                RepositoryTeamAssignment(
                    repository_id=repository_b_id,
                    leader_agent_id=team_b.leader.id,
                    worker_agent_ids=(team_b.workers[0].id,),
                ),
            ),
        ),
        idempotency_key="visibility-project-topology",
    )

    shared_spec_id = uuid4()
    unassigned_shared_notes_id = uuid4()
    repository_a_spec_id = uuid4()
    repository_b_spec_id = uuid4()
    worker_a_task_spec_id = uuid4()
    worker_b_task_spec_id = uuid4()

    visibility = {
        organization_leader.principal.id: frozenset(
            {shared_spec_id, unassigned_shared_notes_id}
        ),
        team_a.leader.id: frozenset({shared_spec_id, repository_a_spec_id}),
        team_b.leader.id: frozenset({shared_spec_id, repository_b_spec_id}),
        team_a.workers[0].id: frozenset({shared_spec_id, worker_a_task_spec_id}),
        team_b.workers[0].id: frozenset({shared_spec_id, worker_b_task_spec_id}),
    }

    def can_read(profile, object_id, scope, repository_id=None):
        return evaluate_permission(
            PermissionRequest(
                action=ContextAction.READ,
                scope=scope,
                context_object_id=object_id,
                repository_id=repository_id,
                tool="context.read",
            ),
            layers=(
                agent_permission_layer(profile),
                project_membership_permission_layer(
                    profile,
                    topology,
                    context_object_ids=visibility[profile.id],
                ),
            ),
        ).allowed

    org = organization_leader.principal
    leader_a = team_a.leader
    leader_b = team_b.leader
    worker_a = team_a.workers[0]
    worker_b = team_b.workers[0]

    assert can_read(org, shared_spec_id, ContextScope.PROJECT_SHARED)
    assert not can_read(
        org, repository_a_spec_id, ContextScope.TEAM_PRIVATE, repository_a_id
    )

    assert can_read(leader_a, shared_spec_id, ContextScope.PROJECT_SHARED)
    assert can_read(
        leader_a, repository_a_spec_id, ContextScope.TEAM_PRIVATE, repository_a_id
    )
    assert not can_read(
        leader_a, repository_b_spec_id, ContextScope.TEAM_PRIVATE, repository_b_id
    )
    assert can_read(
        leader_b, repository_b_spec_id, ContextScope.TEAM_PRIVATE, repository_b_id
    )
    assert not can_read(
        leader_b, repository_a_spec_id, ContextScope.TEAM_PRIVATE, repository_a_id
    )

    assert can_read(worker_a, shared_spec_id, ContextScope.PROJECT_SHARED)
    assert not can_read(
        worker_a, unassigned_shared_notes_id, ContextScope.PROJECT_SHARED
    )
    assert not can_read(
        worker_a, repository_a_spec_id, ContextScope.TEAM_PRIVATE, repository_a_id
    )
    assert can_read(
        worker_a, worker_a_task_spec_id, ContextScope.TASK_PRIVATE, repository_a_id
    )
    assert not can_read(
        worker_a, worker_b_task_spec_id, ContextScope.TASK_PRIVATE, repository_b_id
    )
    assert can_read(
        worker_b, worker_b_task_spec_id, ContextScope.TASK_PRIVATE, repository_b_id
    )
