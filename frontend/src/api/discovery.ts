/** 发现链数据源（批次 B-1/B-2）。契约 v0.4 §3.1 读 / §4.3+§4.5 写触发与轮询 /
 *  §5.2 审批。
 *
 *  **读有 replay 分支，写没有。**扫描那边（repositoryScan.ts）没有 replay 分支是因为
 *  「扫描是写外部世界的动作」；这里的理由更硬一层：发现链的每一步都会改变**步进器
 *  走到哪**，而那个判定的唯一实现在读模型（§3.2）。在回放模式里让写请求就地改夹具，
 *  等于在前端补了一份 §3.2 的影子实现——一旦与服务端漂移，界面会理直气壮地指错步。
 *  所以回放下五个写入口一律**如实拒绝**，由调用方把这句话显示出来。 */
import type {
  DiscoveryAnalysisRequest,
  DiscoveryApprovalRequest,
  DiscoveryCandidatesRequest,
  DiscoveryStepRequest,
  DiscoveryTaskView,
  DiscoveryWriteReceipt,
  DiscoveryView,
} from "./contract";
import { defaultClient } from "./client";
import { resolveDataSourceMode } from "./source";
import { DISCOVERY_FIXTURE_DEFAULT, discoveryFixtures, discoveryTaskFixture } from "../data/discovery";

const REPLAY_REFUSAL =
  "回放模式不写后端：发现链每一步都会改变步进器位置，而那个判定只在读模型里（契约 §3.2）——" +
  "回放里就地改夹具就等于在前端补一份影子判定。加 ?source=live 后可真实触发，链接已保留。";

function refuseInReplay(): void {
  if (resolveDataSourceMode() === "replay") throw new Error(REPLAY_REFUSAL);
}

/** 回放形态选择：`?discovery=<name>`，取值见 data/discovery.ts 的夹具表。
 *  这是**自检开关**（本批后端未合并，写路径无从 live 验证），不是业务参数：
 *  live 模式下它完全不参与取数。 */
function replayFixture(): DiscoveryView {
  const name = new URLSearchParams(window.location.search).get("discovery");
  const picked = name ? discoveryFixtures[name] : undefined;
  if (name && !picked) {
    // 名字打错时不静默回落到默认形态——那会让人以为自己在看 A 其实在看 B
    throw new Error(
      `回放夹具没有形态「${name}」。可选：${Object.keys(discoveryFixtures).join(" / ")}`,
    );
  }
  return picked ?? discoveryFixtures[DISCOVERY_FIXTURE_DEFAULT];
}

/** §3.1。issue 存在但从未发起发现 → 200 空块（不是 404）；issue 不存在 → 404。 */
export function fetchDiscovery(issueId: string): Promise<DiscoveryView> {
  if (resolveDataSourceMode() === "replay") {
    // 同步抛出的错误也要走 Promise 通道，否则调用方的 .catch 接不到
    return Promise.resolve().then(replayFixture);
  }
  return defaultClient().getDiscovery(issueId);
}

/** §4.5 轮询。终态后调用方**仍必须重取** `fetchDiscovery`：任务视图不投影结果。 */
export function fetchDiscoveryTask(issueId: string, taskId: string): Promise<DiscoveryTaskView> {
  if (resolveDataSourceMode() === "replay") {
    return taskId === discoveryTaskFixture.task_id
      ? Promise.resolve(discoveryTaskFixture)
      : Promise.reject(new Error(`回放夹具未覆盖任务 ${taskId}`));
  }
  return defaultClient().getDiscoveryTask(issueId, taskId);
}

export function triggerAnalysis(
  issueId: string,
  payload: DiscoveryAnalysisRequest,
): Promise<DiscoveryWriteReceipt> {
  refuseInReplay();
  return defaultClient().postDiscoveryAnalysis(issueId, payload);
}

export function triggerCandidates(
  issueId: string,
  payload: DiscoveryCandidatesRequest,
): Promise<DiscoveryWriteReceipt> {
  refuseInReplay();
  return defaultClient().postDiscoveryCandidates(issueId, payload);
}

export function triggerClassification(
  issueId: string,
  payload: DiscoveryStepRequest,
): Promise<DiscoveryWriteReceipt> {
  refuseInReplay();
  return defaultClient().postDiscoveryClassification(issueId, payload);
}

export function triggerPlan(
  issueId: string,
  payload: DiscoveryStepRequest,
): Promise<DiscoveryWriteReceipt> {
  refuseInReplay();
  return defaultClient().postDiscoveryPlan(issueId, payload);
}

/** §5.2 同步端点。回执三字段（`task_id` 恒 null）不投影结果，故不消费——
 *  写完一律重取读投影（§4.5 同一条）。 */
export async function submitDiscoveryApproval(
  issueId: string,
  payload: DiscoveryApprovalRequest,
): Promise<void> {
  refuseInReplay();
  await defaultClient().postDiscoveryApproval(issueId, payload);
}

/** §4.1 幂等键：**随表单生成的随机 UUID**（设计稿 ②「幂等键随表单生成」，Q9
 *  「每步一个键」）。
 *
 *  与治理决策那把**按内容确定性生成**的键（decisions.ts）是有意的两套语义：
 *  那边重提同一决策应当被去重，这里「用同样的需求再评一次分」是**合法的重跑**，
 *  内容哈希会把它误判成重放并原样返回旧结果。所以这里必须随机，且由调用方持有——
 *  重试沿用同一把，改了表单再提交换新的一把。
 *
 *  前缀带步名只为让服务端审计日志可读，不参与去重语义。 */
export function newIdempotencyKey(step: "analysis" | "candidates" | "classification" | "plan" | "approval"): string {
  return `console-discovery-${step}-${crypto.randomUUID()}`;
}
