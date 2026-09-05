#!/usr/bin/env python3
"""Send one Matrix message as @admin into the team or leader room, @mentioning a member (same shape as
src/repomesh/integrations/agentteams/matrix.py:178-187: body = "<matrix id> <text>", m.mentions.user_ids).

Usage: send_room.py --room team|leader --to agt-worker-dfb8a4cda6f7 --text "..."
"""
import argparse
import json
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("D:/Project4work/GOAI-infra-repomesh")
CONFIG = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))
MX = "http://127.0.0.1:18080/_matrix/client/v3"
DOMAIN = "matrix-local.agentteams.io:18080"


def token() -> str:
    for line in (ROOT / ".secrets/platform-runtime.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("matrix token not found")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", choices=["team", "leader"], required=True)
    ap.add_argument("--to", required=True, help="localpart of the member to mention")
    ap.add_argument("--text", required=True)
    ap.add_argument("--bare", action="store_true", help="body = text only; the mention travels in m.mentions (needed for /approve)")
    args = ap.parse_args()
    room = CONFIG["team_room_id"] if args.room == "team" else CONFIG["leader_room_id"]
    mention = f"@{args.to}:{DOMAIN}"
    body = args.text if args.bare else f"{mention} {args.text}"
    txn = f"rm-spike-{uuid.uuid4().hex}"
    url = f"{MX}/rooms/{urllib.parse.quote(room, safe='')}/send/m.room.message/{urllib.parse.quote(txn, safe='')}"
    payload = {"msgtype": "m.text", "body": body, "m.mentions": {"user_ids": [mention]}}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="PUT")
    req.add_header("Authorization", f"Bearer {token()}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.loads(resp.read().decode())
    print(json.dumps({"sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "room": args.room, "room_id": room,
                      "txn": txn, "event_id": out.get("event_id"), "body": body}, ensure_ascii=False))


if __name__ == "__main__":
    main()
