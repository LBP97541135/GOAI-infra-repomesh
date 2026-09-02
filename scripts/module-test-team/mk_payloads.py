import json, sys
S = sys.argv[1]
exec_text = ("执行方式（必须照做）：在仓库根目录运行一次命令 `python environments/e2e-fixture-joint/run_round.py`，"
             "它从本任务上下文读取组合、按钉死 commit 检出到 itest-<run-id>/、跑场景并把证据写进 evidence/<run-id>/（然后自行拆除 itest 根）。"
             "运行结束后把它打印的最后一行结论原样报告，不要重复运行，不要修改任何业务仓库或组合文件，不要编辑或删除 evidence/ 下的文件，不要提交。"
             "平台的测试命令会再调用同一脚本做幂等复核。")
for name, combo, sha, prefix in (("b1_green_4.json", "green", "d453ffd9e2410a5e78f4f8fc4eefa53655dc2e69", "w4-b1-green-4"),
                                 ("b2_red.json", "red", "3c72ca6ff5c513b76789b2d4e621dd57487d8aec", "w4-b2-red")):
    p = json.load(open(f"{S}/b1_green.json", encoding="utf-8"))
    p["idempotency_prefix"] = prefix
    node = p["task_dag"][0]
    head = node["instruction"].split("执行方式：")[0]
    head = head.replace("combinations/green.json", f"combinations/{combo}.json").replace(
        "repomesh-e2e-pricing-core@d453ffd9e2410a5e78f4f8fc4eefa53655dc2e69", f"repomesh-e2e-pricing-core@{sha}")
    node["instruction"] = head + exec_text
    if combo == "red":
        p["requirement"] = "联调轮 B2（红组合）：对报价多币种「单测绿联调红」组合执行跨仓联调场景 multi-currency-joint，产出轮次证据。"
    json.dump(p, open(f"{S}/{name}", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(name, prefix, "backtick command present:", "`python environments/e2e-fixture-joint/run_round.py`" in node["instruction"])
