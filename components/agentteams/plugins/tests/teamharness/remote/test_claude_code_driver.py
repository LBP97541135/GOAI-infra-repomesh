#!/usr/bin/env python3
"""Tests for the Claude Code ``RuntimeDriver``.

Stdlib ``unittest`` only, and no real ``claude`` binary: each test generates a
throwaway Python script that speaks the headless ``stream-json`` frame format
and hands it to the driver as an argv prefix (``(sys.executable, script)``).
That keeps the interesting behaviour -- early ``session_ref`` emission, resume
plumbing, dirty-line tolerance, child reaping on ``close()`` -- testable without
a subscription, a network, or a wrapper shim on ``PATH``.

Run:
    python -m unittest discover -s plugins/tests/teamharness/remote -p "test_*.py"
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[4]
# ``bridge`` is shared by every runtime, so its parent goes on sys.path --
# the same way a supervisor consumes it, and the reason bridge/ carries an
# __init__.py at all.
BRIDGE_PARENT = REPO_ROOT / "plugins" / "teamharness" / "remote"
if str(BRIDGE_PARENT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_PARENT))

from bridge.drivers.claude_code import ClaudeCodeDriver  # noqa: E402
from bridge.protocol import RuntimeDriver, TurnEvent, TurnRequest  # noqa: E402

FAKE_CLI_TEMPLATE = '''#!/usr/bin/env python3
"""Generated stand-in for the Claude Code CLI. Not part of the shipped bridge."""
import json
import sys
import time

CONFIG = json.loads(r"""__CONFIG__""")

argv = sys.argv[1:]
if "--version" in argv:
    sys.stdout.write(CONFIG["version"] + "\\n")
    raise SystemExit(0)

if CONFIG["argv_log"]:
    with open(CONFIG["argv_log"], "w", encoding="utf-8") as handle:
        json.dump(argv, handle)

for line in CONFIG["lines"]:
    sys.stdout.write(line + "\\n")
    sys.stdout.flush()
for line in CONFIG["stderr_lines"]:
    sys.stderr.write(line + "\\n")
sys.stderr.flush()
if CONFIG["sleep_seconds"]:
    time.sleep(CONFIG["sleep_seconds"])
raise SystemExit(CONFIG["exit_code"])
'''


def init_frame(session_id: str) -> str:
    """The first line every headless run emits; carries the resume handle."""
    return json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "session_id": session_id,
            "cwd": ".",
            "tools": ["Read", "Bash"],
        }
    )


def assistant_frame(session_id: str, *blocks: dict) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "session_id": session_id,
            "message": {"role": "assistant", "content": list(blocks)},
        }
    )


def tool_result_frame(session_id: str, tool_use_id: str, text: str) -> str:
    return json.dumps(
        {
            "type": "user",
            "session_id": session_id,
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
                ],
            },
        }
    )


def result_frame(session_id: str, text: str, subtype: str = "success") -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": subtype,
            "is_error": subtype != "success",
            "session_id": session_id,
            "result": text,
            "num_turns": 1,
        }
    )


class ClaudeCodeDriverTestCase(unittest.TestCase):
    """Shared scaffolding: a temp workspace plus a per-test fake CLI."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="teamharness-driver-")
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.tmp_path = Path(self._tmp)
        self.workspace = self.tmp_path / "workspace"
        self.workspace.mkdir()
        self.argv_log = self.tmp_path / "argv.json"

    # ---- helpers -----------------------------------------------------

    def make_cli(
        self,
        lines: list[str],
        *,
        exit_code: int = 0,
        sleep_seconds: float = 0,
        stderr_lines: tuple[str, ...] = (),
        version: str = "1.0.99 (Claude Code)",
        record_argv: bool = True,
    ) -> tuple[str, ...]:
        config = {
            "lines": lines,
            "exit_code": exit_code,
            "sleep_seconds": sleep_seconds,
            "stderr_lines": list(stderr_lines),
            "version": version,
            "argv_log": str(self.argv_log) if record_argv else "",
        }
        script = self.tmp_path / "fake_claude.py"
        script.write_text(
            FAKE_CLI_TEMPLATE.replace("__CONFIG__", json.dumps(config)), encoding="utf-8"
        )
        return (sys.executable, str(script))

    def make_request(self, **overrides) -> TurnRequest:
        fields = {
            "task_id": "task-1",
            "room_id": "!room:example.org",
            "prompt": "summarize the repo",
            "workspace": self.workspace,
        }
        fields.update(overrides)
        return TurnRequest(**fields)

    def drive(self, driver: ClaudeCodeDriver, request: TurnRequest):
        """Consume a whole turn, returning ``(events, result)``."""
        events: list[TurnEvent] = []
        generator = driver.run_turn(request)
        while True:
            try:
                events.append(next(generator))
            except StopIteration as stop:
                return events, stop.value

    def recorded_argv(self) -> list[str]:
        return json.loads(self.argv_log.read_text(encoding="utf-8"))


