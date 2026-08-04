#!/usr/bin/env python3
"""Tests for the remote-member bridge supervisor loop.

Stdlib ``unittest`` only, no ``mock.patch``, no subprocess, no network: every
external dependency (inbox poll, room send, driver, clock, sleep) is injected
through the ``Supervisor`` constructor, so the tests exercise the real
sequencing rather than a stubbed-out copy of it.

The fake drivers are written as *real generators* -- ``yield`` events, ``return``
a ``TurnResult`` -- on purpose. That is what makes the consumer contract
testable: a supervisor that consumed with a bare ``for`` loop would silently see
every turn as an empty success, and several assertions here would fail.

Run:
    python -m unittest discover -s plugins/tests/teamharness/remote -p "test_*.py"
"""

from __future__ import annotations

import logging
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from typing import Any, Callable
import unittest

REPO_ROOT = Path(__file__).resolve().parents[4]
# ``claude-code`` is not a valid identifier, so the bridge package is imported
# by putting its parent on sys.path -- exactly how the supervisor is consumed.
BRIDGE_PARENT = REPO_ROOT / "plugins" / "teamharness" / "remote" / "claude-code"
if str(BRIDGE_PARENT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_PARENT))

from bridge import supervisor as sup  # noqa: E402
from bridge.bootstrap import BootstrapConfig  # noqa: E402
from bridge.dedup import InboxState  # noqa: E402
from bridge.protocol import AssetProjection, DriverProbe, TurnEvent, TurnResult  # noqa: E402
from bridge.session_store import SessionStore  # noqa: E402

TEAM_ROOM = "!team:example.org"
PERSONAL_ROOM = "!personal:example.org"
OTHER_ROOM = "!elsewhere:example.org"
MEMBER_ID = "@member:example.org"


def setUpModule() -> None:
    # Keep expected warnings out of the test report without disabling them:
    # assertLogs installs its own handler on this logger regardless.
    logging.getLogger(sup.LOGGER_NAME).addHandler(logging.NullHandler())
    logging.getLogger(sup.LOGGER_NAME).propagate = False


# ---- fakes -----------------------------------------------------------


class ScriptedDriver:
    """A ``RuntimeDriver`` whose turn is a real generator."""

    name = "scripted"

    def __init__(
        self,
        events: tuple[TurnEvent, ...] = (),
        result: TurnResult | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self.events = list(events)
        self.result = result if result is not None else TurnResult(
            status="completed", final_text="ok"
        )
        self.raises = raises
        self.request: Any = None
        self.cancelled = False

    def probe(self) -> DriverProbe:
        return DriverProbe(available=True, binary="fake")

    def run_turn(self, request: Any) -> Any:
        self.request = request
        for event in self.events:
            yield event
        if self.raises is not None:
            raise self.raises
        return self.result

    def cancel(self) -> None:
        self.cancelled = True


class BlockingDriver:
    """Yields once, then parks until ``cancel`` releases it.

    Models the real shape of a cancelled Claude Code turn: the child dies, the
    stream ends without a result frame, and the driver reports ``failed``. Only
    the supervisor knows it was a deadline.
    """

    name = "blocking"

    def __init__(self) -> None:
        self.released = threading.Event()
        self.cancelled = False
        self.request: Any = None

    def probe(self) -> DriverProbe:
        return DriverProbe(available=True, binary="fake")

    def run_turn(self, request: Any) -> Any:
        self.request = request
        yield TurnEvent(kind="assistant_text", text="working on it")
        self.released.wait(10)
        return TurnResult(status="failed", error="runtime exited without a result frame")

    def cancel(self) -> None:
        self.cancelled = True
        self.released.set()


class DriverFactory:
    def __init__(self, *drivers: Any) -> None:
        self._queue = list(drivers)
        self.made: list[Any] = []

    def __call__(self) -> Any:
        driver = self._queue.pop(0) if self._queue else ScriptedDriver()
        self.made.append(driver)
        return driver


class FakeInbox:
    """Scripted ``inbox_tool.poll`` wrapper: ``(arguments) -> result dict``."""

    def __init__(
        self,
        *polls: dict[str, Any],
        backfill: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.polls = list(polls)
        self.backfill = backfill
        self.calls: list[dict[str, Any]] = []
        self.idle = batch(next_batch="s-idle")

    def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(arguments))
        action = arguments.get("action")
        if action == "backfill":
            if self.backfill is None:
                return {"ok": True, "action": "backfill", "events": [], "nextFrom": ""}
            return self.backfill(arguments)
        if action == "rooms":
            # Must not fall through to the scripted batches: a membership
            # lookup is not a poll and would eat the next one.
            return {"ok": True, "action": "rooms", "rooms": []}
        if action == "join":
            return {"ok": True, "action": "join", "roomId": arguments.get("roomId", "")}
        if self.polls:
            return self.polls.pop(0)
        return dict(self.idle)

    @property
    def poll_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c.get("action") == "poll"]

    @property
    def backfill_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c.get("action") == "backfill"]


