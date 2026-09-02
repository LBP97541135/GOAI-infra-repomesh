from repomesh.modules.agent_directory.contracts import AgentPrincipalView, AgentRole

from .contracts import (
    CROSS_REPO_TEST_TEAM_PROFILE,
    DEFAULT_TEAM_PROFILE,
    AgentCapabilityBundle,
    CapabilityAccess,
    CapabilityDefinition,
    CapabilityKind,
    CapabilitySource,
)


def _skill(
    skill_id: str,
    title: str,
    role: AgentRole,
    source: CapabilitySource,
) -> CapabilityDefinition:
    return CapabilityDefinition(
        id=skill_id,
        kind=CapabilityKind.SKILL,
        title=title,
        source=source,
        access=CapabilityAccess.READ_ONLY,
        allowed_roles=frozenset({role}),
        allowed_operations=(f"skill.invoke:{skill_id}",),
        local_path=f"capabilities/skills/{skill_id}/SKILL.md",
    )


SPEC_KIT = CapabilitySource("https://github.com/github/spec-kit", maintainer="GitHub")
AWESOME_COPILOT = CapabilitySource("https://github.com/github/awesome-copilot", maintainer="GitHub")
ANTHROPIC_PLUGINS = CapabilitySource(
    "https://github.com/anthropics/claude-plugins-official",
    maintainer="Anthropic",
)
SUPERPOWERS = CapabilitySource(
    "https://github.com/obra/superpowers",
    "skills/test-driven-development",
    "Jesse Vincent",
)
REPOMESH_INTERNAL = CapabilitySource("internal://repomesh", maintainer="RepoMesh")

SKILLS = {
    item.id: item
    for item in (
        _skill("project-intake", "项目需求接收", AgentRole.ORGANIZATION_LEADER, SPEC_KIT),
        _skill(
            "cross-repo-planning",
            "跨仓库规划",
            AgentRole.ORGANIZATION_LEADER,
            AWESOME_COPILOT,
        ),
        _skill(
            "delivery-governance",
            "项目交付治理",
            AgentRole.ORGANIZATION_LEADER,
            SPEC_KIT,
        ),
        _skill(
            "repository-spec-authoring",
            "仓库 Spec 编写",
            AgentRole.REPOSITORY_LEADER,
            SPEC_KIT,
        ),
        _skill(
            "task-decomposition",
            "仓库任务拆分",
            AgentRole.REPOSITORY_LEADER,
            SPEC_KIT,
        ),
        _skill(
            "code-review",
            "代码审查",
            AgentRole.REPOSITORY_LEADER,
            CapabilitySource(
                ANTHROPIC_PLUGINS.repository,
                "plugins/pr-review-toolkit/agents/code-reviewer.md",
                "Anthropic",
            ),
        ),
        _skill(
            "test-review",
            "测试充分性审查",
            AgentRole.REPOSITORY_LEADER,
            CapabilitySource(
                ANTHROPIC_PLUGINS.repository,
                "plugins/pr-review-toolkit/agents/pr-test-analyzer.md",
                "Anthropic",
            ),
        ),
        _skill(
            "worker-dispatch",
            "Worker 调度",
            AgentRole.REPOSITORY_LEADER,
            AWESOME_COPILOT,
        ),
        _skill(
            "worker-result-evaluation",
            "Worker 结果验收",
            AgentRole.REPOSITORY_LEADER,
            ANTHROPIC_PLUGINS,
        ),
        _skill("task-execution", "当前任务执行", AgentRole.WORKER, SPEC_KIT),
        _skill("self-test", "代码自测", AgentRole.WORKER, SPEC_KIT),
        _skill("blocker-reporting", "阻塞上报", AgentRole.WORKER, AWESOME_COPILOT),
        _skill("tdd", "测试驱动开发", AgentRole.WORKER, SUPERPOWERS),
        _skill(
            "cross-repo-test",
            "跨仓联调队长",
            AgentRole.REPOSITORY_LEADER,
            REPOMESH_INTERNAL,
        ),
        _skill(
            "integration-run",
            "联调测试执行",
            AgentRole.WORKER,
            REPOMESH_INTERNAL,
        ),
    )
}

GITHUB_ORG = CapabilityDefinition(
    id="github-org-readonly",
    kind=CapabilityKind.MCP,
    title="GitHub 组织与交付状态",
    source=CapabilitySource("https://github.com/github/github-mcp-server", maintainer="GitHub"),
    access=CapabilityAccess.READ_ONLY,
    allowed_roles=frozenset({AgentRole.ORGANIZATION_LEADER}),
    allowed_operations=(
        "github.repos.read",
        "github.issues.read",
        "github.pull_requests.read",
        "github.actions.read",
    ),
    denied_operations=("github.contents.write", "github.pull_requests.merge"),
)

