"""The Matrix room adapter, against ``httpx.MockTransport``.

No homeserver is started and no socket is opened: everything this class does is
determined by the bytes it is handed, so a scripted answer exercises it exactly
as a real Synapse would. These are the implementation-detail tests — request
shape, parsing, error classification. Behaviour that crosses the whole Bridge
(which rooms get answered, what happens after a crash) belongs one level up.

The one property worth naming here is what the adapter refuses to do: it invents
no transaction id, drops no event for being in an unconfirmed room, and joins
nothing on its own. Every such decision is the caller's, and these tests pin
that by feeding it input a decision-making adapter would have filtered.
"""

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx
import pytest

from repomesh_agent_bridge import adapters, ports
from repomesh_agent_bridge.adapters import matrix
from repomesh_agent_bridge.adapters.matrix import (
    DEFAULT_SYNC_TIMEOUT_MS,
    MatrixRoomAdapter,
    RoomRefused,
    RoomUnavailable,
)
from repomesh_agent_bridge.ports import RoomBody

from .conftest import HOMESERVER_URL, MATRIX_USER_ID, TEAM_ROOM, WORKER_ROOM

MATRIX_TOKEN_VALUE = "s3cret-matrix-access-token"
OPERATOR = "@operator:matrix.example.org"
OUTSIDE_ROOM = "!not-confirmed:matrix.example.org"
LOCALPART = "@pricing-codex-worker"

WHOAMI_URL = f"{HOMESERVER_URL}/_matrix/client/v3/account/whoami"
SYNC_PATH = "/_matrix/client/v3/sync"


# ---------------------------------------------------------------------------
# Scripted homeserver


