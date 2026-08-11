/** 契约聚合 → 组件视图模型的派生层。
 *  只做展示派生（标签拼接、nullable 降级回退），不做状态映射：
 *  display_status / gate_display / phase 原样透传（契约 §5 是唯一映射实现）。
 *
 *  v1 控制台退役后只剩三个派生函数 + 一个时间格式化。原先的 deriveView / deriveChat
 *  是 v1 那张交付全貌页的整页装配（含 DAG 拓扑布局、门禁检查项拼装、演示叙事覆盖层
 *  合并），随 v1 一同移除。 */
import type {
  DecisionAction,
  DecisionItem,
  DeliveryAggregate,
  RepositoryDeliveryView,
} from "./api/contract";
import type { ApprovalInfo, Decision, RepositoryEnv } from "./types";

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
    urgency: item.kind === "approve" ? "now" : "soon",
    title: item.title,
    body: item.body,
    actions: item.actions.map((a) => ACTION_LABEL[a]),
    actionKinds: item.actions,
    repositoryId: item.repository_id,
    headSha: item.head_sha,
  }));
}

/** 授权单信息。head-bound 语义由后端保证（§4.4：SHA 漂移即 409）——本函数只把
 *  绑定对象呈现出来，不做任何判定。
 *
 *  `authority` 这个入参在 v1 用来接演示叙事层的审批人名；v2 的授权单改为显示
 *  从花名册派生的真实决策主体（见 api/decisions.ts），弹窗不再读这个字段，
 *  保留默认值只为形状不变。 */
export function approvalFromContract(
  agg: DeliveryAggregate,
  items: DecisionItem[],
  authority = "治理审批人",
): ApprovalInfo | null {
  const approve = items.find((d) => d.kind === "approve");
  if (!approve) return null;
  const cs = agg.change_set;
  const repo = cs?.repositories.find((r) => r.repository_id === approve.repository_id);
  const repoName = agg.repositories.find((r) => r.repository_id === approve.repository_id)?.name ?? "目标仓库";
  return {
    authority,
    snapshotLabel: agg.validation_snapshot
      ? `${agg.validation_snapshot.id} · IMMUTABLE`
      : approve.head_sha
        ? `HEAD ${shortSha(approve.head_sha)}`
        : "—",
    scopeLabel: repo?.pull_request_number != null ? `${repoName}（仅合并 PR #${repo.pull_request_number}）` : repoName,
    changeSetId: cs?.change_set_id ?? null,
    repositoryId: approve.repository_id,
    headSha: approve.head_sha,
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
      name: agg.repositories.find((x) => x.repository_id === r.repository_id)?.name ?? r.repository_id.slice(0, 8),
      gate: r.gate_display,
      isCurrent: r.repository_id === repositoryId,
    })),
  };
}

/** at：UTC ISO → HH:MM:SS；非 ISO 原样展示。
 *  防御：联调发现后端 repair_timeline.at 可为 null（契约写 string，已报后端），空值渲染 "—"。 */
export function eventTime(at: string | null | undefined): string {
  if (!at) return "—";
  const m = at.match(/T(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : at;
}
