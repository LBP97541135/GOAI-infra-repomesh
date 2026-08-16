/** 契约聚合 → 组件视图模型的派生层。
 *  只做展示派生（标签拼接、nullable 降级回退），不做状态映射：
 *  display_status / gate_display / phase 原样透传（契约 §5 是唯一映射实现）。 */
import type {
  DecisionAction,
  DecisionItem,
  DeliveryAggregate,
  RepositoryDeliveryView,
  TaskDisplayStatus,
} from "./api/contract";
import type {
  ApprovalInfo,
  DagExecutionView,
  Decision,
  EvidenceView,
  RepositoryEnv,
  TaskAgentReport,
} from "./types";
import { shortId } from "./display";

/** A-18：把 `tasks[].evidence` 改成 camelCase，别的什么都不做。
 *
 *  这里**没有**任何派生：`verified` 是读模型按契约 §5.4 算好的，blockers 与
 *  summary_text 是 agent 的原话。前端要是在这里补一句「大概是没跑测试」，就又成了
 *  一层没人核对过的转述——A-18 本来就是转述丢失造成的。
 *
 *  `evidence === null` 的任务不产出记录：它没有做过任何声明，标它「未验证」是替它
 *  发言。三种「没有」互不相同（没有证据 / 声明了未验证 / 验证过了），压成一种就撒谎。 */
export function agentReportsFromAggregate(
  agg: DeliveryAggregate,
  repositoryId?: string,
): TaskAgentReport[] {
  return agg.tasks
    // `!= null` 而非 `!== null` 是有意的（X6 运行时兜底）：本字段是【提案】，服务端
    // 尚未部署时 `evidence` 是 **undefined** 而不是 null。用严格比较会让 undefined
    // 通过筛选、随后在 `.verified` 上炸掉整页——把一个「后端还没跟上」渲染成白屏。
    // 两种「没有」在这里是同一件事：没有做过声明。
    .filter((task) => task.evidence != null)
    .filter((task) => repositoryId === undefined || task.repository_id === repositoryId)
    .map((task) => ({
      taskId: task.task_id,
      repositoryId: task.repository_id,
      title: task.title,
      // 原样透传（§5.1 是唯一映射实现）。带上它是因为「失败」和「未验证」在展示上
      // 必须分得开：失败的任务本来就不会有可核验的执行记录，再扣一个琥珀标记是
      // 把同一件事说两遍，还会让真正需要注意的那一层（跑成了但没验证）失去分量。
      displayStatus: task.display_status,
      verified: task.evidence!.verified,
      blockers: task.evidence!.blockers,
      summaryText: task.evidence!.summary_text,
      testCommand: task.evidence!.test_command,
      testResults: task.evidence!.test_results.map((r) => ({
        command: r.command,
        exitCode: r.exit_code,
        summary: r.summary,
      })),
      artifactCount: task.evidence!.artifact_count,
    }));
}

/** 该不该给这条任务打「未验证」标记。
 *
 *  **唯一实现**：DAG 节点计数、授权单清单、证据面配色三处共用，免得日后有一处漏改
 *  就成了「同一条任务在两屏一个有标记一个没有」。
 *
 *  失败的任务一律不打。它 `verified` 必然是 false（失败的跑法不可能留下绿的执行
 *  记录），但那不是新信息——失败本身已经说了，而且说得更响。再叠一个琥珀标记只会
 *  让「跑成了、却没验证」这一类——A-18 真正要人看见的那一类——混在失败堆里失去分量。 */
export function marksUnverified(report: TaskAgentReport): boolean {
  return !report.verified && report.displayStatus !== "failed";
}

/** 用 Record<DecisionAction,…> 而非 Record<string,…>：契约新增动作枚举时，
 *  这里缺一项就是编译错误，而不是运行期渲染出 undefined。 */
const ACTION_LABEL: Record<DecisionAction, string> = {
  approve_merge: "批准合并",
  view_evidence: "查看证据",
};

function shortSha(sha: string): string {
  return sha.slice(0, 8);
}