class Homeserver:
    """Answers ``whoami`` from an identity and everything else from a script.

    ``calls`` deliberately excludes the whoami handshake so a test can index the
    call it scripted without counting a request it did not write.
    """

    def __init__(
        self,
        *answers: httpx.Response | Exception,
        whoami: str | httpx.Response | Exception = MATRIX_USER_ID,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.calls: list[httpx.Request] = []
        self._answers = list(answers)
        self._whoami = whoami

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.url.path == "/_matrix/client/v3/account/whoami":
                if isinstance(self._whoami, Exception):
                    raise self._whoami
                if isinstance(self._whoami, httpx.Response):
                    return self._whoami
                return httpx.Response(200, json={"user_id": self._whoami})
            self.calls.append(request)
            if not self._answers:
                return httpx.Response(200, json={})
            index = min(len(self.calls) - 1, len(self._answers) - 1)
            answer = self._answers[index]
            if isinstance(answer, Exception):
                raise answer
            return answer

        return httpx.MockTransport(handle)


async def started(
    *answers: httpx.Response | Exception,
    rooms: Sequence[str] = (TEAM_ROOM, WORKER_ROOM),
    **options: object,
) -> tuple[MatrixRoomAdapter, Homeserver]:
    server = Homeserver(*answers)
    adapter = MatrixRoomAdapter(transport=server.transport(), **options)  # type: ignore[arg-type]
    await adapter.start(
        homeserver_url=HOMESERVER_URL,
        user_id=MATRIX_USER_ID,
        room_ids=rooms,
        access_token=MATRIX_TOKEN_VALUE,
    )
    return adapter, server


# ---------------------------------------------------------------------------
# Wire builders


def text_event(
    event_id: str,
    *,
    sender: str = OPERATOR,
    body: str = "hello",
    ts: int = 1_000,
    msgtype: str = "m.text",
    event_type: str = "m.room.message",
    **content: object,
) -> dict[str, object]:
    payload: dict[str, object] = {"msgtype": msgtype, "body": body}
    payload.update(content)
    return {
        "type": event_type,
        "event_id": event_id,
        "sender": sender,
        "origin_server_ts": ts,
        "content": payload,
    }


def sync_wire(
    *,
    next_batch: str = "s_2",
    timelines: Mapping[str, Sequence[dict[str, object]]] | None = None,
    limited: Sequence[str] = (),
    invites: Mapping[str, str | None] | None = None,
) -> dict[str, object]:
    join = {
        room_id: {
            "timeline": {
                "events": list(events),
                "limited": room_id in limited,
                "prev_batch": "p_1",
            }
        }
        for room_id, events in (timelines or {}).items()
    }
    invite = {room_id: _invite_room(inviter) for room_id, inviter in (invites or {}).items()}
    return {"next_batch": next_batch, "rooms": {"join": join, "invite": invite}}


def _invite_room(inviter: str | None) -> dict[str, object]:
    if inviter is None:
        return {}  # a homeserver (or filter) that returned no invite_state
    return {
        "invite_state": {
            "events": [
                {"type": "m.room.name", "sender": inviter, "content": {"name": "Pricing"}},
                {
                    "type": "m.room.member",
                    "sender": inviter,
                    "state_key": MATRIX_USER_ID,
                    "content": {"membership": "invite"},
                },
            ]
        }
    }


def sync_response(**kwargs: object) -> httpx.Response:
    return httpx.Response(200, json=sync_wire(**kwargs))  # type: ignore[arg-type]


async def one_event(**content: object) -> object:
    """Sync once with a single scripted event and return it (or None if dropped)."""

    adapter, _ = await started(
        sync_response(timelines={TEAM_ROOM: [text_event("$e1", **content)]})
    )
    batch = await adapter.sync(since="s_1")
    await adapter.close()
    return batch.events[0] if batch.events else None


# ---------------------------------------------------------------------------
# start / whoami


async def test_start_verifies_the_token_belongs_to_the_enrolled_user() -> None:
    server = Homeserver()
    adapter = MatrixRoomAdapter(transport=server.transport())

    await adapter.start(
        homeserver_url=f"{HOMESERVER_URL}/",
        user_id=MATRIX_USER_ID,
        room_ids=(TEAM_ROOM,),
        access_token=MATRIX_TOKEN_VALUE,
    )

    request = server.requests[0]
    assert request.method == "GET"
    assert str(request.url) == WHOAMI_URL, "a trailing slash on the homeserver is not a new path"
    assert request.headers["Authorization"] == f"Bearer {MATRIX_TOKEN_VALUE}"
    await adapter.close()


def test_the_transport_vocabulary_is_the_ports_one_under_the_adapters_name() -> None:
    """Same classes, three spellings, because the supervisor needs the first one.

    The family moved to ``ports`` when the supervisor started grading refusals:
    a core module may not import an adapter to get the type it branches on. The
    two re-exports stay because the composition root reaches for these names
    next to the adapter it is wiring, and ``except`` on a *copy* of an exception
    class is a silent no-match — so identity, not just name, is what is pinned.
    """

    for module in (matrix, adapters):
        assert module.RoomTransportError is ports.RoomTransportError
        assert module.RoomUnavailable is ports.RoomUnavailable
        assert module.RoomRefused is ports.RoomRefused
    assert {"RoomTransportError", "RoomUnavailable", "RoomRefused"} <= set(matrix.__all__), (
        "the console script imports them from this module"
    )
    assert issubclass(ports.RoomRefused, ports.RoomTransportError)
    assert issubclass(ports.RoomUnavailable, ports.RoomTransportError)
    assert not issubclass(ports.RoomRefused, ports.RoomUnavailable), (
        "a refusal that a backoff could catch is the bug this split exists to prevent"
    )


async def test_a_token_belonging_to_another_worker_is_refused_at_startup() -> None:
    """The single most likely operator error: the other worker's token pasted in."""

    server = Homeserver(whoami="@some-other-worker:matrix.example.org")
    adapter = MatrixRoomAdapter(transport=server.transport())

    with pytest.raises(RoomRefused) as refused:
        await adapter.start(
            homeserver_url=HOMESERVER_URL,
            user_id=MATRIX_USER_ID,
            room_ids=(TEAM_ROOM,),
            access_token=MATRIX_TOKEN_VALUE,
        )

    assert "@some-other-worker:matrix.example.org" in str(refused.value)
    with pytest.raises(RuntimeError, match="start"):
        await adapter.sync(since=None)


async def test_an_unauthenticated_whoami_is_refused_and_a_5xx_is_retryable() -> None:
    for status, expected in ((401, RoomRefused), (503, RoomUnavailable)):
        adapter = MatrixRoomAdapter(
            transport=Homeserver(whoami=httpx.Response(status, json={})).transport()
        )
        with pytest.raises(expected):
            await adapter.start(
                homeserver_url=HOMESERVER_URL,
                user_id=MATRIX_USER_ID,
                room_ids=(TEAM_ROOM,),
                access_token=MATRIX_TOKEN_VALUE,
            )


async def test_an_empty_access_token_never_reaches_the_wire() -> None:
    server = Homeserver()
    adapter = MatrixRoomAdapter(transport=server.transport())

    with pytest.raises(RoomRefused):
        await adapter.start(
            homeserver_url=HOMESERVER_URL,
            user_id=MATRIX_USER_ID,
            room_ids=(TEAM_ROOM,),
            access_token="   ",
        )

    assert server.requests == []


async def test_close_is_safe_on_a_port_that_was_never_started() -> None:
    adapter = MatrixRoomAdapter()

    await adapter.close()
    await adapter.close()


# ---------------------------------------------------------------------------
# sync: request shape


async def test_the_baseline_round_sends_no_since_and_waits_for_nothing() -> None:
    adapter, server = await started(sync_response())

    batch = await adapter.sync(since=None, timeout_ms=DEFAULT_SYNC_TIMEOUT_MS)

    params = dict(server.calls[0].url.params)
    assert "since" not in params, "a sync without since returns history, not news"
    assert params["timeout"] == "0", "the baseline round only wants a next_batch"
    assert batch.next_batch == "s_2"


async def test_a_normal_round_carries_the_cursor_and_the_long_poll_window() -> None:
    adapter, server = await started(sync_response())

    await adapter.sync(since="s_1")

    request = server.calls[0]
    assert dict(request.url.params)["since"] == "s_1"
    assert dict(request.url.params)["timeout"] == str(DEFAULT_SYNC_TIMEOUT_MS)
    assert request.extensions["timeout"]["read"] == 35.0, "outlast the server's own long poll"


async def test_the_read_timeout_never_drops_below_ten_seconds() -> None:
    adapter, server = await started(sync_response(), sync_timeout_ms=1_000)

    await adapter.sync(since="s_1")

    assert server.calls[0].extensions["timeout"]["read"] == 10.0


async def test_the_filter_pushes_the_confirmed_allowlist_and_the_message_types_down() -> None:
    adapter, server = await started(sync_response(), rooms=(TEAM_ROOM, WORKER_ROOM))

    await adapter.sync(since="s_1")

    sync_filter = dict(server.calls[0].url.params)["filter"]
    assert '"rooms":["!team-pricing:matrix.example.org"' in sync_filter
    assert '"types":["m.room.message"]' in sync_filter
    assert '"limit":100' in sync_filter
    assert '"presence":{"types":[]}' in sync_filter
    assert '"include_leave":false' in sync_filter


# ---------------------------------------------------------------------------
# sync: event normalisation


async def test_this_workers_own_messages_never_come_back_as_events() -> None:
    """The echo loop: answering a room puts a message in the room."""

    assert await one_event(sender=MATRIX_USER_ID) is None


@pytest.mark.parametrize(
    "content",
    [
        pytest.param({"msgtype": "m.notice"}, id="notice"),
        pytest.param({"msgtype": "m.image"}, id="image"),
        pytest.param({"msgtype": "m.emote"}, id="emote"),
        pytest.param({"event_type": "m.reaction"}, id="reaction"),
    ],
)
async def test_only_plain_text_room_messages_become_events(content: dict[str, object]) -> None:
    assert await one_event(**content) is None


async def test_events_arrive_oldest_first_across_rooms() -> None:
    adapter, _ = await started(
        sync_response(
            timelines={
                TEAM_ROOM: [text_event("$b", ts=200), text_event("$d", ts=400)],
                WORKER_ROOM: [text_event("$a", ts=100), text_event("$c", ts=300)],
            }
        )
    )

    batch = await adapter.sync(since="s_1")

    assert [event.event_id for event in batch.events] == ["$a", "$b", "$c", "$d"]


async def test_an_event_is_reduced_to_what_the_inbox_decides_on() -> None:
    adapter, _ = await started(
        sync_response(timelines={TEAM_ROOM: [text_event("$e1", body="ship it", ts=7)]})
    )

    (event,) = (await adapter.sync(since="s_1")).events

    assert (event.event_id, event.room_id, event.sender) == ("$e1", TEAM_ROOM, OPERATOR)
    assert (event.body, event.origin_server_ts) == ("ship it", 7)


async def test_an_unconfirmed_room_is_not_filtered_out_here() -> None:
    """The room rule belongs to the caller, so the adapter must not pre-empt it.

    If this ever starts passing by dropping the event, "the Bridge answered
    somewhere it should not have" gains a second place to look.
    """

    adapter, _ = await started(
        sync_response(timelines={OUTSIDE_ROOM: [text_event("$e1")]}), rooms=(TEAM_ROOM,)
    )

    (event,) = (await adapter.sync(since="s_1")).events

    assert event.room_id == OUTSIDE_ROOM


# ---------------------------------------------------------------------------
# sync: threads


async def test_a_thread_reply_carries_its_root() -> None:
    event = await one_event(
        **{"m.relates_to": {"rel_type": "m.thread", "event_id": "$root"}}
    )

    assert event is not None and event.thread_root_id == "$root"


@pytest.mark.parametrize(
    "relates",
    [
        pytest.param(None, id="no-relation"),
        pytest.param({"rel_type": "m.annotation", "event_id": "$root"}, id="annotation"),
        pytest.param(
            {"rel_type": "m.in_reply_to", "event_id": "$root"}, id="rich-reply-is-not-a-thread"
        ),
    ],
)
async def test_only_a_thread_relation_produces_a_thread_root(
    relates: dict[str, str] | None,
) -> None:
    content = {} if relates is None else {"m.relates_to": relates}
    event = await one_event(**content)

    assert event is not None and event.thread_root_id is None


# ---------------------------------------------------------------------------
# sync: mention detection (design G-2)


async def test_an_explicit_mention_of_this_worker_is_a_mention() -> None:
    event = await one_event(**{"m.mentions": {"user_ids": [OPERATOR, MATRIX_USER_ID]}})

    assert event is not None and event.mentions_me is True


async def test_an_explicit_mention_of_somebody_else_is_not() -> None:
    event = await one_event(**{"m.mentions": {"user_ids": [OPERATOR]}})

    assert event is not None and event.mentions_me is False


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("@room stand-up in five", id="plain"),
        pytest.param(f"@room {MATRIX_USER_ID} stand-up", id="body-also-names-this-worker"),
    ],
)
async def test_an_at_room_announcement_is_not_a_mention(body: str) -> None:
    """One ``@room`` would otherwise start a turn on every worker in the room,
    and every one of them would narrate its run back into that same room."""

    event = await one_event(body=body, **{"m.mentions": {"room": True}})

    assert event is not None and event.mentions_me is False


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(f"{MATRIX_USER_ID} please rerun the tests", id="full-user-id"),
        pytest.param(f"hey {LOCALPART} please rerun the tests", id="localpart"),
    ],
)
async def test_without_m_mentions_the_body_is_read_as_a_fallback(body: str) -> None:
    event = await one_event(body=body)

    assert event is not None and event.mentions_me is True


