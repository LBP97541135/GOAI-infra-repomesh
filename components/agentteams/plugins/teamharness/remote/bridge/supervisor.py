#!/usr/bin/env python3
"""The bridge main loop: poll the inbox, run one bounded turn, forward the answer.

This is the only module that knows the *order* of operations, and the order is
the contract. Everything else in ``bridge/`` is a passive part that can be
tested on its own; the failure modes this file exists to prevent are all
sequencing bugs:

- The first poll is baseline-only (``InboxState.first_run``). A sync with no
  ``since`` token returns room *history*, and executing it replays old task
  assignments against a live workspace.
- ``gaps`` are backfilled *before* the batch is acked. A gap means the server
  truncated the timeline; the dropped events may include a task assignment.
- The cursor advances once, at the end of the batch. A crash mid-batch replays
  the tail, which the seen-set and the turn ledger make harmless. Acking per
  event would look tidier and would silently drop the work of a turn that was
  running when the process died.
- ``session_ref`` is persisted the moment the driver announces it, from inside
  the event loop -- not from ``TurnResult`` at turn end. That is the exact bug
  PR #828 shipped.
- ``RuntimeDriver.run_turn`` is a generator whose ``return`` value is the
  result. It is consumed through ``next()`` + ``StopIteration.value`` here; a
  bare ``for event in gen`` loop would discard every outcome and silently
  report success for a driver that failed before its first yield.

The supervisor owns the clock. Consumption is blocking and single-threaded, so
the deadline is enforced by a ``threading.Timer`` that calls ``driver.cancel()``
out of band; the driver then ends its stream and the supervisor *synthesizes*
the ``timeout`` result, because only the supervisor knows why the turn stopped.

Concurrency is deliberately absent: one turn at a time, in arrival order. A
remote member is one operator's laptop running one Claude Code install against
one workspace, and parallel turns there corrupt the workspace rather than speed
anything up.

Every external dependency is injected. ``main`` is the only place that touches
``os.environ``, the network, or the filesystem layout, which is what keeps the
tests free of mock patching.

Run:
    python -m bridge.supervisor --bootstrap ~/.agentteams/remote/bootstrap.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Any, Callable, Iterable
import urllib.parse
import urllib.request

from .bootstrap import BootstrapConfig, load_bootstrap
from .dedup import InboxState
from .protocol import (
    AssetContext,
    AssetProjector,
    DriverProbe,
    RuntimeDriver,
    TurnEvent,
    TurnRequest,
    TurnResult,
)
from .runtimes import DEFAULT_RUNTIME, load_runtime, runtime_names
from .session_store import SessionStore

LOGGER_NAME = "teamharness.remote.supervisor"

DEFAULT_TURN_TIMEOUT_SECONDS = 900
# Long-poll window handed to ``inbox``; the tool caps it at 30s itself.
DEFAULT_POLL_WAIT_MS = 20_000
# A gap deeper than this is a burst nobody is going to answer usefully anyway.
# The limit exists so a mis-tokened backfill cannot spin forever -- and hitting
# it is logged as a warning, never swallowed, because the events beyond it are
# genuinely lost work.
DEFAULT_BACKFILL_PAGE_LIMIT = 10
INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0

# How long to keep re-probing an unusable local CLI before giving up, and how
# often. Waiting is the default because the two things that make a runtime
# unusable -- not installed, not signed in -- are both fixed in another
# terminal while this process is already running, and re-probing costs one
# ``--version`` call. Five minutes is long enough to finish a browser sign-in
# and short enough that an unattended start still terminates.
DEFAULT_RUNTIME_WAIT_SECONDS = 300
RUNTIME_PROBE_INTERVAL_SECONDS = 5.0

# Only these wake the agent. ``file`` events carry no instruction and state
# events never reach us -- ``inbox`` drops them at the edge.
TRIGGER_KINDS = frozenset({"text", "notice"})

# Canned, detail-free room notices. A failure's stderr can carry absolute paths,
# environment fragments, and occasionally a token echoed by a crashing CLI;
# none of that may reach a room. Details go to the local log only.
STATUS_NOTICE = {
    "failed": "I could not finish that turn. The failure details are in my local bridge log.",
    "timeout": "That turn hit the time limit and was stopped. Mention me again to retry it.",
    "cancelled": "That turn was interrupted by a bridge shutdown; it will be retried when the bridge restarts.",
    "blocked": "That turn was blocked by the local permission policy and did not run.",
}
FALLBACK_NOTICE = "That turn ended without a result. The details are in my local bridge log."
COMPLETED_NOTICE = "Done -- the turn finished without any text to report."


def forward_txn_id(task_id: str, trigger_event_id: str) -> str:
    """Matrix transaction id for forwarding one turn's answer.

    Derived, not random, and that is the whole point: a bridge that crashes
    after sending but before settling the turn replays the turn on restart and
    sends the answer again. With a deterministic txn id the homeserver
    recognises the retry and the room sees exactly one message, which turns
    at-least-once execution into exactly-once delivery.
    """
    digest = hashlib.sha256(f"{task_id}#{trigger_event_id}#fwd".encode("utf-8"))
    return digest.hexdigest()[:32]


class Supervisor:
    """One inbox, one workspace, one turn at a time."""

    def __init__(
        self,
        config: BootstrapConfig,
        inbox_state: InboxState,
        session_store: SessionStore,
        driver_factory: Callable[[], RuntimeDriver],
        poll_fn: Callable[[dict[str, Any]], dict[str, Any]],
        send_fn: Callable[..., str],
        sleep_fn: Callable[[float], Any] = time.sleep,
        now_fn: Callable[[], float] = time.time,
        turn_timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS,
        poll_wait_ms: int = DEFAULT_POLL_WAIT_MS,
        backfill_page_limit: int = DEFAULT_BACKFILL_PAGE_LIMIT,
        driver_name: str = "claude-code",
        projector: AssetProjector | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._inbox_state = inbox_state
        self._session_store = session_store
        # A fresh driver per turn: ``RuntimeDriver`` instances own at most one
        # live child, and reusing one across turns makes ``cancel`` ambiguous.
        self._driver_factory = driver_factory
        self._poll_fn = poll_fn
        self._send_fn = send_fn
        self._sleep_fn = sleep_fn
        self._now_fn = now_fn
        self._turn_timeout_seconds = float(turn_timeout_seconds)
        self._poll_wait_ms = int(poll_wait_ms)
        self._backfill_page_limit = max(1, int(backfill_page_limit))
        self._driver_name = driver_name
        # Optional: a bridge with no projector still runs turns, it just runs
        # them against a workspace that never heard of the team.
        self._projector = projector
        self._log = logger or logging.getLogger(LOGGER_NAME)

        self._backoff = 0.0
        self._stopping = False
        # Rooms joined at runtime on a trusted invite. Not persisted: the
        # invite/join cycle is idempotent and re-derived from sync each start.
        self._joined_rooms: set[str] = set()
        # Guards the handle a signal handler needs to abort the live turn.
        self._driver_lock = threading.Lock()
        self._active_driver: RuntimeDriver | None = None

    # ---- lifecycle ---------------------------------------------------

    @property
    def stopping(self) -> bool:
        return self._stopping

    def request_stop(self, reason: str = "shutdown") -> None:
        """Ask the loop to stop and abort the live turn. Signal-handler safe."""
        self._stopping = True
        self._log.info("stop requested (%s)", reason)
        with self._driver_lock:
            driver = self._active_driver
        if driver is None:
            return
        try:
            driver.cancel()
        except Exception:  # pragma: no cover - driver-specific
            self._log.exception("cancelling the live turn failed")

    def run(self, once: bool = False) -> None:
        self._refresh_membership()
        # After membership, before the first poll: the assets must be on disk
        # by the time a trigger can arrive, but projecting them is not worth
        # failing a start over -- an agent with no team contract still answers,
        # just without knowing which team it answers for.
        self.project_assets()
        self._log.info(
            "supervisor start member=%s rooms=%s workspace=%s",
            self._config.member_name,
            ",".join(self.rooms()),
            self._config.workspace,
        )
        while not self._stopping:
            self.run_once()
            if once:
                break
        self._log.info("supervisor stopped")

    def project_assets(self) -> None:
        """Install the team contract, skills, and MCP config into the workspace.

        A containerised worker gets these from its image; a remote member has
        no image, so this is the only thing that tells the agent it is part of
        a team at all. Best effort by design: every failure mode here (missing
        prompt file, read-only workspace, malformed manifest) degrades the
        agent's context, and none of them is a reason to refuse to answer.
        """
        if self._projector is None:
            return
        try:
            projection = self._projector.project(build_asset_context(self._config))
        except Exception as exc:
            self._log.warning("projecting team assets failed: %s", exc)
            return
        for warning in projection.warnings:
            self._log.warning("asset projection: %s", warning)
        self._log.info(
            "projected team assets into %s: files=%s skills=%s mcp=%s",
            self._config.workspace,
            ",".join(projection.files) or "none",
            ",".join(projection.skills) or "none",
            ",".join(projection.mcp_servers) or "none",
        )

    def probe_driver(self) -> DriverProbe:
        """Startup diagnostic: is the local runtime usable at all?"""
        return self._driver_factory().probe()

    def _runtime_still_usable(self) -> bool:
        """Re-probe after a failed turn. Never raises; unknown counts as usable.

        Only reached on failure, so the extra ``--version`` costs nothing on the
        happy path. Defaulting a broken *probe* to "usable" is deliberate: this
        gate stops the bridge, and a probe that itself errors is much weaker
        evidence than the turn failure already in hand. A wrong stop is
        recoverable but noisy; letting an ordinary failed turn report itself is
        the behaviour everything else expects.
        """
        try:
            probe = self.probe_driver()
        except Exception:  # pragma: no cover - driver-specific
            self._log.exception("re-probing the runtime after a failed turn raised")
            return True
        return bool(probe.available and probe.authenticated)

    def rooms(self) -> list[str]:
        """Rooms this member answers in.

        Configured rooms plus any joined on a trusted invite. The controller
        creates a team room *after* the bootstrap file is written and only then
        invites the member, so a purely static list would filter out the very
        room the member was added to. An empty list means "no filter" -- answer
        in every joined room -- which is the correct behaviour for a member
        whose rooms are all assigned dynamically.
        """
        candidates = (self._config.team_room_id, self._config.personal_room_id)
        configured = [room for room in (str(c or "").strip() for c in candidates) if room]
        return configured + [r for r in sorted(self._joined_rooms) if r not in configured]

    # ---- one poll cycle ----------------------------------------------

    def run_once(self) -> bool:
        """Poll, execute, ack. ``False`` means the batch was not committed."""
        batch = self._poll()
        if batch is None:
            self._sleep_backoff()
            return False
        self._backoff = 0.0

        # Before anything else, and before the first-run baseline: a member
        # that has not joined its team room sees nothing at all, so a bridge
        # that skipped this on the baseline pass would stay deaf until the
        # next invite.
        self._accept_invites(batch.get("invites"))

        next_batch = str(batch.get("nextBatch") or "")
        events = _as_events(batch.get("events"))

        if self._inbox_state.first_run:
            # Baseline only. The events in this batch are history: they may
            # predate this member entirely, or have been answered by a previous
            # incarnation of the bridge that never got to ack. The watermark
            # planted here is what stops a later gap backfill from paging past
            # this point and resurrecting the same history.
            self._inbox_state.ack(next_batch, _event_ids(events), watermark_ts=_max_ts(events))
            self._log.info(
                "first run: recorded %d event(s) as baseline and executed none", len(events)
            )
            return True

        events = _merge(events, self._backfill_gaps(batch.get("gaps")))
        handled_ids = _event_ids(events)

        for event in self._inbox_state.filter_unseen(events):
            if self._stopping:
                break
            if not self._is_trigger(event):
                continue
            self._process(event)
            if self._stopping:
                break

        if self._stopping:
            # No ack: the batch is unfinished, and replaying it on the next
            # start is exactly what the seen-set and the ledger are for.
            self._log.info("shutting down before the batch was committed; cursor left in place")
            return False

        self._inbox_state.ack(next_batch, handled_ids, watermark_ts=_max_ts(events))
        return True

    def _poll(self) -> dict[str, Any] | None:
        arguments = {
            "action": "poll",
            "since": self._inbox_state.cursor,
            "mentionsOnly": True,
            "waitMs": self._poll_wait_ms,
            "rooms": self.rooms(),
        }
        try:
            result = self._poll_fn(arguments)
        except Exception as exc:  # network, MCP wiring, anything
            self._log.warning("inbox poll raised: %s", exc)
            return None
        if not isinstance(result, dict):
            self._log.warning("inbox poll returned %s, expected a dict", type(result).__name__)
            return None
        if not result.get("ok"):
            self._log.warning("inbox poll failed: %s", result.get("error") or "unknown error")
            return None
        return result

    def _sleep_backoff(self) -> None:
        self._backoff = min(
            MAX_BACKOFF_SECONDS,
            self._backoff * 2 if self._backoff else INITIAL_BACKOFF_SECONDS,
        )
        self._log.info("backing off %.1fs before the next poll", self._backoff)
        self._sleep_fn(self._backoff)

    # ---- membership ---------------------------------------------------

    def _refresh_membership(self) -> None:
        """Read current room membership from the server.

        Invites appear in sync once and then become plain joined rooms, so
        membership tracked from invites alone is forgotten on every restart --
        the member rejoins nothing, watches only its configured rooms, and goes
        silent in the team room it is a member of.
        """
        try:
            result = self._poll_fn({"action": "rooms"})
        except Exception as exc:
            self._log.warning("listing joined rooms raised: %s", exc)
            return
        if not isinstance(result, dict) or not result.get("ok"):
            reason = result.get("error") if isinstance(result, dict) else "malformed response"
            self._log.warning("listing joined rooms failed: %s", reason)
            return
        rooms = result.get("rooms")
        if isinstance(rooms, list):
            self._joined_rooms.update(str(r).strip() for r in rooms if str(r).strip())

    def _accept_invites(self, invites: Any) -> None:
        """Join team rooms offered by a trusted inviter; log and skip the rest.

        The controller invites and expects the runtime to accept -- a
        containerised worker does this in its entrypoint. Without it the member
        is created, listed, and shown as a team member everywhere except the
        room itself, where it never appears.
        """
        if not isinstance(invites, list):
            return
        for invite in invites:
            if not isinstance(invite, dict):
                continue
            room_id = str(invite.get("roomId") or "").strip()
            inviter = str(invite.get("inviter") or "").strip()
            if not room_id:
                continue
            if not self._config.accepts_invite_from(inviter):
                self._log.warning(
                    "ignoring invite to %s (%s) from untrusted %s; add it to "
                    "local.autoJoinInviters to accept",
                    room_id,
                    invite.get("name") or "unnamed room",
                    inviter or "<unknown>",
                )
                continue
            try:
                result = self._poll_fn({"action": "join", "roomId": room_id})
            except Exception as exc:
                self._log.warning("joining %s raised: %s", room_id, exc)
                continue
            if isinstance(result, dict) and result.get("ok"):
                joined = str(result.get("roomId") or room_id)
                self._joined_rooms.add(joined)
                self._log.info(
                    "joined %s (%s) on an invite from %s",
                    joined,
                    invite.get("name") or "unnamed room",
                    inviter,
                )
            else:
                reason = result.get("error") if isinstance(result, dict) else "malformed response"
                self._log.warning("joining %s failed: %s", room_id, reason)

    # ---- gap backfill ------------------------------------------------

    def _backfill_gaps(self, gaps: Any) -> list[dict[str, Any]]:
        recovered: list[dict[str, Any]] = []
        if not isinstance(gaps, list):
            return recovered
        for gap in gaps:
            if not isinstance(gap, dict):
                continue
            room_id = str(gap.get("roomId") or "").strip()
            token = str(gap.get("prevBatch") or "").strip()
            if not room_id or not token:
                continue
            recovered.extend(self._backfill_room(room_id, token))
        return recovered

    def _backfill_room(self, room_id: str, token: str) -> list[dict[str, Any]]:
        """Page backwards until the gap meets known territory.

        Three stop conditions, and the third is the interesting one: hitting the
        page limit means events were left unrecovered, so it warns rather than
        returning quietly with a plausible-looking partial batch.
        """
        recovered: list[dict[str, Any]] = []
        pages = 0
        while token and pages < self._backfill_page_limit:
            arguments = {
                "action": "backfill",
                "roomId": room_id,
                "from": token,
                "mentionsOnly": True,
            }
            try:
                page = self._poll_fn(arguments)
            except Exception as exc:
                self._log.warning("backfill of %s raised: %s", room_id, exc)
                return recovered
            pages += 1
            if not isinstance(page, dict) or not page.get("ok"):
                reason = page.get("error") if isinstance(page, dict) else "malformed response"
                self._log.warning("backfill of %s failed after %d page(s): %s", room_id, pages, reason)
                return recovered

            watermark = self._inbox_state.watermark_ts
            reached_known = False
            for event in _as_events(page.get("events")):
                event_id = str(event.get("eventId") or "")
                if event_id and self._inbox_state.is_seen(event_id):
                    reached_known = True
                    continue
                # The seen-set only holds mention events, so in a room with
                # sparse mentions a backfill can page far past the previous
                # cursor without ever meeting one -- straight into pre-baseline
                # history. Anything at or below the ack watermark is known
                # territory by time, not by id.
                if watermark and int(event.get("ts") or 0) <= watermark:
                    reached_known = True
                    continue
                recovered.append(event)
            token = str(page.get("nextFrom") or "")
            if reached_known:
                self._log.info(
                    "backfilled %s: %d event(s) over %d page(s), met a known event",
                    room_id,
                    len(recovered),
                    pages,
                )
                return recovered
            if not token:
                self._log.info(
                    "backfilled %s: %d event(s) over %d page(s), history exhausted",
                    room_id,
                    len(recovered),
                    pages,
                )
                return recovered

        self._log.warning(
            "backfill of %s stopped at the %d page limit with more history pending; "
            "events older than this page were NOT recovered",
            room_id,
            self._backfill_page_limit,
        )
        return recovered

    # ---- trigger model -----------------------------------------------

    def _is_trigger(self, event: dict[str, Any]) -> bool:
        if not event.get("mentionsMe"):
            return False
        if str(event.get("kind") or "") not in TRIGGER_KINDS:
            return False
        return str(event.get("roomId") or "") in set(self.rooms())

    def task_id_for(self, event: dict[str, Any]) -> str:
        """Thread root if there is one, else the event itself.

        This is what makes sessions per-task instead of per-room: two threads in
        the same room are two tasks and get two Claude Code sessions, while a
        follow-up inside a thread resumes the session that thread already owns.
        """
        thread_root = str(event.get("threadRootId") or "").strip()
        return thread_root or str(event.get("eventId") or "")

    def prompt_for(self, event: dict[str, Any]) -> str:
        return _strip_mention_prefix(str(event.get("body") or ""), self._config.matrix_user_id)

    # ---- turn execution ----------------------------------------------

    def _process(self, event: dict[str, Any]) -> None:
        event_id = str(event.get("eventId") or "")
        task_id = self.task_id_for(event)
        if not event_id or not task_id:
            return

        claim = self._inbox_state.claim_turn(task_id, event_id)
        if not claim.granted:
            self._log.info(
                "skipping event %s (task %s): %s", event_id, task_id, claim.reason or "not granted"
            )
            return

        started = self._now_fn()
        try:
            result = self._run_turn(event, task_id, event_id)
        except Exception as exc:
            self._log.exception("supervisor error while running task %s", task_id)
            result = TurnResult(status="failed", error=str(exc))
        self._log.info(
            "turn task=%s event=%s status=%s elapsed=%.1fs",
            task_id,
            event_id,
            result.status,
            max(0.0, self._now_fn() - started),
        )

        if result.status == "failed" and not self._runtime_still_usable():
            # The runtime died under us -- signed out, uninstalled mid-session,
            # a token that expired. This turn never ran, so it has no side
            # effects to reconcile and nothing useful to report to the room.
            # Stopping before the ack leaves the cursor where it is, and the
            # non-terminal settle keeps the claim retryable, so the task is
            # still waiting after the operator signs back in and restarts.
            # Forwarding a failure here would be the one irreversible act: the
            # room would carry a "could not do it" that the replay then
            # contradicts.
            self._log.error(
                "task %s failed and the %s runtime is no longer usable; stopping "
                "before this batch is acked so the task is not lost",
                task_id,
                self._driver_name,
            )
            self._inbox_state.settle_turn(task_id, event_id, "interrupted")
            self.request_stop("local runtime became unusable")
            return

        self._forward(event, task_id, event_id, result)
        # ``cancelled`` here always means "a bridge shutdown interrupted the
        # turn", and dedup treats ``cancelled`` as terminal because it is
        # reserved for a *deliberate* task cancellation (a future leader
        # cancel_task). Settling with a non-terminal marker instead is what
        # makes a Ctrl-C'd turn retryable on restart rather than refused as a
        # duplicate forever.
        settle_status = "interrupted" if result.status == "cancelled" else result.status
        self._inbox_state.settle_turn(task_id, event_id, settle_status)
        self._session_store.record_turn(task_id, result.status, event_id)

    def _run_turn(self, event: dict[str, Any], task_id: str, event_id: str) -> TurnResult:
        room_id = str(event.get("roomId") or "")
        self._session_store.ensure(
            task_id, room_id, self._driver_name, str(self._config.workspace)
        )
        request = TurnRequest(
            task_id=task_id,
            room_id=room_id,
            prompt=self.prompt_for(event),
            workspace=self._config.workspace,
            # Scoped to this runtime: a handle recorded by a different CLI is
            # not resumable by this one, and starting fresh beats resuming
            # something that is not ours.
            session_ref=self._session_store.resume_ref(task_id, self._driver_name),
            timeout_seconds=self._turn_timeout_seconds,
            trigger_event_id=event_id,
        )
        driver = self._driver_factory()

        # Consuming the generator blocks, so the deadline cannot be checked
        # between events -- a turn that stalls inside one read would never see
        # it. The timer fires out of band, ``cancel`` ends the driver's stream,
        # and the flag tells us the difference between "the runtime failed" and
        # "we stopped it".
        deadline_fired = threading.Event()

        def _on_deadline() -> None:
            deadline_fired.set()
            self._log.warning(
                "task %s exceeded %.0fs; cancelling the turn", task_id, self._turn_timeout_seconds
            )
            try:
                driver.cancel()
            except Exception:  # pragma: no cover - driver-specific
                self._log.exception("cancelling task %s at its deadline failed", task_id)

        timer = threading.Timer(self._turn_timeout_seconds, _on_deadline)
        timer.daemon = True
        with self._driver_lock:
            self._active_driver = driver
        timer.start()
        try:
            result = self._consume(driver.run_turn(request), task_id)
        except Exception as exc:
            self._log.exception("driver raised while running task %s", task_id)
            result = TurnResult(status="failed", error=str(exc))
        finally:
            timer.cancel()
            with self._driver_lock:
                self._active_driver = None

        if deadline_fired.is_set():
            # A cancelled driver reports ``failed`` (its stream ends without a
            # result frame) or raises. Both mean "we cut it", and only the
            # supervisor can say so.
            return TurnResult(
                status="timeout",
                session_ref=result.session_ref,
                final_text=result.final_text,
                error=result.error or f"deadline of {self._turn_timeout_seconds:.0f}s exceeded",
            )
        if self._stopping and result.status != "completed":
            return TurnResult(
                status="cancelled",
                session_ref=result.session_ref,
                final_text=result.final_text,
                error=result.error or "supervisor shutting down",
            )
        return result

    def _consume(self, generator: Any, task_id: str) -> TurnResult:
        """Drive the generator and read its ``return`` value.

        ``StopIteration.value`` and not a bare ``for``: the result lives in the
        return, and a driver may legitimately return before its first yield.
        """
        try:
            while True:
                try:
                    event = next(generator)
                except StopIteration as stop:
                    value = stop.value
                    if isinstance(value, TurnResult):
                        return value
                    return TurnResult(
                        status="failed", error="driver finished without returning a TurnResult"
                    )
                self._on_turn_event(task_id, event)
        finally:
            # Also the interrupt path: closing raises GeneratorExit at the
            # pending yield, which is where the driver reaps its child.
            generator.close()

    def _on_turn_event(self, task_id: str, event: TurnEvent) -> None:
        if event.kind == "session_ref":
            if event.text:
                # Now, not at turn end: a crash after this point must still
                # leave a resumable handle behind.
                self._session_store.bind_session_ref(task_id, event.text)
                self._log.info("task %s bound a runtime session handle", task_id)
            return
        # Everything else is MVP-logged only. Room output is the forwarded final
        # text (see README open question 2), never a live event relay.
        self._log.debug("task %s driver event kind=%s", task_id, event.kind)

    # ---- result forwarding -------------------------------------------

    def _forward(
        self, event: dict[str, Any], task_id: str, event_id: str, result: TurnResult
    ) -> None:
        room_id = str(event.get("roomId") or "")
        if result.status == "completed":
            body = (result.final_text or "").strip() or COMPLETED_NOTICE
        else:
            body = STATUS_NOTICE.get(result.status, FALLBACK_NOTICE)
            if result.error:
                # Local log only. This is the string that must never be sent.
                self._log.warning(
                    "task %s ended %s: %s", task_id, result.status, result.error
                )
        txn_id = forward_txn_id(task_id, event_id)
        try:
            sent = self._send_fn(
                room_id=room_id, body=body, thread_root_id=task_id, txn_id=txn_id
            )
        except Exception:
            # A send failure leaves the turn settled but unannounced; the retry
            # would need a fresh trigger. Losing the room message is strictly
            # better than re-running the agent against the workspace.
            self._log.exception("forwarding task %s to %s failed (txn %s)", task_id, room_id, txn_id)
            return
        self._log.info("forwarded task=%s status=%s as %s", task_id, result.status, sent or txn_id)


# ---- helpers ---------------------------------------------------------


def _as_events(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [event for event in raw if isinstance(event, dict)]


def _event_ids(events: Iterable[dict[str, Any]]) -> list[str]:
    ids = []
    for event in events:
        event_id = str(event.get("eventId") or "")
        if event_id:
            ids.append(event_id)
    return ids


def _max_ts(events: Iterable[dict[str, Any]]) -> int:
    """Newest origin_server_ts in the batch; the ack watermark."""
    newest = 0
    for event in events:
        try:
            newest = max(newest, int(event.get("ts") or 0))
        except (TypeError, ValueError):
            continue
    return newest


def _merge(primary: list[dict[str, Any]], recovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union by event id, oldest first. Backfill and sync overlap by design."""
    merged: dict[str, dict[str, Any]] = {}
    for event in [*primary, *recovered]:
        event_id = str(event.get("eventId") or "")
        if event_id:
            merged.setdefault(event_id, event)
    return sorted(merged.values(), key=lambda item: int(item.get("ts") or 0))