class ProtocolConformanceTests(ClaudeCodeDriverTestCase):
    def test_satisfies_runtime_driver_protocol(self) -> None:
        driver = ClaudeCodeDriver()
        self.assertEqual(driver.name, "claude-code")
        self.assertIsInstance(driver, RuntimeDriver)

    def test_probe_reports_missing_binary_without_raising(self) -> None:
        probe = ClaudeCodeDriver(binary="claude-does-not-exist-xyz").probe()
        self.assertFalse(probe.available)
        self.assertIn("not found", probe.reason)

    def test_probe_reads_version_from_the_binary(self) -> None:
        command = self.make_cli([], version="9.9.9 (Claude Code)")
        probe = ClaudeCodeDriver(binary=command).probe()
        self.assertTrue(probe.available, probe.reason)
        self.assertEqual(probe.version, "9.9.9 (Claude Code)")
        # ``--version`` must not be mistaken for a turn.
        self.assertFalse(self.argv_log.exists())


class SessionRefTests(ClaudeCodeDriverTestCase):
    def test_session_ref_is_yielded_before_the_result_frame(self) -> None:
        """The handle must survive a crash, so it cannot wait for the result."""
        command = self.make_cli(
            [
                init_frame("sess-early"),
                assistant_frame("sess-early", {"type": "text", "text": "working"}),
                result_frame("sess-early", "done"),
            ]
        )
        driver = ClaudeCodeDriver(binary=command)
        generator = driver.run_turn(self.make_request())
        first = next(generator)
        # Asserted on the *first* pull: nothing after it has been consumed yet,
        # so the driver cannot have seen the result frame.
        self.assertEqual(first.kind, "session_ref")
        self.assertEqual(first.text, "sess-early")

        events = [first]
        while True:
            try:
                events.append(next(generator))
            except StopIteration as stop:
                result = stop.value
                break
        kinds = [event.kind for event in events]
        self.assertEqual(kinds.index("session_ref"), 0)
        self.assertIn("assistant_text", kinds)
        self.assertEqual(result.status, "completed")

    def test_resume_ref_is_passed_on_the_command_line(self) -> None:
        command = self.make_cli(
            [init_frame("sess-resumed"), result_frame("sess-resumed", "ok")]
        )
        driver = ClaudeCodeDriver(binary=command)
        self.drive(driver, self.make_request(session_ref="sess-prior"))
        argv = self.recorded_argv()
        self.assertIn("--resume", argv)
        self.assertEqual(argv[argv.index("--resume") + 1], "sess-prior")
        self.assertEqual(argv[:5], ["-p", "summarize the repo", "--output-format", "stream-json", "--verbose"])

    def test_no_resume_flag_for_a_cold_turn(self) -> None:
        command = self.make_cli([init_frame("sess-cold"), result_frame("sess-cold", "ok")])
        self.drive(ClaudeCodeDriver(binary=command), self.make_request())
        self.assertNotIn("--resume", self.recorded_argv())

    def test_rotated_session_id_wins_over_the_resume_ref(self) -> None:
        """A resumed turn can be issued a fresh id; the latest one is the truth."""
        command = self.make_cli(
            [
                init_frame("sess-old"),
                assistant_frame("sess-old", {"type": "text", "text": "carrying on"}),
                result_frame("sess-new", "finished"),
            ]
        )
        driver = ClaudeCodeDriver(binary=command)
        events, result = self.drive(driver, self.make_request(session_ref="sess-old"))
        refs = [event.text for event in events if event.kind == "session_ref"]
        self.assertEqual(refs, ["sess-old", "sess-new"])
        self.assertEqual(result.session_ref, "sess-new")