class FakeSender:
    def __init__(self, raises: BaseException | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.raises = raises

    def __call__(
        self, room_id: str, body: str, thread_root_id: str = "", txn_id: str = ""
    ) -> str:
        self.sent.append(
            {
                "room_id": room_id,
                "body": body,
                "thread_root_id": thread_root_id,
                "txn_id": txn_id,
            }
        )
        if self.raises is not None:
            raise self.raises
        return f"$sent{len(self.sent)}"


# ---- fixtures --------------------------------------------------------


def event(
    event_id: str,
    *,
    room_id: str = TEAM_ROOM,
    body: str = f"{MEMBER_ID} do the thing",
    ts: int = 1000,
    kind: str = "text",
    mentions_me: bool = True,
    thread_root: str = "",
) -> dict[str, Any]:
    return {
        "eventId": event_id,
        "roomId": room_id,
        "sender": "@leader:example.org",
        "ts": ts,
        "kind": kind,
        "body": body,
        "mentionsMe": mentions_me,
        "threadRootId": thread_root,
        "url": "",
    }


def batch(
    events: list[dict[str, Any]] | None = None,
    gaps: list[dict[str, Any]] | None = None,
    next_batch: str = "s1",
    ok: bool = True,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "tool": "inbox",
        "action": "poll",
        "events": list(events or []),
        "gaps": list(gaps or []),
        "nextBatch": next_batch,
    }


class SupervisorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="supervisor-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.config = BootstrapConfig(
            team_name="alpha",
            team_room_id=TEAM_ROOM,
            member_name="member",
            matrix_user_id=MEMBER_ID,
            workspace=self.tmp / "workspace",
            state_dir=self.tmp / "state",
            personal_room_id=PERSONAL_ROOM,
            # load_bootstrap derives this; a directly-constructed config
            # defaults to empty (fail closed), so state it here.
            auto_join_inviters=("@admin:example.org", "@manager:example.org"),
        )
        self.state = InboxState(self.tmp / "state" / "inbox.json")
        self.sessions = SessionStore(self.tmp / "state" / "sessions.json", now=lambda: "T0")
        self.sender = FakeSender()
        self.slept: list[float] = []

    def make(
        self,
        inbox: FakeInbox,
        factory: DriverFactory | None = None,
        sender: FakeSender | None = None,
        turn_timeout: float = 30.0,
        **kwargs: Any,
    ) -> sup.Supervisor:
        return sup.Supervisor(
            config=self.config,
            inbox_state=self.state,
            session_store=self.sessions,
            driver_factory=factory or DriverFactory(),
            poll_fn=inbox,
            send_fn=sender or self.sender,
            sleep_fn=self.slept.append,
            now_fn=lambda: 0.0,
            turn_timeout_seconds=turn_timeout,
            **kwargs,
        )

    def baseline(self, cursor: str = "s0", seen: tuple[str, ...] = ()) -> None:
        """Skip the first-run baseline so a test can exercise execution."""
        self.state.ack(cursor, seen)


# ---- 1. first-run baseline -------------------------------------------


class FirstRunTest(SupervisorTestCase):
    def test_first_batch_is_baseline_only(self) -> None:
        inbox = FakeInbox(batch([event("$a"), event("$b", ts=1001)], next_batch="s1"))
        factory = DriverFactory()
        supervisor = self.make(inbox, factory)

        self.assertTrue(self.state.first_run)
        supervisor.run_once()

        self.assertEqual(factory.made, [], "no turn may run on the baseline batch")
        self.assertEqual(self.sender.sent, [], "nothing is forwarded on the baseline batch")
        self.assertEqual(self.state.cursor, "s1")
        self.assertFalse(self.state.first_run)
        self.assertTrue(self.state.is_seen("$a"))
        self.assertTrue(self.state.is_seen("$b"))
        self.assertEqual(
            self.state.watermark_ts, 1001,
            "the baseline ack plants the backfill time floor",
        )

    def test_first_poll_uses_the_empty_cursor(self) -> None:
        inbox = FakeInbox(batch([], next_batch="s1"))
        self.make(inbox).run_once()
        self.assertEqual(inbox.poll_calls[0]["since"], "")
        self.assertTrue(inbox.poll_calls[0]["mentionsOnly"])
        self.assertEqual(inbox.poll_calls[0]["rooms"], [TEAM_ROOM, PERSONAL_ROOM])


# ---- 2. mention -> turn -> forward ------------------------------------


