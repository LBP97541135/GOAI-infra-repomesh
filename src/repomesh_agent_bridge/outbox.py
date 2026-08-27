"""Outbound intent: written down before it is sent, and named deterministically.

Two guarantees that only make sense together:

* **Nothing is lost.** The intent to say something is persisted before the send
  is attempted, so a crash between "the turn produced this" and "the homeserver
  has it" leaves a row that the next start drains.
* **Nothing is doubled.** The transaction id is *derived* from the trigger and
  the response's position, never generated, so a crash between the send and its
  acknowledgement replays under the identical id and Matrix — which deduplicates
  on ``(access token, txnId)`` and this process holds exactly one token —
  returns the original event instead of posting a second message.

The reference implementation this line grew from has no outbox at all: it takes
the loss, on the grounds that dropping one room message beats rerunning an
agent. That trade is defensible when a turn produces a single closing summary.
It stops being defensible here, because a room-observation turn produces a
sequence — started, phase changed, tool ran, tests completed — and losing the
tail turns a narrative into a shrug.

This module is also the *only* place text becomes room-bound. See
:func:`render` and :data:`~repomesh_agent_bridge.ports.RoomBody`.
"""

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from .contracts import RoomObservation
from .ports import RoomBody
from .state import ROOM_BODY_LIMIT, BridgeState, OutboxRow

__all__ = [
    "ROOM_OBSERVATION_NAMESPACE",
    "TXN_PREFIX",
    "Outbox",
    "PendingSend",
    "observation_id",
    "observation_txn_id",
    "render",
]

_logger = logging.getLogger(__name__)

TXN_PREFIX = "rmb-"
"""A readable marker on every transaction id this Bridge issues, following the
house pattern for idempotency keys: legible prefix, bounded digest."""

_TXN_DIGEST_CHARS = 40

ROOM_OBSERVATION_NAMESPACE = uuid5(NAMESPACE_URL, "repomesh://room-observation")
"""Namespace for derived observation ids, in the shape this repository already
uses elsewhere for deterministic identity."""

_KIND_LABELS: dict[str, str] = {
    "run_accepted": "accepted",
    "run_started": "started",
    "phase_changed": "phase",
    "tool_action": "tool",
    "files_changed": "files",
    "test_completed": "tests",
    "question": "question",
    "blocked": "blocked",
    "resumed": "resumed",
    "run_completed": "done",
    "run_failed": "failed",
    "run_interrupted": "interrupted",
    "note": "note",
}


def observation_txn_id(trigger_event_id: str, ordinal: int) -> str:
    """The Matrix transaction id for one response within one turn.

    Matrix deduplicates by ``(access token, txnId)`` and this Bridge holds a
    single token, so the trigger's event id plus the response's position inside
    that trigger's answer is already a unique name for "this exact message".
    Deriving it rather than generating one per attempt is what turns a crash
    between send and acknowledge into a no-op on restart.

    Hashed rather than concatenated because a Matrix event id is ``$`` followed
    by base64url — 43 characters and up — and it goes into the request *path*,
    where it would have to be percent-escaped. A fixed-width hex digest raises
    no escaping question at all.
    """

    material = f"{trigger_event_id}\x1f{ordinal}".encode()
    return TXN_PREFIX + hashlib.sha256(material).hexdigest()[:_TXN_DIGEST_CHARS]


def observation_id(
    worker_agent_id: UUID, room_id: str, trigger_event_id: str, ordinal: int
) -> UUID:
    """The stable identity of one response, as a UUID.

    A digest string would be simpler, but the frozen ``room-observation.v1``
    schema says ``"format": "uuid"`` and the wire model parses it as one, so the
    derivation has to land inside the UUID space: ``uuid5`` over the four parts
    that actually name a response.
    """

    return uuid5(
        ROOM_OBSERVATION_NAMESPACE,
        f"{worker_agent_id}|{room_id}|{trigger_event_id}|{ordinal}",
    )


def render(observation: RoomObservation) -> RoomBody:
    """Project one observation into the text a room is allowed to see.

    The single legitimate constructor of :data:`RoomBody`. Everything a room
    sees passes through here, so "no raw transcript, no THINKING block, no
    protocol frame ever enters a room" is a property of one function rather than
    a habit spread across the send path. ``NewType`` cannot enforce that at
    runtime — it is a ``str`` once compiled — but it does make an alternative
    route something an author has to write out by hand, and a source-scan test
    fails when anyone does.

    The projection is deliberately plain: a label for the kind, the observation's
    own body, and whatever structured detail the kind carried. Anything richer
    belongs to the tier that has richer observations to render.
    """

    label = _KIND_LABELS.get(observation.kind, observation.kind)
    text = observation.body.strip()
    line = f"[{label}] {text}" if text else f"[{label}]"
    detail = _detail(observation)
    if detail:
        line = f"{line} ({detail})"
    return RoomBody(_bounded(line))


