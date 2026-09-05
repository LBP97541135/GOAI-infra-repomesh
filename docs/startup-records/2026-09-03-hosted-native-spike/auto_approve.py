#!/usr/bin/env python3
"""Stand-in for the platform's future auto-approval: answer copaw Tool Guard prompts for the helper script only.

Polls one room; when the member posts "Waiting for approval" whose command contains base/tools/rm-work.sh,
replies with body "/approve" carrying m.mentions (bare body; a textual mention prefix breaks copaw's matcher).
Every prompt and decision is appended to output/hosted-native-e2e/2026-09-03/spike/approvals.jsonl.
Non-helper prompts are logged and left alone (they time out after 600 s = deny).
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("D:/Project4work/GOAI-infra-repomesh")
OUT = ROOT / "output/hosted-native-e2e/2026-09-03/spike"
HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
MX = "http://127.0.0.1:18080/_matrix/client/v3"
DOMAIN = "matrix-local.agentteams.io:18080"


def token() -> str:
    for line in (ROOT / ".secrets/platform-runtime.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("matrix token not found")


MT = token()


def log(entry: dict) -> None:
    entry["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with (OUT / "approvals.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(json.dumps(entry, ensure_ascii=False)[:300], flush=True)


def messages(room: str, limit: int = 30) -> list[dict]:
    url = f"{MX}/rooms/{urllib.parse.quote(room, safe='')}/messages?dir=b&limit={limit}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {MT}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        log({"kind": "error", "detail": str(exc)})
        return []
    return list(reversed([e for e in data.get("chunk", []) if e.get("type") == "m.room.message"]))


def approve(room: str, member: str) -> str:
    mention = f"@{member}:{DOMAIN}"
    txn = f"rm-spike-auto-{int(time.time() * 1000)}"
    url = f"{MX}/rooms/{urllib.parse.quote(room, safe='')}/send/m.room.message/{txn}"
    payload = {"msgtype": "m.text", "body": "/approve", "m.mentions": {"user_ids": [mention]}}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="PUT")
    req.add_header("Authorization", f"Bearer {MT}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode()).get("event_id", "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", choices=["team", "leader"], required=True)
    ap.add_argument("--member", required=True)
    ap.add_argument("--minutes", type=int, default=120)
    ap.add_argument("--pattern", default=r"base/tools/rm-work\.sh|base/tools/work\.sh")
    args = ap.parse_args()
    room = CONFIG["team_room_id"] if args.room == "team" else CONFIG["leader_room_id"]
    member_id = f"@{args.member}:{DOMAIN}"
    stop = OUT / f"STOP-approver-{args.room}"
    seen: set[str] = set()
    first = True
    started = time.time()
    log({"kind": "start", "room": args.room, "member": args.member, "pattern": args.pattern})
    while time.time() - started < args.minutes * 60 and not stop.exists():
        for ev in messages(room):
            eid = ev.get("event_id")
            if eid in seen:
                continue
            seen.add(eid)
            if first:
                continue  # skip history on the first poll
            body = ev.get("content", {}).get("body", "")
            if ev.get("sender") != member_id or "Waiting for approval" not in body:
                continue
            m = re.search(r'"command":\s*"((?:[^"\\]|\\.)*)"', body)
            command = m.group(1) if m else "<unparsed>"
            if re.search(args.pattern, command):
                sent = approve(room, args.member)
                log({"kind": "approved", "prompt_event": eid, "command": command, "approval_event": sent})
            else:
                log({"kind": "left_pending", "prompt_event": eid, "command": command})
        first = False
        time.sleep(5)
    log({"kind": "stop"})


if __name__ == "__main__":
    main()
