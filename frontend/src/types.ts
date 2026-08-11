/** 交付控制台前端视图模型。
 *  契约数据（src/api/contract.ts）经 src/viewmodel.ts 派生成本文件的展示形状；
 *  display_status / gate_display / phase 由后端（或 replay 夹具）给出，前端只渲染，
 *  不做任何状态映射（契约 §5 是唯一实现）。
 *
 *  v1 控制台退役后，本文件只剩 v2 四个消费面用得到的形状。原先另外 16 个导出
 *  （DeliveryView / ChatMessage / RepoGate / PresentationOverlay 等）描述的是 v1
 *  那张交付全貌页，随它一同移除。 */
import type { DecisionAction, GateDisplay } from "./api/contract";

/** approve|watch 来自契约 §4.3；clarify 无后端实体（§6.5），仅回放模式演示 */
export type DecisionKind = "approve" | "watch" | "clarify";

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

/** 审批弹窗（快照绑定授权单）数据；语义见 frontend-prototype/DESIGN-DECISION.md */
export interface ApprovalInfo {
  authority: string;
  snapshotLabel: string;
  scopeLabel: string;
  changeSetId: string | null;
  repositoryId: string | null;
  headSha: string | null;
}

/** 证据面（B-3 最小版）：治理决策的支撑证据，从 v0.1 交付聚合切单仓作用域派生。
 *  全部字段原样透传或 nullable 降级（diffstat 缺失只列文件名、快照 null 显未接入），
 *  不做状态映射。 */
export interface EvidenceView {
  repositoryName: string;
  headSha: string;
  baseSha: string;
  branchName: string;
  prLabel: string | null;
  prUrl: string | null;
  ciChecks: Array<{ name: string; passed: boolean; summary: string; required: boolean }>;
  reviews: Array<{ reviewer: string; state: string; summary: string }>;
  requiredApprovals: number;
  /** null = 合并请求已发出或已过（§6.4），不等于「不允许」 */
  mergeGate: { allowed: boolean; reasons: string[] } | null;
  governance: Array<{ decision: string; headSha: string; reason: string; decidedAt: string }>;
  commits: Array<{ sha: string; files: string[] }>;
  snapshot: { id: string; status: string; environmentHash: string; expiresAt: string } | null;
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
