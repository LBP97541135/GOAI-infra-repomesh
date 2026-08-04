#!/usr/bin/env python3
"""Tests for the Claude Code asset projector.

Stdlib ``unittest`` only, no network, no subprocess, no ``mock.patch``. The
projector's whole job is filesystem shape, so every test runs it against a real
temporary workspace and asserts on the bytes it leaves behind.

Two properties get the most attention, because both are destructive when they
regress:

- **Non-destructiveness.** The workspace is the operator's own repository. A
  projector that overwrites ``CLAUDE.md`` or replaces ``.mcp.json`` costs
  someone their notes or their own MCP servers.
- **The credential red line.** A real token must never reach ``.mcp.json``.
  ``test_mcp_never_writes_a_real_token`` puts a fake token in the environment
  under the passed-through variable name and asserts it does not appear in the
  file, so a future "helpful" ``os.getenv`` call fails the suite.

Run:
    python -m unittest discover -s plugins/tests/teamharness/remote -p "test_*.py"
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[4]
# ``bridge`` is shared by every runtime, so its parent goes on sys.path --
# exactly how the supervisor is consumed.
BRIDGE_PARENT = REPO_ROOT / "plugins" / "teamharness" / "remote"
if str(BRIDGE_PARENT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_PARENT))

from bridge.projectors.claude_code import (  # noqa: E402
    MANAGED_BEGIN,
    MANAGED_END,
    ClaudeCodeProjector,
)
from bridge.protocol import AssetContext, AssetProjector  # noqa: E402

PLUGIN_DIR = REPO_ROOT / "plugins" / "teamharness"

TEAM_NAME = "atlas-team"
TEAM_ROOM = "!teamroom:example.org"
PERSONAL_ROOM = "!personal:example.org"
MEMBER_ID = "@bohan-local:example.org"
TOKEN_VAR = "AGENTTEAMS_WORKER_MATRIX_TOKEN"
FAKE_TOKEN = "syt_fake_token_value_do_not_project_0123456789"
# The member's own scoped MinIO secret. A second passthrough credential with a
# different shape than the Matrix token, so a leak guard that only recognises
# ``syt_`` does not pass by accident.
STORAGE_SECRET_VAR = "AGENTTEAMS_FS_SECRET_KEY"
FAKE_STORAGE_SECRET = "minio_fake_secret_do_not_project_9876543210"

# Granted to remote-member by plugin.yaml.
EXPECTED_SKILLS = {
    "mcporter",
    "find-skills",
    "communication",
    "file-sharing",
    "task-execution",
}
# Leader-only in plugin.yaml; a role filter that silently passes everything
# would hand these to a remote member.
LEADER_ONLY_SKILLS = {
    "roomflow",
    "team-coordination",
    "project-management",
    "task-delegation",
}

USER_NOTES = "# My Project\n\nRun `make test` before pushing.\nDo not touch vendor/.\n"


class ProjectorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="teamharness-projector-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.projector = ClaudeCodeProjector()

    def context(self, plugin_dir: Path | None = None, role: str = "remote-member") -> AssetContext:
        return AssetContext(
            workspace=self.workspace,
            role=role,
            member_name="bohan-local",
            team_name=TEAM_NAME,
            plugin_dir=plugin_dir or PLUGIN_DIR,
            mcp_env_passthrough=("AGENTTEAMS_MATRIX_URL", TOKEN_VAR),
            team_room_id=TEAM_ROOM,
            leader_name="atlas-leader",
            matrix_user_id=MEMBER_ID,
            personal_room_id=PERSONAL_ROOM,
        )

    def read(self, relative: str) -> str:
        return (self.workspace / relative).read_text(encoding="utf-8")

    def mcp(self) -> dict:
        return json.loads(self.read(".mcp.json"))


class ProtocolConformanceTest(ProjectorTestCase):
    def test_satisfies_the_asset_projector_protocol(self) -> None:
        self.assertIsInstance(self.projector, AssetProjector)
        self.assertEqual(self.projector.name, "claude-code")


class ContextFileTest(ProjectorTestCase):
    def test_empty_workspace_gets_contract_role_prompt_and_facts(self) -> None:
        projection = self.projector.project(self.context())

        self.assertIn("CLAUDE.md", projection.files)
        text = self.read("CLAUDE.md")

        # Team contract, verbatim from the package rather than paraphrased.
        contract = (PLUGIN_DIR / "prompts" / "team" / "TEAMS.md").read_text(encoding="utf-8")
        self.assertIn(contract.strip(), text)

        # Role prompt for this member's role, not the worker one.
        role_prompt = (PLUGIN_DIR / "prompts" / "agent" / "remote-member.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(role_prompt.strip(), text)

        # The facts that answer "which team do you belong to".
        self.assertIn("## Runtime Team Context", text)
        self.assertIn(f"- team.name: {TEAM_NAME}", text)
        self.assertIn(f"- team.teamRoomId: {TEAM_ROOM}", text)
        self.assertIn("- team.leaderName: atlas-leader", text)
        self.assertIn("- member.name: bohan-local", text)
        self.assertIn("- member.role: remote-member", text)
        self.assertIn(f"- member.matrixUserId: {MEMBER_ID}", text)
        self.assertIn(f"- member.personalRoomId: {PERSONAL_ROOM}", text)

        self.assertIn("Do not write secrets, credentials", text)
        self.assertTrue(text.startswith(MANAGED_BEGIN))
        self.assertEqual(text.count(MANAGED_BEGIN), 1)
        self.assertEqual(text.count(MANAGED_END), 1)

    def test_existing_user_claude_md_is_preserved_verbatim(self) -> None:
        target = self.workspace / "CLAUDE.md"
        target.write_text(USER_NOTES, encoding="utf-8")

        self.projector.project(self.context())
        text = self.read("CLAUDE.md")

        # Every byte the operator wrote is still there, still at the top.
        self.assertTrue(text.startswith(USER_NOTES))
        self.assertIn("Run `make test` before pushing.", text)
        self.assertIn("Do not touch vendor/.", text)
        # ...and the managed section was appended after it.
        self.assertLess(text.index(USER_NOTES), text.index(MANAGED_BEGIN))
        self.assertIn(f"- team.name: {TEAM_NAME}", text)

    def test_empty_facts_are_omitted_not_rendered_blank(self) -> None:
        ctx = AssetContext(
            workspace=self.workspace,
            role="remote-member",
            member_name="solo",
            team_name=TEAM_NAME,
            plugin_dir=PLUGIN_DIR,
        )
        self.projector.project(ctx)
        text = self.read("CLAUDE.md")
        self.assertIn(f"- team.name: {TEAM_NAME}", text)
        self.assertNotIn("- team.leaderName:", text)
        self.assertNotIn("- member.personalRoomId:", text)

    def test_half_a_marker_pair_appends_rather_than_eating_the_file(self) -> None:
        # A hand-edited file that lost its END marker cannot be delimited; the
        # recoverable choice is to append, not to guess where the section ends.
        target = self.workspace / "CLAUDE.md"
        target.write_text(f"{USER_NOTES}\n{MANAGED_BEGIN}\nhalf a section\n", encoding="utf-8")

        self.projector.project(self.context())
        text = self.read("CLAUDE.md")
        self.assertIn("half a section", text)
        self.assertIn(USER_NOTES, text)


class IdempotencyTest(ProjectorTestCase):
    def test_two_runs_produce_identical_bytes_and_one_section(self) -> None:
        first = self.projector.project(self.context())
        first_text = self.read("CLAUDE.md")
        first_mcp = self.read(".mcp.json")

        second = self.projector.project(self.context())

        self.assertEqual(self.read("CLAUDE.md"), first_text)
        self.assertEqual(self.read(".mcp.json"), first_mcp)
        self.assertEqual(second.files, first.files)
        self.assertEqual(second.skills, first.skills)
        self.assertEqual(first_text.count(MANAGED_BEGIN), 1)
        self.assertEqual(first_text.count(MANAGED_END), 1)

    def test_two_runs_over_user_content_do_not_duplicate_the_section(self) -> None:
        (self.workspace / "CLAUDE.md").write_text(USER_NOTES, encoding="utf-8")
        self.projector.project(self.context())
        after_first = self.read("CLAUDE.md")
        self.projector.project(self.context())

        self.assertEqual(self.read("CLAUDE.md"), after_first)
        self.assertEqual(after_first.count(MANAGED_BEGIN), 1)
        self.assertTrue(after_first.startswith(USER_NOTES))


class SkillsTest(ProjectorTestCase):
    def test_remote_member_gets_its_skills_and_none_of_the_leader_ones(self) -> None:
        projection = self.projector.project(self.context())
        installed = set(projection.skills)

        self.assertEqual(installed, EXPECTED_SKILLS)
        self.assertIn("task-execution", installed)
        for leader_skill in LEADER_ONLY_SKILLS:
            self.assertNotIn(leader_skill, installed)
            self.assertFalse((self.workspace / ".claude" / "skills" / leader_skill).exists())

        # Copied as whole directories, not just registered by name.
        skill_dir = self.workspace / ".claude" / "skills" / "task-execution"
        self.assertTrue(skill_dir.is_dir())
        self.assertTrue(any(skill_dir.iterdir()))
        self.assertIn(".claude/skills/task-execution", projection.files)

    def test_underscore_role_spelling_still_matches_the_manifest(self) -> None:
        projection = self.projector.project(self.context(role="remote_member"))
        self.assertEqual(set(projection.skills), EXPECTED_SKILLS)

    def test_reprojection_replaces_a_stale_managed_skill_directory(self) -> None:
        self.projector.project(self.context())
        stale = self.workspace / ".claude" / "skills" / "task-execution" / "STALE.md"
        stale.write_text("removed upstream", encoding="utf-8")

        self.projector.project(self.context())
        self.assertFalse(stale.exists())

    def test_user_installed_skills_are_left_alone(self) -> None:
        mine = self.workspace / ".claude" / "skills" / "my-own-skill"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("mine", encoding="utf-8")

        projection = self.projector.project(self.context())
        self.assertNotIn("my-own-skill", projection.skills)
        self.assertEqual((mine / "SKILL.md").read_text(encoding="utf-8"), "mine")


class McpConfigTest(ProjectorTestCase):
    def test_server_entry_shape(self) -> None:
        projection = self.projector.project(self.context())
        self.assertEqual(projection.mcp_servers, ("teamharness",))
        self.assertIn(".mcp.json", projection.files)

        entry = self.mcp()["mcpServers"]["teamharness"]
        # The interpreter running the bridge, not the bare name "python":
        # Claude Code spawns the server from its own environment, where
        # "python" may resolve elsewhere or not at all.
        self.assertEqual(entry["command"], sys.executable)
        self.assertTrue(Path(entry["command"]).is_absolute())
        self.assertEqual(len(entry["args"]), 1)
        self.assertTrue(entry["args"][0].endswith("server.py"))
        self.assertTrue(Path(entry["args"][0]).is_absolute())

        env = entry["env"]
        # The role is a fact, not a secret: the one literal value allowed here.
        self.assertEqual(env["AGENTTEAMS_AGENT_ROLE"], "remote-member")
        self.assertEqual(env["AGENTTEAMS_MATRIX_URL"], "${AGENTTEAMS_MATRIX_URL}")
        self.assertEqual(env[TOKEN_VAR], "${" + TOKEN_VAR + "}")

    def test_mcp_env_tells_the_server_where_the_workspace_is(self) -> None:
        """Without this, every taskflow/filesync call fails.

        The server infers the workspace from QWENPAW_WORKING_DIR inside a
        worker container. A remote member has no such variable, so the
        projector supplies TEAMHARNESS_SHARED_DIR -- found when a live Codex
        turn got ``{"ok": false, "error": "workspaceDir is required"}`` back
        from ``ack_task``.
        """
        self.projector.project(self.context())
        env = self.mcp()["mcpServers"]["teamharness"]["env"]
        self.assertEqual(env["TEAMHARNESS_SHARED_DIR"], str(self.workspace / "shared"))

    def test_mcp_never_writes_a_real_token(self) -> None:
        # The passthrough variable is populated for real; the projector must
        # still write only a reference to its name.
        previous = os.environ.get(TOKEN_VAR)
        os.environ[TOKEN_VAR] = FAKE_TOKEN
        try:
            self.projector.project(self.context())
        finally:
            if previous is None:
                os.environ.pop(TOKEN_VAR, None)
            else:
                os.environ[TOKEN_VAR] = previous

        raw = self.read(".mcp.json")
        self.assertNotIn(FAKE_TOKEN, raw)
        self.assertNotIn("syt_", raw)
        self.assertIn("${" + TOKEN_VAR + "}", raw)

    def test_mcp_never_writes_a_real_storage_secret(self) -> None:
        # Same red line as the Matrix token, for the credential that made
        # shared storage reachable. This one is easier to leak by accident: an
        # ``mc`` alias URL embeds the secret inline, so a projector "helpfully"
        # precomputing MC_HOST_* would write it straight into the repository.
        ctx = AssetContext(
            workspace=self.workspace,
            role="remote-member",
            member_name="bohan-local",
            team_name=TEAM_NAME,
            plugin_dir=PLUGIN_DIR,
            mcp_env_passthrough=("AGENTTEAMS_FS_ENDPOINT", STORAGE_SECRET_VAR),
        )
        previous = os.environ.get(STORAGE_SECRET_VAR)
        os.environ[STORAGE_SECRET_VAR] = FAKE_STORAGE_SECRET
        try:
            self.projector.project(ctx)
        finally:
            if previous is None:
                os.environ.pop(STORAGE_SECRET_VAR, None)
            else:
                os.environ[STORAGE_SECRET_VAR] = previous

        raw = self.read(".mcp.json")
        self.assertNotIn(FAKE_STORAGE_SECRET, raw)
        self.assertIn("${" + STORAGE_SECRET_VAR + "}", raw)

    def test_role_variable_in_passthrough_stays_literal(self) -> None:
        ctx = AssetContext(
            workspace=self.workspace,
            role="remote-member",
            member_name="bohan-local",
            team_name=TEAM_NAME,
            plugin_dir=PLUGIN_DIR,
            mcp_env_passthrough=("AGENTTEAMS_AGENT_ROLE", "AGENTTEAMS_MATRIX_URL"),
        )
        self.projector.project(ctx)
        env = self.mcp()["mcpServers"]["teamharness"]["env"]
        self.assertEqual(env["AGENTTEAMS_AGENT_ROLE"], "remote-member")

    def test_existing_user_servers_are_preserved(self) -> None:
        target = self.workspace / ".mcp.json"
        target.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "my-db": {"command": "node", "args": ["db.js"]},
                    },
                    "someOtherKey": {"keep": True},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        self.projector.project(self.context())
        payload = self.mcp()
        self.assertEqual(payload["mcpServers"]["my-db"], {"command": "node", "args": ["db.js"]})
        self.assertEqual(payload["someOtherKey"], {"keep": True})
        self.assertIn("teamharness", payload["mcpServers"])


class UnprojectTest(ProjectorTestCase):
    def test_removes_only_what_project_wrote(self) -> None:
        (self.workspace / "CLAUDE.md").write_text(USER_NOTES, encoding="utf-8")
        (self.workspace / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"my-db": {"command": "node"}}}, indent=2),
            encoding="utf-8",
        )
        mine = self.workspace / ".claude" / "skills" / "my-own-skill"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("mine", encoding="utf-8")

        self.projector.project(self.context())
        projection = self.projector.unproject(self.context())

        text = self.read("CLAUDE.md")
        self.assertEqual(text, USER_NOTES)
        self.assertNotIn(MANAGED_BEGIN, text)
        self.assertNotIn(MANAGED_END, text)

        for skill in EXPECTED_SKILLS:
            self.assertFalse((self.workspace / ".claude" / "skills" / skill).exists())
        self.assertEqual((mine / "SKILL.md").read_text(encoding="utf-8"), "mine")
        self.assertEqual(set(projection.skills), EXPECTED_SKILLS)

        payload = self.mcp()
        self.assertNotIn("teamharness", payload["mcpServers"])
        self.assertIn("my-db", payload["mcpServers"])

    def test_removes_files_it_created_outright(self) -> None:
        self.projector.project(self.context())
        self.projector.unproject(self.context())

        # Nothing but the projection was ever in these files, so an empty
        # CLAUDE.md / .mcp.json left behind would still change the runtime.
        self.assertFalse((self.workspace / "CLAUDE.md").exists())
        self.assertFalse((self.workspace / ".mcp.json").exists())
        self.assertFalse((self.workspace / ".claude" / "skills").exists())

    def test_unproject_is_safe_on_a_workspace_that_was_never_projected(self) -> None:
        (self.workspace / "CLAUDE.md").write_text(USER_NOTES, encoding="utf-8")
        projection = self.projector.unproject(self.context())

        self.assertEqual(projection.files, ())
        self.assertEqual(self.read("CLAUDE.md"), USER_NOTES)

    def test_unproject_is_idempotent(self) -> None:
        self.projector.project(self.context())
        self.projector.unproject(self.context())
        second = self.projector.unproject(self.context())
        self.assertEqual(second.files, ())


class MissingAssetsTest(ProjectorTestCase):
    def test_missing_sources_warn_instead_of_raising(self) -> None:
        empty_plugin = self.tmp / "not-a-plugin"
        empty_plugin.mkdir()

        projection = self.projector.project(self.context(plugin_dir=empty_plugin))

        joined = " | ".join(projection.warnings)
        self.assertIn("TEAMS.md", joined)
        self.assertIn("remote-member.md", joined)
        self.assertIn("plugin.yaml", joined)
        self.assertIn("server.py", joined)
        self.assertEqual(projection.skills, ())

        # Degraded, but still a usable projection: the MCP entry and the
        # managed section exist so the operator can fix the package in place.
        self.assertIn("CLAUDE.md", projection.files)
        self.assertIn(".mcp.json", projection.files)
        self.assertIn("# Team Contract", self.read("CLAUDE.md"))
        self.assertIn(f"- team.name: {TEAM_NAME}", self.read("CLAUDE.md"))

    def test_missing_skill_directory_warns_and_skips(self) -> None:
        # A manifest that lists a path the package does not ship.
        partial = self.tmp / "partial-plugin"
        (partial / "prompts" / "team").mkdir(parents=True)
        (partial / "prompts" / "agent").mkdir(parents=True)
        (partial / "prompts" / "team" / "TEAMS.md").write_text("# Contract", encoding="utf-8")
        (partial / "prompts" / "agent" / "remote-member.md").write_text("# Role", encoding="utf-8")
        (partial / "skills" / "team" / "task-execution").mkdir(parents=True)
        (partial / "skills" / "team" / "task-execution" / "SKILL.md").write_text(
            "do work", encoding="utf-8"
        )
        (partial / "plugin.yaml").write_text(
            "skills:\n"
            "  team:\n"
            "    - id: task-execution\n"
            "      path: skills/team/task-execution\n"
            "      roles: [worker, remote-member]\n"
            "    - id: ghost\n"
            "      path: skills/team/ghost\n"
            "      roles: [remote-member]\n",
            encoding="utf-8",
        )

        projection = self.projector.project(self.context(plugin_dir=partial))

        self.assertEqual(projection.skills, ("task-execution",))
        self.assertTrue(any("ghost" in warning for warning in projection.warnings))

    def test_malformed_manifest_warns_and_projects_no_skills(self) -> None:
        broken = self.tmp / "broken-plugin"
        broken.mkdir()
        (broken / "plugin.yaml").write_text("skills: [oh no\n", encoding="utf-8")

        projection = self.projector.project(self.context(plugin_dir=broken))
        self.assertEqual(projection.skills, ())
        self.assertTrue(any("YAML" in warning for warning in projection.warnings))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
