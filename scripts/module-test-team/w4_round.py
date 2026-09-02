"""Dispatch one W4 integration round and follow its worker task to a verdict.

Usage:
    python scripts/module-test-team/w4_round.py dispatch payloads/b1_green.json
    python scripts/module-test-team/w4_round.py poll <worker-task-id> [--timeout 900]
    python scripts/module-test-team/w4_round.py show <task-id>

``dispatch`` POSTs the payload to ``/bridge/materialize`` and prints the receipt
(``task_ids[1]`` is the worker task -- by receipt position, never by sorting
UUIDs). ``poll`` reads the task row from the disposable W4 postgres until the
status is terminal, then prints the Runner evidence the gateway wrote back.
The round verdict is *not* read from here: it lives in
``evidence/<run-id>/steps.json`` (``overall``) and ``round.md`` §4.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib import request

BASE = "http://127.0.0.1:8077/api/v1"
ACTION_TOKEN = "m8-console-token"
PG_CONTAINER = "repomesh-w4-pg"
TERMINAL = {"succeeded", "failed", "blocked", "cancelled"}


def _psql(sql: str) -> str:
    completed = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", "postgres", "-Atc", sql],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return completed.stdout.strip()


def task_row(task_id: str) -> tuple[str, str, str]:
    out = _psql(
        "select row_to_json(t) from (select status, title, result_summary "
        f"from task_orchestration.tasks where id = '{task_id}') t"
    )
    if not out:
        raise SystemExit(f"task {task_id} not found")
    row = json.loads(out)
    return str(row["status"]), str(row["title"]), str(row["result_summary"] or "")


def dispatch(payload_path: Path) -> dict:
    body = payload_path.read_bytes()
    req = request.Request(
        f"{BASE}/bridge/materialize",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {ACTION_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with request.urlopen(req, timeout=120) as resp:
        receipt = json.loads(resp.read())
    print(json.dumps(receipt, indent=2))
    return receipt


def poll(task_id: str, timeout: int) -> str:
    started = time.time()
    last = None
    while True:
        status, title, summary = task_row(task_id)
        if status != last:
            print(f"[{int(time.time() - started):4d}s] {task_id[:8]} {title[:60]!r} -> {status}")
            last = status
        if status in TERMINAL:
            break
        if time.time() - started > timeout:
            print("timeout; task still", status)
            return status
        time.sleep(5)
    show(task_id)
    return status


def show(task_id: str) -> None:
    status, title, summary = task_row(task_id)
    print(f"task {task_id}: status={status} title={title!r}")
    try:
        evidence = json.loads(summary)
    except json.JSONDecodeError:
        print("result_summary (text):", summary[:2000])
        return
    print("evidence:")
    print(json.dumps(evidence, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("dispatch")
    d.add_argument("payload", type=Path)
    d.add_argument("--follow", action="store_true", help="poll the worker task afterwards")
    d.add_argument("--timeout", type=int, default=900)
    p = sub.add_parser("poll")
    p.add_argument("task_id")
    p.add_argument("--timeout", type=int, default=900)
    s = sub.add_parser("show")
    s.add_argument("task_id")
    args = parser.parse_args()

    if args.command == "dispatch":
        receipt = dispatch(args.payload)
        if args.follow:
            ids = receipt.get("task_ids") or []
            if len(ids) < 2:
                print("no worker task in receipt")
                return 2
            poll(ids[1], args.timeout)
        return 0
    if args.command == "poll":
        poll(args.task_id, args.timeout)
        return 0
    show(args.task_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
