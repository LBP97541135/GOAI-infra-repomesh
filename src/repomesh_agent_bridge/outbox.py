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
    "LANES",
    "NOTE_LANE",
    "ROOM_OBSERVATION_NAMESPACE",
    "RUN_LANE",
    "TURN_LANE",
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

TURN_LANE = "turn"
"""What the coding session itself said."""

NOTE_LANE = "note"
"""What the supervisor had to say on the session's behalf.

One trigger can produce both, and they are *independent* sequences. A turn that
runs out of time puts one note in the room; if the batch is then lost before it
is acknowledged, the replay may succeed and produce three real observations. In
a single ordinal space those three would start at zero — the position the note
already holds — and ``INSERT OR IGNORE`` would drop the first answer on the
floor while the room kept showing "I ran out of time" as the reply. Two lanes
make the two sequences independently idempotent, and the room reads exactly what
happened: first the timeout, then the answer.
"""

RUN_LANE = "run"
"""What a governed run said about itself.

A third sequence for the same reason there is a second one, and the argument is
sharper here: a governed run's messages arrive over minutes from a source that
is not the coding session — RepoMesh accepted the task, then the runner started,
then it finished — while the same thread may be answering ordinary mentions
throughout. Sharing an ordinal space with either of the other two would make one
sequence's third message collide with the other's, and ``INSERT OR IGNORE``
would drop it in silence.
"""

LANES: tuple[str, ...] = (TURN_LANE, NOTE_LANE, RUN_LANE)
"""Every lane there is.

Closed on purpose. A lane is part of a message's durable name, so inventing one
by typo would mint a parallel identity space that silently never collides with
anything — the failure mode the whole derivation exists to prevent.
"""

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


def observation_txn_id(trigger_event_id: str, lane: str, ordinal: int) -> str:
    """The Matrix transaction id for one response within one turn.

    Matrix deduplicates by ``(access token, txnId)`` and this Bridge holds a
    single token, so the trigger's event id, the lane, and the response's
    position inside that lane are already a unique name for "this exact
    message". Deriving it rather than generating one per attempt is what turns a
    crash between send and acknowledge into a no-op on restart.

    The lane is part of the material and not decoration: without it a
    supervisor's timeout note and a session's first real answer to the same
    mention derive the *same* id, and the homeserver would deduplicate the
    answer away as a resend of the note.

    Hashed rather than concatenated because a Matrix event id is ``$`` followed
    by base64url — 43 characters and up — and it goes into the request *path*,
    where it would have to be percent-escaped. A fixed-width hex digest raises
    no escaping question at all. ``\\x1f`` separates the parts so no lane or
    event id can spell another combination by containing the separator.
    """

    material = f"{trigger_event_id}\x1f{lane}\x1f{ordinal}".encode()
    return TXN_PREFIX + hashlib.sha256(material).hexdigest()[:_TXN_DIGEST_CHARS]


