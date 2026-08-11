/** 交付读模型契约 v0.1 的 TypeScript 类型。
 *  唯一来源：docs/contracts/delivery-read-model-v0.1.md（dbd2b1a）。
 *  契约是唯一事实——本文件只转写 §2/§3/§4 的 JSON 形状，禁止添加契约外字段；
 *  契约修订时本文件同步修订。 */

/** §2 交付阶段（读模型推导，前端只渲染） */
export type Phase =
  | "contract"
  | "plan"
  | "execute"
  | "validate"
  | "release"
  | "delivered"
  | "failed"
  | "archived";

/** §5.1 任务展示 6 态（后端唯一映射，前端只渲染） */
export type DisplayStatus = "pending" | "running" | "repairing" | "blocked" | "succeeded" | "failed";

/** §3 任务后端 7 态（原样透出） */
export type BackendTaskStatus =
  | "assigned"
  | "in_progress"
  | "blocked"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "superseded";

/** §5.3 门禁展示 4 态（后端唯一映射，前端只渲染） */
export type GateDisplay = "open" | "blocked" | "running" | "waiting";

/** §5.3 RepositoryDelivery 12 态（原样透出） */
export type RepositoryDeliveryStatus =
  | "pending"
  | "pr_open"
  | "ci_pending"
  | "ci_failed"
  | "review_pending"
  | "review_changes_requested"
  | "ready_to_merge"
  | "merge_requested"
  | "merged"
  | "manual_intervention"
  | "compensation_pending"
  | "compensated";

export type GovernanceDecisionValue = "ready" | "blocked" | "rollback_required";

/* ------------------------------------------------------------------ §2 列表 */

export interface DeliveryListItem {
  /** null = §0 虚拟草稿交付（尚未 materialize） */
  delivery_id: string | null;
  title: string;
  phase: Phase;
  phase_note: string;
  pending_decision_count: number;
  updated_at: string;
}

export interface DeliveryProjectGroup {
  project_id: string;
  project_key: string;
  title: string;
  deliveries: DeliveryListItem[];
}

export interface DeliveryListResponse {
  projects: DeliveryProjectGroup[];
  next_cursor: string | null;
}

/* ------------------------------------------------------------ §3 全貌聚合 */

export interface DeliveryProjectInfo {
  project_id: string;
  project_key: string;
  title: string;
  /** nullable：plan snapshot.requirement_text */
  requirement_text: string | null;
  created_at: string;
}

export interface DeliveryContractView {
  specification_id: string;
  version: number;
  status: string;
  goal: string;
  acceptance: string[];
  constraints: string[];
  allowed_paths: string[];
  /** specification 新增可选字段，随读模型同批实现（契约 §6.2） */
  forbidden_paths: string[];
  tests: string[];
  /** nullable：specification 暂缓（§6.2），v0.1 恒为 null */
  non_goals: string[] | null;
  /** nullable：specification 暂缓（§6.2），v0.1 恒为 null */
  release_rules: Record<string, unknown> | null;
}

export interface DeliveryRepositoryInfo {
  repository_id: string;
  name: string;
  evidence: string | null;
}

export interface DeliveryPlanView {
  plan_version: number;
  status: string;
  current_batch_index: number;
  execution_batches: string[][];
  /** 由 ChangeSet depends_on 拓扑排序导出 */
  merge_order: string[];
}

export interface RepairStep {
  at: string;
  what: string;
}

export interface DeliveryTaskView {
  task_id: string;
  task_key: string | null;
  repository_id: string;
  title: string;
  backend_status: BackendTaskStatus;
  display_status: DisplayStatus;
  agent: string | null;
  /** 1 + 同仓 rework 链长度（§5.2） */
  attempt: number;
  depends_on: string[];
  result_summary: string | null;
  repair_timeline: RepairStep[];
  /** §5.2：仅转述 recovery plan 的 MANUAL_INTERVENTION，读模型不做升级判断 */
  escalated_to_human: boolean;
}

export interface CiCheckView {
  check_name: string;
  passed: boolean;
  summary: string;
}

export interface ReviewView {
  reviewer: string;
  state: string;
  summary: string;
}

