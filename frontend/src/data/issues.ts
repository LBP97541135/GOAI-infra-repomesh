/** issue 列表 replay 夹具（CONS-41）。
 *
 *  ⚠ 形状是**前端提案**，不是已冻结契约：issue 读模型属后端 CONS-31，契约 v0.2
 *  起草中。字段按契约风格（snake_case、状态由后端派生）编写，使 live 接线改动最小；
 *  以契约裁决为准，届时本文件按裁决修订。
 *
 *  红线：state（open/closed）与 phase 均为**读模型派生**，前端只渲染不映射
 *  （契约 v0.1 §5 同款约束）。徽标配色/图标是展示皮肤，不是状态映射。 */
import type { Phase } from "../api/contract";

export interface IssueListItem {
  /** 展示编号（GitHub 式 #42）；后端派生的稳定序号 */
  number: number;
  issue_id: string;
  title: string;
  /** 发起人显示名；null = 身份未关联（诚实降级） */
  author_name: string | null;
  created_label: string;
  /** 交付轮次数（issue 内的 execution plan 轮数） */
  delivery_rounds: number;
  repository_count: number;
  /** 读模型派生：open = 存在未终态交付或尚未交付 */
  state: "open" | "closed";
  /** issue 粒度 phase（读模型派生，v0.1 §2 同枚举） */
  phase: Phase;
  /** phase 的人类可读补充（如「执行中 4/6」的进度尾巴） */
  phase_note: string;
  pending_decision_count: number;
  /** closed 才有：交付完成日期标签；open 为 null */
  closed_label: string | null;
}

export interface IssueListResponse {
  items: IssueListItem[];
  open_count: number;
  closed_count: number;
  /** 未在 items 内展开的已完结条目数（列表尾提示，避免假装全量） */
  closed_truncated: number;
}

/** 场景取自 v2 原型 redesign-issue-centric.html（acme-org 工作区） */
export const issuesFixture: IssueListResponse = {
  open_count: 3,
  closed_count: 12,
  closed_truncated: 10,
  items: [
    {
      number: 42,
      issue_id: "7f3d2a10-93d0-4c8e-9b21-5aa1c0de0042",
      title: "结账价格修改原因：记录、暴露并在后台展示",
      author_name: "王倩",
      created_label: "2 天前",
      delivery_rounds: 2,
      repository_count: 3,
      state: "open",
      phase: "release",
      phase_note: "发布门禁",
      pending_decision_count: 1,
      closed_label: null,
    },
    {
      number: 41,
      issue_id: "7f3d2a10-93d0-4c8e-9b21-5aa1c0de0041",
      title: "账单金额四舍五入错误修复",
      author_name: "王倩",
      created_label: "3 天前",
      delivery_rounds: 1,
      repository_count: 1,
      state: "open",
      phase: "validate",
      phase_note: "修复中",
      pending_decision_count: 0,
      closed_label: null,
    },
    {
      number: 40,
      issue_id: "7f3d2a10-93d0-4c8e-9b21-5aa1c0de0040",
      title: "通知摘要：邮件与站内信合并为每日一封",
      author_name: "李明",
      created_label: "5 天前",
      delivery_rounds: 1,
      repository_count: 2,
      state: "open",
      phase: "execute",
      phase_note: "执行中 4/6",
      pending_decision_count: 0,
      closed_label: null,
    },
    {
      number: 39,
      issue_id: "7f3d2a10-93d0-4c8e-9b21-5aa1c0de0039",
      title: "购物车库存提示优化",
      author_name: "王倩",
      created_label: "08-02",
      delivery_rounds: 1,
      repository_count: 2,
      state: "closed",
      phase: "delivered",
      phase_note: "已交付",
      pending_decision_count: 0,
      closed_label: "08-06",
    },
    {
      number: 38,
      issue_id: "7f3d2a10-93d0-4c8e-9b21-5aa1c0de0038",
      title: "API 定价结果增加 discount_amount",
      author_name: "李明",
      created_label: "07-28",
      delivery_rounds: 2,
      repository_count: 2,
      state: "closed",
      phase: "delivered",
      phase_note: "已交付",
      pending_decision_count: 0,
      closed_label: "08-10",
    },
  ],
};
