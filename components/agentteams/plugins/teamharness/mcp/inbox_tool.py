#!/usr/bin/env python3
"""TeamHarness inbox MCP helper implementation.

The read counterpart to ``message_tool``. TeamHarness already owns the Matrix
credentials and the outbound HTTP path; ``inbox`` exposes the inbound side so
that a local runtime bridge does not have to embed a second Matrix client.
Without it every CLI runtime reimplements sync cursors, mention matching, and
redelivery handling -- which is precisely the duplication cited when PR #828
was closed.

The tool is deliberately **stateless**: the caller supplies ``since`` and gets
``nextBatch`` back. Cursor durability belongs to whoever owns crash semantics
(the bridge's ``InboxState``), not to a request-scoped MCP call. ``ack`` is a
no-op acknowledgement kept in the surface so an agent-side caller has the same
poll/ack shape as the bridge, and so a future server-side cursor can be added
without changing the contract.

Completeness contract: when a room's timeline overflows one sync batch, Matrix
truncates it (``limited: true``) and the overflow is only reachable through
back-pagination. ``poll`` surfaces every such room in ``gaps`` and ``backfill``
pages through a gap via ``/rooms/{id}/messages``. A caller that ignores
``gaps`` is accepting event loss; the bridge must not, because the dropped
event may be a task assignment.

Membership: the controller *invites* a member to a team room and expects the
runtime to accept. A containerised worker does that in its entrypoint; a
remote member has no container, so its bridge has to. ``poll`` reports pending
invites (with the inviter, so the caller can apply a trust rule) and ``join``
accepts one. Without this a remote member is created, listed, and assigned to
a team, yet never actually appears in the team room.

Returned events are normalized, not raw Matrix. A driver should never need to
know what ``m.mentions`` is.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

TOOL = "inbox"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Event kinds the bridge can act on. Everything else is dropped at the edge so
# drivers never see state events, reactions, or redactions.
_MSGTYPE_KIND = {
    "m.text": "text",
    "m.notice": "notice",
    "m.emote": "text",
    "m.file": "file",
    "m.image": "file",
    "m.audio": "file",
    "m.video": "file",
}


@dataclass(frozen=True)
class InboxDeps:
    matrix_env: Callable[[str], tuple[str, str]]
    matrix_user_id: Callable[[], str]
    canonical_room_id: Callable[[Any], str]


def poll(arguments: dict[str, Any], payload: dict[str, Any], deps: InboxDeps) -> dict[str, Any]:
    """Fetch normalized room events newer than ``since``.

    Does not advance any cursor. The caller decides when a batch is durably
    handled and passes the returned ``nextBatch`` on the next call.
    """
    action = str(arguments.get("action") or "poll").strip() or "poll"
    base: dict[str, Any] = {"ok": True, "tool": TOOL, "action": action}

    if action == "ack":
        # Stateless server: acking is the caller's own bookkeeping. Echoed so
        # the agent-side and bridge-side call shapes stay identical.
        base["cursor"] = str(arguments.get("cursor") or payload.get("cursor") or "")
        base["acknowledged"] = True
        return base

    if action == "backfill":
        return _backfill(arguments, payload, deps)

    if action == "join":
        return _join(arguments, payload, deps)

    if action == "rooms":
        return _joined_rooms(arguments, deps)

    if action not in {"poll", "peek"}:
        return {"ok": False, "tool": TOOL, "action": action, "error": "unsupported action"}

    since = str(arguments.get("since") or payload.get("since") or "").strip()
    limit = _limit(arguments.get("limit") or payload.get("limit"))
    wait_ms = _wait_ms(arguments.get("waitMs") or payload.get("waitMs"))
    mentions_only = _mentions_only(arguments, payload)
    room_filter = _room_filter(arguments, payload, deps)

    if arguments.get("dryRun"):
        base.update(
            {
                "dryRun": True,
                "since": since,
                "limit": limit,
                "events": [],
                "gaps": [],
                "invites": [],
                "nextBatch": since,
            }
        )
        return base

    try:
        homeserver, token = deps.matrix_env(TOOL)
    except ValueError as exc:
        return {"ok": False, "tool": TOOL, "action": action, "error": str(exc)}

    try:
        body = _matrix_sync(homeserver, token, since, wait_ms, limit)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "tool": TOOL, "action": action, "error": f"matrix sync failed: HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "tool": TOOL, "action": action, "error": f"matrix sync failed: {exc}"}

    self_id = deps.matrix_user_id()
    events, gaps = _normalize(body, self_id, deps)
    # Invites are never room-filtered: the whole point is that the caller does
    # not know the room id yet -- the controller only just created it.
    invites = _invites(body, self_id, deps)
    if room_filter:
        events = [e for e in events if e["roomId"] in room_filter]
        gaps = [g for g in gaps if g["roomId"] in room_filter]
    if mentions_only:
        events = [e for e in events if e["mentionsMe"]]

    # No post-hoc slice: the per-room timeline filter already caps volume, and
    # slicing here while still advancing nextBatch would silently drop events
    # the caller can never recover.
    base.update(
        {
            "events": events,
            "gaps": gaps,
            "invites": invites,
            "nextBatch": str(body.get("next_batch") or since),
        }
    )
    return base


def _join(arguments: dict[str, Any], payload: dict[str, Any], deps: InboxDeps) -> dict[str, Any]:
    """Accept a room invite. Idempotent: joining a joined room succeeds.

    The trust decision belongs to the caller -- ``poll`` reports each invite's
    ``inviter`` precisely so a bridge can refuse rooms offered by strangers.
    """
    room_id = deps.canonical_room_id(arguments.get("roomId") or payload.get("roomId") or "")
    if not room_id:
        return {"ok": False, "tool": TOOL, "action": "join", "error": "roomId is required"}

    base: dict[str, Any] = {"ok": True, "tool": TOOL, "action": "join", "roomId": room_id}
    if arguments.get("dryRun"):
        base["dryRun"] = True
        return base

    try:
        homeserver, token = deps.matrix_env(TOOL)
    except ValueError as exc:
        return {"ok": False, "tool": TOOL, "action": "join", "error": str(exc)}

    url = f"{homeserver}/_matrix/client/v3/join/{urllib.parse.quote(room_id, safe='')}"
    request = urllib.request.Request(
        url,
        data=b"{}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "tool": TOOL, "action": "join", "roomId": room_id,
                "error": f"matrix join failed: HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "tool": TOOL, "action": "join", "roomId": room_id,
                "error": f"matrix join failed: {exc}"}

    if isinstance(data, dict) and data.get("room_id"):
        base["roomId"] = deps.canonical_room_id(data["room_id"])
    return base


def _joined_rooms(arguments: dict[str, Any], deps: InboxDeps) -> dict[str, Any]:
    """Current room membership.

    An invite appears in sync exactly once; after it is accepted the room is
    simply joined. A caller that tracked membership from invites alone would
    forget every room it had ever joined on the next restart, so membership is
    read from the server rather than remembered.
    """
    base: dict[str, Any] = {"ok": True, "tool": TOOL, "action": "rooms"}
    if arguments.get("dryRun"):
        base.update({"dryRun": True, "rooms": []})
        return base

    try:
        homeserver, token = deps.matrix_env(TOOL)
    except ValueError as exc:
        return {"ok": False, "tool": TOOL, "action": "rooms", "error": str(exc)}

    request = urllib.request.Request(
        f"{homeserver}/_matrix/client/v3/joined_rooms",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "tool": TOOL, "action": "rooms",
                "error": f"matrix joined_rooms failed: HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "tool": TOOL, "action": "rooms",
                "error": f"matrix joined_rooms failed: {exc}"}

    raw = data.get("joined_rooms") if isinstance(data, dict) else None
    base["rooms"] = [deps.canonical_room_id(r) for r in raw] if isinstance(raw, list) else []
    return base


def _invites(body: dict[str, Any], self_id: str, deps: InboxDeps) -> list[dict[str, Any]]:
    """Pending invites, each with the inviter and room name when advertised."""
    invited = ((body.get("rooms") or {}).get("invite") or {})
    result: list[dict[str, Any]] = []
    for room_id, room in invited.items():
        if not isinstance(room, dict):
            continue
        inviter = ""
        name = ""
        for event in ((room.get("invite_state") or {}).get("events") or []):
            if not isinstance(event, dict):
                continue
            etype = str(event.get("type") or "")
            content = event.get("content") if isinstance(event.get("content"), dict) else {}
            if (
                etype == "m.room.member"
                and content.get("membership") == "invite"
                and str(event.get("state_key") or "") == self_id
            ):
                inviter = str(event.get("sender") or "")
            elif etype == "m.room.name":
                name = str(content.get("name") or "")
        result.append(
            {"roomId": deps.canonical_room_id(room_id), "inviter": inviter, "name": name}
        )
    return result


def _backfill(arguments: dict[str, Any], payload: dict[str, Any], deps: InboxDeps) -> dict[str, Any]:
    """Page backwards through a gap reported by ``poll``.

    ``from`` is the ``prevBatch`` token of a gap (or the ``nextFrom`` of a
    previous backfill page). Events come back oldest-first like ``poll``.
    An empty ``nextFrom`` means the room's history is exhausted; otherwise the
    caller decides when to stop -- typically on the first already-seen event
    id, since that is where the gap meets known territory.
    """
    base: dict[str, Any] = {"ok": True, "tool": TOOL, "action": "backfill"}
    room_id = deps.canonical_room_id(arguments.get("roomId") or payload.get("roomId") or "")
    from_token = str(arguments.get("from") or payload.get("from") or "").strip()
    if not room_id or not from_token:
        return {"ok": False, "tool": TOOL, "action": "backfill", "error": "roomId and from are required"}

    limit = _limit(arguments.get("limit") or payload.get("limit"))
    mentions_only = _mentions_only(arguments, payload)

    if arguments.get("dryRun"):
        base.update({"dryRun": True, "roomId": room_id, "from": from_token, "events": [], "nextFrom": ""})
        return base

    try:
        homeserver, token = deps.matrix_env(TOOL)
    except ValueError as exc:
        return {"ok": False, "tool": TOOL, "action": "backfill", "error": str(exc)}

    params = {
        "dir": "b",
        "from": from_token,
        "limit": str(limit),
        "filter": json.dumps({"types": ["m.room.message"]}, separators=(",", ":")),
    }
    url = (
        f"{homeserver}/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}"
        f"/messages?{urllib.parse.urlencode(params)}"
    )
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "tool": TOOL, "action": "backfill", "error": f"matrix messages failed: HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "tool": TOOL, "action": "backfill", "error": f"matrix messages failed: {exc}"}
    if not isinstance(body, dict):
        body = {}

    self_id = deps.matrix_user_id()
    events = []
    for event in body.get("chunk") or []:
        normalized = _normalize_event(event, room_id, self_id)
        if normalized is not None and (not mentions_only or normalized["mentionsMe"]):
            events.append(normalized)
    events.sort(key=lambda item: item["ts"])

    base.update(
        {
            "roomId": room_id,
            "events": events,
            "nextFrom": str(body.get("end") or ""),
        }
    )
    return base


# ---- helpers ---------------------------------------------------------


def _limit(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, value))


def _wait_ms(raw: Any) -> int:
    """Long-poll window. Capped so an MCP call cannot hang a turn."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, min(30_000, value))