class MentionTurnTest(SupervisorTestCase):
    def test_mention_runs_a_turn_and_forwards_the_final_text(self) -> None:
        self.baseline()
        driver = ScriptedDriver(
            events=(
                TurnEvent(kind="session_ref", text="sess-1"),
                TurnEvent(kind="assistant_text", text="partial"),
            ),
            result=TurnResult(status="completed", final_text="the answer"),
        )
        factory = DriverFactory(driver)
        inbox = FakeInbox(batch([event("$trigger")], next_batch="s2"))
        supervisor = self.make(inbox, factory)

        supervisor.run_once()

        self.assertEqual(len(factory.made), 1)
        request = driver.request
        self.assertEqual(request.task_id, "$trigger")
        self.assertEqual(request.room_id, TEAM_ROOM)
        self.assertEqual(request.trigger_event_id, "$trigger")
        self.assertEqual(request.workspace, self.config.workspace)
        self.assertIsNone(request.session_ref)
        # The mention prefix is not part of the instruction.
        self.assertEqual(request.prompt, "do the thing")

        self.assertEqual(len(self.sender.sent), 1)
        sent = self.sender.sent[0]
        self.assertEqual(sent["body"], "the answer")
        self.assertEqual(sent["room_id"], TEAM_ROOM)
        # No thread yet -> the trigger itself becomes the thread root.
        self.assertEqual(sent["thread_root_id"], "$trigger")

        self.assertEqual(self.state.cursor, "s2")
        self.assertEqual(self.sessions.get("$trigger").last_status, "completed")
        self.assertEqual(self.sessions.get("$trigger").turn_count, 1)

    def test_thread_reply_targets_the_thread_root(self) -> None:
        self.baseline()
        inbox = FakeInbox(batch([event("$second", thread_root="$root")], next_batch="s2"))
        driver = ScriptedDriver(result=TurnResult(status="completed", final_text="done"))
        self.make(inbox, DriverFactory(driver)).run_once()

        self.assertEqual(driver.request.task_id, "$root")
        self.assertEqual(self.sender.sent[0]["thread_root_id"], "$root")

    def test_empty_final_text_still_reports_completion(self) -> None:
        self.baseline()
        inbox = FakeInbox(batch([event("$t")], next_batch="s2"))
        driver = ScriptedDriver(result=TurnResult(status="completed", final_text="   "))
        self.make(inbox, DriverFactory(driver)).run_once()
        self.assertEqual(self.sender.sent[0]["body"], sup.COMPLETED_NOTICE)

    def test_txn_id_is_derived_from_task_and_trigger(self) -> None:
        self.baseline()
        inbox = FakeInbox(batch([event("$t")], next_batch="s2"))
        self.make(inbox, DriverFactory(ScriptedDriver())).run_once()

        expected = sup.forward_txn_id("$t", "$t")
        self.assertEqual(self.sender.sent[0]["txn_id"], expected)
        # Deterministic across calls and processes -- this is what makes a
        # replayed forward a Matrix no-op instead of a duplicate message.
        self.assertEqual(expected, sup.forward_txn_id("$t", "$t"))
        self.assertEqual(len(expected), 32)
        self.assertNotEqual(expected, sup.forward_txn_id("$t", "$other"))
        self.assertNotEqual(expected, sup.forward_txn_id("$other", "$t"))

    def test_non_triggers_are_ignored_but_still_acked(self) -> None:
        self.baseline()
        factory = DriverFactory()
        events = [
            event("$a", mentions_me=False),
            event("$b", kind="file", ts=1001),
            event("$c", room_id=OTHER_ROOM, ts=1002),
        ]
        inbox = FakeInbox(batch(events, next_batch="s2"))
        self.make(inbox, factory).run_once()

        self.assertEqual(factory.made, [])
        self.assertEqual(self.sender.sent, [])
        self.assertEqual(self.state.cursor, "s2")
        for event_id in ("$a", "$b", "$c"):
            self.assertTrue(self.state.is_seen(event_id))

    def test_personal_room_mention_triggers(self) -> None:
        self.baseline()
        inbox = FakeInbox(batch([event("$p", room_id=PERSONAL_ROOM)], next_batch="s2"))
        factory = DriverFactory()
        self.make(inbox, factory).run_once()
        self.assertEqual(len(factory.made), 1)
        self.assertEqual(self.sender.sent[0]["room_id"], PERSONAL_ROOM)


# ---- 3. session continuity -------------------------------------------


class SessionContinuityTest(SupervisorTestCase):
    def test_second_message_in_a_thread_resumes_the_session(self) -> None:
        self.baseline()
        first = ScriptedDriver(
            events=(TurnEvent(kind="session_ref", text="sess-abc"),),
            # Deliberately empty session_ref in the *result*: the only way the
            # store can know the handle is the mid-stream event.
            result=TurnResult(status="completed", final_text="first answer"),
        )
        second = ScriptedDriver(result=TurnResult(status="completed", final_text="second answer"))
        factory = DriverFactory(first, second)
        inbox = FakeInbox(
            batch([event("$root")], next_batch="s2"),
            batch([event("$follow", ts=2000, thread_root="$root")], next_batch="s3"),
        )
        supervisor = self.make(inbox, factory)

        supervisor.run_once()
        self.assertEqual(self.sessions.resume_ref("$root"), "sess-abc")

        supervisor.run_once()
        self.assertEqual(second.request.task_id, "$root")
        self.assertEqual(second.request.session_ref, "sess-abc")
        self.assertEqual(self.sessions.get("$root").turn_count, 2)

    def test_session_ref_is_bound_even_when_the_turn_fails(self) -> None:
        self.baseline()
        driver = ScriptedDriver(
            events=(TurnEvent(kind="session_ref", text="sess-mid"),),
            result=TurnResult(status="failed", error="boom"),
        )
        self.make(FakeInbox(batch([event("$t")], next_batch="s2")), DriverFactory(driver)).run_once()
        self.assertEqual(self.sessions.resume_ref("$t"), "sess-mid")

    def test_a_second_thread_in_the_same_room_gets_its_own_session(self) -> None:
        self.baseline()
        one = ScriptedDriver(events=(TurnEvent(kind="session_ref", text="sess-1"),))
        two = ScriptedDriver(events=(TurnEvent(kind="session_ref", text="sess-2"),))
        inbox = FakeInbox(
            batch([event("$one"), event("$two", ts=1001)], next_batch="s2"),
        )
        self.make(inbox, DriverFactory(one, two)).run_once()

        self.assertIsNone(one.request.session_ref)
        self.assertIsNone(two.request.session_ref, "sessions are keyed by task, not by room")
        self.assertEqual(self.sessions.resume_ref("$one"), "sess-1")
        self.assertEqual(self.sessions.resume_ref("$two"), "sess-2")


