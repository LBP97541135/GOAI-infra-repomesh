/** 交付控制台前端视图模型。
 *  契约数据（src/api/contract.ts）经 src/viewmodel.ts 派生成本文件的展示形状；
 *  display_status / gate_display / phase 由后端（或 replay 夹具）给出，前端只渲染，
 *  不做任何状态映射（契约 §5 是唯一实现）。 */
import type { DecisionAction, GateDisplay } from "./api/contract";

/** 契约 §4.3 仅此两类。clarify 已删（X4 裁决：无消费方；真机制落地时按
 *  ChangeRequest 回路立项重建，届时是真实体不是演示枚举） */
export type DecisionKind = "approve" | "watch";

export interface Decision {
  id: string;
  kind: DecisionKind;
  title: string;
  body: string;
  actions: string[];
  actionKinds: DecisionAction[] | null;
  repositoryId: string | null;
  headSha: string | null;
}

/** 审批弹窗（快照绑定授权单）数据；语义见 frontend-prototype/DESIGN-DECISION.md。
 *  授权单按点击的决策卡构建（S1），decisionId 用于消化后从决策夹移除对应项。 */
export interface ApprovalInfo {
  decisionId: string;
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

/** 计划纸面（§5.4）的**锚点仓**。端点是单仓作用域，而 DAG 与 execution_batches 是
 *  issue 级、每个仓取回的是同一份——所以画整张图只需要任取一个落在本 issue 域内的
 *  仓库。issue 详情的 `repositories` 为空时（草稿 issue 尚未冻结范围），发现链候选块
 *  的 `repository_id` 是同一个域内的另一条来路，由发现面板报给容器、容器转给 DAG 面板。 */
export interface PlanAnchor {
  repositoryId: string;
  name: string;
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