async def test_a_quoted_mention_in_a_reply_is_not_a_new_mention() -> None:
    """The rich-reply fallback copies the original message — user id and all —
    into the new body, so reading it unstripped makes every reply to a mention a
    fresh mention, forever."""

    event = await one_event(
        body=(
            f"> <{MATRIX_USER_ID}> can you rerun the tests?\n"
            "> (second line of the quoted message)\n"
            "\n"
            "I will take this one myself"
        ),
        formatted_body=(
            '<mx-reply><blockquote><a href="https://matrix.to/#/$orig">In reply to</a> '
            f'<a href="https://matrix.to/#/{MATRIX_USER_ID}">{LOCALPART}</a>'
            "<br>can you rerun the tests?</blockquote></mx-reply>"
            "I will take this one myself"
        ),
    )

    assert event is not None and event.mentions_me is False


async def test_a_reply_that_does_mention_this_worker_still_counts() -> None:
    """The control for the test above: stripping must not swallow the real text."""

    event = await one_event(
        body=f"> <{OPERATOR}> who can take this?\n\n{MATRIX_USER_ID} I can",
        formatted_body=(
            "<mx-reply><blockquote>who can take this?</blockquote></mx-reply>"
            f'<a href="https://matrix.to/#/{MATRIX_USER_ID}">worker</a> I can'
        ),
    )

    assert event is not None and event.mentions_me is True