# ---- 4. redelivery ---------------------------------------------------


class RedeliveryTest(SupervisorTestCase):
    def test_seen_set_blocks_a_redelivered_event(self) -> None:
        self.baseline()
        factory = DriverFactory()
        inbox = FakeInbox(
            batch([event("$dup")], next_batch="s2"),
            batch([event("$dup")], next_batch="s3"),
        )
        supervisor = self.make(inbox, factory)

        supervisor.run_once()
        supervisor.run_once()

        self.assertEqual(len(factory.made), 1, "the second delivery must not run a turn")
        self.assertEqual(len(self.sender.sent), 1)
        self.assertEqual(self.state.cursor, "s3")

    def test_turn_ledger_blocks_a_replay_the_seen_set_missed(self) -> None:
        # A crash between settling the turn and acking the batch: the ledger
        # remembers the turn, the seen-set never learned the event id.
        self.baseline()
        self.state.claim_turn("$dup", "$dup")
        self.state.settle_turn("$dup", "$dup", "completed")
        self.assertFalse(self.state.is_seen("$dup"))

        factory = DriverFactory()
        inbox = FakeInbox(batch([event("$dup")], next_batch="s2"))
        self.make(inbox, factory).run_once()

        self.assertEqual(factory.made, [], "a terminal ledger entry refuses the replay")
        self.assertEqual(self.sender.sent, [])
        self.assertEqual(self.state.cursor, "s2")

    def test_in_flight_ledger_entry_is_retried(self) -> None:
        # The previous bridge died mid-turn: the entry is non-terminal, so the
        # work must be picked back up rather than dropped.
        self.baseline()
        self.state.claim_turn("$t", "$t")
        self.state.settle_turn("$t", "$t", "timeout")

        factory = DriverFactory()
        self.make(FakeInbox(batch([event("$t")], next_batch="s2")), factory).run_once()
        self.assertEqual(len(factory.made), 1)


# ---- 4b. team room invites -------------------------------------------


class InviteTest(SupervisorTestCase):
    """The controller invites; a containerless member must accept for itself."""

    ADMIN = "@admin:example.org"

    def _invite_batch(self, inviter: str, next_batch: str = "s1") -> dict[str, Any]:
        b = batch([], next_batch=next_batch)
        b["invites"] = [{"roomId": OTHER_ROOM, "inviter": inviter, "name": "Team: alpha"}]
        return b

    def test_trusted_invite_is_joined_and_the_room_becomes_answerable(self) -> None:
        self.baseline()
        joins: list[dict[str, Any]] = []

        class JoiningInbox(FakeInbox):
            def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
                if arguments.get("action") == "join":
                    joins.append(dict(arguments))
                    return {"ok": True, "action": "join", "roomId": arguments["roomId"]}
                return super().__call__(arguments)

        inbox = JoiningInbox(self._invite_batch(self.ADMIN))
        supervisor = self.make(inbox)
        supervisor.run_once()

        self.assertEqual([j["roomId"] for j in joins], [OTHER_ROOM])
        # A statically-configured room list would filter out the very room the
        # controller just added this member to.
        self.assertIn(OTHER_ROOM, supervisor.rooms())
        self.assertTrue(supervisor._is_trigger(event("$x", room_id=OTHER_ROOM)))

    def test_membership_is_read_from_the_server_at_start(self) -> None:
        """An invite is seen once; membership must survive the next restart."""

        class MembershipInbox(FakeInbox):
            def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
                if arguments.get("action") == "rooms":
                    return {"ok": True, "action": "rooms", "rooms": [OTHER_ROOM]}
                return super().__call__(arguments)

        self.baseline()
        # No invite in this batch: the join already happened in a past run.
        supervisor = self.make(MembershipInbox(batch([], next_batch="s2")))
        supervisor.run(once=True)
        self.assertIn(OTHER_ROOM, supervisor.rooms())
        self.assertTrue(supervisor._is_trigger(event("$x", room_id=OTHER_ROOM)))

    def test_untrusted_invite_is_refused(self) -> None:
        self.baseline()
        inbox = FakeInbox(self._invite_batch("@stranger:example.org"))
        supervisor = self.make(inbox)
        with self.assertLogs(sup.LOGGER_NAME, level="WARNING") as logs:
            supervisor.run_once()
        self.assertEqual(inbox.calls[0].get("action"), "poll", "no join was attempted")
        self.assertTrue(any("untrusted" in line for line in logs.output))
        self.assertNotIn(OTHER_ROOM, supervisor.rooms())

    def test_invites_are_accepted_on_the_first_run_too(self) -> None:
        """A member deaf until its second poll would miss its own team room."""
        joined: list[str] = []

        class JoiningInbox(FakeInbox):
            def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
                if arguments.get("action") == "join":
                    joined.append(arguments["roomId"])
                    return {"ok": True, "action": "join", "roomId": arguments["roomId"]}
                return super().__call__(arguments)

        self.assertTrue(self.state.first_run)
        self.make(JoiningInbox(self._invite_batch(self.ADMIN))).run_once()
        self.assertEqual(joined, [OTHER_ROOM])


