"""``RoomPort`` over the Matrix client-server v3 API.

Four endpoints — ``whoami``, ``sync``, ``join`` and ``send`` — written directly
against ``httpx``. No Matrix SDK, and deliberately no reuse of the server-side
client in ``repomesh.integrations.agentteams``: the Bridge is a separate process
installed on an operator's machine, so importing a server integration would drag
FastAPI, SQLAlchemy and asyncpg into a wheel that is meant to stand alone, and
the two clients have already diverged anyway (that one pushes tasks *into* rooms
and resolves recipients through the AgentTeams control plane, which this process
holds no credential for). What is copied is shape, not code: the long-poll
timeout arithmetic, the sync filter, the invite reader and the event normaliser
all follow implementations that have run against a live homeserver.

The adapter decides nothing. Which rooms are answerable, which events are worth
a turn, whether an invitation is trusted, and what transaction id a message
carries all arrive from the caller. In particular it does **not** drop events
from rooms outside the confirmed list — the room filter is pushed to the server
as an optimisation, and the caller re-checks, so "the Bridge answered in a room
it should not have" stays a question with exactly one place to look.

Only the method, the path and the status code are logged: never a request body
(it is the room's text), never a response body, never the access token.
"""

import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from ..ports import (
    RoomBatch,
    RoomBody,
    RoomEvent,
    RoomInvite,
    RoomRefused,
    RoomTransportError,
    RoomUnavailable,
)

__all__ = [
    "DEFAULT_SYNC_TIMEOUT_MS",
    "DEFAULT_TIMELINE_LIMIT",
    "MatrixRoomAdapter",
    "RoomRefused",
    "RoomTransportError",
    "RoomUnavailable",
]
"""The transport vocabulary is re-exported, not defined here.

It moved to :mod:`repomesh_agent_bridge.ports` when the supervisor started
grading refusals — a core module that branches on a failure type cannot import
an adapter to get it. The names stay spelled here because they are still what a
reader of this module is classifying answers *into*, and because the
composition root imports them from the adapter it wires.
"""

_logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_SYNC_TIMEOUT_MS = 30_000
DEFAULT_TIMELINE_LIMIT = 100
"""How many timeline events one room may carry per sync answer.

Generous rather than tuned: a truncated timeline is reported (``limited_rooms``)
and never backfilled, so the limit is the only thing standing between an offline
night and a silently skipped mention.
"""

WHOAMI_PATH = "/_matrix/client/v3/account/whoami"
SYNC_PATH = "/_matrix/client/v3/sync"
JOIN_PATH = "/_matrix/client/v3/rooms/{room_id}/join"
SEND_PATH = "/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}"

_MX_REPLY = re.compile(r"<mx-reply\b[^>]*>.*?</mx-reply>", re.IGNORECASE | re.DOTALL)
"""The rich-reply fallback block Matrix clients prepend to ``formatted_body``."""