class TurnOutcomeTests(ClaudeCodeDriverTestCase):
    def test_successful_turn_maps_every_frame_kind(self) -> None:
        command = self.make_cli(
            [
                init_frame("sess-ok"),
                assistant_frame("sess-ok", {"type": "text", "text": "reading files"}),
                assistant_frame(
                    "sess-ok",
                    {"type": "tool_use", "id": "tu-1", "name": "Read", "input": {"file": "a"}},
                ),
                tool_result_frame("sess-ok", "tu-1", "file contents"),
                result_frame("sess-ok", "all done"),
            ]
        )
        driver = ClaudeCodeDriver(binary=command)
        events, result = self.drive(driver, self.make_request())

        self.assertEqual(
            [event.kind for event in events],
            ["session_ref", "assistant_text", "tool_call", "tool_result"],
        )
        self.assertEqual(events[2].text, "Read")
        self.assertEqual(events[2].raw.get("id"), "tu-1")
        self.assertEqual(events[3].text, "file contents")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.final_text, "all done")
        self.assertEqual(result.session_ref, "sess-ok")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.error, "")

    def test_error_result_frame_fails_the_turn(self) -> None:
        command = self.make_cli(
            [
                init_frame("sess-err"),
                result_frame("sess-err", "hit the turn limit", subtype="error_max_turns"),
            ]
        )
        _, result = self.drive(ClaudeCodeDriver(binary=command), self.make_request())
        self.assertEqual(result.status, "failed")
        self.assertIn("turn limit", result.error)

    def test_nonzero_exit_without_a_result_frame_fails(self) -> None:
        command = self.make_cli(
            [init_frame("sess-crash")],
            exit_code=3,
            stderr_lines=("Error: workspace is not a git repository",),
        )
        _, result = self.drive(ClaudeCodeDriver(binary=command), self.make_request())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.exit_code, 3)
        self.assertTrue(result.error)
        self.assertIn("not a git repository", result.error)
        # The handle seen before the crash is still reported, so the next turn
        # can resume instead of starting cold.
        self.assertEqual(result.session_ref, "sess-crash")

    def test_garbage_stdout_lines_do_not_break_the_turn(self) -> None:
        command = self.make_cli(
            [
                "npm WARN deprecated something",
                init_frame("sess-dirty"),
                "not json at all {",
                assistant_frame("sess-dirty", {"type": "text", "text": "still fine"}),
                "[1, 2, 3]",  # valid JSON, but not a frame object
                "",
                result_frame("sess-dirty", "survived"),
            ]
        )
        events, result = self.drive(ClaudeCodeDriver(binary=command), self.make_request())
        self.assertEqual(
            [event.kind for event in events], ["session_ref", "assistant_text"]
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.final_text, "survived")

    def test_secret_env_values_are_redacted_from_errors(self) -> None:
        secret = "syt_supersecret_matrix_token_value"
        command = self.make_cli([], exit_code=1, stderr_lines=(f"auth failed for {secret}",))
        driver = ClaudeCodeDriver(binary=command, env={"AGENTTEAMS_WORKER_MATRIX_TOKEN": secret})
        _, result = self.drive(driver, self.make_request())
        self.assertEqual(result.status, "failed")
        self.assertNotIn(secret, result.error)
        self.assertIn("***", result.error)


class ReapingTests(ClaudeCodeDriverTestCase):
    def test_close_terminates_the_child(self) -> None:
        """The supervisor cuts a turn by closing the generator; no orphans."""
        command = self.make_cli([init_frame("sess-hang")], sleep_seconds=120)
        driver = ClaudeCodeDriver(binary=command)
        generator = driver.run_turn(self.make_request())
        first = next(generator)
        self.assertEqual(first.kind, "session_ref")

        # White-box on purpose: the protocol exposes no process handle, and the
        # only honest assertion here is that this exact child is gone.
        child = driver._process
        self.assertIsNotNone(child)
        self.assertIsNone(child.poll(), "fake CLI should still be sleeping")

        generator.close()  # must not raise, must not leave the child running
        self.assertIsNotNone(child.poll())
        self.assertIsNone(driver._process)

    def test_cancel_is_idempotent(self) -> None:
        command = self.make_cli([init_frame("sess-cancel")], sleep_seconds=120)
        driver = ClaudeCodeDriver(binary=command)

        driver.cancel()  # no live turn at all
        driver.cancel()

        generator = driver.run_turn(self.make_request())
        next(generator)
        child = driver._process
        driver.cancel()
        driver.cancel()  # second call lands on an already-dead child
        self.assertIsNotNone(child.poll())
        generator.close()

    def test_turn_can_be_rerun_after_a_cancel(self) -> None:
        command = self.make_cli([init_frame("sess-first")], sleep_seconds=120)
        driver = ClaudeCodeDriver(binary=command)
        generator = driver.run_turn(self.make_request())
        next(generator)
        driver.cancel()
        generator.close()

        command = self.make_cli(
            [init_frame("sess-second"), result_frame("sess-second", "second turn")]
        )
        reused = ClaudeCodeDriver(binary=command)
        _, result = self.drive(reused, self.make_request())
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.final_text, "second turn")


if __name__ == "__main__":
    unittest.main()
