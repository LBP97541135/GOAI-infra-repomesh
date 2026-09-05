#!/usr/bin/env python3
"""Baseline scenes 07/08: create the multi-currency issue and run the four discovery steps
(no materialize).

Prints one line per step: time, endpoint, status, key fields.
Reads the action token from .secrets/platform.env.
"""

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime

ROOT = "D:/Project4work/GOAI-infra-repomesh"
API = "http://127.0.0.1:8000/api/v1"
MANAGER = "703b1dfa-024d-41f0-ab10-ce3ebec025c1"
RUN = os.environ.get("BASELINE_RUN", "bl0903")
REQUIREMENT = (
    "报价支持多币种（基线验收 2026-09-03）："
    "pricing-core 的 quote() 增加 currency 参数并按币种规则计算"
    "（零小数币种如 JPY 必须取整为整数金额）；"
    "checkout 的订单摘要与 billing 的发票渲染要按新契约展示带币种的金额。"
    "三个仓都要改并保持各自单测通过。"
)


def token() -> str:
    with open(f"{ROOT}/.secrets/platform.env", encoding="utf-8") as fh:
        lines = [
            line.split("=", 1)[1].strip()
            for line in fh
            if line.startswith("REPOMESH_AGENT_ACTION_TOKEN=")
        ]
    return lines[-1]


T = token()


def call(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {T}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode() or "null")


def stamp() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S")


def key(step: str) -> str:
    return f"{RUN}-{step}-{uuid.uuid4().hex[:6]}"


def read(issue: str):
    return call("GET", f"/issues/{issue}/discovery")[1]


def wait_idle(issue: str, label: str, limit: int = 300):
    t0 = time.time()
    while time.time() - t0 < limit:
        d = read(issue)
        if d.get("step_state") != "running":
            print(
                f"{stamp()}  [{label}] step={d.get('step')} state={d.get('step_state')} "
                f"({time.time() - t0:.0f}s)"
            )
            return d
        time.sleep(3)
    raise SystemExit(f"{label}: still running after {limit}s")


def trigger(issue: str, step: str, body: dict):
    status, payload = call(
        "POST",
        f"/issues/{issue}/discovery/{step}",
        {"created_by_agent_id": MANAGER, "idempotency_key": key(step), **body},
    )
    print(
        f"{stamp()}  POST /issues/{issue[:8]}/discovery/{step} -> {status} "
        f"{json.dumps(payload, ensure_ascii=False)[:160]}"
    )
    if status >= 400:
        raise SystemExit(f"{step} failed: {status}")
    return wait_idle(issue, step)


def main() -> None:
    issue_id = os.environ.get("BASELINE_ISSUE")
    if not issue_id:
        status, payload = call(
            "POST",
            "/issues",
            {
                "requirement_text": REQUIREMENT,
                "created_by_agent_id": MANAGER,
                "idempotency_key": key("issue"),
            },
        )
        print(
            f"{stamp()}  POST /issues -> {status} {json.dumps(payload, ensure_ascii=False)[:300]}"
        )
        if status != 201:
            raise SystemExit("issue creation failed")
        issue_id = payload["issue_id"]
    print(f"issue_id={issue_id}")
    d = read(issue_id)
    if not d.get("analysis"):
        d = trigger(issue_id, "analysis", {})
        analysis = d.get("analysis") or {}
        print(
            f"{stamp()}  analysis: sufficient={analysis.get('sufficient')} "
            f"confidence={analysis.get('confidence')} "
            f"questions={len(analysis.get('questions') or [])}"
        )
        if analysis.get("sufficient") is False:
            d = trigger(issue_id, "analysis", {"force_continue": True})
    if not d.get("candidates"):
        d = trigger(issue_id, "candidates", {"limit": 5})
    print(f"{stamp()}  candidates: {json.dumps(d.get('candidates'), ensure_ascii=False)[:300]}")
    if not d.get("classification"):
        d = trigger(issue_id, "classification", {})
    print(
        f"{stamp()}  classification: "
        f"{json.dumps(d.get('classification'), ensure_ascii=False)[:400]}"
    )
    if (d.get("approval") or {}).get("state") != "approved":
        status, payload = call(
            "POST",
            f"/issues/{issue_id}/discovery/approval",
            {
                "decided_by_agent_id": MANAGER,
                "idempotency_key": key("approval"),
                "decision": "approved",
                "evidence_version": d["classification_evidence_version"],
                "reason": "baseline acceptance 2026-09-03: approve classification as-is",
                "adjustments": [],
            },
        )
        print(
            f"{stamp()}  POST approval -> {status} {json.dumps(payload, ensure_ascii=False)[:200]}"
        )
        d = read(issue_id)
    if not d.get("plan_id") and not (d.get("plan") or {}).get("task_dag"):
        d = trigger(issue_id, "plan", {})
    print(
        f"{stamp()}  plan: step={d.get('step')} state={d.get('step_state')} "
        f"integration={json.dumps(d.get('integration'))} "
        f"tiers={json.dumps(d.get('effective_tiers'), ensure_ascii=False)[:300]}"
    )
    print(f"{stamp()}  plan_id={d.get('plan_id')} error={(d.get('plan') or {}).get('error')}")
    print(
        json.dumps(
            {
                k: d.get(k)
                for k in ("step", "step_state", "plan_id", "integration", "effective_tiers")
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
