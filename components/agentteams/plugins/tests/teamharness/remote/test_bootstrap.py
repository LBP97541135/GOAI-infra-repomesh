#!/usr/bin/env python3
"""Tests for the remote member bootstrap config loader.

stdlib ``unittest`` only: the bridge runs on an operator's laptop, where the
one thing that can be assumed is a Python install.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

_BRIDGE_ROOT = (
    Path(__file__).resolve().parents[3] / "teamharness" / "remote"
)
if str(_BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_ROOT))

from bridge.bootstrap import (  # noqa: E402
    BOOTSTRAP_ENV_VAR,
    DEFAULT_BOOTSTRAP_PATH,
    DEFAULT_MCP_ENV_PASSTHROUGH,
    BootstrapConfig,
    load_bootstrap,
    resolve_bootstrap_path,
)

FULL_YAML = """\
apiVersion: agentteams.io/v1beta1
kind: MemberRuntimeConfig
team:
  name: demo-team
  teamRoomId: "!team:matrix.local"
  leaderName: leader
member:
  name: worker-a
  runtimeName: worker-a-runtime
  matrixUserId: "@worker-a:matrix.local"
  personalRoomId: "!dm:matrix.local"
storage:
  bucket: agentteams-storage
  endpoint: http://minio:9000
  sharedPrefix: teams/demo-team/shared
local:
  workspace: /srv/workspaces/worker-a
"""

MINIMAL_YAML = """\
team:
  name: demo-team
  teamRoomId: "!team:matrix.local"
member:
  name: worker-a
  matrixUserId: "@worker-a:matrix.local"
local:
  workspace: /srv/ws
