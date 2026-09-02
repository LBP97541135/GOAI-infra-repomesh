"""Derive the B1-4 and B2 round payloads from the B1 template (handoff §7.6).

Usage: ``python mk_payloads.py <payload-dir>``. Reads ``b1_green.json`` from
that directory and writes ``b1_green_4.json`` and ``b2_red.json`` next to it,
swapping the combination file, the pinned pricing-core commit and the
idempotency prefix, and replacing the instruction's execution paragraph.
"""

import json
import sys
from pathlib import Path

PAYLOAD_DIR = Path(sys.argv[1])

EXEC_TEXT = (
    "执行方式（必须照做）：在仓库根目录运行一次命令 "
    "`python environments/e2e-fixture-joint/run_round.py`，"
    "它从本任务上下文读取组合、按钉死 commit 检出到 itest-<run-id>/、"
    "跑场景并把证据写进 evidence/<run-id>/（然后自行拆除 itest 根）。"
    "运行结束后把它打印的最后一行结论原样报告，不要重复运行，"
    "不要修改任何业务仓库或组合文件，不要编辑或删除 evidence/ 下的文件，不要提交。"
    "平台的测试命令会再调用同一脚本做幂等复核。"
)
GREEN_SHA = "d453ffd9e2410a5e78f4f8fc4eefa53655dc2e69"
RED_SHA = "3c72ca6ff5c513b76789b2d4e621dd57487d8aec"
RED_REQUIREMENT = (
    "联调轮 B2（红组合）：对报价多币种「单测绿联调红」组合执行跨仓联调场景 "
    "multi-currency-joint，产出轮次证据。"
)
ROUNDS = (
    ("b1_green_4.json", "green", GREEN_SHA, "w4-b1-green-4"),
    ("b2_red.json", "red", RED_SHA, "w4-b2-red"),
)

for name, combo, sha, prefix in ROUNDS:
    payload = json.loads((PAYLOAD_DIR / "b1_green.json").read_text(encoding="utf-8"))
    payload["idempotency_prefix"] = prefix
    node = payload["task_dag"][0]
    head = node["instruction"].split("执行方式：")[0]
    head = head.replace("combinations/green.json", f"combinations/{combo}.json")
    head = head.replace(
        f"repomesh-e2e-pricing-core@{GREEN_SHA}", f"repomesh-e2e-pricing-core@{sha}"
    )
    node["instruction"] = head + EXEC_TEXT
    if combo == "red":
        payload["requirement"] = RED_REQUIREMENT
    (PAYLOAD_DIR / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    present = "`python environments/e2e-fixture-joint/run_round.py`" in node["instruction"]
    print(name, prefix, "backtick command present:", present)
