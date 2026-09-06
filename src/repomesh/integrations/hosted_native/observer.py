"""``SharedTaskDirectoryObserver``: shared-directory changes become idempotent events (§4.2 M2).

The observer is the only reader of what copaw writes back into an attempt
directory. Every tick it takes the open attempts from its own store and, for
each, reads the two files copaw rewrites — ``meta.json`` (``ack_task`` stamps
``acknowledged_at``, ``submit_task`` stamps ``status="submitted"`` and
``submitted_at``) and ``result.md`` (the ``submit_task`` body) — under the
directory whose *name* is the attempt id. That name is the whole claim (D-6):
the ``repomesh`` block copaw drops from ``meta.json`` on the first ``ack`` is
never read, and a directory nobody has a row for is never opened.

An observation is keyed by ``(attempt_id, kind, marker)`` where the marker is
the copaw timestamp the fact came with; the store's unique constraint turns
the second sighting into a no-op, and only a freshly inserted event reaches
``round.observe``. Room text is not an event source (D-12, S-5): a worker that
*says* it submitted has submitted nothing until ``result.md`` is there.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from .contracts import (
    WORKER_SIDE_PHASES,
    AttemptPhase,
    EventKind,
    HostedNativeAttempt,
    HostedNativeAttemptStore,
    HostedNativeEvent,
    RoundOutcome,
    RoundTransition,
    SharedTaskDirectoryReader,
    SharedTaskEvent,
    SubmitStatus,
    SubmittedResult,
    utcnow,
)

logger = logging.getLogger(__name__)

META_FILE = "meta.json"
RESULT_FILE = "result.md"
SUBMITTED_STATUS = "submitted"


class RoundObserver(Protocol):
    """The round's ``observe`` verb (M1); the observer knows nothing else about it."""

    async def observe(self, event: SharedTaskEvent) -> RoundTransition: ...


@dataclass(frozen=True, slots=True)
class ObserverReport:
    """What one ``run_once`` did. ``errors`` are per-attempt messages; one
    attempt's failure never stops the scan of the others."""

    scanned: int = 0
    events_recorded: int = 0
    applied: int = 0
    ignored: int = 0
    skipped_duplicates: int = 0
    errors: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Parsing what copaw writes
# ---------------------------------------------------------------------------