/** "https://<host>/o/r/pull/1" → "o/r#1"（host 不限 github.com）；无 PR 时 "未创建" */
function prLabel(repo: RepositoryDeliveryView): string {
  if (!repo.pull_request_url || repo.pull_request_number === null) return "未创建";
  const m = repo.pull_request_url.match(/^https?:\/\/[^/]+\/(.+?)\/pull\//);
  return `${m ? m[1] : "PR"}#${repo.pull_request_number}`;
}

/** §4.3 决策项 → 组件模型。决策夹是控制台唯一的写回路，派生只此一份。 */
export function decisionsFromContract(items: DecisionItem[]): Decision[] {
  return items.map((item) => ({
    id: item.id,
    kind: item.kind,
    title: item.title,
    body: item.body,
    actions: item.actions.map((a) => ACTION_LABEL[a]),
    actionKinds: item.actions,
    repositoryId: item.repository_id,
    headSha: item.head_sha,
  }));
}

/** 授权单信息。head-bound 语义由后端保证（§4.4：SHA 漂移即 409）——本函数只把
 *  绑定对象呈现出来，不做任何判定。审批人显示从花名册派生的真实决策主体
 *  （见 api/decisions.ts），不在此拼装。 */
export function approvalForDecision(
  agg: DeliveryAggregate,
  decision: { id: string; repositoryId: string | null; headSha: string | null },
): ApprovalInfo | null {
  // S1 修复：授权单按**用户点击的那张决策卡**构建。此前这里 find 第一个 approve
  // 项——多仓同时待批时，点 B 卡会弹出并提交 A 卡的仓库与 SHA（给未审仓库发
  // READY 的治理级缺陷）。
  if (!decision.repositoryId) return null;
  const cs = agg.change_set;
  const repo = cs?.repositories.find((r) => r.repository_id === decision.repositoryId);
  const repoName =
    agg.repositories.find((r) => r.repository_id === decision.repositoryId)?.name ?? "目标仓库";
  return {
    decisionId: decision.id,
    snapshotLabel: agg.validation_snapshot
      ? `${agg.validation_snapshot.id} · IMMUTABLE`
      : decision.headSha
        ? `HEAD ${shortSha(decision.headSha)}`
        : "—",
    scopeLabel: repo?.pull_request_number != null ? `${repoName}（仅合并 PR #${repo.pull_request_number}）` : repoName,
    changeSetId: cs?.change_set_id ?? null,
    repositoryId: decision.repositoryId,
    headSha: decision.headSha,
    // A-18：授权单绑的是**这个仓**这一次合并，所以只摆这个仓的未验证声明。
    // 摆整轮的会把别人的话算到这次授权头上——授权单说什么就得是它签什么。
    unverified: agentReportsFromAggregate(agg, decision.repositoryId).filter(marksUnverified),
  };
}

/** 环境窗（CONS-43）的单仓切片。v0.1 交付聚合是轮次粒度，本函数只做**切片与标签
 *  拼接**——`gate_display` 原样透传（§5 是唯一映射实现），diffstat 为 null 时只列
 *  文件名不编行数（§6.3）。 */
export function repositoryEnvFromAggregate(agg: DeliveryAggregate, repositoryId: string): RepositoryEnv | null {
  const info = agg.repositories.find((r) => r.repository_id === repositoryId);
  if (!info) return null;

  const csRepos = (agg.change_set?.repositories ?? []).slice().sort((a, b) => a.merge_order - b.merge_order);
  const mine = csRepos.find((r) => r.repository_id === repositoryId) ?? null;
  const runs = agg.diffs.filter((d) => d.repository_id === repositoryId);

  return {
    repositoryName: info.name,
    gateDisplay: mine?.gate_display ?? null,
    prLabel: mine ? prLabel(mine) : null,
    prUrl: mine?.pull_request_url ?? null,
    // §6.4 追认：合并请求发出后 merge_gate 为 null，不能当成「不允许」
    mergeAllowed: mine?.merge_gate?.allowed ?? null,
    changedFiles: runs.flatMap((r) => r.changed_files.map((path) => ({ path }))),
    commitShas: runs.map((r) => shortSha(r.commit_sha)),
    validationSnapshotId: agg.validation_snapshot?.id ?? null,
    siblings: csRepos.map((r) => ({
      name: agg.repositories.find((x) => x.repository_id === r.repository_id)?.name ?? shortId(r.repository_id),
      gate: r.gate_display,
      isCurrent: r.repository_id === repositoryId,
    })),
  };
}

/** 计划 DAG 的执行态着色输入（C-4）：本轮聚合的 `tasks[]` 按 `repository_id` 归拢。
 *
 *  **这里没有状态映射**：`display_status` 是读模型按 §5.1 算好的（后端 7 态 → 展示
 *  6 态），本函数只把它按仓分组、原样搬过去。函数里读不到 `backend_status`、也读不到
 *  rework 链，想改判也无从改起——这是有意的。
 *
 *  同仓多任务是真实形态（CI rework task 与父任务同仓，§5.2 的 attempt 就这么数）。
 *  多条**态一致**时那就是该仓的态；**态不一致**时读模型并没有给出「这个仓整体算什么」
 *  这一事实，所以归成 `null` 让调用方如实留白，而不是挑一条充数。 */
export function dagExecutionFromAggregate(agg: DeliveryAggregate, roundLabel: string): DagExecutionView {
  const seen = new Map<string, Set<TaskDisplayStatus>>();
  for (const task of agg.tasks) {
    const bucket = seen.get(task.repository_id) ?? new Set<TaskDisplayStatus>();
    bucket.add(task.display_status);
    seen.set(task.repository_id, bucket);
  }

  const byRepository: Record<string, TaskDisplayStatus | null> = {};
  const taskCountByRepository: Record<string, number> = {};
  for (const [repositoryId, statuses] of seen) {
    byRepository[repositoryId] = statuses.size === 1 ? [...statuses][0] : null;
  }
  for (const task of agg.tasks) {
    taskCountByRepository[task.repository_id] = (taskCountByRepository[task.repository_id] ?? 0) + 1;
  }

  // A-18：未验证是**另加一层**，不参与上面的态归拢。一条任务可以同时是 succeeded
  // 和未验证（live 的 6ba476ab 正是），把它并进态里就得二选一，那就又丢一个事实。
  const unverifiedCountByRepository: Record<string, number> = {};
  const blockerCountByRepository: Record<string, number> = {};
  const failureReasonsByRepository: Record<string, string[]> = {};
  for (const report of agentReportsFromAggregate(agg)) {
    // A-18 第四面：失败任务的理由（`changed_path_denied: …` 这类）此前无处可去——
    // 读模型给不出，界面就只能说一句「failed」。它是可执行的操作信息（「把 tests/
    // 加进 allowed_paths」），摆在图上比藏在弹窗里有用。
    if (report.displayStatus === "failed" && report.summaryText) {
      (failureReasonsByRepository[report.repositoryId] ??= []).push(report.summaryText);
    }
    if (!marksUnverified(report)) continue;
    unverifiedCountByRepository[report.repositoryId] =
      (unverifiedCountByRepository[report.repositoryId] ?? 0) + 1;
    blockerCountByRepository[report.repositoryId] =
      (blockerCountByRepository[report.repositoryId] ?? 0) + report.blockers.length;
  }

  return {
    byRepository,
    taskCountByRepository,
    unverifiedCountByRepository,
    blockerCountByRepository,
    failureReasonsByRepository,
    roundLabel,
  };
}

/** 证据面（B-3 最小版）：把决策指向的仓库在本轮聚合里的全部既有证据切出来。
 *  没有新数据源——CI/评审/门禁/治理/变更/快照全部来自 v0.1 §3 聚合；
 *  完整证据面（CI 原始报告、变更 diff 正文）是另一个后端能力，届时本函数只加字段。 */
export function evidenceFromAggregate(agg: DeliveryAggregate, repositoryId: string): EvidenceView | null {
  const mine = (agg.change_set?.repositories ?? []).find((r) => r.repository_id === repositoryId);
  if (!mine) return null;
  const info = agg.repositories.find((r) => r.repository_id === repositoryId);
  const runs = agg.diffs.filter((d) => d.repository_id === repositoryId);

  return {
    repositoryName: info?.name ?? shortId(repositoryId),
    headSha: mine.head_sha,
    baseSha: mine.base_sha,
    branchName: mine.branch_name,
    prLabel: mine.pull_request_url ? prLabel(mine) : null,
    prUrl: mine.pull_request_url,
    ciChecks: mine.ci_checks.map((c) => ({
      name: c.check_name,
      passed: c.passed,
      summary: c.summary,
      required: mine.required_checks.includes(c.check_name),
    })),
    reviews: mine.reviews.map((r) => ({ reviewer: r.reviewer, state: r.state, summary: r.summary })),
    requiredApprovals: mine.required_approvals,
    mergeGate: mine.merge_gate,
    governance: (agg.change_set?.governance_decisions ?? [])
      .filter((g) => g.repository_id === repositoryId)
      .map((g) => ({ decision: g.decision, headSha: g.head_sha, reason: g.reason, decidedAt: g.decided_at })),
    commits: runs.map((r) => ({ sha: shortSha(r.commit_sha), files: r.changed_files })),
    snapshot: agg.validation_snapshot
      ? {
          id: agg.validation_snapshot.id,
          status: agg.validation_snapshot.status,
          environmentHash: agg.validation_snapshot.environment_hash,
          expiresAt: agg.validation_snapshot.expires_at,
        }
      : null,
    agentReports: agentReportsFromAggregate(agg, repositoryId),
  };
}