def _mentions_only(arguments: dict[str, Any], payload: dict[str, Any]) -> bool:
    for source in (arguments, payload):
        if "mentionsOnly" in source:
            return bool(source.get("mentionsOnly"))
        if "mentions_only" in source:
            return bool(source.get("mentions_only"))
    # Remote members and workers are addressed by mention; defaulting to True
    # keeps a busy team room from waking the local agent on every line.
    return True


def _room_filter(arguments: dict[str, Any], payload: dict[str, Any], deps: InboxDeps) -> set[str]:
    raw = arguments.get("rooms") or payload.get("rooms") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return set()
    return {deps.canonical_room_id(item) for item in raw if str(item).strip()}


def _matrix_sync(homeserver: str, token: str, since: str, wait_ms: int, limit: int) -> dict[str, Any]:
    sync_filter = json.dumps(
        {
            "room": {
                "timeline": {"limit": limit, "types": ["m.room.message"]},
                "ephemeral": {"types": []},
                "account_data": {"types": []},
                "state": {"types": []},
                # invite_state must survive the filter: a pending team-room
                # invite is how membership is offered, and the member id and
                # room name in it are what the caller's trust rule reads.
                "include_leave": False,
            },
            "presence": {"types": []},
        },
        separators=(",", ":"),
    )
    params = {"timeout": str(wait_ms), "filter": sync_filter}
    if since:
        params["since"] = since
    url = f"{homeserver}/_matrix/client/v3/sync?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
    timeout = (wait_ms / 1000.0) + 10.0
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8") or "{}")
    return data if isinstance(data, dict) else {}


