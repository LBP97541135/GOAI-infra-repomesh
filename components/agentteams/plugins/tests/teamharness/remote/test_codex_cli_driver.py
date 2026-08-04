#!/usr/bin/env python3
"""Tests for the Codex CLI driver.

Stdlib ``unittest`` only, no network, no ``mock.patch``, and no real ``codex``.
The driver is exercised against a scripted Python stand-in that prints a
recorded JSONL stream -- the same technique the Claude Code driver tests use,
and the reason both drivers take an injectable ``binary``.

Every event shape asserted here was captured from a real ``codex-cli 0.145.0``
run, not invented. The four behaviours that get the most attention are the ones
a plausible-looking driver gets wrong:

- an ``error`` *item* is a diagnostic, not a failed turn;
- the answer is the **last** ``agent_message``, not the first and not all of
  them joined;
- ``thread.started`` must reach the supervisor as a ``session_ref`` event
  immediately, not at turn end;
- a clean exit with no ``turn.completed`` is a FAILED turn.

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
# exactly how the supervisor consumes it.
BRIDGE_PARENT = REPO_ROOT / "plugins" / "teamharness" / "remote"
if str(BRIDGE_PARENT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_PARENT))

from bridge.drivers.codex_cli import (  # noqa: E402
    CodexCliDriver,
    mcp_config_args,
)
from bridge.protocol import AssetContext, RuntimeDriver, TurnRequest  # noqa: E402

THREAD_ID = "019fce60-3b0b-79c2-be28-ab904c4fc282"


def frames(*objects: dict) -> str:
    return "\n".join(json.dumps(o, ensure_ascii=False) for o in objects)


# A faithful transcript of the probe run that produced this driver: the skills
# budget warning really does arrive as a completed ``error`` item on a turn
# that then succeeds.
REAL_TRANSCRIPT = frames(
    {"type": "thread.started", "thread_id": THREAD_ID},
    {"type": "turn.started"},
    {
        "type": "item.completed",
        "item": {
            "id": "item_0",
            "type": "error",
            "message": "Skill descriptions were shortened to fit the 2% skills context budget.",
        },
    },
    {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "OK"}},
    {"type": "turn.completed", "usage": {"input_tokens": 17253, "output_tokens": 5}},
)


class DriverTestCase(unittest.TestCase):
    """Builds a fake ``codex`` whose stdout is a canned JSONL stream."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="teamharness-codex-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()

    def driver(
        self,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        extra_args: tuple[str, ...] = (),
    ) -> CodexCliDriver:
        script = self.tmp / "fake_codex.py"
        script.write_text(
            "import sys\n"
            f"sys.stdout.write({stdout!r})\n"
            "sys.stdout.flush()\n"
            f"sys.stderr.write({stderr!r})\n"
            f"sys.exit({exit_code})\n",
            encoding="utf-8",
        )
        # argv[0] is a real interpreter, so nothing here depends on codex being
        # installed -- and ``--version`` still routes through the same argv.
        return CodexCliDriver(binary=(sys.executable, str(script)), extra_args=extra_args)

    def request(self, session_ref: str | None = None) -> TurnRequest:
        return TurnRequest(
            task_id="task-1",
            room_id="!room:example.org",
            prompt="do the thing",
            workspace=self.workspace,
            session_ref=session_ref,
            trigger_event_id="$evt",
        )

    def drive(self, driver: CodexCliDriver, request: TurnRequest | None = None):
        """Consume the generator the way the protocol requires.

        A bare ``for`` loop would discard the ``TurnResult`` -- the exact
        contract violation the protocol docstring warns about -- so the tests
        must not model it that way either.
        """
        events = []
        gen = driver.run_turn(request or self.request())
        try:
            while True:
                events.append(next(gen))
        except StopIteration as stop:
            return events, stop.value


