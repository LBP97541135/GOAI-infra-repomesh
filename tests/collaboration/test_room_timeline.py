"""The room-transcript ingest, through its own interface (PR 9).

Every test below goes through ``RecordRoomTimeline`` / ``ReadRoomTimeline``
rather than the store, because the properties that matter are the use case's:
what it refuses to record, what it refuses to re-resolve, and the order it
guarantees to whoever reads. The store is the in-memory adapter; the same
behaviour is pinned against PostgreSQL and the real migration chain in
``tests/integration/test_room_timeline_postgres.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from repomesh.modules.collaboration import (
    AuthorizedRoom,
    InMemoryRoomTimelineStore,
    ReadRoomTimeline,
    RecordRoomTimeline,
    RecordRoomTimelineCommand,
    RoomTimelineCursor,
)

TEAM_ROOM = "!team-pricing:matrix.local"
LEADER_DM = "!leader-pricing:matrix.local"
OUTSIDE_ROOM = "!somebody-elses-room:matrix.local"

T0 = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)


class StaticAuthorizedRooms:
    """The topology's whitelist, frozen: two rooms belong to one team."""

    def __init__(self, project_id: UUID, repository_id: UUID) -> None:
        self.project_id = project_id
        self.repository_id = repository_id
        self.lookups: list[str] = []

    async def authorized_room(self, room_id: str) -> AuthorizedRoom | None:
        self.lookups.append(room_id)
        if room_id not in {TEAM_ROOM, LEADER_DM}:
            return None
        return AuthorizedRoom(
            room_id=room_id,
            project_id=self.project_id,
            repository_id=self.repository_id,
        )