def observation_id(
    worker_agent_id: UUID, room_id: str, trigger_event_id: str, lane: str, ordinal: int
) -> UUID:
    """The stable identity of one response, as a UUID.

    A digest string would be simpler, but the frozen ``room-observation.v1``
    schema says ``"format": "uuid"`` and the wire model parses it as one, so the
    derivation has to land inside the UUID space: ``uuid5`` over the five parts
    that actually name a response. The lane is one of them for the same reason
    it is part of the transaction id — two lanes share an ordinal space and
    would otherwise share an identity.
    """

    return uuid5(
        ROOM_OBSERVATION_NAMESPACE,
        f"{worker_agent_id}|{room_id}|{trigger_event_id}|{lane}|{ordinal}",
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
    if observation.run_id:
        # The run id and not the task id, though an observation about a governed
        # run carries both: the task id is what the person typed to start it,
        # and the run id is the handle they do not otherwise have — the one
        # thing that lets somebody in the room ask RepoMesh about this run.
        parts.append(f"run {observation.run_id}")
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


def _require_lane(lane: str) -> None:
    """Refuse a lane nobody declared, at whichever door the caller came in.

    A lane is part of a message's durable name, so a typo would mint a parallel
    identity space that silently never collides with anything — every replay
    would post a new message and the deduplication this module exists for would
    be quietly off for that caller.
    """

    if lane not in LANES:
        raise ValueError(f"unknown outbox lane {lane!r}; one of {', '.join(LANES)}")


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
    lane: str
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
        lane: str = TURN_LANE,
    ) -> tuple[PendingSend, ...]:
        """Write a turn's responses down, and return what still needs sending.

        The ordinal is assigned here, at persist time, from the response's
        position in the turn's answer — and it is the *stored* ordinal that every
        later attempt uses. Computing it at send time from an in-memory list
        would be the bug this design is shaped around: any change to drain order
        or filtering after a restart would shift positions, every message would
        derive a different transaction id, and the deduplication that makes a
        crash harmless would quietly stop working. ``UNIQUE (trigger_event_id,
        lane, ordinal)`` hands the invariant to SQLite rather than to a
        convention.

        The lane defaults to what a caller almost always means — the session's
        own answer — and is keyword-only so the rarer case has to name itself.
        Positions are counted *within* the lane, which is exactly what makes a
        replayed turn's real answers land beside the note a timed-out attempt
        already put in the room instead of underneath it.

        The identity the session put on its observations is not used. A turn
        that reran produced fresh random ids for the same facts; the outbox's
        derivation is what makes the replay land on the same row. The emission
        timestamp is taken once, here, for the same reason — one
        ``observationId`` must never be seen carrying two ``emittedAt`` values.

        Takes the destination as plain values rather than a ``Trigger`` so this
        module does not depend on the inbox: the two are siblings over the state
        layer, and the outbox is told where to write, never deciding it.
        """

        _require_lane(lane)
        emitted_at = self._state.now()
        rows = tuple(
            self._row(
                room_id=room_id,
                thread_root_id=thread_root_id,
                trigger_event_id=trigger_event_id,
                observation=observation,
                lane=lane,
                ordinal=ordinal,
                emitted_at=emitted_at,
            )
            for ordinal, observation in enumerate(observations)
        )
        written = self._state.enqueue_sends(rows)
        if written < len(rows):
            _logger.info(
                "outbox already held %d of %d %s intents for %s; replay is a no-op",
                len(rows) - written,
                len(rows),
                lane,
                trigger_event_id,
            )
        return tuple(
            _pending_from_row(row)
            for row in self._state.sends_for_trigger(trigger_event_id)
            if row.sent_event_id is None and row.refused_at is None
        )

    def enqueue_at(
        self,
        *,
        room_id: str,
        thread_root_id: str | None,
        trigger_event_id: str,
        observation: RoomObservation,
        lane: str,
        ordinal: int,
    ) -> tuple[PendingSend, ...]:
        """Write one response down at a position the caller already knows.

        :meth:`enqueue` counts ordinals off a list, which is the right shape for
        a turn: the whole answer exists at once. A governed run's lifecycle does
        not — accepted, started, finished — so its positions are known in advance
        and arrive one at a time, minutes apart and possibly either side of a
        restart. Naming the position is what keeps those messages idempotent
        without holding the sequence in memory.

        The same position with the same message is a no-op; with a *different*
        message it raises :class:`~repomesh_agent_bridge.state.StateRefused`,
        because a caller assigning its own ordinals is the one place the
        collision this module exists to prevent could be reintroduced.
        """

        _require_lane(lane)
        self._state.enqueue_send_at(
            self._row(
                room_id=room_id,
                thread_root_id=thread_root_id,
                trigger_event_id=trigger_event_id,
                observation=observation,
                lane=lane,
                ordinal=ordinal,
                emitted_at=self._state.now(),
            )
        )
        return tuple(
            _pending_from_row(row)
            for row in self._state.sends_for_trigger(trigger_event_id)
            if row.sent_event_id is None and row.refused_at is None
        )

    def _row(
        self,
        *,
        room_id: str,
        thread_root_id: str | None,
        trigger_event_id: str,
        observation: RoomObservation,
        lane: str,
        ordinal: int,
        emitted_at: datetime,
    ) -> OutboxRow:
        """One intent, named and rendered. The single place both paths derive from.

        Spelled once so the two enqueues cannot drift: the transaction id, the
        observation id and the rendered body are the three things a replay has to
        reproduce exactly, and two copies of that derivation would be two things
        to keep in step.
        """

        return OutboxRow(
            room_id=room_id,
            thread_root_id=thread_root_id,
            trigger_event_id=trigger_event_id,
            lane=lane,
            ordinal=ordinal,
            txn_id=observation_txn_id(trigger_event_id, lane, ordinal),
            observation_id=observation_id(
                self._worker_agent_id, room_id, trigger_event_id, lane, ordinal
            ),
            emitted_at=emitted_at,
            kind=observation.kind,
            body=str(render(observation)),
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

    def mark_refused(self, txn_id: str) -> bool:
        """Put one intent down for good. ``False`` if it was already settled.

        The dead-letter half of the reliability core, and the only honest answer
        to a room that says "never". Retrying costs the whole queue: the drain
        runs at the head of every round and stops at the first failure, so one
        message the homeserver will always reject would hold every later answer
        behind it until an operator noticed. Dropping it silently would be
        worse. Recording it, skipping it, and shouting once in the log is the
        third option, and it is the only one that keeps both the queue and the
        record true.
        """

        return self._state.mark_refused(txn_id)


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
        lane=row.lane,
        ordinal=row.ordinal,
        txn_id=row.txn_id,
        observation_id=row.observation_id,
        emitted_at=row.emitted_at,
        kind=row.kind,
        body=RoomBody(row.body),
    )