# ---- 5. gap backfill -------------------------------------------------


class BackfillTest(SupervisorTestCase):
    def test_backfill_stops_at_a_known_event_and_runs_the_recovered_turn(self) -> None:
        self.baseline(seen=("$known",))
        pages = {
            "p1": {
                "ok": True,
                "action": "backfill",
                "roomId": TEAM_ROOM,
                "events": [event("$missed", ts=500), event("$known", ts=400)],
                "nextFrom": "p2",
            }
        }
        inbox = FakeInbox(
            batch([], gaps=[{"roomId": TEAM_ROOM, "prevBatch": "p1"}], next_batch="s2"),
            backfill=lambda args: pages[args["from"]],
        )
        factory = DriverFactory()
        self.make(inbox, factory).run_once()

        self.assertEqual(len(inbox.backfill_calls), 1, "stops as soon as it meets known history")
        self.assertEqual(inbox.backfill_calls[0]["roomId"], TEAM_ROOM)
        self.assertEqual(inbox.backfill_calls[0]["from"], "p1")
        self.assertEqual(len(factory.made), 1)
        self.assertEqual(factory.made[0].request.task_id, "$missed")
        self.assertTrue(self.state.is_seen("$missed"))

    def test_backfill_stops_at_the_ack_watermark(self) -> None:
        """Sparse mentions must not let a backfill resurrect pre-baseline history.

        The seen-set only holds mention events, so a gap in a mostly-unmentioned
        room can page far past the previous cursor without meeting a known id.
        The ack watermark is the time floor that stops it.
        """
        self.baseline()
        self.state.ack("s0", (), watermark_ts=1000)
        # Watermark must survive a bridge restart to be worth anything.
        self.assertEqual(InboxState(self.tmp / "state" / "inbox.json").watermark_ts, 1000)

        pages = {
            "p1": {
                "ok": True,
                "action": "backfill",
                "roomId": TEAM_ROOM,
                # $fresh is genuinely gapped; $ancient is a pre-baseline mention
                # that was never seen because the baseline batch never held it.
                "events": [event("$fresh", ts=1200), event("$ancient", ts=900)],
                "nextFrom": "p2",
            }
        }
        inbox = FakeInbox(
            batch([], gaps=[{"roomId": TEAM_ROOM, "prevBatch": "p1"}], next_batch="s2"),
            backfill=lambda args: pages[args["from"]],
        )
        factory = DriverFactory()
        self.make(inbox, factory).run_once()

        self.assertEqual(len(inbox.backfill_calls), 1, "the watermark counts as known territory")
        self.assertEqual([d.request.task_id for d in factory.made], ["$fresh"])
        self.assertFalse(
            self.state.is_seen("$ancient"),
            "pre-baseline history is skipped by time, not executed and recorded",
        )

    def test_backfill_stops_when_history_is_exhausted(self) -> None:
        self.baseline()
        pages = {
            "p1": {
                "ok": True,
                "action": "backfill",
                "events": [event("$old1", ts=500)],
                "nextFrom": "p2",
            },
            "p2": {
                "ok": True,
                "action": "backfill",
                "events": [event("$old2", ts=400)],
                "nextFrom": "",
            },
        }
        inbox = FakeInbox(
            batch([], gaps=[{"roomId": TEAM_ROOM, "prevBatch": "p1"}], next_batch="s2"),
            backfill=lambda args: pages[args["from"]],
        )
        factory = DriverFactory()
        self.make(inbox, factory).run_once()

        self.assertEqual(len(inbox.backfill_calls), 2)
        self.assertEqual([d.request.task_id for d in factory.made], ["$old2", "$old1"])

    def test_backfill_page_limit_warns_instead_of_looping(self) -> None:
        self.baseline()
        inbox = FakeInbox(
            batch([], gaps=[{"roomId": TEAM_ROOM, "prevBatch": "p0"}], next_batch="s2"),
            backfill=lambda args: {
                "ok": True,
                "action": "backfill",
                "events": [],
                "nextFrom": args["from"] + "x",
            },
        )
        supervisor = self.make(inbox, backfill_page_limit=4)
        with self.assertLogs(sup.LOGGER_NAME, level="WARNING") as captured:
            supervisor.run_once()

        self.assertEqual(len(inbox.backfill_calls), 4)
        self.assertTrue(
            any("page limit" in line for line in captured.output),
            f"the truncated backfill must be surfaced, got {captured.output}",
        )
        self.assertEqual(self.state.cursor, "s2")

    def test_failed_backfill_page_does_not_abort_the_batch(self) -> None:
        self.baseline()
        inbox = FakeInbox(
            batch([event("$live")], gaps=[{"roomId": TEAM_ROOM, "prevBatch": "p1"}], next_batch="s2"),
            backfill=lambda args: {"ok": False, "error": "matrix messages failed: HTTP 502"},
        )
        factory = DriverFactory()
        with self.assertLogs(sup.LOGGER_NAME, level="WARNING"):
            self.make(inbox, factory).run_once()

        self.assertEqual([d.request.task_id for d in factory.made], ["$live"])
        self.assertEqual(self.state.cursor, "s2")