class StaticResolver:
    def __init__(self, mapping: dict[str, UUID] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[str] = []

    async def resolve(self, matrix_user_id: str) -> UUID | None:
        self.calls.append(matrix_user_id)
        return self.mapping.get(matrix_user_id)


def _command(
    event_id: str,
    *,
    room_id: str = TEAM_ROOM,
    sender: str = "@worker:matrix.local",
    body: str = "starting on the pricing change",
    at: datetime = T0,
) -> RecordRoomTimelineCommand:
    return RecordRoomTimelineCommand(
        event_id=event_id,
        room_id=room_id,
        sender_matrix_user_id=sender,
        body=body,
        occurred_at=at,
    )


def _ingest(
    *,
    resolver: StaticResolver | None = None,
    store: InMemoryRoomTimelineStore | None = None,
    rooms: StaticAuthorizedRooms | None = None,
):
    rooms = rooms or StaticAuthorizedRooms(uuid4(), uuid4())
    store = store or InMemoryRoomTimelineStore()
    recorder = RecordRoomTimeline(rooms, resolver or StaticResolver(), store)
    return recorder, ReadRoomTimeline(store), rooms, store


@pytest.mark.asyncio
async def test_a_recorded_message_keeps_the_rooms_own_clock_and_its_team() -> None:
    worker_id = uuid4()
    resolver = StaticResolver({"@worker:matrix.local": worker_id})
    recorder, reader, rooms, _ = _ingest(resolver=resolver)

    entry = await recorder.record(_command("$evt-1"))

    assert entry is not None
    assert entry.sender_agent_id == worker_id
    assert entry.sender_matrix_user_id == "@worker:matrix.local"
    # The homeserver's timestamp, not "now": the room's order is the room's.
    assert entry.occurred_at == T0
    # Attribution comes from the whitelist entry, so no second lookup is needed
    # to say which team's room this was.
    assert entry.project_id == rooms.project_id
    assert entry.repository_id == rooms.repository_id
    assert await reader.list_room(TEAM_ROOM) == (entry,)


@pytest.mark.asyncio
async def test_replaying_a_sync_batch_records_nothing_twice() -> None:
    """A batch is replayed whole whenever any message in it fails, so this is
    the normal case rather than an edge one."""

    recorder, reader, _, store = _ingest()

    first = await recorder.record(_command("$evt-1"))
    again = await recorder.record(_command("$evt-1"))

    assert again == first
    assert len(store.entries) == 1
    assert await reader.list_room(TEAM_ROOM) == (first,)


@pytest.mark.asyncio
async def test_a_replay_does_not_re_attribute_a_message_later() -> None:
    """A sender unknown at ingest stays unknown, even once it is knowable.

    The alternative — resolving again on replay — would let a message change
    its attributed author days after it was said, because somebody was
    registered in between. Whoever read the room before would have read a
    different name than whoever reads it after, with no event to explain it.
    """

    resolver = StaticResolver()
    recorder, reader, _, _ = _ingest(resolver=resolver)

    await recorder.record(_command("$evt-1", sender="@stranger:matrix.local"))
    resolver.mapping["@stranger:matrix.local"] = uuid4()
    await recorder.record(_command("$evt-1", sender="@stranger:matrix.local"))

    (stored,) = await reader.list_room(TEAM_ROOM)
    assert stored.sender_agent_id is None
    assert stored.sender_matrix_user_id == "@stranger:matrix.local"
    # Resolution was attempted once, for the write that happened.
    assert resolver.calls == ["@stranger:matrix.local"]


@pytest.mark.asyncio
async def test_an_unresolvable_sender_is_recorded_as_itself() -> None:
    """D-4: an honest unknown, never a plausible neighbour.

    A human typing in the team room has no AgentTeams runtime and therefore no
    principal to map onto. The row keeps the raw Matrix handle and no agent id,
    which is what lets the console render a sender it cannot name without
    naming somebody else.
    """

    known = uuid4()
    recorder, reader, _, _ = _ingest(
        resolver=StaticResolver({"@worker:matrix.local": known})
    )

    await recorder.record(_command("$evt-1", sender="@worker:matrix.local"))
    await recorder.record(_command("$evt-2", sender="@bohan:matrix.local"))

    agent, human = await reader.list_room(TEAM_ROOM)
    assert agent.sender_agent_id == known
    assert human.sender_agent_id is None
    assert human.sender_matrix_user_id == "@bohan:matrix.local"


@pytest.mark.asyncio
async def test_an_unauthorized_room_is_dropped_rather_than_mirrored() -> None:
    """RepoMesh's Matrix account can be invited anywhere.

    The whitelist is the topology and nothing else, so a room no team names is
    not recorded — and the caller can tell the difference between "dropped"
    and "stored", because a dropped message returns None rather than raising.
    """

    recorder, reader, _, store = _ingest()

    assert await recorder.record(_command("$evt-1", room_id=OUTSIDE_ROOM)) is None
    assert store.entries == {}
    assert await reader.list_room(OUTSIDE_ROOM) == ()

    # ...and the leader DM, which the topology does name, is recorded.
    assert await recorder.record(_command("$evt-2", room_id=LEADER_DM)) is not None
    assert len(await reader.list_room(LEADER_DM)) == 1


@pytest.mark.asyncio
async def test_a_late_message_sorts_where_it_happened_not_where_it_arrived() -> None:
    """Delivery order is the poller's; message order is the room's."""

    recorder, reader, _, _ = _ingest()

    await recorder.record(_command("$evt-late", at=T0.replace(minute=30)))
    await recorder.record(_command("$evt-early", at=T0))
    await recorder.record(_command("$evt-middle", at=T0.replace(minute=15)))

    assert [entry.event_id for entry in await reader.list_room(TEAM_ROOM)] == [
        "$evt-early",
        "$evt-middle",
        "$evt-late",
    ]


@pytest.mark.asyncio
async def test_messages_sharing_a_timestamp_still_have_one_order() -> None:
    """A homeserver stamps in milliseconds and two messages can collide.

    Without the event id as tiebreak the pair could swap between two reads of
    the same room, which a reader would see as the conversation reordering
    itself under them.
    """

    recorder, reader, _, _ = _ingest()

    await recorder.record(_command("$evt-b", at=T0))
    await recorder.record(_command("$evt-a", at=T0))

    first = [entry.event_id for entry in await reader.list_room(TEAM_ROOM)]
    second = [entry.event_id for entry in await reader.list_room(TEAM_ROOM)]
    assert first == second == ["$evt-a", "$evt-b"]


@pytest.mark.asyncio
async def test_a_cursor_resumes_on_both_halves_of_the_sort_key() -> None:
    """Resuming on the timestamp alone would repeat or skip a tie."""

    recorder, reader, _, _ = _ingest()
    for event_id, minute in (("$evt-a", 0), ("$evt-b", 0), ("$evt-c", 5)):
        await recorder.record(_command(event_id, at=T0.replace(minute=minute)))

    page = await reader.list_room(TEAM_ROOM, limit=2)
    rest = await reader.list_room(
        TEAM_ROOM,
        after=RoomTimelineCursor(page[-1].occurred_at, page[-1].event_id),
    )

    assert [entry.event_id for entry in page] == ["$evt-a", "$evt-b"]
    assert [entry.event_id for entry in rest] == ["$evt-c"]


@pytest.mark.asyncio
async def test_rooms_do_not_leak_into_each_other() -> None:
    recorder, reader, _, _ = _ingest()

    await recorder.record(_command("$evt-team", room_id=TEAM_ROOM))
    await recorder.record(_command("$evt-dm", room_id=LEADER_DM))

    assert [entry.event_id for entry in await reader.list_room(TEAM_ROOM)] == ["$evt-team"]
    assert [entry.event_id for entry in await reader.list_room(LEADER_DM)] == ["$evt-dm"]
