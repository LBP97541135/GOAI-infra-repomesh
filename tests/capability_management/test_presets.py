from dataclasses import replace
from uuid import uuid4

import pytest

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.capability_management import (
    CROSS_REPO_TEST_TEAM_PROFILE,
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


def skill_ids(bundle) -> set[str]:
    return {capability.id for capability in bundle.skills}


@pytest.mark.parametrize(
    ("role", "skill_count", "mcp_count"),
    (
        (AgentRole.ORGANIZATION_LEADER, 3, 1),
        (AgentRole.REPOSITORY_LEADER, 6, 2),
        (AgentRole.WORKER, 4, 2),
    ),
)
def test_default_role_presets(role, skill_count, mcp_count) -> None:
    bundle = PresetCapabilityAssembler().assemble(principal(role))

    assert len(bundle.skills) == skill_count
    assert len(bundle.mcp_servers) == mcp_count
    assert all(role in capability.allowed_roles for capability in bundle.skills)
    assert all(role in capability.allowed_roles for capability in bundle.mcp_servers)


def test_every_worker_carries_the_tdd_skill() -> None:
    """The superpowers equivalent: mounting is universal, not task-negotiated."""

    bundle = PresetCapabilityAssembler().assemble(principal(AgentRole.WORKER))

    assert "tdd" in skill_ids(bundle)


def test_cross_repo_profile_adds_team_skills_on_top_of_role_presets() -> None:
    leader = PresetCapabilityAssembler().assemble(
        principal(AgentRole.REPOSITORY_LEADER), profile=CROSS_REPO_TEST_TEAM_PROFILE
    )
    worker = PresetCapabilityAssembler().assemble(
        principal(AgentRole.WORKER), profile=CROSS_REPO_TEST_TEAM_PROFILE
    )
    untouched = PresetCapabilityAssembler().assemble(
        principal(AgentRole.ORGANIZATION_LEADER), profile=CROSS_REPO_TEST_TEAM_PROFILE
    )

    # Additive: the specialised team keeps every role preset and gains its own.
    assert "cross-repo-test" in skill_ids(leader)
    assert skill_ids(leader) >= {
        "repository-spec-authoring",
        "task-decomposition",
        "code-review",
        "test-review",
        "worker-dispatch",
        "worker-result-evaluation",
    }
    assert "integration-run" in skill_ids(worker)
    assert "tdd" in skill_ids(worker)
    # The profile names no skills for the organization leader, who keeps hers.
    assert "cross-repo-test" not in skill_ids(untouched)


def test_business_team_under_default_profile_gets_no_test_team_skills() -> None:
    leader = PresetCapabilityAssembler().assemble(principal(AgentRole.REPOSITORY_LEADER))
    worker = PresetCapabilityAssembler().assemble(principal(AgentRole.WORKER))

    assert "cross-repo-test" not in skill_ids(leader)
    assert "integration-run" not in skill_ids(worker)


def test_unknown_profile_is_refused_rather_than_silently_ignored() -> None:
    with pytest.raises(ValueError, match="unknown team capability profile"):
        PresetCapabilityAssembler().assemble(
            principal(AgentRole.WORKER), profile="team-x"
        )


def test_web_worker_gets_isolated_playwright_capability() -> None:
    bundle = PresetCapabilityAssembler().assemble(
        principal(AgentRole.WORKER), task_features=frozenset({"web_e2e"})
    )

    assert [server.id for server in bundle.mcp_servers] == [
        "repomesh-task-control",
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
    bundle = PresetCapabilityAssembler().assemble(principal(AgentRole.REPOSITORY_LEADER))
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


async def test_disabled_agent_has_no_capabilities() -> None:
    agent = replace(
        principal(AgentRole.WORKER), status=AgentPrincipalStatus.DISABLED
    )
    with pytest.raises(AgentCapabilityNotFound, match="disabled"):
        await ResolveAgentCapabilities(StubDirectory(agent)).execute(agent.id)