def _detail(observation: RoomObservation) -> str:
    parts: list[str] = []
    if observation.phase:
        parts.append(f"phase {observation.phase}")
    if observation.tool_name:
        parts.append(observation.tool_name)
    if observation.test_command:
        parts.append(observation.test_command)
    if observation.test_exit_code is not None:
        parts.append(f"exit {observation.test_exit_code}")
    if observation.changed_files:
        parts.append(f"{len(observation.changed_files)} files")
    if observation.commit_sha:
        parts.append(observation.commit_sha[:12])
    return ", ".join(parts)


def _bounded(line: str) -> str:
    """Hold the schema's 4000-character ceiling on ``body``."""

    if len(line) <= ROOM_BODY_LIMIT:
        return line
    return line[: ROOM_BODY_LIMIT - 1] + "…"


@dataclass(frozen=True, slots=True)
class PendingSend:
    """An intent that is on disk and not yet acknowledged by the homeserver."""

    outbox_id: int
    room_id: str
    thread_root_id: str | None
    trigger_event_id: str
    ordinal: int
    txn_id: str
    observation_id: UUID
    emitted_at: datetime
    kind: str
    body: RoomBody


class Outbox:
    """The outbound half of the reliability core.

    Holds no opinion about *when* to send or *whether* a room is answerable —
    those belong to the supervisor. Its whole job is that a response has one
    durable name and one durable body, no matter how many times the turn behind
    it is replayed.
    """

    def __init__(self, state: BridgeState, *, worker_agent_id: UUID) -> None:
        self._state = state
        self._worker_agent_id = worker_agent_id

    def enqueue(
        self,
        *,
        room_id: str,
        thread_root_id: str | None,
        trigger_event_id: str,
        observations: Sequence[RoomObservation],
    ) -> tuple[PendingSend, ...]:
        """Write a turn's responses down, and return what still needs sending.

        The ordinal is assigned here, at persist time, from the response's
        position in the turn's answer — and it is the *stored* ordinal that every
        later attempt uses. Computing it at send time from an in-memory list
        would be the bug this design is shaped around: any change to drain order
        or filtering after a restart would shift positions, every message would
        derive a different transaction id, and the deduplication that makes a
        crash harmless would quietly stop working. ``UNIQUE (trigger_event_id,
        ordinal)`` hands the invariant to SQLite rather than to a convention.

        The identity the session put on its observations is not used. A turn
        that reran produced fresh random ids for the same facts; the outbox's
        derivation is what makes the replay land on the same row. The emission
        timestamp is taken once, here, for the same reason — one
        ``observationId`` must never be seen carrying two ``emittedAt`` values.

        Takes the destination as plain values rather than a ``Trigger`` so this
        module does not depend on the inbox: the two are siblings over the state
        layer, and the outbox is told where to write, never deciding it.
        """

        emitted_at = self._state.now()
        rows = tuple(
            OutboxRow(
                room_id=room_id,
                thread_root_id=thread_root_id,
                trigger_event_id=trigger_event_id,
                ordinal=ordinal,
                txn_id=observation_txn_id(trigger_event_id, ordinal),
                observation_id=observation_id(
                    self._worker_agent_id, room_id, trigger_event_id, ordinal
                ),
                emitted_at=emitted_at,
                kind=observation.kind,
                body=str(render(observation)),
            )
            for ordinal, observation in enumerate(observations)
        )
        written = self._state.enqueue_sends(rows)
        if written < len(rows):
            _logger.info(
                "outbox already held %d of %d intents for %s; replay is a no-op",
                len(rows) - written,
                len(rows),
                trigger_event_id,
            )
        return tuple(
            _pending_from_row(row)
            for row in self._state.sends_for_trigger(trigger_event_id)
            if row.sent_event_id is None
        )

    def pending(self) -> tuple[PendingSend, ...]:
        """Everything still owed to a room, in the order it was written.

        The supervisor drains this *before* its first sync: an intent stranded
        by a crash has to reach the room before new messages are taken on, or a
        room reads its answers out of order.
        """

        return tuple(_pending_from_row(row) for row in self._state.pending_sends())

    def mark_sent(self, txn_id: str, event_id: str) -> bool:
        """Record the homeserver's event id. ``False`` if it was already recorded.

        Idempotent because the acknowledgement itself can be replayed: a resend
        of an already-delivered transaction gets the original event id back, and
        recording it twice must not be an error.
        """

        return self._state.mark_sent(txn_id, event_id)


def _pending_from_row(row: OutboxRow) -> PendingSend:
    """Rehydrate an intent an earlier process already rendered.

    The second and last place a :data:`RoomBody` is constructed. The text in
    this row came out of :func:`render` in some earlier turn — possibly in an
    earlier *process* — and re-deriving it would mean rerunning a turn that has
    already happened. Reading back what render produced is the same guarantee,
    carried across a restart.
    """

    assert row.outbox_id is not None, "rows read from the database always carry an id"
    return PendingSend(
        outbox_id=row.outbox_id,
        room_id=row.room_id,
        thread_root_id=row.thread_root_id,
        trigger_event_id=row.trigger_event_id,
        ordinal=row.ordinal,
        txn_id=row.txn_id,
        observation_id=row.observation_id,
        emitted_at=row.emitted_at,
        kind=row.kind,
        body=RoomBody(row.body),
    )