def _strip_mention_prefix(body: str, matrix_user_id: str) -> str:
    """Drop a leading ``@member`` address so the prompt reads as an instruction.

    Only a *leading* mention, and only in its addressed forms: a mid-sentence
    mention is part of what the user said, and stripping a bare localpart
    anywhere would quietly eat words out of the prompt.
    """
    text = body.strip()
    user_id = str(matrix_user_id or "").strip()
    if not text or not user_id:
        return text
    localpart = user_id.split(":", 1)[0]  # "@member"
    bare = localpart.lstrip("@")
    candidates = [user_id, localpart]
    if bare:
        candidates.append(f"{bare}:")
    for candidate in candidates:
        if not candidate or not text.startswith(candidate):
            continue
        rest = text[len(candidate) :].lstrip()
        if rest[:1] in (":", ","):
            rest = rest[1:].lstrip()
        return rest or text
    return text


# ---- real wiring -----------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bridge.supervisor",
        description="TeamHarness remote member bridge for a local coding CLI.",
    )
    parser.add_argument(
        "--bootstrap",
        default=None,
        help="path to the local bootstrap file (default: $AGENTTEAMS_REMOTE_BOOTSTRAP)",
    )
    parser.add_argument(
        "--runtime",
        default=None,
        choices=runtime_names(),
        help=(
            "local coding CLI to drive; overrides local.runtime in the "
            f"bootstrap file (default: {DEFAULT_RUNTIME})"
        ),
    )
    parser.add_argument(
        "--wait-for-runtime",
        type=int,
        default=DEFAULT_RUNTIME_WAIT_SECONDS,
        metavar="SECONDS",
        help=(
            "keep re-probing an unusable local CLI for this long before giving "
            f"up (default: {DEFAULT_RUNTIME_WAIT_SECONDS}); 0 exits immediately"
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single poll cycle and exit (debugging)",
    )
    parser.add_argument(
        "--turn-timeout",
        type=int,
        default=DEFAULT_TURN_TIMEOUT_SECONDS,
        help=f"per-turn deadline in seconds (default: {DEFAULT_TURN_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="python logging level name (default: INFO)",
    )
    return parser


def _import_inbox_tool() -> Any:
    """Import the TeamHarness ``inbox`` helper from the base package.

    The bridge consumes the MCP tool's implementation directly rather than
    embedding a second Matrix sync client -- that duplication is what got
    PR #828 closed. ``mcp/`` is not an installable package, so its directory
    goes on ``sys.path`` the same way the MCP server's own entry point does.
    """
    mcp_dir = Path(__file__).resolve().parents[2] / "mcp"
    if str(mcp_dir) not in sys.path:
        sys.path.insert(0, str(mcp_dir))
    import inbox_tool  # type: ignore[import-not-found]

    return inbox_tool


def _matrix_env() -> tuple[str, str]:
    """Homeserver + token, from the environment only. Never from a file."""
    homeserver = os.getenv("AGENTTEAMS_MATRIX_URL", "").rstrip("/")
    token = os.getenv("AGENTTEAMS_WORKER_MATRIX_TOKEN", "")
    if not homeserver or not token:
        raise ValueError("AGENTTEAMS_MATRIX_URL and AGENTTEAMS_WORKER_MATRIX_TOKEN are required")
    return homeserver, token


def build_poll_fn(config: BootstrapConfig) -> Callable[[dict[str, Any]], dict[str, Any]]:
    inbox_tool = _import_inbox_tool()
    deps = inbox_tool.InboxDeps(
        matrix_env=lambda _tool: _matrix_env(),
        matrix_user_id=lambda: config.matrix_user_id,
        canonical_room_id=lambda value: str(value or "").strip(),
    )

    def poll(arguments: dict[str, Any]) -> dict[str, Any]:
        return inbox_tool.poll(arguments, {}, deps)

    return poll


def build_send_fn(config: BootstrapConfig) -> Callable[..., str]:
    """Outbound room message with a caller-chosen transaction id.

    ``message`` (the MCP tool) is blocked for ``remote-member`` on purpose, so
    forwarding a turn's final text is the bridge's own job. The txn id in the
    URL is what makes a replayed forward idempotent server-side.
    """

    def send(room_id: str, body: str, thread_root_id: str = "", txn_id: str = "") -> str:
        homeserver, token = _matrix_env()
        content: dict[str, Any] = {"msgtype": "m.text", "body": body}
        if thread_root_id:
            content["m.relates_to"] = {"rel_type": "m.thread", "event_id": thread_root_id}
        url = (
            f"{homeserver}/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}"
            f"/send/m.room.message/{urllib.parse.quote(txn_id, safe='')}"
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(content, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return str(payload.get("event_id") or "") if isinstance(payload, dict) else ""

    return send


def plugin_dir() -> Path:
    """The unpacked TeamHarness package root (``plugins/teamharness``).

    Derived from this file's location rather than configured: the bridge ships
    *inside* the package it projects, so any path an operator could set here
    would only be a way to point at a different, wrong copy.
    """
    return Path(__file__).resolve().parents[2]


def build_asset_context(config: BootstrapConfig) -> AssetContext:
    """Bootstrap facts, minus anything secret. See ``AssetContext``."""
    return AssetContext(
        workspace=config.workspace,
        role=config.role,
        member_name=config.member_name,
        team_name=config.team_name,
        plugin_dir=plugin_dir(),
        mcp_env_passthrough=config.mcp_env_passthrough,
        team_room_id=config.team_room_id,
        leader_name=config.leader_name,
        matrix_user_id=config.matrix_user_id,
        personal_room_id=config.personal_room_id,
    )


def build_supervisor(
    config: BootstrapConfig,
    turn_timeout: float,
    runtime: str = DEFAULT_RUNTIME,
) -> Supervisor:
    """Assemble a supervisor around one runtime's leaves.

    The runtime is resolved through ``runtimes.load_runtime`` rather than
    imported here, so that adding a CLI never edits this module. Supervision
    must stay ignorant of which CLI it is driving.
    """
    binding = load_runtime(runtime)
    # Built once: a runtime whose configuration travels as invocation arguments
    # needs the same facts the projector writes into files.
    asset_ctx = build_asset_context(config)
    return Supervisor(
        # Recorded against every session so a stored resume handle carries the
        # runtime that issued it. Left to its default, every runtime claimed to
        # be claude-code -- caught only by running a real Codex turn and
        # reading sessions.json afterwards.
        driver_name=binding.name,
        config=config,
        inbox_state=InboxState(config.state_dir / "inbox.json"),
        session_store=SessionStore(config.state_dir / "sessions.json"),
        # ``driver_args`` is how the operator grants write authority. Without it
        # a headless CLI declines every edit, and the room just gets "I need
        # permission to write" back -- verified against a live deployment
        # before this was wired through.
        driver_factory=lambda: binding.driver_factory(config.driver_args, asset_ctx),
        poll_fn=build_poll_fn(config),
        send_fn=build_send_fn(config),
        turn_timeout_seconds=turn_timeout,
        projector=binding.projector_factory(),
    )


def await_runtime(
    supervisor: Supervisor,
    runtime: str,
    wait_seconds: float,
    log: logging.Logger,
    *,
    sleep_fn: Any = time.sleep,
    now_fn: Any = time.monotonic,
    interval: float = RUNTIME_PROBE_INTERVAL_SECONDS,
) -> bool:
    """Block until the local CLI is usable. ``False`` means give up, do not start.

    Refusing to start is the point, and it is a correctness fix rather than a
    nicety. The poll loop acks a whole batch once it has been walked --
    ``handled_ids`` is every event in it, not the ones that succeeded -- so a
    bridge that joins the room without a working CLI takes a first-run baseline
    over the backlog and then acks each new task as its turn fails. Signing in
    afterwards recovers none of it: those events are past the cursor and in the
    seen-set. The messages are simply gone, and nothing in the room says so.

    Both failure modes are worth waiting on rather than exiting for, because
    both are repaired in another terminal while this process runs: an absent
    binary gets installed, an absent login gets `codex login`. The probe is one
    ``--version`` call plus a file-existence check, so polling it is cheap.

    What this deliberately does **not** do is verify the credential actually
    works. ``authenticated`` is a file-existence check -- opening those files is
    forbidden -- and for Codex a bare ``config.toml`` satisfies it, so a signed
    out laptop can pass this gate. That residual case is caught later, by the
    mid-run guard in ``_process``.
    """
    hint = load_runtime(runtime).sign_in_hint
    started = now_fn()
    announced = False

    while True:
        probe = supervisor.probe_driver()
        if probe.available and probe.authenticated:
            log.info(
                "%s is ready: %s%s",
                runtime,
                probe.binary,
                f" ({probe.version})" if probe.version else "",
            )
            return True

        if probe.available:
            reason = f"no local credentials found; {hint}"
        else:
            reason = probe.reason or "runtime is not usable"

        if now_fn() - started >= wait_seconds:
            log.error(
                "%s is still not usable after %.0fs: %s. Not starting: joining "
                "the room now would consume tasks that every turn then fails, "
                "and they are not replayed once acked.",
                runtime,
                max(0.0, wait_seconds),
                reason,
            )
            return False

        if not announced:
            log.warning("waiting for %s: %s", runtime, reason)
            announced = True
        sleep_fn(interval)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger(LOGGER_NAME)

    try:
        config = load_bootstrap(args.bootstrap)
    except (FileNotFoundError, ValueError) as exc:
        log.error("%s", exc)
        return 2

    # Precedence: explicit flag, then the bootstrap file, then the registry
    # default. The flag wins so one config can be pointed at a second CLI for a
    # one-off comparison run without editing it.
    runtime = args.runtime or config.runtime or DEFAULT_RUNTIME

    try:
        supervisor = build_supervisor(config, float(args.turn_timeout), runtime)
    except ValueError as exc:
        log.error("%s", exc)
        return 2

    if not await_runtime(supervisor, runtime, args.wait_for_runtime, log):
        return 3

    def _handle_signal(signum: int, _frame: Any) -> None:
        supervisor.request_stop(f"signal {signum}")

    for name in ("SIGINT", "SIGTERM"):
        handler = getattr(signal, name, None)
        if handler is not None:
            try:
                signal.signal(handler, _handle_signal)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                pass

    supervisor.run(once=bool(args.once))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
