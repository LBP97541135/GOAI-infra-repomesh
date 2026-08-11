/** 契约聚合 → 组件视图模型的派生层。
 *  只做展示派生（DAG 布局、标签拼接、nullable 降级回退），不做状态映射：
 *  display_status / gate_display / phase 原样透传（契约 §5 是唯一映射实现）。 */
import type {
  CollaborationMessageView,
  DecisionAction,
  DecisionItem,
  DeliveryAggregate,
  DeliveryTaskView,
  RepositoryDeliveryView,
} from "./api/contract";
import type { DeliveryData } from "./api/source";
import type {
  ApprovalInfo,
  ChatMessage,
  Decision,
  DeliveryEvent,
  DeliveryTask,
  DeliveryView,
  GateCheck,
  RepoDiff,
  RepoGate,
  RepositoryEnv,
} from "./types";

const ACTION_LABEL: Record<DecisionAction, string> = {
  approve_merge: "批准合并",
  view_evidence: "查看证据",
};

/** DAG 拓扑层（纯布局，非状态） */
function topoCols(tasks: DeliveryTaskView[]): Map<string, number> {
  const byId = new Map(tasks.map((t) => [t.task_id, t]));
  const memo = new Map<string, number>();
  const depth = (id: string): number => {
    const known = memo.get(id);
    if (known !== undefined) return known;
    memo.set(id, 0); // 环保护
    const t = byId.get(id);
    const d = !t || t.depends_on.length === 0 ? 0 : Math.max(...t.depends_on.map(depth)) + 1;
    memo.set(id, d);
    return d;
  };
  for (const t of tasks) depth(t.task_id);
  return memo;
}

function shortSha(sha: string): string {
  return sha.slice(0, 8);
}

/** "https://<host>/o/r/pull/1" → "o/r#1"（host 不限 github.com）；无 PR 时 "未创建" */
function prLabel(repo: RepositoryDeliveryView): string {
  if (!repo.pull_request_url || repo.pull_request_number === null) return "未创建";
  const m = repo.pull_request_url.match(/^https?:\/\/[^/]+\/(.+?)\/pull\//);
  return `${m ? m[1] : "PR"}#${repo.pull_request_number}`;
}

function gateChecks(repo: RepositoryDeliveryView): GateCheck[] {
  const done = new Map(repo.ci_checks.map((c) => [c.check_name, c]));
  const checks: GateCheck[] = repo.required_checks.map((name) => {
    const c = done.get(name);
    if (c) return { name, s: c.passed ? "pass" : "fail", note: c.summary };
    return { name, s: repo.gate_display === "running" ? "run" : "wait", note: repo.gate_display === "running" ? "CI 运行中" : "排队" };
  });
  for (const c of repo.ci_checks) {
    if (!repo.required_checks.includes(c.check_name)) {
      checks.push({ name: c.check_name, s: c.passed ? "pass" : "fail", note: c.summary });
    }
  }
  if (repo.required_approvals > 0) {
    const approved = repo.reviews.filter((r) => r.state === "approved");
    const changes = repo.reviews.filter((r) => r.state === "changes_requested");
    checks.push(
      changes.length > 0
        ? { name: "独立 Review", s: "fail", note: changes[0].summary }
        : approved.length >= repo.required_approvals
          ? { name: "独立 Review", s: "pass", note: approved[0].summary }
          : { name: "独立 Review", s: "wait", note: `等待 ${repo.required_approvals} 个必需 Review` },
    );
  }
  return checks;
}

function deriveDiffs(data: DeliveryData): RepoDiff[] {
  if (data.overlay?.repoDiffs) return data.overlay.repoDiffs;
  const agg = data.aggregate;
  if (!agg) return [];
  // 契约 §6.3：diffstat 为 null → 只列文件名，±行数占位
  return agg.repositories.map((repo) => {
    const runs = agg.diffs.filter((d) => d.repository_id === repo.repository_id);
    return {
      id: repo.name,
      note: runs.length > 0 ? runs.map((r) => shortSha(r.commit_sha)).join(" · ") : "尚无变更",
      files: runs.flatMap((r) => r.changed_files.map((path) => ({ path }))),
    } satisfies RepoDiff;
  });
}

/** §4.3 决策项 → 组件模型。v1 交付控制台与 v2 issue 详情页共用这一份，
 *  两处各写一套就会漂移（决策夹是控制台唯一的写回路，漂移代价高）。 */
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

function deriveDecisions(data: DeliveryData): Decision[] {
  return [...decisionsFromContract(data.decisions.items), ...(data.overlay?.extraDecisions ?? [])];
}

/** 授权单信息。head-bound 语义由后端保证（§4.4：SHA 漂移即 409）——本函数只把
 *  绑定对象呈现出来，不做任何判定。v1 与 v2 共用。 */
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
    snapshotLabel: agg.validation_snapshot ? `${agg.validation_snapshot.id} · IMMUTABLE` : approve.head_sha ? `HEAD ${shortSha(approve.head_sha)}` : "—",
    scopeLabel: repo?.pull_request_number != null ? `${repoName}（仅合并 PR #${repo.pull_request_number}）` : repoName,
    changeSetId: cs?.change_set_id ?? null,
    repositoryId: approve.repository_id,
    headSha: approve.head_sha,
  };
}