"""


def _norm(path: Path) -> str:
    return os.path.normpath(str(path))


class BootstrapTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, text: str, name: str = "bootstrap.yaml") -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path


class TestLoadFullFile(BootstrapTestCase):
    def test_all_fields_and_defaults(self) -> None:
        path = self.write(FULL_YAML)
        config = load_bootstrap(path, env={})

        self.assertIsInstance(config, BootstrapConfig)
        self.assertEqual(config.team_name, "demo-team")
        self.assertEqual(config.team_room_id, "!team:matrix.local")
        self.assertEqual(config.leader_name, "leader")
        self.assertEqual(config.member_name, "worker-a")
        self.assertEqual(config.runtime_name, "worker-a-runtime")
        self.assertEqual(config.matrix_user_id, "@worker-a:matrix.local")
        self.assertEqual(config.personal_room_id, "!dm:matrix.local")
        self.assertEqual(config.storage.bucket, "agentteams-storage")
        self.assertEqual(config.storage.endpoint, "http://minio:9000")
        self.assertEqual(config.storage.shared_prefix, "teams/demo-team/shared")
        self.assertEqual(config.storage.provider, "")
        self.assertEqual(_norm(config.workspace), _norm(Path("/srv/workspaces/worker-a")))
        self.assertEqual(config.source_path, path)

        # role defaults to the only role a bridge can hold
        self.assertEqual(config.role, "remote-member")
        # stateDir defaults to ~/.agentteams/remote/{member.name}
        self.assertEqual(
            _norm(config.state_dir),
            _norm(Path("~/.agentteams/remote/worker-a").expanduser()),
        )
        self.assertEqual(config.mcp_env_passthrough, DEFAULT_MCP_ENV_PASSTHROUGH)

    def test_default_passthrough_covers_matrix_and_scoped_storage(self) -> None:
        """Named explicitly, because the equality check above is tautological.

        The storage variables are what make ``filesync`` reach the member's own
        scoped MinIO user; dropping one silently degrades pull/push to the
        "alias is not configured" error with nothing to point at.
        """
        self.assertEqual(
            set(DEFAULT_MCP_ENV_PASSTHROUGH),
            {
                "AGENTTEAMS_MATRIX_URL",
                "AGENTTEAMS_WORKER_MATRIX_TOKEN",
                "AGENTTEAMS_FS_ENDPOINT",
                "AGENTTEAMS_FS_ACCESS_KEY",
                "AGENTTEAMS_FS_SECRET_KEY",
                "AGENTTEAMS_STORAGE_PREFIX",
            },
        )

    def test_driver_args_are_read_verbatim_and_default_empty(self) -> None:
        """Operator-granted runtime authority; never defaulted on their behalf."""
        self.assertEqual(load_bootstrap(self.write(MINIMAL_YAML), env={}).driver_args, ())
        path = self.write(
            MINIMAL_YAML
            + "  driverArgs:\n"
            + "    - --permission-mode\n"
            + "    - acceptEdits\n"
        )
        self.assertEqual(
            load_bootstrap(path, env={}).driver_args, ("--permission-mode", "acceptEdits")
        )

    def test_auto_join_inviters_default_to_admin_and_manager_on_own_server(self) -> None:
        c = load_bootstrap(self.write(MINIMAL_YAML), env={})
        self.assertEqual(
            c.auto_join_inviters, ("@admin:matrix.local", "@manager:matrix.local")
        )
        self.assertTrue(c.accepts_invite_from("@admin:matrix.local"))
        self.assertFalse(c.accepts_invite_from("@stranger:matrix.local"))
        self.assertFalse(c.accepts_invite_from("@admin:elsewhere.org"), "same server only")
        self.assertFalse(c.accepts_invite_from(""), "an unknown inviter is never trusted")

    def test_explicit_auto_join_inviters_replace_the_default(self) -> None:
        path = self.write(
            MINIMAL_YAML
            + "  autoJoinInviters:\n"
            + '    - "@leader:matrix.local"\n'
        )
        c = load_bootstrap(path, env={})
        self.assertEqual(c.auto_join_inviters, ("@leader:matrix.local",))
        self.assertFalse(c.accepts_invite_from("@admin:matrix.local"))

    def test_runtime_name_falls_back_to_member_name(self) -> None:
        config = load_bootstrap(self.write(MINIMAL_YAML), env={})
        self.assertEqual(config.runtime_name, "worker-a")

    def test_local_runtime_names_the_cli_and_defaults_to_unstated(self) -> None:
        """Which CLI drives this member belongs in the file, not only in argv.

        Two members on one laptop used to be distinguishable only by how each
        had been launched; nothing in either bootstrap said which CLI it meant.
        Empty means "unstated" so ``--runtime`` and then the registry default
        still decide -- it is not this loader's job to pick one.
        """
        self.assertEqual(load_bootstrap(self.write(MINIMAL_YAML), env={}).runtime, "")
        path = self.write(MINIMAL_YAML + "  runtime: codex-cli\n")
        self.assertEqual(load_bootstrap(path, env={}).runtime, "codex-cli")

    def test_local_runtime_is_independent_of_member_runtime_name(self) -> None:
        """The two ``runtime`` words name different things, and must not bleed.

        ``member.runtimeName`` is the AgentTeams agent name behind the
        ``agents/{runtimeName}/`` storage prefix; ``local.runtime`` is a driver
        registry key. Setting one must leave the other alone, or an operator who
        writes the CLI into ``runtimeName`` silently moves their storage prefix
        instead of changing runtimes.
        """
        path = self.write(FULL_YAML + "  runtime: codex-cli\n")
        config = load_bootstrap(path, env={})
        self.assertEqual(config.runtime, "codex-cli")
        self.assertEqual(config.runtime_name, "worker-a-runtime")

    def test_explicit_local_section_overrides_defaults(self) -> None:
        path = self.write(
            MINIMAL_YAML
            + "  stateDir: /var/lib/agentteams/worker-a\n"
            + "  mcpEnvPassthrough:\n"
            + "    - AGENTTEAMS_MATRIX_URL\n"
        )
        config = load_bootstrap(path, env={})
        self.assertEqual(
            _norm(config.state_dir), _norm(Path("/var/lib/agentteams/worker-a"))
        )
        self.assertEqual(config.mcp_env_passthrough, ("AGENTTEAMS_MATRIX_URL",))


class TestRequiredFields(BootstrapTestCase):
    def test_missing_matrix_user_id_names_the_field_path(self) -> None:
        path = self.write(MINIMAL_YAML.replace('  matrixUserId: "@worker-a:matrix.local"\n', ""))
        with self.assertRaises(ValueError) as ctx:
            load_bootstrap(path, env={})
        self.assertIn("member.matrixUserId", str(ctx.exception))

    def test_every_required_field_is_enforced(self) -> None:
        cases = {
            "team.name": "  name: demo-team\n",
            "team.teamRoomId": '  teamRoomId: "!team:matrix.local"\n',
            "member.name": "  name: worker-a\n",
            "local.workspace": "  workspace: /srv/ws\n",
        }
        for dotted, line in cases.items():
            with self.subTest(field=dotted):
                path = self.write(MINIMAL_YAML.replace(line, "", 1), name=f"{dotted}.yaml")
                with self.assertRaises(ValueError) as ctx:
                    load_bootstrap(path, env={})
                self.assertIn(dotted, str(ctx.exception))


class TestPathResolutionOrder(BootstrapTestCase):
    def test_explicit_path_wins_over_env(self) -> None:
        explicit = self.write(MINIMAL_YAML, name="explicit.yaml")
        from_env = self.write(
            MINIMAL_YAML.replace("worker-a", "worker-b"), name="from-env.yaml"
        )
        config = load_bootstrap(explicit, env={BOOTSTRAP_ENV_VAR: str(from_env)})
        self.assertEqual(config.member_name, "worker-a")

    def test_env_wins_over_builtin_default(self) -> None:
        from_env = self.write(
            MINIMAL_YAML.replace("worker-a", "worker-b"), name="from-env.yaml"
        )
        config = load_bootstrap(None, env={BOOTSTRAP_ENV_VAR: str(from_env)})
        self.assertEqual(config.member_name, "worker-b")
        self.assertEqual(config.source_path, from_env)

    def test_builtin_default_is_last(self) -> None:
        resolved = resolve_bootstrap_path(None, env={})
        self.assertEqual(resolved, Path(DEFAULT_BOOTSTRAP_PATH).expanduser())

    def test_env_path_expands_tilde(self) -> None:
        resolved = resolve_bootstrap_path(None, env={BOOTSTRAP_ENV_VAR: "~/custom.yaml"})
        self.assertEqual(resolved, Path("~/custom.yaml").expanduser())


class TestCredentialRedLine(BootstrapTestCase):
    def test_pasted_matrix_token_is_refused(self) -> None:
        path = self.write(MINIMAL_YAML + "  matrixToken: syt_xxx_realtoken\n")
        with self.assertRaises(ValueError) as ctx:
            load_bootstrap(path, env={})
        message = str(ctx.exception)
        self.assertIn("local.matrixToken", message)
        # the refusal must not echo the value back into logs
        self.assertNotIn("syt_xxx_realtoken", message)

    def test_secret_like_keys_are_refused(self) -> None:
        for line in (
            "gatewaySecret: abc123",
            "password: hunter2",
            "apiKey: sk-live-1",
            "api_key: sk-live-1",
            "accessKey: LTAIxxxx",
            "access_key: LTAIxxxx",
            "storageCredential: blob",
        ):
            with self.subTest(line=line):
                path = self.write(
                    MINIMAL_YAML + f"  {line}\n", name=f"{line.split(':')[0]}.yaml"
                )
                with self.assertRaises(ValueError):
                    load_bootstrap(path, env={})

    def test_secret_in_nested_section_is_refused(self) -> None:
        path = self.write(MINIMAL_YAML + "credentials:\n  matrixToken: syt_live\n")
        with self.assertRaises(ValueError) as ctx:
            load_bootstrap(path, env={})
        self.assertIn("credentials.matrixToken", str(ctx.exception))

    def test_secret_inside_a_list_is_refused(self) -> None:
        path = self.write(MINIMAL_YAML + "tokens:\n  - syt_live\n")
        with self.assertRaises(ValueError):
            load_bootstrap(path, env={})

    def test_pointer_fields_are_allowed(self) -> None:
        path = self.write(
            MINIMAL_YAML
            + "credentials:\n"
            + "  matrixTokenEnv: AGENTTEAMS_WORKER_MATRIX_TOKEN\n"
            + "  storageAccessKeyEnv: AGENTTEAMS_FS_ACCESS_KEY\n"
            + "  serviceAccountTokenPath: /var/run/secrets/token\n"
        )
        config = load_bootstrap(path, env={})
        self.assertEqual(config.member_name, "worker-a")

    def test_empty_secret_key_is_allowed(self) -> None:
        path = self.write(MINIMAL_YAML + "  matrixToken: \"\"\n")
        self.assertEqual(load_bootstrap(path, env={}).member_name, "worker-a")

    def test_default_env_passthrough_names_are_not_secrets(self) -> None:
        path = self.write(
            MINIMAL_YAML
            + "  mcpEnvPassthrough:\n"
            + "    - AGENTTEAMS_MATRIX_URL\n"
            + "    - AGENTTEAMS_WORKER_MATRIX_TOKEN\n"
        )
        config = load_bootstrap(path, env={})
        self.assertEqual(
            config.mcp_env_passthrough,
            ("AGENTTEAMS_MATRIX_URL", "AGENTTEAMS_WORKER_MATRIX_TOKEN"),
        )


class TestLocalPathResolution(BootstrapTestCase):
    def test_relative_workspace_resolves_against_the_bootstrap_file(self) -> None:
        nested = self.root / "conf"
        nested.mkdir()
        path = nested / "bootstrap.yaml"
        path.write_text(
            MINIMAL_YAML.replace("  workspace: /srv/ws\n", "  workspace: ../ws\n"),
            encoding="utf-8",
        )
        config = load_bootstrap(path, env={})
        self.assertEqual(_norm(config.workspace), _norm(self.root / "ws"))

    def test_tilde_is_expanded_in_local_paths(self) -> None:
        path = self.write(
            MINIMAL_YAML.replace("  workspace: /srv/ws\n", "  workspace: ~/ws/worker-a\n")
            + "  stateDir: ~/state/worker-a\n"
        )
        config = load_bootstrap(path, env={})
        self.assertEqual(_norm(config.workspace), _norm(Path("~/ws/worker-a").expanduser()))
        self.assertEqual(
            _norm(config.state_dir), _norm(Path("~/state/worker-a").expanduser())
        )
        self.assertNotIn("~", str(config.workspace))


class TestMalformedFiles(BootstrapTestCase):
    def test_missing_file_raises_file_not_found(self) -> None:
        missing = self.root / "nope.yaml"
        with self.assertRaises(FileNotFoundError) as ctx:
            load_bootstrap(missing, env={})
        self.assertIn("nope.yaml", str(ctx.exception))

    def test_invalid_yaml_raises_value_error(self) -> None:
        path = self.write("team: [unclosed\n")
        with self.assertRaises(ValueError):
            load_bootstrap(path, env={})

    def test_non_mapping_root_raises_value_error(self) -> None:
        path = self.write("- just\n- a list\n")
        with self.assertRaises(ValueError):
            load_bootstrap(path, env={})

    def test_unexpected_kind_raises_value_error(self) -> None:
        path = self.write("kind: SomethingElse\n" + MINIMAL_YAML)
        with self.assertRaises(ValueError) as ctx:
            load_bootstrap(path, env={})
        self.assertIn("kind", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