GITHUB_REPOSITORY = CapabilityDefinition(
    id="github-repository-review",
    kind=CapabilityKind.MCP,
    title="GitHub 仓库审查",
    source=GITHUB_ORG.source,
    access=CapabilityAccess.CONTROLLED_WRITE,
    allowed_roles=frozenset({AgentRole.REPOSITORY_LEADER}),
    allowed_operations=(
        "github.repository.read",
        "github.pull_requests.read",
        "github.pull_requests.review",
        "github.actions.read",
    ),
    denied_operations=("github.contents.write", "github.pull_requests.merge"),
)

CONTEXT7_LEADER = CapabilityDefinition(
    id="context7-docs-leader",
    kind=CapabilityKind.MCP,
    title="Context7 技术文档",
    source=CapabilitySource("https://github.com/upstash/context7", maintainer="Upstash"),
    access=CapabilityAccess.READ_ONLY,
    allowed_roles=frozenset({AgentRole.REPOSITORY_LEADER}),
    allowed_operations=("context7.resolve-library-id", "context7.query-docs"),
)

CONTEXT7_WORKER = CapabilityDefinition(
    id="context7-docs-worker",
    kind=CapabilityKind.MCP,
    title="Context7 技术文档",
    source=CONTEXT7_LEADER.source,
    access=CapabilityAccess.READ_ONLY,
    allowed_roles=frozenset({AgentRole.WORKER}),
    allowed_operations=CONTEXT7_LEADER.allowed_operations,
)

PLAYWRIGHT_WORKER = CapabilityDefinition(
    id="playwright-web-test",
    kind=CapabilityKind.MCP,
    title="Playwright 隔离式 Web 测试",
    source=CapabilitySource("https://github.com/microsoft/playwright-mcp", maintainer="Microsoft"),
    access=CapabilityAccess.EXECUTION,
    allowed_roles=frozenset({AgentRole.WORKER}),
    allowed_operations=("playwright.navigate", "playwright.inspect", "playwright.interact"),
    denied_operations=("playwright.persistent-profile",),
    conditional_on=frozenset({"web_e2e"}),
)

REPOMESH_TASK_CONTROL = CapabilityDefinition(
    id="repomesh-task-control",
    kind=CapabilityKind.MCP,
    title="RepoMesh 任务执行控制",
    source=CapabilitySource("internal://repomesh", maintainer="RepoMesh"),
    access=CapabilityAccess.EXECUTION,
    allowed_roles=frozenset({AgentRole.WORKER}),
    allowed_operations=("repomesh.start_assigned_task",),
)

ROLE_SKILLS = {
    AgentRole.ORGANIZATION_LEADER: (
        "project-intake",
        "cross-repo-planning",
        "delivery-governance",
    ),
    AgentRole.REPOSITORY_LEADER: (
        "repository-spec-authoring",
        "task-decomposition",
        "code-review",
        "test-review",
        "worker-dispatch",
        "worker-result-evaluation",
    ),
    AgentRole.WORKER: ("task-execution", "self-test", "blocker-reporting", "tdd"),
}

#: Extra skills assembled *on top of* the role presets for teams whose
#: repository carries the profile. Additive by design: a specialised team is a
#: repository team with a charter, not a new role, so its leader keeps the
#: leader presets and its Workers keep the worker presets.
TEAM_PROFILES: dict[str, dict[AgentRole, tuple[str, ...]]] = {
    DEFAULT_TEAM_PROFILE: {},
    CROSS_REPO_TEST_TEAM_PROFILE: {
        AgentRole.REPOSITORY_LEADER: ("cross-repo-test",),
        AgentRole.WORKER: ("integration-run",),
    },
}

ROLE_MCP = {
    AgentRole.ORGANIZATION_LEADER: (GITHUB_ORG,),
    AgentRole.REPOSITORY_LEADER: (GITHUB_REPOSITORY, CONTEXT7_LEADER),
    AgentRole.WORKER: (REPOMESH_TASK_CONTROL, CONTEXT7_WORKER, PLAYWRIGHT_WORKER),
}


class PresetCapabilityAssembler:
    """Resolve the governed default capability bundle for one RepoMesh principal."""

    def assemble(
        self,
        principal: AgentPrincipalView,
        *,
        task_features: frozenset[str] = frozenset(),
        profile: str | None = None,
    ) -> AgentCapabilityBundle:
        profile_name = profile or DEFAULT_TEAM_PROFILE
        if profile_name not in TEAM_PROFILES:
            raise ValueError(f"unknown team capability profile: {profile_name}")
        extra_skills = TEAM_PROFILES[profile_name].get(principal.role, ())
        skill_ids = tuple(dict.fromkeys((*ROLE_SKILLS[principal.role], *extra_skills)))
        skills = tuple(SKILLS[skill_id] for skill_id in skill_ids)
        servers = tuple(
            server
            for server in ROLE_MCP[principal.role]
            if not server.conditional_on or server.conditional_on <= task_features
        )
        for capability in (*skills, *servers):
            if principal.role not in capability.allowed_roles:
                raise PermissionError(
                    f"{capability.id} cannot be attached to {principal.role.value}"
                )
        return AgentCapabilityBundle(principal.role, skills, servers)