async def test_a_mention_that_only_the_html_body_spells_out_is_found() -> None:
    """HTML clients put the user id in the anchor and a display name in the body."""

    event = await one_event(
        body="Pricing Worker can you take this?",
        formatted_body=f'<a href="https://matrix.to/#/{MATRIX_USER_ID}">Pricing Worker</a> ...',
    )

    assert event is not None and event.mentions_me is True


async def test_an_unrelated_message_is_not_a_mention() -> None:
    event = await one_event(body="the deploy finished, thanks everyone")

    assert event is not None and event.mentions_me is False


# ---------------------------------------------------------------------------
# sync: invites and truncation


async def test_invites_arrive_with_whoever_offered_them() -> None:
    adapter, _ = await started(sync_response(invites={TEAM_ROOM: OPERATOR}))

    (invite,) = (await adapter.sync(since="s_1")).invites

    assert (invite.room_id, invite.inviter) == (TEAM_ROOM, OPERATOR)


async def test_an_invite_survives_a_homeserver_that_sent_no_invite_state() -> None:
    """An unknown inviter costs a log line: the trust rule is the room, not who
    asked, so losing the name must not lose the invitation."""

    adapter, _ = await started(sync_response(invites={OUTSIDE_ROOM: None}))

    (invite,) = (await adapter.sync(since="s_1")).invites

    assert (invite.room_id, invite.inviter) == (OUTSIDE_ROOM, "")