function deriveApproval(data: DeliveryData): ApprovalInfo | null {
  if (!data.aggregate) return null;
  return approvalFromContract(data.aggregate, data.decisions.items, data.overlay?.approvalAuthority ?? undefined);
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

export function deriveView(data: DeliveryData): DeliveryView | null {
  const agg = data.aggregate;
  if (!agg) return null;
  const ov = data.overlay;

  const listItem = data.list.projects
    .flatMap((p) => p.deliveries)
    .find((d) => d.delivery_id === agg.delivery_id);

  const repoName = new Map(agg.repositories.map((r) => [r.repository_id, r.name]));
  const lanes = agg.repositories.map((r) => r.name);
  const cols = topoCols(agg.tasks);
  const keyOf = new Map(agg.tasks.map((t, i) => [t.task_id, t.task_key ?? `T${i + 1}`]));

  const visibleTasks = agg.tasks.filter((t) => t.backend_status !== "superseded");
  // 同泳道同拓扑层的任务（如原任务 + 返工任务）横向顺延，避免 DAG 节点重叠
  const usedCells = new Set<string>();
  const placeCol = (lane: number, col: number): number => {
    let c = col;
    while (usedCells.has(`${lane}:${c}`)) c += 1;
    usedCells.add(`${lane}:${c}`);
    return c;
  };
  const tasks: DeliveryTask[] = visibleTasks.map((t) => {
    const lane = Math.max(0, lanes.indexOf(repoName.get(t.repository_id) ?? ""));
    return {
      id: keyOf.get(t.task_id) ?? t.task_id.slice(0, 6),
      taskId: t.task_id,
      repo: repoName.get(t.repository_id) ?? t.repository_id,
      col: placeCol(lane, cols.get(t.task_id) ?? 0),
      lane,
      title: t.title,
      status: t.display_status,
      agent: t.agent,
      attempt: t.attempt,
      detail: t.result_summary,
      deps: t.depends_on.map((d) => keyOf.get(d) ?? d.slice(0, 6)),
      repair: t.repair_timeline.map((r) => ({ at: eventTime(r.at), what: r.what })),
      escalated: t.escalated_to_human,
    };
  });

  const csRepos = (agg.change_set?.repositories ?? []).slice().sort((a, b) => a.merge_order - b.merge_order);
  const gates: RepoGate[] = csRepos.map((r) => ({
    repo: repoName.get(r.repository_id) ?? r.repository_id,
    state: r.gate_display,
    checks: gateChecks(r),
    pr: prLabel(r),
    prUrl: r.pull_request_url,
    mergeAllowed: r.merge_gate?.allowed ?? false,
    merged: r.merge_sha !== null,
  }));

  const events: DeliveryEvent[] = data.events.items.map((e) => ({ at: eventTime(e.at), kind: e.kind, text: e.text }));

  const mergeOrderLabel =
    ov?.mergeOrderLabel ??
    (agg.plan.merge_order.length > 0 ? agg.plan.merge_order.map((id) => repoName.get(id) ?? id).join(" → ") : null);

  return {
    label: ov?.deliveryLabel ?? `DLV-${agg.delivery_id.slice(0, 8)}`,
    projectLabel: agg.project.project_key ?? agg.project.title,
    title: agg.project.title,
    // 诚实数据：列表中找不到该交付时不编造 phase（null → 隐藏徽标）
    phase: listItem?.phase ?? null,
    phaseNote: listItem?.phase_note ?? null,
    createdAt: agg.project.created_at.slice(0, 10),
    requirement: agg.project.requirement_text,
    runLabel: ov?.runLabel ?? null,
    matrixRoom: ov?.matrixAlias ?? agg.matrix_room_id,
    traceId: agg.trace_id,
    // 契约 §6.4：cost 为 null → 隐藏成本行（overlay 提供演示值）
    costLabel: ov?.costLabel ?? null,
    snapshotLabel: agg.validation_snapshot?.id ?? null,
    stagingNote: ov?.stagingNote ?? null,
    planRev: agg.plan.plan_version,
    mergeOrderLabel,
    // 契约 4744c71：contract 整块可为 null（未建 ENGINEERING spec）
    contract: agg.contract
      ? {
          version: agg.contract.version,
          status: agg.contract.status,
          goal: agg.contract.goal,
          acceptance: agg.contract.acceptance,
          // 契约 §6.2：non_goals 为 null → 隐藏区块（overlay 提供演示值）
          nonGoals: agg.contract.non_goals ?? ov?.nonGoals ?? null,
          repositories: lanes,
          allowedPaths: agg.contract.allowed_paths,
          forbiddenPaths: agg.contract.forbidden_paths,
          tests: agg.contract.tests,
        }
      : null,
    repos: agg.repositories.map((r) => ({ id: r.name, evidence: r.evidence })),
    lanes,
    tasks,
    gates,
    repoDiffs: deriveDiffs(data),
    events,
    decisions: deriveDecisions(data),
    rollbackPlan: ov?.rollbackPlan ?? null,
    envProcesses: ov?.envProcesses ?? [],
    approval: deriveApproval(data),
  };
}

/** 房间流初始消息：回放用叙事层；live 用 §4.2 CollaborationMessageView 直投影。
 *  sender_name/recipient_name 可 null（§6 降级：回退 agent id 短版，不编名字）。 */
export function deriveChat(data: DeliveryData): ChatMessage[] {
  if (data.overlay) return data.overlay.chat;
  return data.messages.items.map((m: CollaborationMessageView): ChatMessage => {
    const author = m.sender_name ?? `AGENT ${m.sender_agent_id.slice(0, 8)}`;
    const recipient = m.recipient_name ?? m.recipient_agent_id.slice(0, 8);
    return {
      id: m.id,
      author,
      role: "AGENT",
      time: eventTime(m.created_at),
      tone: "agent",
      text: `${m.subject} — ${m.body}（→ ${recipient} · ${m.status}）`,
    };
  });
}
