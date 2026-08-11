/** 交付控制台前端视图模型。
 *  契约数据（src/api/contract.ts）经 src/viewmodel.ts 派生成本文件的展示形状；
 *  display_status / gate_display / phase 由后端（或 replay 夹具）给出，前端只渲染，
 *  不做任何状态映射（契约 §5 是唯一实现）。 */
import type { DecisionAction, DisplayStatus, GateDisplay, Phase, RepairStep } from "./api/contract";

/** 展示 6 态 = 契约 §5.1 display_status，原样透传 */
export type TaskStatus = DisplayStatus;
/** 门禁 4 态 = 契约 §5.3 gate_display，原样透传 */
export type GateState = GateDisplay;
export type { Phase, RepairStep };

export type CheckStatus = "pass" | "fail" | "run" | "wait";

/** approve|watch 来自契约 §4.3；clarify 无后端实体（§6.5），仅回放模式演示 */
export type DecisionKind = "approve" | "watch" | "clarify";

export interface Clarification {
  q: string;
  a: string;
  by: string;
  at: string;
}

export interface RepoInfo {
  id: string;
  evidence: string | null;
}

export interface DeliveryTask {
  /** 展示短标（task_key，缺省回退 T{n}） */
  id: string;
  taskId: string;
  repo: string;
  /** DAG 拓扑层（横向，前端按 depends_on 计算布局，非状态映射） */
  col: number;
  /** DAG 泳道 = 仓库索引（纵向） */
  lane: number;
  title: string;
  status: TaskStatus;
  agent: string | null;
  attempt: number;
  detail: string | null;
  deps: string[];
  repair: RepairStep[];
  escalated: boolean;
}

export interface GateCheck {
  name: string;
  s: CheckStatus;
  note: string;
}

export interface RepoGate {
  repo: string;
  state: GateState;
  checks: GateCheck[];
  /** PR 展示行，如 "saleor/saleor#19466 · 待合并" */
  pr: string;
  prUrl: string | null;
  /** merge_gate.allowed 直投影（治理决策放行后变 true，环境窗可观察） */
  mergeAllowed: boolean;
  /** merge_sha 非空 = GitHub 已观测到合并 */
  merged: boolean;
}

export interface Decision {
  id: string;
  kind: DecisionKind;
  urgency: "now" | "soon" | "later";
  title: string;
  body: string;
  actions: string[];
  /** 契约 actions 枚举（clarify 演示项为 null） */
  actionKinds: DecisionAction[] | null;
  repositoryId: string | null;
  headSha: string | null;
}

export interface RepoDiffFile {
  path: string;
  /** 契约 diffstat 为 null 时缺省（§6.3 降级：只列文件名） */
  add?: number;
  del?: number;
}

export interface RepoDiff {
  id: string;
  add?: number;
  del?: number;
  note: string;
  files: RepoDiffFile[];
}

export interface DeliveryEvent {
  at: string;
  kind: "runner" | "matrix" | "plan" | "gate" | "deny";
  text: string;
}

export type ArtifactKind = "scope" | "dag" | "fail" | "approve";

export interface ChatMessage {
  id: string;
  author: string;
  role: "HUMAN" | "AGENT";
  time: string;
  /** 消息头/气泡皮肤 */
  tone: "user" | "agent" | "qa";
  text?: string;
  attach?: { label: string; name: string; meta: string };
  clarifications?: Clarification[];
  artifact?: ArtifactKind;
}

/** 契约卡（PlanView 1.0/2.0 使用）。nullable 字段为 null 时隐藏对应区块。 */
export interface ContractCard {
  version: number;
  status: string;
  goal: string;
  acceptance: string[];
  nonGoals: string[] | null;
  repositories: string[];
  allowedPaths: string[];
  forbiddenPaths: string[];
  tests: string[];
}

/** 审批弹窗（快照绑定授权单）数据；语义见 frontend-prototype/DESIGN-DECISION.md */
export interface ApprovalInfo {
  authority: string;
  snapshotLabel: string;
  scopeLabel: string;
  changeSetId: string | null;
  repositoryId: string | null;
  headSha: string | null;
}

/** 组件消费的交付全貌视图（由 viewmodel.ts 从契约聚合派生） */
export interface DeliveryView {
  label: string;
  /** 顶栏项目标识：project_key，未落地时回退 project title */
  projectLabel: string;
  title: string;
  /** null = 聚合交付未出现在列表中（不编造默认 phase，隐藏徽标） */
  phase: Phase | null;
  phaseNote: string | null;
  createdAt: string;
  requirement: string | null;
  runLabel: string | null;
  matrixRoom: string | null;
  traceId: string | null;
  costLabel: string | null;
  snapshotLabel: string | null;
  stagingNote: string | null;
  planRev: number;
  mergeOrderLabel: string | null;
  /** null = 该交付未建 ENGINEERING spec（契约 4744c71），纸面 1.0/2.0 显示占位 */
  contract: ContractCard | null;
  repos: RepoInfo[];
  lanes: string[];
  tasks: DeliveryTask[];
  gates: RepoGate[];
  repoDiffs: RepoDiff[];
  events: DeliveryEvent[];
  decisions: Decision[];
  rollbackPlan: string[] | null;
  envProcesses: string[];
  approval: ApprovalInfo | null;
}

/** 回放数据源附带的演示叙事层：覆盖契约未提供（nullable/缺口）字段的 Demo 展示。
 *  live 模式无此层，对应区块走降级渲染路径。 */
export interface PresentationOverlay {
  deliveryLabel: string;
  runLabel: string | null;
  chat: ChatMessage[];
  /** clarify 决策演示（契约 §6.5：仅回放模式存在） */
  extraDecisions: Decision[];
  /** 契约 non_goals 暂缓（§6.2）的演示值 */
  nonGoals: string[] | null;
  /** 契约无回滚预案实体时的演示文案（计划纸面 5.0） */
  rollbackPlan: string[] | null;
  /** 契约 diffstat=null（§6.3）时的演示 ± 行数 */
  repoDiffs: RepoDiff[] | null;
  /** 契约 cost=null（§6.4）时的演示成本行 */
  costLabel: string | null;
  matrixAlias: string | null;
  approvalAuthority: string | null;
  mergeOrderLabel: string | null;
  stagingNote: string | null;
  envProcesses: string[];
}

/** 环境窗（CONS-43）的单仓切片：轮次粒度的交付聚合切到本仓作用域。
 *  gate 相关字段原样透传读模型，前端不映射。 */
export interface RepositoryEnv {
  repositoryName: string;
  gateDisplay: GateDisplay | null;
  prLabel: string | null;
  prUrl: string | null;
  /** null = 合并请求已发出或已过（§6.4 追认），**不等于**「不允许」 */
  mergeAllowed: boolean | null;
  changedFiles: Array<{ path: string }>;
  commitShas: string[];
  validationSnapshotId: string | null;
  /** CHANGESET 中各仓位置（按 merge_order），标出当前所在仓 */
  siblings: Array<{ name: string; gate: GateDisplay; isCurrent: boolean }>;
}