async def test_a_truncated_timeline_is_reported_rather_than_backfilled() -> None:
    adapter, _ = await started(
        sync_response(
            timelines={TEAM_ROOM: [text_event("$e1")], WORKER_ROOM: [text_event("$e2")]},
            limited=(TEAM_ROOM,),
        )
    )

    batch = await adapter.sync(since="s_1")

    assert batch.limited_rooms == (TEAM_ROOM,)
    assert len(batch.events) == 2, "the events that did arrive are still events"


# ---------------------------------------------------------------------------
# join


async def test_join_posts_to_the_room_with_its_id_escaped() -> None:
    adapter, server = await started(httpx.Response(200, json={"room_id": TEAM_ROOM}))

    await adapter.join(TEAM_ROOM)

    request = server.calls[0]
    assert request.method == "POST"
    assert request.url.raw_path.decode() == (
        "/_matrix/client/v3/rooms/%21team-pricing%3Amatrix.example.org/join"
    )
    assert request.content == b"{}"


# ---------------------------------------------------------------------------
# send


async def test_send_puts_the_callers_transaction_id_in_the_path_escaped() -> None:
    adapter, server = await started(httpx.Response(200, json={"event_id": "$sent"}))

    event_id = await adapter.send(
        room_id=TEAM_ROOM,
        thread_root_id=None,
        txn_id="rmb-a/b c",
        body=RoomBody("tests are green"),
    )

    request = server.calls[0]
    assert event_id == "$sent"
    assert request.method == "PUT"
    assert request.url.raw_path.decode() == (
        "/_matrix/client/v3/rooms/%21team-pricing%3Amatrix.example.org"
        "/send/m.room.message/rmb-a%2Fb%20c"
    ), "the txn id is one path segment, whatever the caller derived it from"
    assert request.content == b'{"msgtype":"m.text","body":"tests are green"}'


async def test_send_relates_a_threaded_reply_to_its_root() -> None:
    adapter, server = await started(httpx.Response(200, json={"event_id": "$sent"}))

    await adapter.send(
        room_id=TEAM_ROOM, thread_root_id="$root", txn_id="rmb-1", body=RoomBody("on it")
    )

    assert b'"m.relates_to":{"rel_type":"m.thread","event_id":"$root"}' in server.calls[0].content


async def test_send_refuses_to_invent_a_transaction_id() -> None:
    adapter, server = await started()

    with pytest.raises(RoomRefused, match="never invents"):
        await adapter.send(
            room_id=TEAM_ROOM, thread_root_id=None, txn_id="  ", body=RoomBody("hi")
        )

    assert server.calls == []


async def test_a_send_the_homeserver_did_not_name_is_refused() -> None:
    adapter, _ = await started(httpx.Response(200, json={}))

    with pytest.raises(RoomRefused, match="event_id"):
        await adapter.send(
            room_id=TEAM_ROOM, thread_root_id=None, txn_id="rmb-1", body=RoomBody("hi")
        )


# ---------------------------------------------------------------------------
# error classification


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_a_busy_or_broken_homeserver_is_retryable(status: int) -> None:
    adapter, _ = await started(httpx.Response(status, json={"errcode": "M_UNKNOWN"}))

    with pytest.raises(RoomUnavailable):
        await adapter.sync(since="s_1")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405])
async def test_a_refusal_is_final(status: int) -> None:
    adapter, _ = await started(httpx.Response(status, json={"errcode": "M_FORBIDDEN"}))

    with pytest.raises(RoomRefused, match=str(status)):
        await adapter.sync(since="s_1")