class ProtocolConformanceTest(DriverTestCase):
    def test_satisfies_the_runtime_driver_protocol(self) -> None:
        driver = self.driver()
        self.assertIsInstance(driver, RuntimeDriver)
        self.assertEqual(driver.name, "codex-cli")


class RealTranscriptTest(DriverTestCase):
    def test_error_item_does_not_fail_a_completed_turn(self) -> None:
        """The captured transcript really does carry an ``error`` item."""
        events, result = self.drive(self.driver(stdout=REAL_TRANSCRIPT))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.final_text, "OK")
        self.assertEqual(result.session_ref, THREAD_ID)
        # The diagnostic is still surfaced -- suppressed, it would hide a real
        # problem; promoted, it would fail a turn that worked.
        self.assertIn("error", [e.kind for e in events])

    def test_session_ref_is_emitted_before_any_text(self) -> None:
        """The handle must be storable the moment it exists, not at turn end."""
        events, _ = self.drive(self.driver(stdout=REAL_TRANSCRIPT))
        kinds = [e.kind for e in events]
        self.assertEqual(kinds[0], "session_ref")
        self.assertEqual(events[0].text, THREAD_ID)
        self.assertLess(kinds.index("session_ref"), kinds.index("assistant_text"))


class FinalTextTest(DriverTestCase):
    def test_answer_is_the_last_agent_message_not_the_narration(self) -> None:
        # Three agent_message items, exactly as the probe produced: two of
        # narration and then the answer. Joining them would post the running
        # commentary the remote-member prompt forbids.
        stream = frames(
            {"type": "thread.started", "thread_id": THREAD_ID},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "I'll read sample.txt directly."},
            },
            {
                "type": "item.started",
                "item": {"type": "command_execution", "command": "cat sample.txt", "status": "in_progress"},
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "The shell hasn't returned yet."},
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "cat sample.txt",
                    "aggregated_output": "hello",
                    "exit_code": 0,
                    "status": "completed",
                },
            },
            {"type": "item.completed", "item": {"type": "agent_message", "text": "It said: hello"}},
            {"type": "turn.completed", "usage": {}},
        )
        events, result = self.drive(self.driver(stdout=stream))

        self.assertEqual(result.final_text, "It said: hello")
        self.assertNotIn("hasn't returned yet", result.final_text)
        # All three are still streamed, so a supervisor may show progress; only
        # the result is narrowed.
        self.assertEqual(len([e for e in events if e.kind == "assistant_text"]), 3)

    def test_command_execution_maps_to_tool_events(self) -> None:
        events, _ = self.drive(
            self.driver(
                stdout=frames(
                    {"type": "thread.started", "thread_id": THREAD_ID},
                    {
                        "type": "item.started",
                        "item": {"type": "command_execution", "command": "ls -la"},
                    },
                    {
                        "type": "item.completed",
                        "item": {"type": "command_execution", "command": "ls -la", "exit_code": 0},
                    },
                    {"type": "turn.completed"},
                )
            )
        )
        kinds = [(e.kind, e.text) for e in events if e.kind in ("tool_call", "tool_result")]
        self.assertEqual(kinds, [("tool_call", "ls -la"), ("tool_result", "ls -la")])

    def test_unknown_item_type_never_becomes_room_text(self) -> None:
        """A future Codex item type must not inject unreviewed prose."""
        events, result = self.drive(
            self.driver(
                stdout=frames(
                    {"type": "thread.started", "thread_id": THREAD_ID},
                    {
                        "type": "item.completed",
                        "item": {"type": "some_future_thing", "text": "surprise"},
                    },
                    {"type": "turn.completed"},
                )
            )
        )
        self.assertEqual(result.final_text, "")
        self.assertNotIn("assistant_text", [e.kind for e in events])


