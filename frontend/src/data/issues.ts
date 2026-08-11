/** issue 列表 replay 夹具。
 *
 *  形状**已对齐契约 v0.2 §2 冻结形状**（后端 b08240f..f7b2df9，合并于 fd40e53）——
 *  不再是 CONS-41 时期的前端提案。live 与 replay 走同一套类型，切换数据源时零改动。
 *
 *  红线：state / phase / phase_note 均为读模型派生，前端只渲染不映射。
 *
 *  首条带 `operational_status: "paused"`，演示 §2.1 的独立徽标（paused 不改写 state，
 *  仍留在 Open 标签内）。这一形态在 live 上**同样存在**：2026-08-11 实测 5533 种子的
 *  e94499f9 即 paused/supervised 且 team_count=1，拓扑并非空表（后端通知称恒 null，
 *  已横向纠正）。故本夹具不是「造一个 live 撞不到的形态」，而是与 live 同构。
 *
 *  issue_id 前 8 位刻意各不相同——列表按 id 短版显示（`issue_key` 恒 null），
 *  共用前缀会让几行看起来是同一个 issue。 */
import type { IssueListItemView, IssueListResponse } from "../api/contract";

const ORG = "0a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d";

function issue(over: Partial<IssueListItemView> & Pick<IssueListItemView, "issue_id" | "title" | "state" | "phase" | "phase_note" | "opened_at" | "updated_at">): IssueListItemView {
  return {
    issue_key: null,
    organization_id: ORG,
    requirement_text: null,
    round_count: 1,
    active_round_id: null,
    latest_round_id: null,
    pending_decision_count: 0,
    repository_count: 1,
    team_count: 0,
    operational_status: null,
    execution_mode: null,
    opened_by_agent_id: null,
    opened_by_name: null,
    ...over,
  };
}

/** 场景取自 v2 原型 redesign-issue-centric.html */
export const issuesFixture: IssueListResponse = {
  // 计数与条目自洽：夹具即全量，不写一个凑不出条目的大数字
  open_count: 3,
  closed_count: 2,
  next_cursor: null,
  issues: [
    issue({
      issue_id: "7f3d2a10-93d0-4c8e-9b21-5aa1c0de0042",
      title: "结账价格修改原因：记录、暴露并在后台展示",
      requirement_text:
        "运营侧需要在订单结账时记录价格被修改的原因（促销、议价、纠错），原因随订单落库并在后台订单详情页展示。",
      state: "open",
      phase: "release",
      phase_note: "发布门禁",
      round_count: 2,
      repository_count: 3,
      team_count: 3,
      pending_decision_count: 1,
      operational_status: "paused",
      execution_mode: "supervised",
      opened_by_agent_id: "9c8b7a60-1122-4d33-8e44-5f6a7b8c9d00",
      opened_by_name: "console-demo-org-leader",
      opened_at: "2026-08-09T02:14:00Z",
      updated_at: "2026-08-11T12:20:01Z",
    }),
    issue({
      issue_id: "b41d0c77-5e2a-4f18-9c30-77aa10de0041",
      title: "账单金额四舍五入错误修复",
      state: "open",
      phase: "validate",
      phase_note: "修复中",
      opened_by_agent_id: "9c8b7a60-1122-4d33-8e44-5f6a7b8c9d00",
      opened_by_name: "console-demo-org-leader",
      opened_at: "2026-08-08T09:02:00Z",
      updated_at: "2026-08-11T09:41:00Z",
    }),
    issue({
      issue_id: "2a9f5e31-8c74-4b60-a1d2-33bc90de0040",
      title: "通知摘要：邮件与站内信合并为每日一封",
      state: "open",
      phase: "execute",
      phase_note: "执行中 4/6",
      repository_count: 2,
      opened_by_agent_id: "4b2c1d80-5566-4a77-9b88-1c2d3e4f5a66",
      // opened_by_name 解析不到 → null（前端回退 agent id 短版，不编造）
      opened_at: "2026-08-06T14:20:00Z",
      updated_at: "2026-08-10T18:05:00Z",
    }),
    issue({
      issue_id: "c8e07b12-4a91-4d55-b7e6-19df20de0039",
      title: "购物车库存提示优化",
      state: "closed",
      phase: "delivered",
      phase_note: "已交付",
      repository_count: 2,
      opened_by_agent_id: "9c8b7a60-1122-4d33-8e44-5f6a7b8c9d00",
      opened_by_name: "console-demo-org-leader",
      opened_at: "2026-08-02T03:10:00Z",
      updated_at: "2026-08-06T11:32:00Z",
    }),
    issue({
      issue_id: "5d3a91f4-6b28-4e03-8f41-64ca70de0038",
      title: "API 定价结果增加 discount_amount",
      state: "closed",
      phase: "delivered",
      phase_note: "已交付",
      round_count: 2,
      repository_count: 2,
      opened_by_agent_id: "4b2c1d80-5566-4a77-9b88-1c2d3e4f5a66",
      opened_by_name: "console-demo-repo-leader",
      opened_at: "2026-07-28T06:44:00Z",
      updated_at: "2026-08-10T15:18:00Z",
    }),
  ],
};