@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_a_redirect_is_refused_and_never_followed(status: int) -> None:
    """The body is a *valid* sync answer, so nothing but the status can refuse it.

    Following the redirect would hand the access token to whatever host the
    ``Location`` names, which the enrollment never authorised; treating the body
    as an answer would let that host set this worker's cursor.
    """

    adapter, server = await started(
        httpx.Response(
            status,
            headers={"Location": "https://elsewhere.example.org/_matrix/client/v3/sync"},
            json=sync_wire(),
        )
    )

    with pytest.raises(RoomRefused, match=str(status)):
        await adapter.sync(since="s_1")

    assert len(server.calls) == 1, "the token must not chase a redirect"


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(httpx.ConnectError("no route to host"), id="connect"),
        pytest.param(httpx.ReadTimeout("the long poll outlasted us"), id="timeout"),
    ],
)
async def test_a_transport_failure_is_retryable(failure: Exception) -> None:
    adapter, _ = await started(failure)

    with pytest.raises(RoomUnavailable):
        await adapter.sync(since="s_1")


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(httpx.Response(200, text="<html>login</html>"), id="not-json"),
        pytest.param(httpx.Response(200, json=["nope"]), id="not-an-object"),
        pytest.param(httpx.Response(200, json={"rooms": {}}), id="no-next-batch"),
        pytest.param(httpx.Response(200, json={"next_batch": ""}), id="empty-next-batch"),
    ],
)
async def test_an_answer_that_cannot_be_parsed_is_refused(answer: httpx.Response) -> None:
    adapter, _ = await started(answer)

    with pytest.raises(RoomRefused):
        await adapter.sync(since="s_1")


@pytest.mark.parametrize(
    "rooms",
    [
        pytest.param({"join": "not-a-mapping"}, id="join-not-a-mapping"),
        pytest.param({"join": {TEAM_ROOM: "not-a-room"}}, id="room-not-a-mapping"),
        pytest.param({"join": {TEAM_ROOM: {"timeline": {"events": "nope"}}}}, id="events-not-list"),
        pytest.param({"join": {TEAM_ROOM: {"timeline": {"events": ["nope"]}}}}, id="event-not-map"),
        pytest.param({"invite": {TEAM_ROOM: {"invite_state": {"events": 3}}}}, id="invite-junk"),
    ],
)
async def test_a_structurally_odd_but_readable_sync_yields_what_it_can(
    rooms: dict[str, object],
) -> None:
    """A malformed corner of one room must not cost the whole batch its cursor."""

    adapter, _ = await started(httpx.Response(200, json={"next_batch": "s_2", "rooms": rooms}))

    batch = await adapter.sync(since="s_1")

    assert batch.next_batch == "s_2"
    assert batch.events == ()


async def test_the_adapter_does_not_retry_on_its_own() -> None:
    """Backoff belongs to the supervisor: only it knows whether a failed round
    should cost the sync cursor its place."""

    adapter, server = await started(httpx.Response(503, json={}))

    with pytest.raises(RoomUnavailable):
        await adapter.sync(since="s_1")

    assert len(server.calls) == 1


# ---------------------------------------------------------------------------
# what never reaches a log


async def test_the_access_token_never_reaches_the_log_or_an_error(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="repomesh_agent_bridge.adapters.matrix")
    adapter, _ = await started(httpx.Response(503, json={}))

    with pytest.raises(RoomUnavailable) as unavailable:
        await adapter.sync(since="s_1")

    assert MATRIX_TOKEN_VALUE not in caplog.text
    assert MATRIX_TOKEN_VALUE not in str(unavailable.value)
    assert f"GET {SYNC_PATH} -> 503" in caplog.text, "method, path and status are the whole log"


async def test_room_text_never_reaches_the_log(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="repomesh_agent_bridge.adapters.matrix")
    adapter, _ = await started(
        sync_response(timelines={TEAM_ROOM: [text_event("$e1", body="the secret plan")]}),
        httpx.Response(200, json={"event_id": "$sent"}),
    )

    await adapter.sync(since="s_1")
    await adapter.send(
        room_id=TEAM_ROOM, thread_root_id=None, txn_id="rmb-1", body=RoomBody("my answer")
    )

    assert "the secret plan" not in caplog.text
    assert "my answer" not in caplog.text


# ---------------------------------------------------------------------------
# dependency direction (merge gate: the Bridge wheel stands alone)


def test_the_adapter_imports_nothing_from_the_repomesh_server_package() -> None:
    """Shape was copied from ``repomesh.integrations.agentteams.matrix``; the
    import was not. Reaching for it would pull FastAPI, SQLAlchemy and asyncpg
    into a wheel that installs on an operator's laptop."""

    source = Path(matrix.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert "import repomesh." not in code
    assert "from repomesh." not in code
    assert "integrations" not in code.split('"""', 2)[-1], "no server integration is imported"