class FailureTest(DriverTestCase):
    def test_turn_failed_is_reported_with_its_message(self) -> None:
        _, result = self.drive(
            self.driver(
                stdout=frames(
                    {"type": "thread.started", "thread_id": THREAD_ID},
                    {"type": "turn.failed", "error": {"message": "model refused"}},
                )
            )
        )
        self.assertEqual(result.status, "failed")
        self.assertIn("model refused", result.error)
        # The handle still made it out, so the task can be resumed.
        self.assertEqual(result.session_ref, THREAD_ID)

    def test_clean_exit_without_a_terminal_frame_is_a_failure(self) -> None:
        """A runtime that stops without committing has not completed a turn."""
        _, result = self.drive(
            self.driver(
                stdout=frames(
                    {"type": "thread.started", "thread_id": THREAD_ID},
                    {"type": "item.completed", "item": {"type": "agent_message", "text": "half"}},
                ),
                exit_code=0,
            )
        )
        self.assertEqual(result.status, "failed")
        self.assertIn("turn.completed", result.error)
        # Accumulated text is preserved for diagnosis but never promoted into a
        # success.
        self.assertEqual(result.final_text, "half")

    def test_spawn_failure_returns_before_the_first_yield(self) -> None:
        driver = CodexCliDriver(binary=str(self.tmp / "does-not-exist"))
        events, result = self.drive(driver)
        self.assertEqual(events, [])
        self.assertEqual(result.status, "failed")
        self.assertIn("spawn failed", result.error)

    def test_non_json_stdout_is_diagnostic_not_fatal(self) -> None:
        _, result = self.drive(
            self.driver(
                stdout="npm WARN something\n"
                + frames(
                    {"type": "thread.started", "thread_id": THREAD_ID},
                    {"type": "item.completed", "item": {"type": "agent_message", "text": "fine"}},
                    {"type": "turn.completed"},
                )
            )
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.final_text, "fine")


class ArgvTest(DriverTestCase):
    """Argv assembly, where Codex differs most from Claude Code."""

    def argv(self, session_ref: str | None = None, extra: tuple[str, ...] = ()) -> list[str]:
        driver = self.driver(extra_args=extra)
        return driver._build_argv(self.request(session_ref))

    def test_fresh_turn_puts_the_prompt_last(self) -> None:
        argv = self.argv()
        self.assertIn("exec", argv)
        self.assertIn("--json", argv)
        self.assertEqual(argv[-1], "do the thing")
        self.assertNotIn("resume", argv)

    def test_resume_is_a_subcommand_and_options_precede_it(self) -> None:
        """``codex exec resume`` rejects options ``codex exec`` accepts.

        Verified against the real CLI: putting ``--sandbox`` after ``resume``
        fails with ``unexpected argument``. So every flag, including the
        operator's own, must come first.
        """
        argv = self.argv(session_ref=THREAD_ID, extra=("--sandbox", "read-only"))

        resume_at = argv.index("resume")
        self.assertEqual(argv[resume_at + 1], THREAD_ID)
        self.assertEqual(argv[-1], "do the thing")
        # Operator flags land before the subcommand, not after it.
        self.assertLess(argv.index("--sandbox"), resume_at)
        self.assertLess(argv.index("--json"), resume_at)

    def test_workspace_is_passed_as_the_working_root(self) -> None:
        argv = self.argv()
        self.assertEqual(argv[argv.index("-C") + 1], str(self.workspace))


