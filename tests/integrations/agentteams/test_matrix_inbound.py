"""The sync loop's two consumers, and the identity map behind one of them.

``AgentTeamsMatrixInboundPoller`` is where a Matrix event becomes two things:
a line in a room's transcript and, sometimes, a task report. The order and the
timestamp are the whole of what this seam owes downstream, so both are pinned
here against the real poller over a fake client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from repomesh.integrations.agentteams import (
    AgentTeamsMatrixIdentityResolver,
    AgentTeamsMatrixInboundPoller,
)
from repomesh.integrations.agentteams.matrix import MatrixRoomMessage, MatrixSyncBatch
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.ports import ManagerRuntimeRef, WorkerRuntimeRef
from repomesh.modules.collaboration.contracts import MatrixInboundResult

TEAM_ROOM = "!team-pricing:matrix.local"
#: 2026-08-28T09:00:00Z, as a homeserver would stamp it.
TS = 1_787_907_600_000


class FakeSyncClient:
    def __init__(self, *batches: MatrixSyncBatch) -> None:
        self.batches = list(batches)
        self.since_seen: list[str | None] = []

    async def sync_once(self, *, since: str | None = None, timeout_ms: int = 0):
        self.since_seen.append(since)
        return self.batches.pop(0)


class RecordingTimeline:
    def __init__(self) -> None:
        self.commands = []

    async def record(self, command):
        self.commands.append(command)
        return None


class RecordingProcessor:
    def __init__(self) -> None:
        self.messages = []

    async def execute(self, message):
        self.messages.append(message)
        return MatrixInboundResult.IGNORED


class OrderRecordingTimeline(RecordingTimeline):
    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self.log = log

    async def record(self, command):
        self.log.append("timeline")
        return await super().record(command)


class OrderRecordingProcessor(RecordingProcessor):
    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self.log = log

    async def execute(self, message):
        self.log.append("report")
        return await super().execute(message)


def _message(event_id: str, *, ts: int = TS, body: str = "hello") -> MatrixRoomMessage:
    return MatrixRoomMessage(event_id, TEAM_ROOM, "@bohan:matrix.local", body, ts)


@pytest.mark.asyncio
async def test_the_poller_feeds_the_transcript_before_the_report_consumer() -> None:
    """Recording is the weaker act, so it goes first.

    A message that goes on to be refused as a task report must still be visible
    in the room — that is exactly what somebody asking "why did nothing happen
    when I said that?" needs to see. Running the recorder second would make the
    transcript conditional on the report path not raising.
    """

    log: list[str] = []
    timeline = OrderRecordingTimeline(log)
    processor = OrderRecordingProcessor(log)
    client = FakeSyncClient(MatrixSyncBatch("batch-2", (_message("$evt-1"),)))

    processed = await AgentTeamsMatrixInboundPoller(
        client,  # type: ignore[arg-type]
        processor,
        timeline,
    ).run_once()

    assert processed == 1
    assert log == ["timeline", "report"]


@pytest.mark.asyncio
async def test_origin_server_ts_reaches_both_consumers_as_the_rooms_clock() -> None:
    """Not our receive time. Both consumers get the same instant, from Matrix."""

    timeline = RecordingTimeline()
    processor = RecordingProcessor()
    client = FakeSyncClient(MatrixSyncBatch("batch-2", (_message("$evt-1"),)))

    await AgentTeamsMatrixInboundPoller(
        client,  # type: ignore[arg-type]
        processor,
        timeline,
    ).run_once()

    expected = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    assert timeline.commands[0].occurred_at == expected
    assert timeline.commands[0].event_id == "$evt-1"
    assert timeline.commands[0].sender_matrix_user_id == "@bohan:matrix.local"
    assert processor.messages[0].occurred_at == expected


@pytest.mark.asyncio
async def test_a_batch_that_fails_is_not_acknowledged() -> None:
    """``since`` only advances on a clean batch, which is why both consumers
    have to be idempotent by event id: the retry replays the whole batch."""

    class FailingProcessor:
        async def execute(self, message):
            raise RuntimeError("downstream is having a moment")

    timeline = RecordingTimeline()
    client = FakeSyncClient(
        MatrixSyncBatch("batch-2", (_message("$evt-1"),)),
        MatrixSyncBatch("batch-3", (_message("$evt-1"),)),
    )
    poller = AgentTeamsMatrixInboundPoller(
        client,  # type: ignore[arg-type]
        FailingProcessor(),
        timeline,
    )

    with pytest.raises(RuntimeError, match="will be retried"):
        await poller.run_once()
    with pytest.raises(RuntimeError, match="will be retried"):
        await poller.run_once()

    # Both passes resumed from the same cursor: nothing was acknowledged.
    assert client.since_seen == [None, None]
    # ...and the recorder saw the same event twice, which its own idempotency
    # absorbs.
    assert [command.event_id for command in timeline.commands] == ["$evt-1", "$evt-1"]


@pytest.mark.asyncio
async def test_a_poller_without_a_timeline_still_runs() -> None:
    """A deployment with no timeline store composed keeps its old behaviour
    rather than failing every batch on a missing collaborator."""

    processor = RecordingProcessor()
    client = FakeSyncClient(MatrixSyncBatch("batch-2", (_message("$evt-1"),)))

    assert (
        await AgentTeamsMatrixInboundPoller(
            client,  # type: ignore[arg-type]
            processor,
        ).run_once()
        == 1
    )
    assert processor.messages[0].event_id == "$evt-1"


# ------------------------------------------------------- identity resolution


def _principal(agent_id: UUID, role: AgentRole, name: str) -> AgentPrincipalView:
    return AgentPrincipalView(
        id=agent_id,
        organization_id=uuid4(),
        role=role,
        leader_agent_id=None,
        repository_id=None,
        responsibility_paths=(),
        agentteams_resource_name=name,
        status=AgentPrincipalStatus.ACTIVE,
    )


class StubDirectory:
    def __init__(self, *principals: AgentPrincipalView) -> None:
        self.principals = list(principals)
        self.reads = 0

    async def list_views(self):
        self.reads += 1
        return tuple(self.principals)

    async def get_view(self, agent_id: UUID):
        return next((item for item in self.principals if item.id == agent_id), None)


class StubControlPlane:
    """Answers the two per-resource lookups the resolver builds its map from."""

    def __init__(self, workers: dict[str, str], managers: dict[str, str]) -> None:
        self.workers = workers
        self.managers = managers

    async def get_worker(self, name: str):
        matrix_user_id = self.workers.get(name)
        if matrix_user_id is None:
            return None
        return WorkerRuntimeRef(name=name, phase="Ready", matrix_user_id=matrix_user_id)

    async def get_manager(self, name: str):
        matrix_user_id = self.managers.get(name)
        if matrix_user_id is None:
            return None
        return ManagerRuntimeRef(name=name, phase="Ready", matrix_user_id=matrix_user_id)


@pytest.mark.asyncio
async def test_the_resolver_maps_both_managers_and_workers_back_to_principals() -> None:
    """AgentTeams has no "who owns this Matrix user" endpoint, so the reverse
    map is built from the forward lookups — and a manager is looked up as a
    manager, which is the one place role matters here."""

    worker_id, leader_id = uuid4(), uuid4()
    directory = StubDirectory(
        _principal(worker_id, AgentRole.WORKER, "pricing-worker"),
        _principal(leader_id, AgentRole.ORGANIZATION_LEADER, "org-leader"),
    )
    resolver = AgentTeamsMatrixIdentityResolver(
        directory,
        StubControlPlane(
            workers={"pricing-worker": "@worker:matrix.local"},
            managers={"org-leader": "@manager:matrix.local"},
        ),
    )

    assert await resolver.resolve("@worker:matrix.local") == worker_id
    assert await resolver.resolve("@manager:matrix.local") == leader_id


@pytest.mark.asyncio
async def test_an_unknown_matrix_user_resolves_to_nobody_after_one_refresh() -> None:
    """A human in the room maps onto no principal, and must not map onto the
    nearest one. One rebuild per miss, so a newly provisioned member is picked
    up without a restart and a stranger costs one pass, not one per message."""

    worker_id = uuid4()
    directory = StubDirectory(_principal(worker_id, AgentRole.WORKER, "pricing-worker"))
    control_plane = StubControlPlane(
        workers={"pricing-worker": "@worker:matrix.local"}, managers={}
    )
    resolver = AgentTeamsMatrixIdentityResolver(directory, control_plane)

    assert await resolver.resolve("@bohan:matrix.local") is None
    assert directory.reads == 1

    # The miss rebuilt the map, so every known user is now answered from it
    # without another registry sweep.
    assert await resolver.resolve("@worker:matrix.local") == worker_id
    assert await resolver.resolve("@worker:matrix.local") == worker_id
    assert directory.reads == 1

    # A second stranger costs one more pass — which is what picks up a member
    # provisioned since the last rebuild, without a restart.
    control_plane.workers["pricing-worker-2"] = "@worker-2:matrix.local"
    directory.principals.append(
        _principal(uuid4(), AgentRole.WORKER, "pricing-worker-2")
    )
    assert await resolver.resolve("@worker-2:matrix.local") is not None
    assert directory.reads == 2


@pytest.mark.asyncio
async def test_a_principal_the_controller_never_heard_of_is_simply_absent() -> None:
    """404 from the control plane is "no Matrix identity yet", not an error.

    Such a principal contributes nothing to the map, so messages from anyone
    else keep resolving; nothing about one unprovisioned member blinds the
    resolver to the rest.
    """

    known_id, ghost_id = uuid4(), uuid4()
    directory = StubDirectory(
        _principal(known_id, AgentRole.WORKER, "pricing-worker"),
        _principal(ghost_id, AgentRole.WORKER, "never-provisioned"),
    )
    resolver = AgentTeamsMatrixIdentityResolver(
        directory,
        StubControlPlane(workers={"pricing-worker": "@worker:matrix.local"}, managers={}),
    )

    assert await resolver.resolve("@worker:matrix.local") == known_id
    assert await resolver.resolve("@nobody:matrix.local") is None
