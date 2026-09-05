#!/usr/bin/env python3
"""Wave-0 spike watcher: rooms (Matrix), shared drive (MinIO via the controller's mc), worker container state.

Appends to output/hosted-native-e2e/2026-09-03/spike/watch.log and rooms.jsonl. Stops when STOP file exists
or after --minutes. Tokens are read from .secrets and never written out.
"""
import argparse
import json
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("D:/Project4work/GOAI-infra-repomesh")
OUT = ROOT / "output/hosted-native-e2e/2026-09-03/spike"
OUT.mkdir(parents=True, exist_ok=True)
CONFIG = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))
MX = "http://127.0.0.1:18080/_matrix/client/v3"
CONTROLLER = "agentteams-controller"
WORKER = "agentteams-worker-agt-worker-dfb8a4cda6f7"
LEADER = "agentteams-worker-agt-leader-dfb8a4cda6f7"


def token() -> str:
    for line in (ROOT / ".secrets/platform-runtime.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("matrix token not found")


MT = token()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def log(line: str) -> None:
    text = f"{now()} {line}"
    print(text, flush=True)
    with (OUT / "watch.log").open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


def sh(cmd: list[str], timeout: int = 60) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return "<timeout>"


def room_messages(room: str, limit: int = 60) -> list[dict]:
    url = f"{MX}/rooms/{urllib.parse.quote(room, safe='')}/messages?dir=b&limit={limit}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {MT}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        log(f"matrix error: {exc}")
        return []
    events = [e for e in data.get("chunk", []) if e.get("type") == "m.room.message"]
    return list(reversed(events))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--attempt", required=True)
    ap.add_argument("--review", default=None, help="review attempt id (leader room) once it exists")
    ap.add_argument("--minutes", type=int, default=90)
    ap.add_argument("--interval", type=int, default=10)
    ap.add_argument("--no-rooms", action="store_true")
    ap.add_argument("--no-worker", action="store_true")
    args = ap.parse_args()
    team = CONFIG["team_name"]
    prefix = f"agentteams/agentteams-storage/teams/{team}/shared/tasks"
    seen: set[str] = set()
    last_drive = ""
    last_worker = ""
    last_leader_drive = ""
    started = time.time()
    stop_file = OUT / "STOP"
    log(f"watcher start attempt={args.attempt} review={args.review}")
    while time.time() - started < args.minutes * 60 and not stop_file.exists():
        # rooms
        for label, room in ([] if args.no_rooms else (("team", CONFIG["team_room_id"]), ("leader", CONFIG["leader_room_id"]))):
            for ev in room_messages(room):
                eid = ev.get("event_id")
                if eid in seen:
                    continue
                seen.add(eid)
                ts = datetime.fromtimestamp(ev["origin_server_ts"] / 1000, tz=timezone.utc).strftime("%H:%M:%S")
                body = ev.get("content", {}).get("body", "")
                with (OUT / "rooms.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"room": label, "ts": ts, "sender": ev.get("sender"), "event_id": eid, "body": body}, ensure_ascii=False) + "\n")
                log(f"[{label}] {ts} {ev.get('sender')} | {body[:600].replace(chr(10), ' / ')}")
        # shared drive
        drive = sh(["docker", "exec", CONTROLLER, "mc", "ls", "-r", f"{prefix}/{args.attempt}/"])
        drive = "\n".join(sorted(l.split("STANDARD")[-1].strip() + " " + l.split()[3] for l in drive.splitlines() if "STANDARD" in l))
        if drive != last_drive:
            last_drive = drive
            log("[drive] " + " | ".join(drive.splitlines()))
            meta = sh(["docker", "exec", CONTROLLER, "mc", "cat", f"{prefix}/{args.attempt}/meta.json"])
            try:
                m = json.loads(meta)
                log(f"[drive] meta.json status={m.get('status')} acknowledged_at={m.get('acknowledged_at')} submitted_at={m.get('submitted_at')} repomesh_block={'repomesh' in m}")
            except Exception:  # noqa: BLE001
                log("[drive] meta.json unreadable")
            if "result.md" in drive:
                res = sh(["docker", "exec", CONTROLLER, "mc", "cat", f"{prefix}/{args.attempt}/result.md"])
                log("[drive] result.md: " + res[:800].replace("\n", " / "))
        if args.review:
            ldrive = sh(["docker", "exec", CONTROLLER, "mc", "ls", "-r", f"{prefix}/{args.review}/"])
            ldrive = "\n".join(sorted(l.split("STANDARD")[-1].strip() + " " + l.split()[3] for l in ldrive.splitlines() if "STANDARD" in l))
            if ldrive != last_leader_drive:
                last_leader_drive = ldrive
                log("[review-drive] " + " | ".join(ldrive.splitlines()))
                if "result.md" in ldrive:
                    res = sh(["docker", "exec", CONTROLLER, "mc", "cat", f"{prefix}/{args.review}/result.md"])
                    log("[review-drive] result.md: " + res[:800].replace("\n", " / "))
        # worker container
        wcmd = (
            f"ls /work 2>/dev/null | tr '\\n' ' '; echo; "
            f"[ -d /work/{args.attempt}/.git ] && echo work_head=$(git -C /work/{args.attempt} rev-parse --short HEAD) "
            f"changed=$(git -C /work/{args.attempt} status --porcelain | wc -l | tr -d ' ') || echo no_workspace; "
            f"ls /root/.copaw-worker/agt-worker-dfb8a4cda6f7/.copaw/workspaces/default/shared/tasks/ 2>/dev/null | tr '\\n' ' '; echo; "
            f"ls /root/.copaw-worker/agt-worker-dfb8a4cda6f7/.copaw/workspaces/default/shared/tasks/{args.attempt}/ 2>/dev/null | tr '\\n' ' '"
        )
        wstate = "" if args.no_worker else sh(["docker", "exec", WORKER, "sh", "-c", wcmd]).strip()
        wstate = " | ".join(l.strip() for l in wstate.splitlines() if l.strip())
        if wstate != last_worker:
            last_worker = wstate
            log("[worker] " + wstate)
        time.sleep(args.interval)
    log("watcher stop")


if __name__ == "__main__":
    main()