class McpConfigArgsTest(unittest.TestCase):
    """The ``-c`` overrides that stand in for a projected MCP file."""

    def context(self, passthrough: tuple[str, ...] = ()) -> AssetContext:
        return AssetContext(
            workspace=Path("/ws"),
            role="remote-member",
            member_name="bohan-local",
            team_name="atlas-team",
            plugin_dir=Path("/plugins/teamharness"),
            mcp_env_passthrough=passthrough,
        )

    def test_declares_the_server_without_touching_any_file(self) -> None:
        args = mcp_config_args(self.context())
        joined = " ".join(args)
        self.assertIn("mcp_servers.teamharness.command=", joined)
        self.assertIn("mcp_servers.teamharness.args=", joined)
        self.assertIn("server.py", joined)
        # Every value is introduced by its own -c, as the CLI requires.
        self.assertEqual(args.count("-c"), 4)

    def test_pins_utf8_and_the_role(self) -> None:
        joined = " ".join(mcp_config_args(self.context()))
        self.assertIn('PYTHONIOENCODING = "utf-8"', joined)
        self.assertIn('AGENTTEAMS_AGENT_ROLE = "remote-member"', joined)

    def test_tells_the_server_where_the_workspace_is(self) -> None:
        """Same fix as the Claude projector, same reason: a remote member has
        no QWENPAW_WORKING_DIR, so taskflow cannot infer its workspace and
        every call fails with "workspaceDir is required"."""
        joined = " ".join(mcp_config_args(self.context()))
        self.assertIn("TEAMHARNESS_SHARED_DIR", joined)
        self.assertIn("shared", joined)

    def test_the_passthrough_set_is_inherited_by_name(self) -> None:
        """Codex hands a stdio MCP child nothing of its own environment.

        An ``env`` table is the child's *entire* environment, so the three
        literals declared there were all the TeamHarness server ever saw --
        no Matrix token, no storage prefix. ``env_vars`` is the companion
        field that inherits from the parent process by name.
        """
        args = mcp_config_args(
            self.context(
                passthrough=("AGENTTEAMS_MATRIX_URL", "AGENTTEAMS_FS_ENDPOINT")
            )
        )
        env_vars = [a for a in args if a.startswith("mcp_servers.teamharness.env_vars=")]
        self.assertEqual(len(env_vars), 1)
        self.assertIn('"AGENTTEAMS_MATRIX_URL"', env_vars[0])
        self.assertIn('"AGENTTEAMS_FS_ENDPOINT"', env_vars[0])
        # A list, not a table: Codex rejects a map here, and a list has nowhere
        # to put a value even by mistake.
        self.assertTrue(env_vars[0].endswith("]"))
        self.assertNotIn("{", env_vars[0])

    def test_a_credential_crosses_as_a_name_and_never_as_a_value(self) -> None:
        """argv is world-readable, so the name/value split is the whole defence.

        A variable *name* is not a secret -- it is the same thing the Claude
        Code projector writes as ``${VAR}``. A value would be a leak, and so
        would a ``${VAR}`` reference here, because nothing expands it.
        """
        secret = "s3cr3t-minio-password"
        previous = os.environ.get("AGENTTEAMS_FS_SECRET_KEY")
        os.environ["AGENTTEAMS_FS_SECRET_KEY"] = secret
        try:
            joined = " ".join(
                mcp_config_args(
                    self.context(
                        passthrough=(
                            "AGENTTEAMS_WORKER_MATRIX_TOKEN",
                            "AGENTTEAMS_FS_SECRET_KEY",
                        )
                    )
                )
            )
        finally:
            if previous is None:
                del os.environ["AGENTTEAMS_FS_SECRET_KEY"]
            else:
                os.environ["AGENTTEAMS_FS_SECRET_KEY"] = previous
        self.assertIn("AGENTTEAMS_FS_SECRET_KEY", joined)
        self.assertNotIn(secret, joined)
        self.assertNotIn("${", joined)

    def test_an_empty_passthrough_still_emits_a_well_formed_list(self) -> None:
        joined = " ".join(mcp_config_args(self.context()))
        self.assertIn("mcp_servers.teamharness.env_vars=[]", joined)

    def test_windows_paths_are_escaped_for_toml(self) -> None:
        ctx = AssetContext(
            workspace=Path("/ws"),
            role="remote-member",
            member_name="m",
            team_name="t",
            plugin_dir=Path("C:\\plugins\\teamharness"),
        )
        joined = " ".join(mcp_config_args(ctx))
        # A single backslash in a TOML basic string is an escape introducer; an
        # unescaped Windows path is a parse error, not a path.
        self.assertNotIn("\\p", joined.replace("\\\\", ""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