def parse_copaw_timestamp(value: object) -> datetime | None:
    """copaw stamps ``acknowledged_at`` / ``submitted_at`` as ISO-8601 with a
    trailing ``Z`` (``2026-09-02T20:00:51Z``). ``None`` for anything that is not
    a parseable timestamp; a naive value is taken as UTC."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def parse_result_markdown(text: str) -> SubmittedResult:
    """``result.md`` by copaw's own rules (``copaw_worker/task.py`` ``parse_task_result``):
    ``STATUS:`` and ``SUMMARY:`` lines, bullets under ``DELIVERABLES:`` / ``NOTES:``.
    Raises ``ValueError`` on a status outside ``SubmitStatus`` or a missing summary."""

    status = ""
    summary = ""
    deliverables: list[str] = []
    notes: list[str] = []
    section = ""
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("STATUS:"):
            status = line[len("STATUS:") :].strip()
            section = ""
            continue
        if line.startswith("SUMMARY:"):
            summary = line[len("SUMMARY:") :].strip()
            section = ""
            continue
        if line == "DELIVERABLES:":
            section = "deliverables"
            continue
        if line == "NOTES:":
            section = "notes"
            continue
        if line.startswith("- "):
            item = line[2:].strip()
            if section == "deliverables":
                deliverables.append(item)
            elif section == "notes":
                notes.append(item)
    try:
        parsed_status = SubmitStatus(status)
    except ValueError:
        raise ValueError(f"invalid result status: {status or '<missing>'}") from None
    if not summary:
        raise ValueError("result summary is required")
    return SubmittedResult(
        status=parsed_status,
        summary=summary,
        deliverables=tuple(deliverables),
        notes=tuple(notes),
    )


@dataclass(frozen=True, slots=True)
class _TaskMeta:
    """The three native fields the observer reads from ``meta.json`` — and no
    other: ``repomesh`` is a publish-time snapshot the observer never consults (D-6)."""

    status: str | None
    acknowledged_at: str | None
    submitted_at: str | None


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def parse_task_meta(raw: bytes) -> _TaskMeta:
    """Raises ``ValueError`` when the bytes are not a JSON object."""

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"meta.json is not valid JSON: {error}") from None
    if not isinstance(data, dict):
        raise ValueError("meta.json is not a JSON object")
    return _TaskMeta(
        status=_optional_text(data.get("status")),
        acknowledged_at=_optional_text(data.get("acknowledged_at")),
        submitted_at=_optional_text(data.get("submitted_at")),
    )


# ---------------------------------------------------------------------------
# The observer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Observation:
    kind: EventKind
    marker: str
    payload: Mapping[str, object]
    result: SubmittedResult | None = None


@dataclass(slots=True)
class _Tally:
    scanned: int = 0
    events_recorded: int = 0
    applied: int = 0
    ignored: int = 0
    skipped_duplicates: int = 0
    errors: list[str] = field(default_factory=list)

    def report(self) -> ObserverReport:
        return ObserverReport(
            scanned=self.scanned,
            events_recorded=self.events_recorded,
            applied=self.applied,
            ignored=self.ignored,
            skipped_duplicates=self.skipped_duplicates,
            errors=tuple(self.errors),
        )


class SharedTaskDirectoryObserver:
    """Background service in the shape of ``WorkerRecoveryReconciler``:
    ``run_once`` for tests and the loop, ``start`` / ``close`` for the container."""

    def __init__(
        self,
        attempts: HostedNativeAttemptStore,
        reader: SharedTaskDirectoryReader,
        round: RoundObserver,  # noqa: A002 - the spec's name for the M1 service
        *,
        interval_seconds: float = 10,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._attempts = attempts
        self._reader = reader
        self._round = round
        self._interval_seconds = interval_seconds
        self._clock = clock
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> ObserverReport:
        tally = _Tally()
        for attempt in await self._attempts.list_open():
            tally.scanned += 1
            try:
                await self._observe_attempt(attempt, tally)
            except Exception as error:
                logger.exception("hosted-native observer failed on attempt %s", attempt.id)
                tally.errors.append(f"{attempt.id}: {error}")
        return tally.report()

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="hosted-native-observer")

    async def close(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("hosted-native observer scan failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self._interval_seconds)

    # -- one attempt ---------------------------------------------------------

    async def _observe_attempt(self, attempt: HostedNativeAttempt, tally: _Tally) -> None:
        if attempt.phase in WORKER_SIDE_PHASES:
            observations = await self._worker_side(attempt, tally)
        elif attempt.phase is AttemptPhase.REVIEW_PENDING and attempt.review_dir:
            observations = await self._review_side(attempt, tally)
        else:
            return
        for observation in observations:
            await self._deliver(attempt, observation, tally)

    async def _worker_side(
        self, attempt: HostedNativeAttempt, tally: _Tally
    ) -> tuple[_Observation, ...]:
        task_dir = str(attempt.id)
        meta = await self._read_meta(attempt, task_dir, tally)
        if meta is None:
            return ()
        observations: list[_Observation] = []
        if meta.acknowledged_at and attempt.phase is AttemptPhase.NOTIFIED:
            observations.append(
                _Observation(
                    kind=EventKind.ACKNOWLEDGED,
                    marker=meta.acknowledged_at,
                    payload={"task_dir": task_dir, "acknowledged_at": meta.acknowledged_at},
                )
            )
        if meta.status == SUBMITTED_STATUS and meta.submitted_at:
            submission = await self._read_submission(attempt, task_dir, tally)
            if submission is not None:
                observations.append(
                    _Observation(
                        kind=EventKind.SUBMITTED,
                        marker=meta.submitted_at,
                        payload=submission.payload,
                        result=submission.result,
                    )
                )
        return tuple(observations)

    async def _review_side(
        self, attempt: HostedNativeAttempt, tally: _Tally
    ) -> tuple[_Observation, ...]:
        assert attempt.review_dir is not None
        task_dir = attempt.review_dir.rstrip("/").rsplit("/", 1)[-1]
        meta = await self._read_meta(attempt, task_dir, tally)
        if meta is None or meta.status != SUBMITTED_STATUS or not meta.submitted_at:
            # The Leader's ack is not an event: only its verdict moves the attempt.
            return ()
        submission = await self._read_submission(attempt, task_dir, tally)
        if submission is None:
            return ()
        return (
            _Observation(
                kind=EventKind.REVIEW_SUBMITTED,
                marker=meta.submitted_at,
                payload={**submission.payload, "review_dir": attempt.review_dir},
                result=submission.result,
            ),
        )

    async def _read_meta(
        self, attempt: HostedNativeAttempt, task_dir: str, tally: _Tally
    ) -> _TaskMeta | None:
        raw = await self._reader.read(attempt.team_name, task_dir, META_FILE)
        if raw is None:
            return None
        try:
            return parse_task_meta(raw)
        except ValueError as error:
            tally.errors.append(f"{attempt.id}: {task_dir}/{META_FILE}: {error}")
            return None

    async def _read_submission(
        self, attempt: HostedNativeAttempt, task_dir: str, tally: _Tally
    ) -> _Submission | None:
        """``None`` when ``result.md`` is not there yet — copaw pushes the
        directory file by file, so ``meta.json`` can land a tick before the
        result does — or when it does not parse (then an error is recorded)."""

        raw = await self._reader.read(attempt.team_name, task_dir, RESULT_FILE)
        if raw is None:
            return None
        try:
            result = parse_result_markdown(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            tally.errors.append(f"{attempt.id}: {task_dir}/{RESULT_FILE}: {error}")
            return None
        stat = await self._reader.stat(attempt.team_name, task_dir, RESULT_FILE)
        payload: dict[str, object] = {
            "task_dir": task_dir,
            "status": result.status.value,
            "summary": result.summary,
            "deliverables": list(result.deliverables),
            "notes": list(result.notes),
            "result_etag": stat.etag if stat is not None else None,
        }
        return _Submission(result=result, payload=payload)

    async def _deliver(
        self, attempt: HostedNativeAttempt, observation: _Observation, tally: _Tally
    ) -> None:
        observed_at = parse_copaw_timestamp(observation.marker) or self._clock()
        event = HostedNativeEvent(
            id=uuid4(),
            attempt_id=attempt.id,
            kind=observation.kind,
            marker=observation.marker,
            payload=dict(observation.payload),
            observed_at=observed_at,
        )
        if await self._attempts.record_event(event):
            tally.events_recorded += 1
        else:
            existing = await self._attempts.find_event(
                attempt.id, observation.kind, observation.marker
            )
            if existing is None or existing.applied_at is not None:
                tally.skipped_duplicates += 1
                return
            # Recorded on an earlier tick but never applied (the round raised or
            # the process died in between): hand the same row to the round again.
            event = existing
        transition = await self._round.observe(
            SharedTaskEvent(
                attempt_id=attempt.id,
                kind=observation.kind,
                marker=observation.marker,
                observed_at=event.observed_at,
                result=observation.result,
                payload=event.payload,
            )
        )
        await self._attempts.mark_applied(event.id, applied_at=self._clock())
        if transition.outcome is RoundOutcome.APPLIED:
            tally.applied += 1
        else:
            tally.ignored += 1
            logger.info(
                "hosted-native observer: %s for attempt %s ignored by the round (%s)",
                observation.kind.value,
                attempt.id,
                transition.reason,
            )


@dataclass(frozen=True, slots=True)
class _Submission:
    result: SubmittedResult
    payload: Mapping[str, object]