# ---- 6. failure reporting --------------------------------------------


class FailureTest(SupervisorTestCase):
    STDERR = "Traceback: /home/op/.claude/.credentials.json missing; TOKEN=syt_live_secret"

    def test_failed_turn_reports_a_canned_phrase_without_details(self) -> None:
        self.baseline()
        driver = ScriptedDriver(result=TurnResult(status="failed", error=self.STDERR))
        inbox = FakeInbox(batch([event("$t")], next_batch="s2"))
        with self.assertLogs(sup.LOGGER_NAME, level="WARNING"):
            self.make(inbox, DriverFactory(driver)).run_once()

        body = self.sender.sent[0]["body"]
        self.assertEqual(body, sup.STATUS_NOTICE["failed"])
        self.assertLessEqual(len(body), 200)
        for leak in ("Traceback", "credentials", "syt_live_secret", "/home/op"):
            self.assertNotIn(leak, body)

        # Terminal: a redelivery of the same trigger is refused.
        self.assertFalse(self.state.claim_turn("$t", "$t").granted)

    def test_driver_exception_is_a_failed_turn_not_a_crash(self) -> None:
        self.baseline()
        driver = ScriptedDriver(raises=RuntimeError("spawn exploded"))
        inbox = FakeInbox(batch([event("$t")], next_batch="s2"))
        with self.assertLogs(sup.LOGGER_NAME, level="WARNING"):
            self.make(inbox, DriverFactory(driver)).run_once()

        self.assertEqual(self.sender.sent[0]["body"], sup.STATUS_NOTICE["failed"])
        self.assertNotIn("spawn exploded", self.sender.sent[0]["body"])
        self.assertEqual(self.state.cursor, "s2")

    def test_driver_that_returns_nothing_is_a_failed_turn(self) -> None:
        # The trap the generator contract warns about: a bare ``for`` loop would
        # read this as an empty, successful turn.
        self.baseline()

        class NoResultDriver(ScriptedDriver):
            def run_turn(self, request: Any) -> Any:
                self.request = request
                yield TurnEvent(kind="assistant_text", text="text without an outcome")

        inbox = FakeInbox(batch([event("$t")], next_batch="s2"))
        with self.assertLogs(sup.LOGGER_NAME, level="WARNING"):
            self.make(inbox, DriverFactory(NoResultDriver())).run_once()

        self.assertEqual(self.sender.sent[0]["body"], sup.STATUS_NOTICE["failed"])
        self.assertEqual(self.sessions.get("$t").last_status, "failed")

    def test_send_failure_does_not_lose_the_settlement(self) -> None:
        self.baseline()
        sender = FakeSender(raises=OSError("matrix unreachable"))
        inbox = FakeInbox(batch([event("$t")], next_batch="s2"))
        with self.assertLogs(sup.LOGGER_NAME, level="ERROR"):
            self.make(inbox, DriverFactory(), sender=sender).run_once()

        self.assertEqual(self.sessions.get("$t").last_status, "completed")
        self.assertFalse(self.state.claim_turn("$t", "$t").granted)


# ---- 7. deadline -----------------------------------------------------


