from uuid import uuid4

import pytest

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.capability_management import (
    AgentCapabilityNotFound,
    PresetCapabilityAssembler,
    ResolveAgentCapabilities,
)


def principal(role: AgentRole) -> AgentPrincipalView:
    scoped = role is not AgentRole.ORGANIZATION_LEADER
    return AgentPrincipalView(
        id=uuid4(),
        organization_id=uuid4(),
        role=role,
        leader_agent_id=uuid4() if scoped else None,
        repository_id=uuid4() if scoped else None,
        responsibility_paths=("**",) if scoped else (),
        agentteams_resource_name=f"test-{role.value}",
        status=AgentPrincipalStatus.ACTIVE,
    )


@pytest.mark.parametrize(
    ("role", "skill_count", "mcp_count"),
    (
        (AgentRole.ORGANIZATION_LEADER, 3, 1),
        (AgentRole.REPOSITORY_LEADER, 6, 2),
        (AgentRole.WORKER, 3, 1),
    ),
)
def test_default_role_presets(role, skill_count, mcp_count) -> None:
    bundle = PresetCapabilityAssembler().assemble(principal(role))

    assert len(bundle.skills) == skill_count
    assert len(bundle.mcp_servers) == mcp_count
    assert all(role in capability.allowed_roles for capability in bundle.skills)
    assert all(role in capability.allowed_roles for capability in bundle.mcp_servers)


def test_web_worker_gets_isolated_playwright_capability() -> None:
    bundle = PresetCapabilityAssembler().assemble(
        principal(AgentRole.WORKER), task_features=frozenset({"web_e2e"})
    )

    assert [server.id for server in bundle.mcp_servers] == [
        "context7-docs-worker",
        "playwright-web-test",
    ]


def test_worker_never_receives_github_or_merge_operations() -> None:
    bundle = PresetCapabilityAssembler().assemble(
        principal(AgentRole.WORKER), task_features=frozenset({"web_e2e"})
    )

    assert all(not server.id.startswith("github") for server in bundle.mcp_servers)
    assert all("merge" not in operation for operation in bundle.tool_allowlist)


def test_repository_leader_can_review_but_not_merge_or_edit_code() -> None:
    bundle = PresetCapabilityAssembler().assemble(
        principal(AgentRole.REPOSITORY_LEADER)
    )
    github = next(server for server in bundle.mcp_servers if server.id.startswith("github"))

    assert "github.pull_requests.review" in github.allowed_operations
    assert "github.pull_requests.merge" in github.denied_operations
    assert "github.contents.write" in github.denied_operations


class StubDirectory:
    def __init__(self, agent: AgentPrincipalView | None) -> None:
        self.agent = agent

    async def get_view(self, agent_id):
        if self.agent is not None and self.agent.id == agent_id:
            return self.agent
        return None


async def test_resolve_capabilities_for_registered_agent() -> None:
    agent = principal(AgentRole.REPOSITORY_LEADER)

    bundle = await ResolveAgentCapabilities(StubDirectory(agent)).execute(agent.id)

    assert bundle.role is AgentRole.REPOSITORY_LEADER
    assert len(bundle.skills) == 6


async def test_unknown_agent_has_no_capabilities() -> None:
    with pytest.raises(AgentCapabilityNotFound):
        await ResolveAgentCapabilities(StubDirectory(None)).execute(uuid4())
