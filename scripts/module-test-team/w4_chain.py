"""Drive the console discovery chain on the W4 stack (handoff §7.6 step 7).

Product path only: the same triggers the panel presses, polled through
GET /issues/{id}/discovery's step_state. The approval carries tier
adjustments so the business plan covers pricing-fixture alone — the test
team must enter the topology through S-1's append, not through a plan node.
"""
import json, os, sys, time, uuid
import httpx

BASE = os.environ.get("W4_API", "http://127.0.0.1:8077/api/v1")
H = {"Authorization": "Bearer " + os.environ.get("W4_TOKEN", "m8-console-token")}
MANAGER = "22222222-0000-4000-8000-000000000002"
RUN = os.environ.get("W4_RUN", uuid.uuid4().hex[:6])
c = httpx.Client(base_url=BASE, headers=H, timeout=60)


def key(step):
    return f"w4-{RUN}-{step}-{uuid.uuid4().hex[:6]}"


def read(issue):
    r = c.get(f"/issues/{issue}/discovery"); r.raise_for_status(); return r.json()


def wait_idle(issue, label, limit=600):
    t0 = time.time()
    while time.time() - t0 < limit:
        d = read(issue)
        if d.get("step_state") != "running":
            print(f"  [{label}] step={d.get('step')} state={d.get('step_state')} ({time.time()-t0:.0f}s)")
            return d
        time.sleep(3)
    raise SystemExit(f"{label}: still running after {limit}s")


def trigger(issue, step, body):
    r = c.post(f"/issues/{issue}/discovery/{step}", json={"created_by_agent_id": MANAGER, "idempotency_key": key(step), **body})
    print(f"  POST {step} -> {r.status_code} {r.text[:160]}")
    r.raise_for_status()
    return wait_idle(issue, step)


issue_id = os.environ.get("W4_ISSUE")
if not issue_id:
    r = c.post("/issues", json={
        "requirement_text": "pricing-fixture 的 calculate_total 需要按 tax_rate 计税（当前直接返回 subtotal，自带测试失败）。只改 pricing-fixture 仓。",
        "created_by_agent_id": MANAGER, "idempotency_key": key("issue")})
    print("issue", r.status_code, r.text[:200]); r.raise_for_status()
    issue_id = r.json()["issue_id"]
print("issue_id =", issue_id)

d = read(issue_id)
if not (d.get("analysis") or {}):
    d = trigger(issue_id, "analysis", {})
    analysis = d.get("analysis") or {}
    print("  analysis:", {k: analysis.get(k) for k in ("sufficient", "confidence", "questions")})
    if analysis.get("sufficient") is False:
        d = trigger(issue_id, "analysis", {"force_continue": True})
d = read(issue_id)
if not d.get("candidates"):
    d = trigger(issue_id, "candidates", {"limit": 5})
print("  candidates:", json.dumps(d.get("candidates"), ensure_ascii=False)[:400])
if not d.get("classification"):
    d = trigger(issue_id, "classification", {})
print("  classification:", json.dumps(d.get("classification"), ensure_ascii=False)[:600])
print("  approval block:", json.dumps(d.get("approval"), ensure_ascii=False)[:200])
if (d.get("approval") or {}).get("state") != "approved":
    ev = d["classification_evidence_version"]
    r = c.post(f"/issues/{issue_id}/discovery/approval", json={
        "decided_by_agent_id": MANAGER, "idempotency_key": key("approval"), "decision": "approved",
        "evidence_version": ev, "reason": "W4 验收：业务计划只覆盖 pricing-fixture",
        "adjustments": []})
    print("  approval ->", r.status_code, r.text[:200]); r.raise_for_status()
    d = read(issue_id)
if not (d.get("plan") or {}).get("task_dag") and not d.get("plan_id"):
    d = trigger(issue_id, "plan", {})
plan = d.get("plan") or {}
print("  plan repositories:", [n.get("repository") for n in (plan.get("task_dag") or [])], "error:", plan.get("error"))
if os.environ.get("W4_MATERIALIZE", "1") == "1":
    for attempt in range(1, 6):
        r = c.post(f"/issues/{issue_id}/discovery/materialize", json={"created_by_agent_id": MANAGER, "idempotency_key": f"w4-{RUN}-materialize"})
        print(f"  materialize attempt {attempt} -> {r.status_code} {r.text[:300]}")
        if r.status_code == 200:
            break
        if r.status_code == 503 and "rooms" in r.text:
            time.sleep(20); continue
        break
print(json.dumps({"issue_id": issue_id, "materialization": read(issue_id).get("materialization")}, ensure_ascii=False))