class TimeoutTest(SupervisorTestCase):
    def test_deadline_cancels_the_driver_and_synthesizes_timeout(self) -> None:
        self.baseline()
        driver = BlockingDriver()
        inbox = FakeInbox(batch([event("$slow")], next_batch="s2"))
        supervisor = self.make(inbox, DriverFactory(driver), turn_timeout=0.25)

        with self.assertLogs(sup.LOGGER_NAME, level="WARNING"):
            supervisor.run_once()

        self.assertTrue(driver.cancelled, "the deadline must reach the driver")
        self.assertEqual(self.sender.sent[0]["body"], sup.STATUS_NOTICE["timeout"])
        self.assertEqual(self.sessions.get("$slow").last_status, "timeout")

        # timeout is not terminal: the same trigger can be retried.
        claim = self.state.claim_turn("$slow", "$slow")
        self.assertTrue(claim.granted)

    def test_timed_out_trigger_is_retried_on_redelivery(self) -> None:
        self.baseline()
        factory = DriverFactory(BlockingDriver())
        # The redelivery carries the same event id but the seen-set only learns
        # it at ack time, so this models the crash-before-ack replay.
        inbox = FakeInbox(batch([event("$slow")], next_batch="s2"))
        supervisor = self.make(inbox, factory, turn_timeout=0.25)
        with self.assertLogs(sup.LOGGER_NAME, level="WARNING"):
            supervisor.run_once()

        state2 = InboxState(self.tmp / "state" / "inbox.json")
        state2.ack("s2", ())  # fresh process, same file, event never marked seen
        self.assertTrue(state2.claim_turn("$slow", "$slow").granted)


# ---- 8. poll failure backoff -----------------------------------------


class BackoffTest(SupervisorTestCase):
    def test_failed_polls_back_off_without_moving_the_cursor(self) -> None:
        inbox = FakeInbox(
            {"ok": False, "tool": "inbox", "action": "poll", "error": "matrix sync failed: HTTP 502"},
            {"ok": False, "tool": "inbox", "action": "poll", "error": "matrix sync failed: HTTP 502"},
            batch([], next_batch="s1"),
        )
        supervisor = self.make(inbox)

        with self.assertLogs(sup.LOGGER_NAME, level="WARNING"):
            self.assertFalse(supervisor.run_once())
            self.assertFalse(supervisor.run_once())

        self.assertEqual(self.slept, [1.0, 2.0])
        self.assertEqual(self.state.cursor, "", "a failed poll must not advance the cursor")
        self.assertTrue(self.state.first_run)

        self.assertTrue(supervisor.run_once())
        self.assertEqual(self.slept, [1.0, 2.0], "a successful poll does not sleep")
        self.assertEqual(self.state.cursor, "s1")

    def test_backoff_resets_after_a_success(self) -> None:
        failure = {"ok": False, "error": "nope"}
        inbox = FakeInbox(failure, batch([], next_batch="s1"), dict(failure))
        supervisor = self.make(inbox)
        with self.assertLogs(sup.LOGGER_NAME, level="WARNING"):
            supervisor.run_once()
            supervisor.run_once()
            supervisor.run_once()
        self.assertEqual(self.slept, [1.0, 1.0])

    def test_poll_exception_is_treated_as_a_failed_poll(self) -> None:
        class Exploding(FakeInbox):
            def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
                raise OSError("dns is down")

        supervisor = self.make(Exploding())
        with self.assertLogs(sup.LOGGER_NAME, level="WARNING"):
            self.assertFalse(supervisor.run_once())
        self.assertEqual(self.slept, [1.0])
        self.assertEqual(self.state.cursor, "")


# ---- 9. run loop and shutdown ----------------------------------------


class RunLoopTest(SupervisorTestCase):
    def test_once_runs_a_single_cycle(self) -> None:
        inbox = FakeInbox(batch([], next_batch="s1"), batch([], next_batch="s2"))
        self.make(inbox).run(once=True)
        self.assertEqual(len(inbox.poll_calls), 1)
        self.assertEqual(self.state.cursor, "s1")

    def test_probe_driver_goes_through_the_factory(self) -> None:
        factory = DriverFactory()
        self.assertTrue(self.make(FakeInbox(), factory).probe_driver().available)
        self.assertEqual(len(factory.made), 1)

    def test_loop_exits_when_stop_is_requested(self) -> None:
        supervisor: sup.Supervisor

        class StoppingInbox(FakeInbox):
            def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
                result = super().__call__(arguments)
                if len(self.poll_calls) >= 3:
                    supervisor.request_stop("test")
                return result

        inbox = StoppingInbox()
        inbox.idle = batch([], next_batch="s1")
        supervisor = self.make(inbox)
        supervisor.run()
        self.assertEqual(len(inbox.poll_calls), 3)

    def test_shutdown_cancels_the_turn_and_leaves_the_batch_uncommitted(self) -> None:
        self.baseline()
        driver = BlockingDriver()
        supervisor: sup.Supervisor
        inbox = FakeInbox(batch([event("$t")], next_batch="s2"))
        supervisor = self.make(inbox, DriverFactory(driver), turn_timeout=30.0)

        stopper = threading.Timer(0.25, lambda: supervisor.request_stop("SIGTERM"))
        stopper.daemon = True
        stopper.start()
        try:
            self.assertFalse(supervisor.run_once())
        finally:
            stopper.cancel()

        self.assertTrue(driver.cancelled)
        self.assertEqual(self.sender.sent[0]["body"], sup.STATUS_NOTICE["cancelled"])
        self.assertEqual(self.sessions.get("$t").last_status, "cancelled")
        self.assertEqual(self.state.cursor, "s0", "an interrupted batch is not acked")
        self.assertFalse(self.state.is_seen("$t"))
        # A shutdown-interrupted turn is a controlled crash, not a decided
        # outcome: it settles non-terminal ("interrupted"), so the replayed
        # batch on the next start re-claims and re-runs it instead of being
        # refused as a duplicate forever.
        self.assertTrue(
            self.state.claim_turn("$t", "$t").granted,
            "a shutdown-interrupted turn must stay retryable on restart",
        )


