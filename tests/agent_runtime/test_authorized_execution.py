from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from repomesh.integrations.coding_agents.mock import MockCodingAgent
from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.infrastructure import InMemoryAgentDirectory
from repomesh.modules.agent_runtime.application import (
    CodingRunDenied,
    ExecuteAuthorizedCodingRun,
)
from repomesh.modules.agent_runtime.contracts import AuthorizedCodingRunRequest
from repomesh.modules.agent_runtime.ports import CodingRunRequest, RunStatus
from repomesh.modules.context.contracts import ExecutionContextGrant
from repomesh.modules.identity_access import PolicyAuthorizationGateway
from repomesh.modules.project import (
    CreateProjectAgentTopology,
    CreateProjectAgentTopologyRequest,
    RepositoryTeamAssignment,
)
from repomesh.modules.project.infrastructure import InMemoryProjectTopologyStore


async def build_execution_gate():
    directory = InMemoryAgentDirectory()
    create = CreateAgent(directory)
    organization_id = uuid4()
    repository_id = uuid4()
    project_id = uuid4()
    organization_leader = await create.execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="execution-org-leader",
        ),
        idempotency_key="execution-org-leader",
    )
    team = await CreateRepositoryAgentTeam(directory).execute(
        CreateRepositoryAgentTeamRequest(
            organization_id=organization_id,
            organization_leader_id=organization_leader.principal.id,
            repository_id=repository_id,
            leader_agentteams_resource_name="execution-repo-leader",
            worker_agentteams_resource_names=("execution-worker",),
            worker_responsibility_paths=("src/pricing/**", "tests/pricing/**"),
        ),
        idempotency_key="execution-team",
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
                    worker_agent_ids=(team.workers[0].id,),
                ),
            ),
        ),
        idempotency_key="execution-topology",
    )
    service = ExecuteAuthorizedCodingRun(
        MockCodingAgent(), directory, topologies, PolicyAuthorizationGateway()
    )
    return service, organization_id, project_id, repository_id, team.workers[0]


def request_for(
    organization_id, project_id, repository_id, worker, *, paths, tools
) -> AuthorizedCodingRunRequest:
    coding_request = CodingRunRequest(
        task_id=uuid4(),
        repository_url="https://github.com/example/saleor",
        instruction="Update pricing API",
    )
    grant = ExecutionContextGrant(
        bundle_id=uuid4(),
        project_id=project_id,
        run_id=coding_request.run_id,
        agent_id=worker.id,
        repository_id=repository_id,
        allowed_tools=("read", "edit", "test"),
        allowed_paths=("src/pricing/**", "tests/pricing/**"),
        denied_paths=("src/pricing/secrets/**",),
        network_policy=(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        content_hash="sha256:execution-grant",
    )
    return AuthorizedCodingRunRequest(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        agent_id=worker.id,
        coding_request=coding_request,
        context_grant=grant,
        requested_paths=paths,
        requested_tools=tools,
    )


@pytest.mark.asyncio
async def test_authorized_worker_can_start_coding_run() -> None:
    service, organization_id, project_id, repository_id, worker = (
        await build_execution_gate()
    )
    result = await service.execute(
        request_for(
            organization_id,
            project_id,
            repository_id,
            worker,
            paths=("src/pricing/resolver.py", "tests/pricing/test_resolver.py"),
            tools=("read", "edit", "test"),
        )
    )
    assert result.status is RunStatus.SUCCEEDED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("paths", "tools", "reason"),
    [
        (("src/orders/service.py",), ("edit",), "path_not_allowed"),
        (("src/pricing/secrets/key.py",), ("read",), "path_denied"),
        (("src/pricing/resolver.py",), ("shell",), "tool_denied"),
    ],
)
async def test_context_grant_blocks_undelegated_execution(paths, tools, reason) -> None:
    service, organization_id, project_id, repository_id, worker = (
        await build_execution_gate()
    )
    with pytest.raises(CodingRunDenied, match=reason):
        await service.execute(
            request_for(
                organization_id,
                project_id,
                repository_id,
                worker,
                paths=paths,
                tools=tools,
            )
        )
