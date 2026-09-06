"""The observer's auto-approval branch (D-23, spec §8.17).

copaw's Tool Guard stops every ``execute_shell_command`` that hits one of its
rules and asks the room for ``/approve``; the helper script's own name used
to trip it eight times out of eight (S-1), and even with the safe name the
platform cannot switch the guard off without changing the controller (D-23).
So the platform answers the prompt itself — for exactly one class of command:
the four helper command lines it wrote into the attempt's own
``base/package.json.helper_commands[]``, typed from the attempt's own
directory. Everything else stays in the room for a human.

``normalize_helper_command`` is the whole of the leniency: at most one
``cd <this attempt's shared/tasks directory> && `` prefix is removed and the
rest is compared character for character. ``parse_tool_guard_request`` is
what keeps a fabricated "waiting for approval" sentence (S-5) out of the
comparison: only a real prompt carries the ``execute_shell_command`` tool line
and a JSON parameters block. ``ToolGuardAutoApprover`` is a
``MatrixInboundProcessor``, so the existing ``/sync`` poller can fan messages
into it beside the task-report consumer.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from repomesh.modules.collaboration.contracts import (
    InboundMatrixMessage,
    MatrixInboundProcessor,
    MatrixInboundResult,
)

from .contracts import (
    WORKER_SIDE_PHASES,
    ApprovalSender,
    EventKind,
    HostedNativeAttempt,
    HostedNativeAttemptStore,
    HostedNativeEvent,
    MatrixSenderResolver,
    SharedTaskDirectoryReader,
    utcnow,
)

logger = logging.getLogger(__name__)

APPROVAL_TRANSACTION_PREFIX = "hosted-native-approve-"
PACKAGE_FILE = "base/package.json"
SHELL_TOOL = "execute_shell_command"
WAITING_HEADER = "Waiting for approval"

# ---------------------------------------------------------------------------
# §8.17 ②: the one prefix shape that may be removed
# ---------------------------------------------------------------------------

#: A directory with no shell metacharacter: no whitespace, ``; | & < > $``,
#: backtick, ``( ) { } * ? [ ] ! ~ #`` or quote of either kind.
_DIR_CHARS = r"[^\s;|&<>$`(){}*?\[\]!~#'\"]+"
#: ``cd`` + one space + the directory (bare, or in one pair of quotes) + one
#: space + ``&&`` + one space. Anchored at the start; nothing else is a prefix.
_CD_PREFIX = re.compile(rf"^cd (?P<dir>{_DIR_CHARS}|'{_DIR_CHARS}'|\"{_DIR_CHARS}\") && ")
#: Anything that starts like a ``cd`` at all — decides "no prefix" versus
#: "a prefix that does not qualify".
_CD_START = re.compile(r"^cd(?:\s|$)")


def _canonical_attempt_id(attempt_id: UUID | str) -> str:
    value = attempt_id if isinstance(attempt_id, UUID) else UUID(str(attempt_id))
    return str(value)


def normalize_helper_command(command: str, *, attempt_id: UUID | str) -> str | None:
    """Apply §8.17 ①–② to one intercepted command line.

    Returns the command with at most one qualifying ``cd <dir> && `` prefix
    removed (leading/trailing whitespace stripped, nothing else touched), the
    stripped command unchanged when it has no ``cd`` prefix at all, and
    ``None`` when it starts with a ``cd`` that does not qualify: the wrong
    shape (two spaces, ``;``, a bare ``&&`` with no space), a directory with a
    metacharacter or unbalanced quotes, or a directory that is not this
    attempt's ``shared/tasks/<attempt_id>`` — another attempt's, the
    ``shared/tasks`` root, the workspace ``/work/<id>``, a different case, a
    longer name that merely starts with the id.
    """

    stripped = command.strip()
    if not _CD_START.match(stripped):
        return stripped
    match = _CD_PREFIX.match(stripped)
    if match is None:
        return None
    directory = match.group("dir")
    if directory[0] in "'\"":
        directory = directory[1:-1]
    if directory.endswith("/"):
        directory = directory[:-1]
    expected = f"shared/tasks/{_canonical_attempt_id(attempt_id)}"
    if directory != expected and not directory.endswith("/" + expected):
        return None
    return stripped[match.end() :]


# ---------------------------------------------------------------------------
# §8.17 ①: the prompt shape copaw really sends
# ---------------------------------------------------------------------------

_TOOL_LINE = re.compile(
    r"^\s*-\s*Tool(?:\s*/\s*工具)?\s*[:：]\s*`(?P<tool>[^`]*)`\s*$",
    re.MULTILINE,
)
_JSON_FENCE = re.compile(r"```json[ \t]*\r?\n(?P<json>.*?)\r?\n[ \t]*```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class ToolGuardRequest:
    """One copaw Tool Guard prompt for ``execute_shell_command``, as parsed from
    the room message: the Matrix event id is the idempotency marker, the
    sender is the worker that asked, the command is what it wanted to run."""

    event_id: str
    room_id: str
    sender: str
    command: str
    occurred_at: datetime


def parse_tool_guard_request(message: InboundMatrixMessage) -> ToolGuardRequest | None:
    """``None`` unless the body has the ``Waiting for approval`` header, a tool
    line naming exactly ``execute_shell_command`` and a fenced ```` ```json ````
    object with a string ``command``. The JSON is parsed as JSON, never
    regex-extracted."""

    body = message.body
    if WAITING_HEADER not in body:
        return None
    tool = _TOOL_LINE.search(body)
    if tool is None or tool.group("tool").strip() != SHELL_TOOL:
        return None
    fence = _JSON_FENCE.search(body)
    if fence is None:
        return None
    try:
        parameters = json.loads(fence.group("json"))
    except json.JSONDecodeError:
        return None
    if not isinstance(parameters, dict):
        return None
    command = parameters.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    return ToolGuardRequest(
        event_id=message.event_id,
        room_id=message.room_id,
        sender=message.sender,
        command=command,
        occurred_at=message.occurred_at,
    )


# ---------------------------------------------------------------------------
# The approver
# ---------------------------------------------------------------------------


class ToolGuardAutoApprover(MatrixInboundProcessor):
    """Answer a worker's Tool Guard prompt for its own helper command lines.

    The gates, in order (D-23, §8.17 ⑤): the message is a real prompt; some
    open, worker-side attempt lives in this room; the sender resolves to that
    attempt's worker; the prompt is newer than the attempt's ``notified_at``;
    the command normalises (§8.17 ②) and equals one of the attempt's own
    ``helper_commands[]`` (§8.17 ③). Then the ``auto_approved`` event is
    written *before* the ``/approve`` goes out, and the Matrix transaction id
    is derived from the event id, so a retry after a failed send repeats the
    same transaction instead of a second approval.
    """

    def __init__(
        self,
        attempts: HostedNativeAttemptStore,
        reader: SharedTaskDirectoryReader,
        senders: MatrixSenderResolver,
        approvals: ApprovalSender,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._attempts = attempts
        self._reader = reader
        self._senders = senders
        self._approvals = approvals
        self._clock = clock

    async def execute(self, message: InboundMatrixMessage) -> MatrixInboundResult:
        request = parse_tool_guard_request(message)
        if request is None:
            return MatrixInboundResult.IGNORED

        candidates = [
            attempt
            for attempt in await self._attempts.list_open()
            if attempt.room_id == message.room_id and attempt.phase in WORKER_SIDE_PHASES
        ]
        if not candidates:
            return MatrixInboundResult.IGNORED

        agent_id = await self._senders.resolve(message.sender)
        if agent_id is None:
            logger.info(
                "tool guard prompt %s left for a human: sender %s is unknown",
                message.event_id,
                message.sender,
            )
            return MatrixInboundResult.IGNORED
        owned = [attempt for attempt in candidates if attempt.worker_agent_id == agent_id]
        if not owned:
            logger.info(
                "tool guard prompt %s left for a human: sender %s holds no attempt in %s",
                message.event_id,
                message.sender,
                message.room_id,
            )
            return MatrixInboundResult.IGNORED
        current = [attempt for attempt in owned if message.occurred_at >= attempt.notified_at]
        if not current:
            # Replayed history: the prompt predates every attempt this worker holds.
            return MatrixInboundResult.IGNORED

        # One worker normally holds one attempt per room; when it holds more, the
        # ``cd`` directory in the command names the one the prompt is about.
        matched = self._match(request.command, current)
        if matched is None:
            logger.info(
                "tool guard prompt %s left for a human: command does not normalise for %s: %r",
                message.event_id,
                ", ".join(str(attempt.id) for attempt in current),
                request.command,
            )
            return MatrixInboundResult.IGNORED
        attempt, normalized = matched

        helper_commands = await self._helper_commands(attempt)
        if helper_commands is None:
            logger.warning(
                "tool guard prompt %s left for a human: %s of attempt %s is missing or invalid",
                message.event_id,
                PACKAGE_FILE,
                attempt.id,
            )
            return MatrixInboundResult.IGNORED
        if normalized not in helper_commands:
            logger.info(
                "tool guard prompt %s left for a human: %r is not a helper command of attempt %s",
                message.event_id,
                normalized,
                attempt.id,
            )
            return MatrixInboundResult.IGNORED

        event = HostedNativeEvent(
            id=uuid4(),
            attempt_id=attempt.id,
            kind=EventKind.AUTO_APPROVED,
            marker=message.event_id,
            payload={
                "command": request.command,
                "normalized": normalized,
                "sender": message.sender,
                "room_id": message.room_id,
            },
            observed_at=message.occurred_at,
        )
        if not await self._attempts.record_event(event):
            existing = await self._attempts.find_event(
                attempt.id, EventKind.AUTO_APPROVED, message.event_id
            )
            if existing is None or existing.applied_at is not None:
                return MatrixInboundResult.DUPLICATE
            # Recorded earlier but the send failed: same row, same transaction id.
            event = existing

        await self._approvals.send_approval(
            message.room_id,
            message.sender,
            transaction_id=f"{APPROVAL_TRANSACTION_PREFIX}{event.id}",
        )
        await self._attempts.mark_applied(event.id, applied_at=self._clock())
        logger.info(
            "auto-approved %r for attempt %s (prompt %s from %s)",
            normalized,
            attempt.id,
            message.event_id,
            message.sender,
        )
        return MatrixInboundResult.PROCESSED

    @staticmethod
    def _match(
        command: str, attempts: list[HostedNativeAttempt]
    ) -> tuple[HostedNativeAttempt, str] | None:
        for attempt in attempts:
            normalized = normalize_helper_command(command, attempt_id=attempt.id)
            if normalized is not None:
                return attempt, normalized
        return None

    async def _helper_commands(self, attempt: HostedNativeAttempt) -> tuple[str, ...] | None:
        """``helper_commands[]`` from the attempt's own ``base/package.json``
        (§8.17 ③: the file in the directory, never the ``HELPER_COMMANDS`` constant)."""

        raw = await self._reader.read(attempt.team_name, str(attempt.id), PACKAGE_FILE)
        if raw is None:
            return None
        try:
            control = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(control, dict):
            return None
        commands = control.get("helper_commands")
        if not isinstance(commands, list) or not all(isinstance(item, str) for item in commands):
            return None
        return tuple(commands)