# ---- 10. asset projection --------------------------------------------


class RecordingProjector:
    """An ``AssetProjector`` that records its calls, or raises on demand."""

    name = "recording"

    def __init__(self, warnings: tuple[str, ...] = (), raises: Exception | None = None) -> None:
        self.contexts: list[Any] = []
        self._warnings = warnings
        self._raises = raises

    def project(self, ctx: Any) -> Any:
        self.contexts.append(ctx)
        if self._raises is not None:
            raise self._raises
        return AssetProjection(files=("CLAUDE.md",), warnings=self._warnings)

    def unproject(self, ctx: Any) -> Any:  # pragma: no cover - not used here
        raise AssertionError("the supervisor must never uninstall assets")


class AssetProjectionTest(SupervisorTestCase):
    def test_run_projects_once_before_the_loop(self) -> None:
        projector = RecordingProjector()
        inbox = FakeInbox(batch([], next_batch="s1"))
        self.make(inbox, projector=projector).run(once=True)

        self.assertEqual(len(projector.contexts), 1, "assets are projected once, at start")
        ctx = projector.contexts[0]
        self.assertEqual(ctx.workspace, self.config.workspace)
        self.assertEqual(ctx.team_name, self.config.team_name)
        self.assertEqual(ctx.team_room_id, self.config.team_room_id)
        self.assertEqual(ctx.member_name, self.config.member_name)
        self.assertEqual(ctx.matrix_user_id, self.config.matrix_user_id)
        self.assertEqual(ctx.role, self.config.role)
        self.assertEqual(ctx.mcp_env_passthrough, self.config.mcp_env_passthrough)
        self.assertTrue((Path(ctx.plugin_dir) / "plugin.yaml").is_file())

    def test_projection_failure_does_not_stop_the_bridge(self) -> None:
        projector = RecordingProjector(raises=OSError("workspace is read-only"))
        inbox = FakeInbox(batch([], next_batch="s1"))
        supervisor = self.make(inbox, projector=projector)

        with self.assertLogs(sup.LOGGER_NAME, level="WARNING") as logs:
            supervisor.run(once=True)

        self.assertIn("workspace is read-only", " ".join(logs.output))
        self.assertEqual(len(inbox.poll_calls), 1, "the poll loop still ran")
        self.assertEqual(self.state.cursor, "s1")

    def test_projection_warnings_are_logged(self) -> None:
        projector = RecordingProjector(warnings=("skill source missing: /nope",))
        with self.assertLogs(sup.LOGGER_NAME, level="WARNING") as logs:
            self.make(FakeInbox(batch([], next_batch="s1")), projector=projector).run(once=True)
        self.assertIn("skill source missing: /nope", " ".join(logs.output))

    def test_a_bridge_without_a_projector_still_runs(self) -> None:
        inbox = FakeInbox(batch([], next_batch="s1"))
        self.make(inbox).run(once=True)
        self.assertEqual(self.state.cursor, "s1")


# ---- helpers ---------------------------------------------------------


class ArgParserTest(unittest.TestCase):
    def test_defaults(self) -> None:
        args = sup.build_arg_parser().parse_args([])
        self.assertIsNone(args.bootstrap)
        self.assertFalse(args.once)
        self.assertEqual(args.turn_timeout, sup.DEFAULT_TURN_TIMEOUT_SECONDS)

    def test_flags(self) -> None:
        args = sup.build_arg_parser().parse_args(
            ["--bootstrap", "/tmp/b.yaml", "--once", "--turn-timeout", "60"]
        )
        self.assertEqual(args.bootstrap, "/tmp/b.yaml")
        self.assertTrue(args.once)
        self.assertEqual(args.turn_timeout, 60)


class MentionStrippingTest(unittest.TestCase):
    def strip(self, body: str) -> str:
        return sup._strip_mention_prefix(body, MEMBER_ID)

    def test_full_user_id_prefix(self) -> None:
        self.assertEqual(self.strip(f"{MEMBER_ID}: ship it"), "ship it")
        self.assertEqual(self.strip(f"{MEMBER_ID} ship it"), "ship it")

    def test_localpart_prefix(self) -> None:
        self.assertEqual(self.strip("@member ship it"), "ship it")
        self.assertEqual(self.strip("member: ship it"), "ship it")

    def test_mid_sentence_mention_is_left_alone(self) -> None:
        self.assertEqual(self.strip("ask member about it"), "ask member about it")

    def test_mention_only_body_keeps_something_to_run(self) -> None:
        self.assertEqual(self.strip(f"{MEMBER_ID}"), MEMBER_ID)

    def test_no_user_id_is_a_no_op(self) -> None:
        self.assertEqual(sup._strip_mention_prefix("  hello  ", ""), "hello")


if __name__ == "__main__":
    unittest.main()
