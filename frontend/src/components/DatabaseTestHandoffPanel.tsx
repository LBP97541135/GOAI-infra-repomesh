import { useCallback, useEffect, useState } from "react";
import type { DatabaseTestHandoffView } from "../api/contract";
import { defaultClient } from "../api/client";

const ACTIVE = new Set(["planned", "testing", "provisioning", "validating", "cleaning"]);
const LABELS: Record<string, string> = {
  planned: "测试计划已生成",
  testing: "测试团队执行中",
  evidence_ready: "测试证据已完成",
  provisioning: "正在创建数据库 Branch",
  validating: "数据库验证中",
  passed: "数据库验证通过",
  failed: "数据库验证失败",
  cleaned: "验证完成且环境已清理",
  blocked_external: "外部数据库环境不可用",
  test_team_rework: "测试计划需要返工",
};

export function DatabaseTestHandoffPanel({ taskId }: { taskId: string }) {
  const [handoff, setHandoff] = useState<DatabaseTestHandoffView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const load = useCallback(async () => {
    try {
      setError(null);
      const next = await defaultClient().getDatabaseTestHandoff(taskId);
      setHandoff(next);
      return next;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return null;
    }
  }, [taskId]);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      if (cancelled) return;
      const next = await load();
      if (!cancelled && next && ACTIVE.has(next.status)) timer = window.setTimeout(poll, 3000);
    };
    void poll();
    return () => { cancelled = true; if (timer !== undefined) window.clearTimeout(timer); };
  }, [load, tick]);
  if (error) return <div className="mt-4 rounded-hard border border-salmon px-3 py-3 text-[12px] text-salmon">数据库测试状态读取失败：{error}<button className="ml-2 underline" onClick={() => setTick((value) => value + 1)}>重试</button></div>;
  if (!handoff) return <div className="mt-4 rounded-hard border border-line px-3 py-3 text-[12px] text-tx2">正在读取数据库测试状态…</div>;
  return <div className="mt-4 rounded-hard border border-line bg-panel px-3.5 py-3" data-testid="database-test-handoff">
    <div className="flex flex-wrap items-center gap-2"><span className="microlabel">数据库测试</span><span className="rounded-hard border border-amber px-2 py-px text-[11px] text-amber-hi">{LABELS[handoff.branchValidationStatus ?? handoff.status] ?? handoff.status}</span></div>
    <div className="mt-2 grid gap-1 text-[11.5px] text-tx2"><div>候选提交：<span className="font-mono text-tx">{handoff.candidateSha.slice(0, 12)}</span></div><div>影响表：{handoff.affectedTables.join(", ") || "无"}</div><div>必需检查：{handoff.requiredChecks.join(", ") || "无"}</div>{handoff.evidenceRef && <div>证据：<span className="font-mono text-tx">{handoff.evidenceRef}</span></div>}</div>
  </div>;
}
