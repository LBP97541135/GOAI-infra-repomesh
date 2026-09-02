"""AgentTeams-side skill lists, chosen per role and per team capability profile.

The controller's worker/manager resources carry free-form skill names, and two
creation paths each hold their own default tuple today: the onboarding endpoint
chooses per agent (``api/human_control.py``) and the topology projection
defaults fresh creations (``runtime_projection._SKILLS``). Those defaults stay
where they are — the controller compares skill lists on every ensure, so
re-pointing one path at the other's tuple would 409 against every resource
already out there.

What neither path could do is answer "and what about a team with a charter?".
A cross-repo test team's Workers should not present as coders, and its leader
is not reviewing PRs. This module is the one place a capability profile
overrides either path's tuple: profile-specific replacements, keyed by role,
defined beside nothing else so the two paths cannot drift into different
answers for the same team.

Names mirror the platform skill ids where one exists (``cross-repo-test``,
``integration-run``) so a controller-side skill list and the workspace's
``.repomesh/skills`` mount stay visually the same story.
"""

from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.capability_management.contracts import (
    CROSS_REPO_TEST_TEAM_PROFILE,
    DEFAULT_TEAM_PROFILE,
)

_PROFILE_SKILLS: dict[str, dict[AgentRole, tuple[str, ...]]] = {
    CROSS_REPO_TEST_TEAM_PROFILE: {
        AgentRole.REPOSITORY_LEADER: ("cross-repo-test", "worker-management", "reporting"),
        AgentRole.WORKER: ("integration-run", "task-execution"),
    },
}


def agentteams_skills(
    role: AgentRole,
    base: tuple[str, ...],
    *,
    profile: str | None = None,
) -> tuple[str, ...]:
    """Answer the skill tuple to project for one role under one profile.

    ``base`` is the caller's own default — onboarding's or the projection's —
    and is returned untouched for the default profile, so every existing team
    keeps exactly the skills it was created with. A profile that names the role
    *replaces* the tuple rather than extending it: a test Worker presenting
    ``coding`` is the wrong story even if ``integration-run`` is appended
    after it.
    """

    profile_name = profile or DEFAULT_TEAM_PROFILE
    override = _PROFILE_SKILLS.get(profile_name, {}).get(role)
    return override if override is not None else tuple(base)
