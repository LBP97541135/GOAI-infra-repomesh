/** 交付控制台前端数据模型。字段对齐 frontend-prototype/data.js，
 *  未来由 GET /api/v1/deliveries* 读模型 API 提供（契约另行同步）。 */

/** 展示 6 态，映射规则见 docs/contracts/delivery-read-model-v0.1.md §5.1 */
export type TaskStatus = "succeeded" | "running" | "repairing" | "blocked" | "pending" | "failed";
export type GateState = "open" | "blocked" | "running" | "waiting";
export type CheckStatus = "pass" | "fail" | "run" | "wait";
export type DecisionKind = "approve" | "watch" | "clarify";
export type StageStatus = "done" | "running" | "attention" | "waiting";

export interface DeliveryStage {
  key: string;
  name: string;
  status: StageStatus;
  note: string;
}

export interface Delivery {
  id: string;
  title: string;
  summary: string;
  requester: string;
  createdAt: string;
  runId: string;
  stages: DeliveryStage[];
}

export interface Contract {
  goal: string;
  acceptance: string[];
  nonGoals: string[];
  scope: {
    repositories: string[];
    allowedPaths: string[];
    forbiddenPaths: string[];
  };
  gatesRequired: string[];
  release: { humanApproval: boolean; rollbackCondition: string };
}

export interface Clarification {
  q: string;
  a: string;
  by: string;
  at: string;
}

export interface RepoInfo {
  id: string;
  lang: string;
  role: string;
  evidence: string;
}

export interface RepairStep {
  at: string;
  what: string;
}

export interface DeliveryTask {
  id: string;
  repo: string;
  /** DAG 拓扑层（横向） */
  col: number;
  /** DAG 泳道 = 仓库索引（纵向） */
  lane: number;
  title: string;
  status: TaskStatus;
  agent: string;
  attempt: number;
  detail: string;
  deps?: string[];
  repair?: RepairStep[];
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
  pr: string;
}

export interface Decision {
  id: string;
  kind: DecisionKind;
  urgency: "now" | "soon" | "later";
  title: string;
  body: string;
  actions: string[];
}

export interface RepoDiffFile {
  path: string;
  add: number;
  del: number;
}

export interface RepoDiff {
  id: string;
  add: number;
  del: number;
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
