/** 契约聚合 → 组件视图模型的派生层。
 *  只做展示派生（DAG 布局、标签拼接、nullable 降级回退），不做状态映射：
 *  display_status / gate_display / phase 原样透传（契约 §5 是唯一映射实现）。 */
import type {
  CollaborationMessageView,
  DecisionAction,
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

/** "https://github.com/o/r/pull/1" → "o/r#1"；无 PR 时 "未创建" */
function prLabel(repo: RepositoryDeliveryView): string {
  if (!repo.pull_request_url || repo.pull_request_number === null) return "未创建";
  const m = repo.pull_request_url.match(/github\.com\/([^/]+\/[^/]+)\/pull\//);
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

function deriveDecisions(data: DeliveryData): Decision[] {
  const fromApi: Decision[] = data.decisions.items.map((item) => ({
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
  return [...fromApi, ...(data.overlay?.extraDecisions ?? [])];
}

function deriveApproval(data: DeliveryData): ApprovalInfo | null {
  const agg = data.aggregate;
  const approve = data.decisions.items.find((d) => d.kind === "approve");
  if (!agg || !approve) return null;
  const cs = agg.change_set;
  const repo = cs?.repositories.find((r) => r.repository_id === approve.repository_id);
  const repoName = agg.repositories.find((r) => r.repository_id === approve.repository_id)?.name ?? "目标仓库";
  return {
    authority: data.overlay?.approvalAuthority ?? "治理审批人",
    snapshotLabel: agg.validation_snapshot ? `${agg.validation_snapshot.id} · IMMUTABLE` : approve.head_sha ? `HEAD ${shortSha(approve.head_sha)}` : "—",
    scopeLabel: repo?.pull_request_number != null ? `${repoName}（仅合并 PR #${repo.pull_request_number}）` : repoName,
    changeSetId: cs?.change_set_id ?? null,
    repositoryId: approve.repository_id,
    headSha: approve.head_sha,
  };
}

/** at：UTC ISO → HH:MM:SS；非 ISO 原样展示 */
function eventTime(at: string): string {
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
  const tasks: DeliveryTask[] = visibleTasks.map((t) => ({
    id: keyOf.get(t.task_id) ?? t.task_id.slice(0, 6),
    taskId: t.task_id,
    repo: repoName.get(t.repository_id) ?? t.repository_id,
    col: cols.get(t.task_id) ?? 0,
    lane: Math.max(0, lanes.indexOf(repoName.get(t.repository_id) ?? "")),
    title: t.title,
    status: t.display_status,
    agent: t.agent,
    attempt: t.attempt,
    detail: t.result_summary,
    deps: t.depends_on.map((d) => keyOf.get(d) ?? d.slice(0, 6)),
    repair: t.repair_timeline.map((r) => ({ at: eventTime(r.at), what: r.what })),
    escalated: t.escalated_to_human,
  }));

  const csRepos = (agg.change_set?.repositories ?? []).slice().sort((a, b) => a.merge_order - b.merge_order);
  const gates: RepoGate[] = csRepos.map((r) => ({
    repo: repoName.get(r.repository_id) ?? r.repository_id,
    state: r.gate_display,
    checks: gateChecks(r),
    pr: prLabel(r),
    prUrl: r.pull_request_url,
  }));

  const events: DeliveryEvent[] = data.events.items.map((e) => ({ at: eventTime(e.at), kind: e.kind, text: e.text }));

  const mergeOrderLabel =
    ov?.mergeOrderLabel ??
    (agg.plan.merge_order.length > 0 ? agg.plan.merge_order.map((id) => repoName.get(id) ?? id).join(" → ") : null);

  return {
    label: ov?.deliveryLabel ?? `DLV-${agg.delivery_id.slice(0, 8)}`,
    title: agg.project.title,
    phase: listItem?.phase ?? "execute",
    phaseNote: listItem?.phase_note ?? "",
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
    contract: {
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
    },
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

/** 房间流初始消息：回放用叙事层；live 用 §4.2 CollaborationMessageView 直投影 */
export function deriveChat(data: DeliveryData): ChatMessage[] {
  if (data.overlay) return data.overlay.chat;
  return data.messages.items.map((m: CollaborationMessageView, i) => ({
    id: m.event_id ?? `msg-${i}`,
    author: m.sender,
    role: "AGENT",
    time: m.status,
    tone: "agent",
    text: `${m.subject} — ${m.body}（→ ${m.recipient}）`,
  }));
}