def _normalize(
    body: dict[str, Any], self_id: str, deps: InboxDeps
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Flatten a sync body into (events oldest-first, per-room gap markers).

    A gap means the server truncated that room's timeline for this batch: the
    events between the previous cursor and ``prevBatch`` were dropped by the
    server, not by us, and are only reachable through ``backfill``.
    """
    joined = ((body.get("rooms") or {}).get("join") or {})
    events: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for room_id, room in joined.items():
        if not isinstance(room, dict):
            continue
        canonical = deps.canonical_room_id(room_id)
        timeline = room.get("timeline") or {}
        if timeline.get("limited") and timeline.get("prev_batch"):
            gaps.append({"roomId": canonical, "prevBatch": str(timeline["prev_batch"])})
        for event in (timeline.get("events") or []):
            normalized = _normalize_event(event, canonical, self_id)
            if normalized is not None:
                events.append(normalized)
    events.sort(key=lambda item: item["ts"])
    return events, gaps


def _normalize_event(event: Any, room_id: str, self_id: str) -> dict[str, Any] | None:
    if not isinstance(event, dict) or event.get("type") != "m.room.message":
        return None
    sender = str(event.get("sender") or "")
    if self_id and sender == self_id:
        return None  # never trigger on our own echo
    content = event.get("content")
    if not isinstance(content, dict):
        return None
    kind = _MSGTYPE_KIND.get(str(content.get("msgtype") or ""))
    if kind is None:
        return None

    relates = content.get("m.relates_to")
    thread_root = ""
    if isinstance(relates, dict) and relates.get("rel_type") == "m.thread":
        thread_root = str(relates.get("event_id") or "")

    return {
        "eventId": str(event.get("event_id") or ""),
        "roomId": room_id,
        "sender": sender,
        "ts": int(event.get("origin_server_ts") or 0),
        "kind": kind,
        "body": str(content.get("body") or ""),
        "mentionsMe": _mentions_me(content, self_id),
        "threadRootId": thread_root,
        "url": str(content.get("url") or "") if kind == "file" else "",
    }


def _mentions_me(content: dict[str, Any], self_id: str) -> bool:
    if not self_id:
        return False
    mentions = content.get("m.mentions")
    if isinstance(mentions, dict):
        user_ids = mentions.get("user_ids")
        if isinstance(user_ids, list) and any(str(uid) == self_id for uid in user_ids):
            return True
        if mentions.get("room") is True:
            return True
    # Fallback for clients that predate m.mentions. AgentTeams runtimes are
    # expected to send m.mentions, so this is a compatibility net only.
    localpart = self_id.split(":", 1)[0]
    body = str(content.get("body") or "")
    return self_id in body or (len(localpart) > 1 and localpart in body)