export interface RepositoryDeliveryView {
  repository_id: string;
  task_id: string;
  status: RepositoryDeliveryStatus;
  gate_display: GateDisplay;
  pull_request_url: string | null;
  pull_request_number: number | null;
  head_sha: string;
  base_sha: string;
  branch_name: string;
  depends_on: string[];
  merge_order: number;
  ci_checks: CiCheckView[];
  required_checks: string[];
  required_approvals: number;
  reviews: ReviewView[];
  merge_gate: { allowed: boolean; reasons: string[] };
  merge_sha: string | null;
}

export interface GovernanceDecisionView {
  id: string;
  repository_id: string;
  head_sha: string;
  decision: GovernanceDecisionValue;
  decided_by_agent_id: string;
  reason: string;
  decided_at: string;
}

export interface RecoveryPlanView {
  trigger: string;
  reason: string;
  actions: unknown[];
}

export interface ChangeSetView {
  change_set_id: string;
  status: string;
  merge_cursor: number;
  repositories: RepositoryDeliveryView[];
  governance_decisions: GovernanceDecisionView[];
  recovery_plans: RecoveryPlanView[];
}

export interface ValidationSnapshotView {
  id: string;
  status: string;
  candidate_heads: Record<string, string>;
  environment_hash: string;
  expires_at: string;
}

export interface DeliveryDiffView {
  repository_id: string;
  run_id: string;
  commit_sha: string;
  changed_files: string[];
  /** nullable：Runner 暂未采集 ±行数（§6.3），v0.1 恒为 null */
  diffstat: Record<string, unknown> | null;
}

export interface DeliveryAggregate {
  delivery_id: string;
  project: DeliveryProjectInfo;
  contract: DeliveryContractView;
  repositories: DeliveryRepositoryInfo[];
  plan: DeliveryPlanView;
  tasks: DeliveryTaskView[];
  change_set: ChangeSetView | null;
  validation_snapshot: ValidationSnapshotView | null;
  diffs: DeliveryDiffView[];
  /** nullable：无 token/成本采集（§6.4），v0.1 恒为 null */
  cost: Record<string, unknown> | null;
  matrix_room_id: string | null;
  trace_id: string | null;
}

/* ------------------------------------------------- §4 事件、消息与决策 */

/** §4.1：`deny` 在 v0.1 不应由后端产出（出现即契约违约）；回放夹具可用于演示叙事 */
export type DeliveryEventKind = "runner" | "matrix" | "gate" | "plan" | "deny";

export interface DeliveryEventItem {
  at: string;
  kind: DeliveryEventKind;
  text: string;
  task_id: string | null;
  repository_id: string | null;
  payload_ref: string | null;
}

export interface DeliveryEventsPage {
  items: DeliveryEventItem[];
  next_cursor: string | null;
}

/** §4.2 CollaborationMessageView 直投影（当前仅 Leader→Worker 方向，§6.7） */
export interface CollaborationMessageView {
  kind: string;
  subject: string;
  body: string;
  sender: string;
  recipient: string;
  status: string;
  event_id: string | null;
  correlation_id: string | null;
}

export interface DeliveryMessagesPage {
  items: CollaborationMessageView[];
  next_cursor: string | null;
}

/** §4.3：v0.1 仅 approve|watch；clarify 无后端实体（§6.5），只存在于前端回放模式 */
export type DecisionItemKind = "approve" | "watch";

export type DecisionAction = "approve_merge" | "view_evidence";

export interface DecisionItem {
  id: string;
  kind: DecisionItemKind;
  title: string;
  body: string;
  repository_id: string | null;
  head_sha: string | null;
  created_at: string;
  actions: DecisionAction[];
}

export interface DecisionsResponse {
  items: DecisionItem[];
}

/* --------------------------------------------------------- §4.4/§4.5 写端点 */

export interface GovernanceDecisionRequest {
  change_set_id: string;
  repository_id: string;
  /** 必填：head-bound，SHA 漂移即 409 */
  head_sha: string;
  decision: GovernanceDecisionValue;
  /** 必填：决策主体（bearer 为共享动作 token，无法承载身份）；
   *  须为同组织活跃 ORGANIZATION_LEADER 或该仓库 REPOSITORY_LEADER，否则 403 */
  decided_by_agent_id: string;
  reason: string;
  /** 幂等语义为内容重放去重（相同决策重放 no-op、版本不涨） */
  idempotency_key: string;
}
