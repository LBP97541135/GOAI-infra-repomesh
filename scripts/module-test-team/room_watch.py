"""Tail Matrix rooms (and the console room stream) into an archive while a round runs.

Usage:
    python scripts/module-test-team/room_watch.py --tag r2 --rooms rooms.txt [--interval 3]

``rooms.txt`` holds one ``<label> <room_id>`` per line and is re-read every
cycle, so rooms that only exist after materialize can be appended without a
restart. Events come straight from Matrix (``/rooms/{id}/messages``, newest
50 per poll, de-duplicated by event id) using the server-side ``@admin``
token; the console's platform stream (``/api/v1/rooms/{id}/stream``) is
archived beside it for the rooms it knows. Output under
``output/bridge-team/w4-live/logs/rooms/<tag>/``:

    <label>.matrix.jsonl      every Matrix event, one per line
    <label>.platform.jsonl    every console stream item
    timeline.md               merged, chronological, human-readable

Stop with a ``STOP`` file in the output directory or ``--duration``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib import parse, request

ROOT = Path(__file__).resolve().parents[2]
SECRETS = ROOT / "output" / "bridge-team" / "w4-live" / "secrets"
MATRIX = "http://127.0.0.1:18080"
API = "http://127.0.0.1:8077/api/v1"
ACTION_TOKEN = "m8-console-token"


def _get(url: str, token: str) -> dict | None:
    req = request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"), strict=False)
    except Exception as error:  # noqa: BLE001 - a watcher never dies on one poll
        sys.stderr.write(f"poll failed {url[:80]}: {error}\n")
        return None


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat(timespec="milliseconds")


def _summary(event: dict) -> str:
    kind = event.get("type", "")
    content = event.get("content") or {}
    if kind == "m.room.message":
        body = str(content.get("body") or "")
        return body.replace("\r", "").replace("\n", " / ")
    if kind == "m.room.member":
        who = content.get("displayname") or event.get("state_key")
        return f"[member] {who} {content.get('membership')}"
    if kind == "m.room.create":
        return f"[create] {content.get('roomKind') or ''} by {content.get('createdBy') or ''}"
    return f"[{kind}] " + json.dumps(content, ensure_ascii=False)[:200]


def read_rooms(path: Path) -> list[tuple[str, str]]:
    rooms: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        label, room_id = line.split(None, 1)
        rooms.append((label, room_id.strip()))
    return rooms


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--rooms", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--duration", type=float, default=0, help="seconds; 0 = until STOP file")
    parser.add_argument("--out", type=Path, default=ROOT / "output/bridge-team/w4-live/logs/rooms")
    args = parser.parse_args()

    out = args.out / args.tag
    out.mkdir(parents=True, exist_ok=True)
    stop_file = out / "STOP"
    if stop_file.exists():
        stop_file.unlink()
    matrix_token = (SECRETS / "admin-matrix-token.txt").read_text(encoding="utf-8").strip()
    timeline = out / "timeline.md"
    if not timeline.exists():
        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        timeline.write_text(
            f"# Room timeline `{args.tag}` (started {started_at})\n\n", encoding="utf-8"
        )

    seen_matrix: dict[str, set[str]] = {}
    seen_platform: dict[str, set[str]] = {}
    started = time.time()
    cycles = 0
    print(f"watching -> {out} (STOP file ends it)", flush=True)
    while True:
        if stop_file.exists() or (args.duration and time.time() - started > args.duration):
            break
        rooms = read_rooms(args.rooms)
        new_lines: list[tuple[int, str]] = []
        for label, room_id in rooms:
            quoted = parse.quote(room_id, safe="")
            data = _get(
                f"{MATRIX}/_matrix/client/v3/rooms/{quoted}/messages?dir=b&limit=50",
                matrix_token,
            )
            if data is not None:
                seen = seen_matrix.setdefault(label, set())
                fresh = [e for e in data.get("chunk", []) if e.get("event_id") not in seen]
                fresh.sort(key=lambda e: e.get("origin_server_ts", 0))
                with (out / f"{label}.matrix.jsonl").open("a", encoding="utf-8") as fh:
                    for event in fresh:
                        seen.add(event["event_id"])
                        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                        ts = int(event.get("origin_server_ts", 0))
                        sender = event.get("sender", "")
                        new_lines.append(
                            (ts, f"| {_iso(ts)} | {label} | {sender} | {_summary(event)[:400]} |")
                        )
            stream = _get(f"{API}/rooms/{quoted}/stream?limit=200", ACTION_TOKEN)
            if stream is not None:
                seen = seen_platform.setdefault(label, set())
                with (out / f"{label}.platform.jsonl").open("a", encoding="utf-8") as fh:
                    for item in stream.get("items", []):
                        key = f"{item.get('at')}|{item.get('text')}|{item.get('payload_ref')}"
                        if key in seen:
                            continue
                        seen.add(key)
                        fh.write(json.dumps(item, ensure_ascii=False) + "\n")
                        at = item.get("at") or ""
                        try:
                            parsed = datetime.fromisoformat(at.replace("Z", "+00:00"))
                            ts = int(parsed.timestamp() * 1000)
                        except ValueError:
                            ts = int(time.time() * 1000)
                        text = str(item.get("text") or "").replace("\n", " / ")[:300]
                        source = item.get("source", "")
                        new_lines.append(
                            (ts, f"| {at} | {label}/console | {source} | {text} |")
                        )
        if new_lines:
            new_lines.sort(key=lambda pair: pair[0])
            with timeline.open("a", encoding="utf-8") as fh:
                for _ts, line in new_lines:
                    fh.write(line + "\n")
            print(f"+{len(new_lines)} events", flush=True)
        cycles += 1
        if cycles % 20 == 0:
            print(f"heartbeat cycle={cycles} rooms={len(rooms)}", flush=True)
        time.sleep(args.interval)
    print("stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