class MatrixRoomAdapter:
    """Production :class:`~repomesh_agent_bridge.ports.RoomPort`.

    Retry and backoff are deliberately absent: this class makes one call and
    classifies one answer. The supervisor owns the loop, because it is the only
    party that knows whether a failure should cost the sync cursor its place.
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        sync_timeout_ms: int = DEFAULT_SYNC_TIMEOUT_MS,
        timeline_limit: int = DEFAULT_TIMELINE_LIMIT,
    ) -> None:
        self._transport = transport
        self._timeout = timeout
        self._sync_timeout_ms = sync_timeout_ms
        self._timeline_limit = timeline_limit
        self._client: httpx.AsyncClient | None = None
        self._user_id = ""
        self._filter = ""

    async def start(
        self,
        *,
        homeserver_url: str,
        user_id: str,
        room_ids: Sequence[str],
        access_token: str,
    ) -> None:
        """Open the client and verify the token really belongs to ``user_id``.

        One extra round-trip turns "the operator pasted the other worker's
        token" from a Bridge that quietly answers as somebody else — in rooms
        that other worker is in, under that other worker's name — into a refusal
        before a single message is read.
        """

        if not access_token.strip():
            raise RoomRefused("a Matrix access token is required")
        client = httpx.AsyncClient(
            base_url=homeserver_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=self._timeout,
            # A redirect on an authenticated Matrix call would send the access
            # token somewhere the enrollment never named. (httpx already
            # defaults to False; spelled out because it is a security property.)
            follow_redirects=False,
            transport=self._transport,
        )
        try:
            whoami = await self._call(client, "GET", WHOAMI_PATH)
            identity = _text(whoami, "user_id")
            if not identity:
                raise RoomRefused("Matrix whoami answered without a user_id")
            if identity != user_id:
                raise RoomRefused(
                    f"the Matrix access token belongs to {identity!r}, "
                    f"but the enrollment claims {user_id!r}"
                )
        except BaseException:
            await client.aclose()
            raise
        self._client = client
        self._user_id = user_id
        self._filter = _sync_filter(room_ids, self._timeline_limit)

    async def sync(self, *, since: str | None, timeout_ms: int | None = None) -> RoomBatch:
        """Long-poll once, or take the baseline position when ``since`` is None.

        The baseline round waits for nothing: it is not looking for new
        messages, only for a ``next_batch`` to start counting from, and a sync
        without ``since`` returns *history* — waiting 30 seconds for more of it
        would be 30 seconds spent not starting.
        """

        client = self._require_started()
        chosen = self._sync_timeout_ms if timeout_ms is None else timeout_ms
        wait_ms = 0 if since is None else max(0, chosen)
        params: dict[str, str | int] = {"timeout": wait_ms, "filter": self._filter}
        if since:
            params["since"] = since
        payload = await self._call(
            client,
            "GET",
            SYNC_PATH,
            params=params,
            # Outlast the server's own long poll, or httpx aborts the request
            # the homeserver was about to answer.
            read_timeout=max(10.0, (wait_ms / 1000.0) + 5.0),
        )
        return _batch(payload, self._user_id)

    async def join(self, room_id: str) -> None:
        """Accept an invitation. Idempotent: the homeserver answers 200 for a
        room this user has already joined, which is what makes "join everything
        confirmed, every round" a safe way to converge after a restart."""

        client = self._require_started()
        path = JOIN_PATH.format(room_id=quote(room_id, safe=""))
        await self._call(client, "POST", path, json_body={})

    async def send(
        self, *, room_id: str, thread_root_id: str | None, txn_id: str, body: RoomBody
    ) -> str:
        """Put one message under the caller's transaction id.

        The id is never generated here and never re-keyed on a retry: Matrix
        deduplicates by ``(access token, txnId)``, so a resend of the same id is
        the homeserver's problem to collapse, and an adapter that invented one
        would quietly turn a crash between send and acknowledge into a second
        message in the room.
        """

        client = self._require_started()
        if not txn_id.strip():
            raise RoomRefused("a transaction id is required: this adapter never invents one")
        path = SEND_PATH.format(
            room_id=quote(room_id, safe=""),
            txn_id=quote(txn_id, safe=""),
        )
        content: dict[str, Any] = {"msgtype": "m.text", "body": body}
        if thread_root_id:
            content["m.relates_to"] = {"rel_type": "m.thread", "event_id": thread_root_id}
        payload = await self._call(client, "PUT", path, json_body=content)
        event_id = _text(payload, "event_id")
        if not event_id:
            raise RoomRefused("the homeserver acknowledged the send without an event_id")
        return event_id

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    def _require_started(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("MatrixRoomAdapter.start() has not run: there is no session")
        return self._client

    async def _call(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json_body: Mapping[str, Any] | None = None,
        read_timeout: float | None = None,
    ) -> Mapping[str, Any]:
        """One request, one classification, and the only place HTTP is graded.

        Where this adapter draws the line the port's vocabulary asks for:
        ``RoomUnavailable`` for connection failures, timeouts, 429 and every
        5xx; ``RoomRefused`` for every 4xx, every 3xx (redirects are disabled,
        so one means the homeserver is somewhere other than where the enrollment
        says), and a 200 whose body is not a JSON object. The caller never sees
        a status code, which is what keeps the supervisor free of HTTP.
        """

        try:
            response = await client.request(
                method,
                path,
                params=params,
                json=json_body,
                timeout=self._timeout if read_timeout is None else read_timeout,
            )
        except httpx.HTTPError as unreachable:
            _logger.warning("%s %s failed with %s", method, path, type(unreachable).__name__)
            raise RoomUnavailable(
                f"the homeserver is unreachable: {type(unreachable).__name__}"
            ) from unreachable
        status = response.status_code
        _logger.debug("%s %s -> %d", method, path, status)
        if status == 429 or status >= 500:
            raise RoomUnavailable(f"the homeserver answered {status} for {method} {path}")
        if status != 200:
            # 3xx included: with redirects disabled it means the homeserver is
            # not where the enrollment says, which no retry fixes.
            raise RoomRefused(f"the homeserver refused {method} {path} with {status}")
        try:
            payload = response.json()
        except ValueError as unreadable:
            raise RoomRefused(
                f"{method} {path} answered 200 with a body that is not JSON"
            ) from unreadable
        if not isinstance(payload, dict):
            raise RoomRefused(f"{method} {path} answered 200 with a body that is not an object")
        return payload


def _sync_filter(room_ids: Sequence[str], timeline_limit: int) -> str:
    """Everything the Bridge reads, and nothing else, in one filter.

    ``room.rooms`` pushes the confirmed allowlist down to the server so a
    homeserver-side timeline the Bridge may not act on never crosses the wire.
    It constrains joined and left rooms only — ``rooms.invite`` is unaffected,
    which is exactly right: an invitation is the one way into a room, so it has
    to arrive even from a room this filter would otherwise exclude, and the
    caller applies the trust rule.
    """

    return json.dumps(
        {
            "room": {
                "rooms": list(room_ids),
                "timeline": {"limit": timeline_limit, "types": ["m.room.message"]},
                "ephemeral": {"types": []},
                "account_data": {"types": []},
                "state": {"types": []},
                "include_leave": False,
            },
            "presence": {"types": []},
        },
        separators=(",", ":"),
    )


def _batch(payload: Mapping[str, Any], user_id: str) -> RoomBatch:
    next_batch = _text(payload, "next_batch")
    if not next_batch:
        raise RoomRefused("the sync answer carries no next_batch")
    rooms = _mapping(payload.get("rooms"))
    events: list[RoomEvent] = []
    limited: list[str] = []
    for room_id, room in _mapping(rooms.get("join")).items():
        timeline = _mapping(_mapping(room).get("timeline"))
        if timeline.get("limited"):
            limited.append(room_id)
        raw_events = timeline.get("events")
        for raw in raw_events if isinstance(raw_events, list) else ():
            event = _event(raw, room_id, user_id)
            if event is not None:
                events.append(event)
    # Oldest first, and stable, so events sharing a timestamp keep the order the
    # homeserver put them in rather than an order this sort invented.
    events.sort(key=lambda event: event.origin_server_ts)
    invites = tuple(
        RoomInvite(room_id=room_id, inviter=_inviter(room, user_id))
        for room_id, room in _mapping(rooms.get("invite")).items()
    )
    return RoomBatch(
        next_batch=next_batch,
        events=tuple(events),
        invites=invites,
        limited_rooms=tuple(limited),
    )


def _event(raw: Any, room_id: str, user_id: str) -> RoomEvent | None:
    if not isinstance(raw, dict) or raw.get("type") != "m.room.message":
        return None
    sender = _text(raw, "sender")
    if not sender or sender == user_id:
        return None  # never trigger on this worker's own echo
    content = _mapping(raw.get("content"))
    if content.get("msgtype") != "m.text":
        return None
    event_id = _text(raw, "event_id")
    if not event_id:
        return None
    relates = _mapping(content.get("m.relates_to"))
    thread_root_id = None
    if relates.get("rel_type") == "m.thread":
        thread_root_id = _text(relates, "event_id") or None
    timestamp = raw.get("origin_server_ts")
    return RoomEvent(
        event_id=event_id,
        room_id=room_id,
        sender=sender,
        body=_text(content, "body"),
        origin_server_ts=timestamp if isinstance(timestamp, int) else 0,
        thread_root_id=thread_root_id,
        mentions_me=_mentions_me(content, user_id),
    )


def _inviter(room: Any, user_id: str) -> str:
    """Who offered the invitation, or ``""`` when the server did not say.

    Empty is a normal answer, not a failure: ``invite_state`` is stripped by
    some filters and is "may include" in the specification either way. Nothing
    downstream decides on it — the trust rule is room membership in the
    preflight-confirmed list — so an unknown inviter costs a log line and
    nothing else.
    """

    events = _mapping(_mapping(room).get("invite_state")).get("events")
    if not isinstance(events, list):
        return ""
    for raw in events:
        if not isinstance(raw, dict) or raw.get("type") != "m.room.member":
            continue
        content = _mapping(raw.get("content"))
        if content.get("membership") == "invite" and _text(raw, "state_key") == user_id:
            return _text(raw, "sender")
    return ""


def _mentions_me(content: Mapping[str, Any], user_id: str) -> bool:
    """Whether this message addresses this worker, and only this worker.

    Three rules, in this order:

    ``m.mentions.user_ids`` is authoritative when present. A client that fills
    it in has said exactly who it meant, so a body that happens to contain this
    user id is not second-guessed — which is what makes the next rule bite.

    ``m.mentions.room`` is **not** a mention. One ``@room`` announcement would
    otherwise start a turn on every external worker in the room at once, and
    each of them would narrate its run back into that same room.

    Only when ``m.mentions`` is absent altogether does the body get read, for
    clients that predate the field — and then the rich-reply quotation is
    stripped first. Without that step, replying to a message that mentioned this
    worker mentions it again, forever, because the fallback copies the original
    (user id and all) into the new body.
    """

    if not user_id:
        return False
    mentions = content.get("m.mentions")
    if isinstance(mentions, dict):
        user_ids = mentions.get("user_ids")
        if not isinstance(user_ids, list):
            return False
        return any(str(candidate) == user_id for candidate in user_ids)
    localpart = user_id.split(":", 1)[0]
    for text in _unquoted(content):
        if user_id in text or (len(localpart) > 1 and localpart in text):
            return True
    return False


def _unquoted(content: Mapping[str, Any]) -> tuple[str, ...]:
    """The message's own text, with the rich-reply fallback removed.

    Both projections are searched because a mention can live in only one of
    them: HTML clients render it as a ``matrix.to`` anchor carrying the user id
    while the plain body shows a display name.
    """

    plain = content.get("body")
    texts = [_drop_quoted_lines(plain if isinstance(plain, str) else "")]
    formatted = content.get("formatted_body")
    if isinstance(formatted, str) and formatted:
        texts.append(_MX_REPLY.sub("", formatted))
    return tuple(texts)


def _drop_quoted_lines(body: str) -> str:
    """Strip the ``> <@user:server> …`` fallback and the blank line after it."""

    lines = body.split("\n")
    index = 0
    while index < len(lines) and lines[index].startswith(">"):
        index += 1
    if index == 0:
        return body
    while index < len(lines) and not lines[index].strip():
        index += 1
    return "\n".join(lines[index:])


def _text(source: Mapping[str, Any], key: str) -> str:
    value = source.get(key)
    return value if isinstance(value, str) else ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}
