#!/usr/bin/env python3
"""Tests for the Codex CLI asset projector.

Same shape as the Claude Code projector tests, against a real temporary
workspace. Two properties carry most of the weight:

- **Non-destructiveness.** ``AGENTS.md`` is very likely the operator's own
  file; only the marker-delimited section is ours.
- **Nothing global is written.** Codex keeps MCP servers in
  ``~/.codex/config.toml``, shared with everything else the operator runs.
  This projector must never touch it -- ``test_projects_nothing_outside_the_
  workspace`` is the guard, because "joining a team edited my machine config"
  is not a failure anyone should discover by noticing it later.

Run:
    python -m unittest discover -s plugins/tests/teamharness/remote -p "test_*.py"
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[4]
BRIDGE_PARENT = REPO_ROOT / "plugins" / "teamharness" / "remote"
if str(BRIDGE_PARENT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_PARENT))

from bridge.projectors._assets import MANAGED_BEGIN, MANAGED_END  # noqa: E402
from bridge.projectors.codex_cli import (  # noqa: E402
    CONTEXT_FILE,
    SKILLS_DIR,
    CodexCliProjector,
)
from bridge.protocol import AssetContext, AssetProjector  # noqa: E402

PLUGIN_DIR = REPO_ROOT / "plugins" / "teamharness"

TEAM_NAME = "atlas-team"
TEAM_ROOM = "!teamroom:example.org"
MEMBER_ID = "@bohan-local:example.org"

# Granted to remote-member by plugin.yaml.
EXPECTED_SKILLS = {"mcporter", "find-skills", "communication", "file-sharing", "task-execution"}
# Leader-only; a role filter that passed everything would hand these over.
LEADER_ONLY_SKILLS = {"roomflow", "team-coordination", "project-management", "task-delegation"}

USER_NOTES = "# My Project\n\nRun `make test` before pushing.\nDo not touch vendor/.\n"


class ProjectorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="teamharness-codex-proj-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.projector = CodexCliProjector()

    def context(self, role: str = "remote-member") -> AssetContext:
        return AssetContext(
            workspace=self.workspace,
            role=role,
            member_name="bohan-local",
            team_name=TEAM_NAME,
            plugin_dir=PLUGIN_DIR,
            mcp_env_passthrough=("AGENTTEAMS_MATRIX_URL",),
            team_room_id=TEAM_ROOM,
            leader_name="atlas-leader",
            matrix_user_id=MEMBER_ID,
        )

    def read(self, relative: str) -> str:
        return (self.workspace / relative).read_text(encoding="utf-8")


class ProtocolConformanceTest(ProjectorTestCase):
    def test_satisfies_the_asset_projector_protocol(self) -> None:
        self.assertIsInstance(self.projector, AssetProjector)
        self.assertEqual(self.projector.name, "codex-cli")


class ContextFileTest(ProjectorTestCase):
    def test_writes_agents_md_not_claude_md(self) -> None:
        projection = self.projector.project(self.context())

        self.assertEqual(CONTEXT_FILE, "AGENTS.md")
        self.assertIn("AGENTS.md", projection.files)
        self.assertTrue((self.workspace / "AGENTS.md").is_file())
        self.assertFalse((self.workspace / "CLAUDE.md").exists())

    def test_carries_contract_role_prompt_and_facts(self) -> None:
        self.projector.project(self.context())
        text = self.read(CONTEXT_FILE)

        contract = (PLUGIN_DIR / "prompts" / "team" / "TEAMS.md").read_text(encoding="utf-8")
        self.assertIn(contract.strip(), text)
        role_prompt = (PLUGIN_DIR / "prompts" / "agent" / "remote-member.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(role_prompt.strip(), text)

        self.assertIn(f"- team.name: {TEAM_NAME}", text)
        self.assertIn(f"- team.teamRoomId: {TEAM_ROOM}", text)
        self.assertIn("- member.role: remote-member", text)
        self.assertEqual(text.count(MANAGED_BEGIN), 1)
        self.assertEqual(text.count(MANAGED_END), 1)

    def test_existing_agents_md_is_preserved_verbatim(self) -> None:
        (self.workspace / CONTEXT_FILE).write_text(USER_NOTES, encoding="utf-8")

        self.projector.project(self.context())
        text = self.read(CONTEXT_FILE)

        self.assertTrue(text.startswith(USER_NOTES))
        self.assertLess(text.index(USER_NOTES), text.index(MANAGED_BEGIN))

    def test_two_runs_produce_identical_bytes(self) -> None:
        self.projector.project(self.context())
        first = self.read(CONTEXT_FILE)
        self.projector.project(self.context())
        self.assertEqual(self.read(CONTEXT_FILE), first)


class SkillsTest(ProjectorTestCase):
    def test_installs_role_granted_skills_under_dot_codex(self) -> None:
        projection = self.projector.project(self.context())

        self.assertEqual(SKILLS_DIR, ".codex/skills")
        self.assertEqual(set(projection.skills), EXPECTED_SKILLS)
        for skill in EXPECTED_SKILLS:
            self.assertTrue((self.workspace / SKILLS_DIR / skill / "SKILL.md").is_file())

    def test_leader_only_skills_are_not_given_to_a_remote_member(self) -> None:
        projection = self.projector.project(self.context())
        self.assertFalse(LEADER_ONLY_SKILLS.intersection(projection.skills))
        for skill in LEADER_ONLY_SKILLS:
            self.assertFalse((self.workspace / SKILLS_DIR / skill).exists())


class NoGlobalWritesTest(ProjectorTestCase):
    def test_projects_no_mcp_config_at_all(self) -> None:
        """MCP travels as ``-c`` overrides; nothing is installed, so nothing is
        reported as installed."""
        projection = self.projector.project(self.context())

        self.assertEqual(projection.mcp_servers, ())
        self.assertFalse((self.workspace / ".mcp.json").exists())
        self.assertFalse((self.workspace / ".codex" / "config.toml").exists())

    def test_projects_nothing_outside_the_workspace(self) -> None:
        """The guard against ever growing a global-config write.

        Everything the projector creates must live under the workspace it was
        handed. A future change that "just adds" the server to
        ``~/.codex/config.toml`` fails here.
        """
        projection = self.projector.project(self.context())
        for name in projection.files:
            resolved = (self.workspace / name).resolve()
            self.assertTrue(
                str(resolved).startswith(str(self.workspace.resolve())),
                f"{name} escapes the workspace",
            )
            self.assertTrue(resolved.exists(), f"{name} was reported but not written")


class UnprojectTest(ProjectorTestCase):
    def test_removes_only_what_it_wrote(self) -> None:
        (self.workspace / CONTEXT_FILE).write_text(USER_NOTES, encoding="utf-8")
        mine = self.workspace / SKILLS_DIR / "not-ours"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("operator's own", encoding="utf-8")

        self.projector.project(self.context())
        self.projector.unproject(self.context())

        # The operator's notes survive, byte for byte.
        self.assertEqual(self.read(CONTEXT_FILE), USER_NOTES)
        self.assertNotIn(MANAGED_BEGIN, self.read(CONTEXT_FILE))
        # ...and so does the skill they installed themselves.
        self.assertTrue((mine / "SKILL.md").is_file())
        for skill in EXPECTED_SKILLS:
            self.assertFalse((self.workspace / SKILLS_DIR / skill).exists())

    def test_context_file_is_deleted_when_it_only_held_our_section(self) -> None:
        self.projector.project(self.context())
        self.projector.unproject(self.context())
        # Leaving an empty AGENTS.md behind would still change how Codex starts.
        self.assertFalse((self.workspace / CONTEXT_FILE).exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
