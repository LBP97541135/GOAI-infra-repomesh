/** 历史决策 replay 夹具（decision-chain-v0.1 §6）。
 *
 *  一套自洽的成功项目世界，6 个项目与 issue 列表（data/issues.ts issuesFixture）
 *  一一对应：用户在 issue 列表里看到的每个 issue（标题 / #短id）都能在「需求
 *  定位」入口定位到决策链。主项目是完整五步链（含一次「确认改档 → 重做分类」
 *  的版本化演示），其余五个项目覆盖不同的中段缺口形态，共同构成语义检索与
 *  需求定位两种入口共用的一份语料。
 *  仓库/组织 id 与 data/issueDetail.ts 同一宇宙（saleor-core / dashboard / docs）。
 *
 *  ⚠ 夹具是演示剧本，不是本机事实：replay 模式下界面渲染的就是这份数据。
 *  形状与 api/contract.ts 的决策链类型一一对应（那边镜像后端 models.py）。 */

import type {
  DecisionChainView,
  DecisionNodeActorView,
  DecisionNodeView,
  DecisionStatus,
  DecisionStep,
  SemanticSearchView,
  SimilarDecisionView,
  SimilarDecisionsView,
} from "../api/contract";

// ══════════════ 同一宇宙的常量（与 data/issueDetail.ts 同源） ══════════════

const ORG_ID = "0a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d";
const REPO_CORE = "b1c2d3e4-0001-4a2b-9c3d-4e5f6a7b8c01"; // saleor-core
const REPO_WEB = "b1c2d3e4-0002-4a2b-9c3d-4e5f6a7b8c02"; // saleor-dashboard
const REPO_DOCS = "b1c2d3e4-0003-4a2b-9c3d-4e5f6a7b8c03"; // saleor-docs

/** 仓库 id → 展示名。live 模式没有这份映射（决策链端点只给 id），
 *  夹具世界里补齐名字让演示可读；live 下页面按 shortId 兜底。 */
export const DECISION_REPO_NAMES: Record<string, string> = {
  [REPO_CORE]: "saleor-core",
  [REPO_WEB]: "saleor-dashboard",
  [REPO_DOCS]: "saleor-docs",
};

const AGENT_CLASSIFY = "9c8b7a60-1122-4d33-8e44-5f6a7b8c9d10";
const AGENT_INTEGRATE = "9c8b7a60-1122-4d33-8e44-5f6a7b8c9d11";
const ORG_LEADER = "9c8b7a60-1122-4d33-8e44-5f6a7b8c9d00";

const LLM = (agentId: string): DecisionNodeActorView => ({ type: "llm", agent_id: agentId });
const HUMAN = (agentId: string): DecisionNodeActorView => ({ type: "human", agent_id: agentId });
const SERVICE: DecisionNodeActorView = { type: "service", agent_id: null };

// ══════════════ 节点构建辅助（确定性 UUID + 投影器同款缺省） ══════════════

/** 由 projectId + 序号生成确定性 UUID（同一个 seed 恒定，回放可复现）。
 *  projectId 本体是 hex，去掉短横后取前 24 位 + 序号 8 位十六进制尾。 */
function fabricateUuid(projectId: string, seq: number): string {
  const head = projectId.replace(/-/g, "").slice(0, 24).toLowerCase();
  const hex = (head + seq.toString(16).padStart(8, "0")).slice(0, 32);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

interface SheetInput {
  step: DecisionStep;
  version: number;
  status: DecisionStatus;
  actor: DecisionNodeActorView;
  upstream_ref?: string | null;
  evidence_refs?: Record<string, string[]>;
  payload_summary: Record<string, unknown>;
  affected_repository_ids?: string[];
  business_time: string;
  event_type: string;
}

/** 投影器同款缺省：evidence 缺省空分账、recorded_at = business_time、source = event。 */
function makeSheet(projectId: string, seq: number, input: SheetInput): DecisionNodeView {
  return {
    decision_id: fabricateUuid(projectId, seq),
    event_id: fabricateUuid(projectId, seq + 1000),
    project_id: projectId,
    organization_id: ORG_ID,
    step: input.step,
    version: input.version,
    status: input.status,
    actor: input.actor,
    upstream_ref: input.upstream_ref ?? null,
    evidence_refs: input.evidence_refs ?? { result: [], process: [] },
    payload_summary: input.payload_summary,
    affected_repository_ids: input.affected_repository_ids ?? [],
    business_time: input.business_time,
    recorded_at: input.business_time,
    source: "event",
    event_type: input.event_type,
  };
}

/** §7 同款中段缺口判定：链上有后步节点却缺前步时列出缺失步（缺尾不是缺口）。 */
const CHAIN_STEPS: readonly DecisionStep[] = [
  "classification",
  "confirmation",
  "integration",
  "task",
  "pr",
];

function legacyGapsFor(nodes: DecisionNodeView[]): string[] {
  if (nodes.length === 0) return [];
  const present = new Set(nodes.map((n) => n.step));
  return CHAIN_STEPS.filter(
    (step, i) => !present.has(step) && nodes.some((n) => CHAIN_STEPS.indexOf(n.step) > i),
  );
}

// ══════════════ 语料：主项目 + 相似历史 ══════════════

/** 主项目 = issue 列表里的「结账价格修改原因」（issuesFixture 7f3d2a10）——
 *  决策链 project_id 与 issue_id 同源（E1 根），用户从 issue 列表看到的 #7f3d2a10
 *  在这里直接可查。 */
export const MAIN_PROJECT_ID = "7f3d2a10-93d0-4c8e-9b21-5aa1c0de0042";

/** P1 = issue 列表里的「账单金额四舍五入错误修复」（issuesFixture b41d0c77）。 */
export const P1_PROJECT_ID = "b41d0c77-5e2a-4f18-9c30-77aa10de0041";

interface FixtureProject {
  id: string;
  title: string;
  keywords: string[];
  chain: DecisionChainView;
}

const PROJECTS: FixtureProject[] = [
  // ── 主项目：完整五步链，确认环节改过一次档（版本化演示） ────────────────
  {
    id: MAIN_PROJECT_ID,
    title: "结账页价格展示重构：统一显示口径并记录调整原因",
    keywords: ["结账", "价格", "展示", "口径", "调整", "原因", "订单", "促销", "议价", "审计", "修改", "暴露", "后台", "记录"],
    chain: {
      project_id: MAIN_PROJECT_ID,
      organization_id: ORG_ID,
      requirement: {
        text: "订单结算页的价格展示口径不统一（含税/不含税、促销前/后），运营需要按 SKU 记录每次价格调整的原因（促销、议价、纠错），原因随订单落库并在后台订单详情页展示。需要统一前端展示组件、扩展后端订单接口，并新增审计字段。",
        plan_version: 2,
        snapshot_id: fabricateUuid(MAIN_PROJECT_ID, 900),
      },
      nodes: [
        makeSheet(MAIN_PROJECT_ID, 1, {
          step: "classification",
          version: 1,
          status: "proposed",
          actor: LLM(AGENT_CLASSIFY),
          evidence_refs: { result: ["ev:class-7f3d-a1"], process: [] },
          payload_summary: {
            required: ["saleor-core"],
            maybe: ["saleor-dashboard", "saleor-docs"],
            excluded: [],
            effective_tiers: {
              "saleor-core": "required",
              "saleor-dashboard": "maybe",
              "saleor-docs": "maybe",
            },
            supplemented_repository_ids: [],
          },
          affected_repository_ids: [REPO_CORE, REPO_WEB, REPO_DOCS],
          business_time: "2026-08-01T02:30:00Z",
          event_type: "ClassificationDecided",
        }),
        makeSheet(MAIN_PROJECT_ID, 2, {
          step: "confirmation",
          version: 1,
          status: "changes_requested",
          actor: HUMAN(ORG_LEADER),
          upstream_ref: fabricateUuid(MAIN_PROJECT_ID, 1),
          evidence_refs: { result: ["ev:conf-7f3d-b1"], process: [] },
          payload_summary: {
            approval: {
              state: "changes_requested",
              decided_by_agent_id: ORG_LEADER,
              reason: "后台订单详情页同样需要展示价格调整原因，把 saleor-docs 提升到必需档。",
            },
            adjustments: [{ repository: "saleor-docs", from: "maybe", to: "required" }],
            effective_tiers: {
              "saleor-core": "required",
              "saleor-dashboard": "maybe",
              "saleor-docs": "required",
            },
          },
          affected_repository_ids: [REPO_CORE, REPO_WEB, REPO_DOCS],
          business_time: "2026-08-01T05:10:00Z",
          event_type: "ConfirmationDecided",
        }),
        makeSheet(MAIN_PROJECT_ID, 3, {
          step: "classification",
          version: 2,
          status: "proposed",
          actor: LLM(AGENT_CLASSIFY),
          // 改档后重做：上游指向它前面的确认单（最新前步），链仍是闭环
          upstream_ref: fabricateUuid(MAIN_PROJECT_ID, 2),
          evidence_refs: { result: ["ev:class-7f3d-a2"], process: [] },
          payload_summary: {
            required: ["saleor-core", "saleor-docs"],
            maybe: ["saleor-dashboard"],
            excluded: [],
            effective_tiers: {
              "saleor-core": "required",
              "saleor-dashboard": "maybe",
              "saleor-docs": "required",
            },
            supplemented_repository_ids: [],
          },
          affected_repository_ids: [REPO_CORE, REPO_WEB, REPO_DOCS],
          business_time: "2026-08-01T06:20:00Z",
          event_type: "ClassificationDecided",
        }),
        makeSheet(MAIN_PROJECT_ID, 4, {
          step: "confirmation",
          version: 2,
          status: "confirmed",
          actor: HUMAN(ORG_LEADER),
          upstream_ref: fabricateUuid(MAIN_PROJECT_ID, 3),
          evidence_refs: { result: ["ev:conf-7f3d-b2"], process: [] },
          payload_summary: {
            approval: {
              state: "approved",
              decided_by_agent_id: ORG_LEADER,
              reason: "分档确认：核心接口与后台展示均纳入必改范围。",
            },
            adjustments: [],
            effective_tiers: {
              "saleor-core": "required",
              "saleor-dashboard": "maybe",
              "saleor-docs": "required",
            },
          },
          affected_repository_ids: [REPO_CORE, REPO_WEB, REPO_DOCS],
          business_time: "2026-08-01T08:00:00Z",
          event_type: "ConfirmationDecided",
        }),
        makeSheet(MAIN_PROJECT_ID, 5, {
          step: "integration",
          version: 1,
          status: "proposed",
          actor: LLM(AGENT_INTEGRATE),
          upstream_ref: fabricateUuid(MAIN_PROJECT_ID, 4),
          payload_summary: {
            execution_batches: [["saleor-core", "saleor-dashboard"], ["saleor-docs"]],
            contracts: [
              {
                interface: "PriceAdjustmentReason",
                provider: "saleor-core",
                consumers: ["saleor-dashboard", "saleor-docs"],
              },
            ],
          },
          affected_repository_ids: [REPO_CORE, REPO_WEB, REPO_DOCS],
          business_time: "2026-08-01T09:15:00Z",
          event_type: "IntegrationDecided",
        }),
        makeSheet(MAIN_PROJECT_ID, 6, {
          step: "task",
          version: 1,
          status: "proposed",
          actor: LLM(AGENT_CLASSIFY),
          upstream_ref: fabricateUuid(MAIN_PROJECT_ID, 5),
          payload_summary: {
            task_id: "t-7f3d-0001",
            repository_id: REPO_CORE,
            title: "在订单结账链路记录价格调整原因并随订单落库",
            parent_task_id: null,
          },
          affected_repository_ids: [REPO_CORE],
          business_time: "2026-08-02T01:05:00Z",
          event_type: "TasksPlanned",
        }),
        makeSheet(MAIN_PROJECT_ID, 7, {
          step: "pr",
          version: 1,
          status: "proposed",
          actor: SERVICE,
          upstream_ref: fabricateUuid(MAIN_PROJECT_ID, 6),
          payload_summary: {
            change_set_id: "cs-7f3d-0001",
            repository_id: REPO_CORE,
            pull_request_number: 2841,
            pull_request_url: "https://repo.localhost/saleor-core/pull/2841",
            task_ids: ["t-7f3d-0001"],
          },
          affected_repository_ids: [REPO_CORE],
          business_time: "2026-08-02T03:40:00Z",
          event_type: "PullRequestObserved",
        }),
      ],
      legacy_gaps: [],
    },
  },

  // ── P1：金额精度（四步，已合 PR 的第二个成功案例） ──────────────────────
  {
    id: P1_PROJECT_ID,
    title: "订单金额精度与舍入规则统一",
    keywords: ["订单", "金额", "精度", "舍入", "统一", "价格", "计算", "修复", "四舍五入", "账单"],
    chain: {
      project_id: P1_PROJECT_ID,
      organization_id: ORG_ID,
      requirement: {
        text: "订单金额在不同页面出现小数位不一致与舍入方向差异，需要统一精度与舍入规则，避免对账差异。",
        plan_version: 1,
        snapshot_id: fabricateUuid(P1_PROJECT_ID, 900),
      },
      nodes: [
        makeSheet(P1_PROJECT_ID, 1, {
          step: "classification",
          version: 1,
          status: "proposed",
          actor: LLM(AGENT_CLASSIFY),
          evidence_refs: { result: ["ev:class-2b8f-a1"], process: [] },
          payload_summary: {
            required: ["saleor-core"],
            maybe: ["saleor-dashboard"],
            excluded: [],
            effective_tiers: { "saleor-core": "required", "saleor-dashboard": "maybe" },
            supplemented_repository_ids: [],
          },
          affected_repository_ids: [REPO_CORE, REPO_WEB],
          business_time: "2026-07-18T03:00:00Z",
          event_type: "ClassificationDecided",
        }),
        makeSheet(P1_PROJECT_ID, 2, {
          step: "confirmation",
          version: 1,
          status: "confirmed",
          actor: HUMAN(ORG_LEADER),
          upstream_ref: fabricateUuid(P1_PROJECT_ID, 1),
          evidence_refs: { result: ["ev:conf-2b8f-b1"], process: [] },
          payload_summary: {
            approval: {
              state: "approved",
              decided_by_agent_id: ORG_LEADER,
              reason: "精度规则统一到核心层，前端只消费。",
            },
            adjustments: [],
            effective_tiers: { "saleor-core": "required", "saleor-dashboard": "maybe" },
          },
          affected_repository_ids: [REPO_CORE, REPO_WEB],
          business_time: "2026-07-18T06:30:00Z",
          event_type: "ConfirmationDecided",
        }),
        makeSheet(P1_PROJECT_ID, 3, {
          step: "integration",
          version: 1,
          status: "proposed",
          actor: LLM(AGENT_INTEGRATE),
          upstream_ref: fabricateUuid(P1_PROJECT_ID, 2),
          payload_summary: {
            execution_batches: [["saleor-core", "saleor-dashboard"]],
            contracts: [
              {
                interface: "RoundingConfig",
                provider: "saleor-core",
                consumers: ["saleor-dashboard"],
              },
            ],
          },
          affected_repository_ids: [REPO_CORE, REPO_WEB],
          business_time: "2026-07-18T08:20:00Z",
          event_type: "IntegrationDecided",
        }),
        makeSheet(P1_PROJECT_ID, 4, {
          step: "pr",
          version: 1,
          status: "proposed",
          actor: SERVICE,
          // 无 task 节点：hint 落空，回退最新前步（integration）——链仍是闭环
          upstream_ref: fabricateUuid(P1_PROJECT_ID, 3),
          payload_summary: {
            change_set_id: "cs-2b8f-0001",
            repository_id: REPO_CORE,
            pull_request_number: 2833,
            pull_request_url: "https://repo.localhost/saleor-core/pull/2833",
            task_ids: [],
          },
          affected_repository_ids: [REPO_CORE],
          business_time: "2026-07-19T02:10:00Z",
          event_type: "PullRequestObserved",
        }),
      ],
      legacy_gaps: ["task"],
    },
  },

  // ── P2：退货部分退款（三步，走到任务；issue 列表 9d1e4c56） ──────────────
  {
    id: "9d1e4c56-1b39-4f72-a4e5-88fd30de0037",
    title: "退货流程支持部分退款",
    keywords: ["退货", "退款", "部分", "售后", "订单", "金额", "流程"],
    chain: {
      project_id: "9d1e4c56-1b39-4f72-a4e5-88fd30de0037",
      organization_id: ORG_ID,
      requirement: {
        text: "退货流程目前只支持整单退款，多商品订单无法按商品部分退款。需要支持部分退款：按商品明细计算应退金额、走人工确认流程，并在订单详情展示退款明细。",
        plan_version: 1,
        snapshot_id: fabricateUuid("9d1e4c56-1b39-4f72-a4e5-88fd30de0037", 900),
      },
      nodes: [
        makeSheet("9d1e4c56-1b39-4f72-a4e5-88fd30de0037", 1, {
          step: "classification",
          version: 1,
          status: "proposed",
          actor: LLM(AGENT_CLASSIFY),
          evidence_refs: { result: ["ev:class-9d1e-a1"], process: [] },
          payload_summary: {
            required: ["saleor-core", "saleor-dashboard"],
            maybe: ["saleor-docs"],
            excluded: [],
            effective_tiers: {
              "saleor-core": "required",
              "saleor-dashboard": "required",
              "saleor-docs": "maybe",
            },
            supplemented_repository_ids: [],
          },
          affected_repository_ids: [REPO_CORE, REPO_WEB, REPO_DOCS],
          business_time: "2026-07-25T04:00:00Z",
          event_type: "ClassificationDecided",
        }),
        makeSheet("9d1e4c56-1b39-4f72-a4e5-88fd30de0037", 2, {
          step: "confirmation",
          version: 1,
          status: "confirmed",
          actor: HUMAN(ORG_LEADER),
          upstream_ref: fabricateUuid("9d1e4c56-1b39-4f72-a4e5-88fd30de0037", 1),
          evidence_refs: { result: ["ev:conf-9d1e-b1"], process: [] },
          payload_summary: {
            approval: {
              state: "approved",
              decided_by_agent_id: ORG_LEADER,
              reason: "部分退款涉及核心订单计算与后台操作，范围确认：核心仓与后台均纳入必改。",
            },
            adjustments: [],
            effective_tiers: {
              "saleor-core": "required",
              "saleor-dashboard": "required",
              "saleor-docs": "maybe",
            },
          },
          affected_repository_ids: [REPO_CORE, REPO_WEB, REPO_DOCS],
          business_time: "2026-07-25T07:15:00Z",
          event_type: "ConfirmationDecided",
        }),
        makeSheet("9d1e4c56-1b39-4f72-a4e5-88fd30de0037", 3, {
          step: "task",
          version: 1,
          status: "proposed",
          actor: LLM(AGENT_CLASSIFY),
          upstream_ref: fabricateUuid("9d1e4c56-1b39-4f72-a4e5-88fd30de0037", 2),
          payload_summary: {
            task_id: "t-9d1e-0001",
            repository_id: REPO_CORE,
            title: "退货流程支持部分退款：按商品明细计算应退金额并落订单详情",
            parent_task_id: null,
          },
          affected_repository_ids: [REPO_CORE],
          business_time: "2026-07-25T09:40:00Z",
          event_type: "TasksPlanned",
        }),
      ],
      legacy_gaps: ["integration"],
    },
  },

  // ── P3：通知摘要（两步；issue 列表 2a9f5e31） ────────────────────────────
  {
    id: "2a9f5e31-8c74-4b60-a1d2-33bc90de0040",
    title: "通知摘要：邮件与站内信合并为每日一封",
    keywords: ["通知", "摘要", "邮件", "站内信", "合并", "每日", "消息"],
    chain: {
      project_id: "2a9f5e31-8c74-4b60-a1d2-33bc90de0040",
      organization_id: ORG_ID,
      requirement: {
        text: "站内信与邮件通知逐条发送，噪音大。需要把低频提醒合并为每日一封摘要（邮件 + 站内信双通道），并支持订阅开关。",
        plan_version: 1,
        snapshot_id: fabricateUuid("2a9f5e31-8c74-4b60-a1d2-33bc90de0040", 900),
      },
      nodes: [
        makeSheet("2a9f5e31-8c74-4b60-a1d2-33bc90de0040", 1, {
          step: "classification",
          version: 1,
          status: "proposed",
          actor: LLM(AGENT_CLASSIFY),
          evidence_refs: { result: ["ev:class-2a9f-a1"], process: [] },
          payload_summary: {
            required: ["saleor-dashboard"],
            maybe: ["saleor-core"],
            excluded: [],
            effective_tiers: { "saleor-dashboard": "required", "saleor-core": "maybe" },
            supplemented_repository_ids: [],
          },
          affected_repository_ids: [REPO_WEB, REPO_CORE],
          business_time: "2026-07-29T01:00:00Z",
          event_type: "ClassificationDecided",
        }),
        makeSheet("2a9f5e31-8c74-4b60-a1d2-33bc90de0040", 2, {
          step: "integration",
          version: 1,
          status: "proposed",
          actor: LLM(AGENT_INTEGRATE),
          upstream_ref: fabricateUuid("2a9f5e31-8c74-4b60-a1d2-33bc90de0040", 1),
          evidence_refs: { result: ["ev:integ-2a9f-c1"], process: [] },
          payload_summary: {
            execution_batches: [["saleor-dashboard"], ["saleor-core"]],
            contracts: [
              {
                interface: "INotifyDigestSubscribe",
                provider: "saleor-dashboard",
                consumers: ["saleor-core"],
              },
            ],
          },
          affected_repository_ids: [REPO_WEB, REPO_CORE],
          business_time: "2026-07-29T03:30:00Z",
          event_type: "IntegrationDecided",
        }),
      ],
      legacy_gaps: ["confirmation"],
    },
  },

  // ── P4：购物车库存提示（两步，确认过；issue 列表 c8e07b12） ─────────────
  {
    id: "c8e07b12-4a91-4d55-b7e6-19df20de0039",
    title: "购物车库存提示优化",
    keywords: ["购物车", "库存", "提示", "优化", "商品", "展示", "数量"],
    chain: {
      project_id: "c8e07b12-4a91-4d55-b7e6-19df20de0039",
      organization_id: ORG_ID,
      requirement: {
        text: "购物车在库存不足时提示不明确，用户加购后才在结算时报错。需要在购物车行内实时提示库存余量并禁用超量加购。",
        plan_version: 1,
        snapshot_id: fabricateUuid("c8e07b12-4a91-4d55-b7e6-19df20de0039", 900),
      },
      nodes: [
        makeSheet("c8e07b12-4a91-4d55-b7e6-19df20de0039", 1, {
          step: "classification",
          version: 1,
          status: "proposed",
          actor: LLM(AGENT_CLASSIFY),
          evidence_refs: { result: ["ev:class-c8e0-a1"], process: [] },
          payload_summary: {
            required: ["saleor-dashboard"],
            maybe: ["saleor-docs"],
            excluded: [],
            effective_tiers: { "saleor-dashboard": "required", "saleor-docs": "maybe" },
            supplemented_repository_ids: [],
          },
          affected_repository_ids: [REPO_WEB, REPO_DOCS],
          business_time: "2026-08-05T05:00:00Z",
          event_type: "ClassificationDecided",
        }),
        makeSheet("c8e07b12-4a91-4d55-b7e6-19df20de0039", 2, {
          step: "confirmation",
          version: 1,
          status: "confirmed",
          actor: HUMAN(ORG_LEADER),
          upstream_ref: fabricateUuid("c8e07b12-4a91-4d55-b7e6-19df20de0039", 1),
          evidence_refs: { result: ["ev:conf-c8e0-b1"], process: [] },
          payload_summary: {
            approval: {
              state: "approved",
              decided_by_agent_id: ORG_LEADER,
              reason: "购物车库存提示先落前台展示层，后台数据接口同步补足余量字段。",
            },
            adjustments: [],
            effective_tiers: { "saleor-dashboard": "required", "saleor-docs": "maybe" },
          },
          affected_repository_ids: [REPO_WEB, REPO_DOCS],
          business_time: "2026-08-05T08:10:00Z",
          event_type: "ConfirmationDecided",
        }),
      ],
      legacy_gaps: [],
    },
  },

  // ── P5：API 定价折扣字段（一步，低相似干扰项；issue 列表 5d3a91f4） ─────
  {
    id: "5d3a91f4-6b28-4e03-8f41-64ca70de0038",
    title: "API 定价结果增加 discount_amount",
    keywords: ["API", "定价", "结果", "折扣", "金额", "字段", "返回"],
    chain: {
      project_id: "5d3a91f4-6b28-4e03-8f41-64ca70de0038",
      organization_id: ORG_ID,
      requirement: {
        text: "定价 API 返回结果缺少折扣金额字段，客户端无法拆分原价与折后价。需要在响应中补充 discount_amount，并保证含税/不含税口径一致。",
        plan_version: 1,
        snapshot_id: fabricateUuid("5d3a91f4-6b28-4e03-8f41-64ca70de0038", 900),
      },
      nodes: [
        makeSheet("5d3a91f4-6b28-4e03-8f41-64ca70de0038", 1, {
          step: "classification",
          version: 1,
          status: "proposed",
          actor: LLM(AGENT_CLASSIFY),
          evidence_refs: { result: ["ev:class-5d3a-a1"], process: [] },
          payload_summary: {
            required: ["saleor-core"],
            maybe: ["saleor-docs"],
            excluded: [],
            effective_tiers: { "saleor-core": "required", "saleor-docs": "maybe" },
            supplemented_repository_ids: [],
          },
          affected_repository_ids: [REPO_CORE, REPO_DOCS],
          business_time: "2026-08-08T06:00:00Z",
          event_type: "ClassificationDecided",
        }),
      ],
      legacy_gaps: [],
    },
  },
];

// ══════════════ 供页面提示/错误文案的元数据 ══════════════

export const DECISION_PROJECT_META: Array<{ id: string; title: string }> = PROJECTS.map((p) => ({
  id: p.id,
  title: p.title,
}));

// ══════════════ 追溯（需求定位入口） ══════════════

export function replayTrace(projectId: string): DecisionChainView | null {
  const project = PROJECTS.find((p) => p.id === projectId);
  if (!project) return null;
  // §7 中段缺口由节点集合实时推导（与后端读模型一致），fixture 手写值仅作注释存档。
  return { ...project.chain, legacy_gaps: legacyGapsFor(project.chain.nodes) };
}

/** replay 需求定位解析：完整 UUID 或唯一短前缀（8 位展示 shortId）都行。
 *  不匹配 / 前缀不唯一返回 null，由调用方给出候选列表。 */
export function resolveReplayProjectId(input: string): string | null {
  const raw = input.trim().toLowerCase();
  if (!raw) return null;
  const matches = PROJECTS.filter((p) => p.id === raw || p.id.startsWith(raw));
  return matches.length === 1 ? matches[0].id : null;
}

/** 需求定位候选（replay 侧，页面「需求定位」入口用）。 */
export interface ReplayProjectCandidate {
  project_id: string;
  title: string;
  latest_at: string;
}

/** 需求定位（replay）：剥掉 # 后先按 id（完整/前缀）直命中，再按标题+关键词
 *  分词匹配。用户手里只有 issue 列表的信息（标题、#短id）时也能找到项目。 */
export function searchReplayProjects(input: string): ReplayProjectCandidate[] {
  const raw = input.trim().replace(/^#/, "").toLowerCase();
  if (!raw) return [];
  const byId = (p: FixtureProject): ReplayProjectCandidate => {
    const last = latestNode(p.chain.nodes);
    return { project_id: p.id, title: p.title, latest_at: last?.business_time ?? "" };
  };
  const exact = PROJECTS.filter((p) => p.id === raw);
  if (exact.length > 0) return exact.map(byId);
  const prefix = PROJECTS.filter((p) => p.id.startsWith(raw));
  if (prefix.length > 0) return prefix.map(byId);
  // 关键词分词匹配（tokenize 与语义检索共用）
  const tokens = tokenize(raw);
  if (tokens.length === 0) return [];
  return PROJECTS.map((p) => {
    const haystack = `${p.title} ${p.keywords.join(" ")}`.toLowerCase();
    const matched = tokens.filter((t) => haystack.includes(t)).length;
    return { p, ratio: matched / tokens.length };
  })
    .filter((x) => x.ratio > 0)
    .sort(
      (a, b) =>
        b.ratio - a.ratio ||
        latestNode(b.p.chain.nodes)!.business_time.localeCompare(
          latestNode(a.p.chain.nodes)!.business_time,
        ),
    )
    .map((x) => byId(x.p));
}

// ══════════════ 相似历史（structural：同仓 + 最新优先） ══════════════

function latestNode(nodes: DecisionNodeView[]): DecisionNodeView | null {
  return nodes.length === 0 ? null : nodes.reduce((a, b) => (b.business_time > a.business_time ? b : a));
}

function collapse(
  node: DecisionNodeView,
  score: number | null,
  requirementText: string | null,
): SimilarDecisionView {
  return {
    decision_id: node.decision_id,
    project_id: node.project_id,
    organization_id: node.organization_id,
    step: node.step,
    version: node.version,
    status: node.status,
    affected_repository_ids: node.affected_repository_ids,
    payload_summary: node.payload_summary,
    business_time: node.business_time,
    score,
    requirement_text: requirementText,
  };
}

/** 需求级命中：卡头用需求根句（无快照回退夹具标题），决策单只是命中依据。 */
function requirementRoot(p: (typeof PROJECTS)[number]): string | null {
  return p.chain.requirement?.text ?? p.title ?? null;
}

export function replaySimilar(projectId: string, topK = 5): SimilarDecisionsView {
  const target = PROJECTS.find((p) => p.id === projectId);
  if (!target) {
    return { project_id: projectId, organization_id: ORG_ID, mode: "structural", hits: [] };
  }
  const targetRepos = new Set(target.chain.nodes.flatMap((n) => n.affected_repository_ids));
  const hits = PROJECTS.filter((p) => p.id !== projectId)
    .map((p) => ({
      p,
      shared: p.chain.nodes.flatMap((n) => n.affected_repository_ids).filter((r) => targetRepos.has(r)),
    }))
    .filter((x) => x.shared.length > 0)
    .map((x) => collapse(latestNode(x.p.chain.nodes)!, null, requirementRoot(x.p)))
    .sort((a, b) => b.business_time.localeCompare(a.business_time))
    .slice(0, topK);
  return { project_id: projectId, organization_id: ORG_ID, mode: "structural", hits };
}

// ══════════════ 语义检索（replay 版：关键词重合度当相似分） ══════════════

/** 分词：拉丁词按整词、CJK 按单字。演示级近似即可——夹具本就不是真实向量。 */
function tokenize(query: string): string[] {
  const words = (query.match(/[a-zA-Z0-9]+/g) ?? []).map((w) => w.toLowerCase());
  const cjk = Array.from(query.replace(/[a-zA-Z0-9\s，。,.!?！？：:；;、/\\\-_()（）[\]]/g, ""));
  return [...words, ...cjk];
}

export function replaySemanticSearch(queryText: string, topK = 5): SemanticSearchView {
  const tokens = tokenize(queryText);
  const ranked = PROJECTS.map((p) => {
    const haystack = `${p.title} ${p.keywords.join(" ")}`;
    const matched = tokens.filter((t) => haystack.includes(t)).length;
    // 无 token（空查询）→ 全 0.28 平局，靠时间倒序定序
    const ratio = tokens.length === 0 ? 0 : matched / tokens.length;
    const score = Math.round((0.28 + 0.62 * ratio) * 100) / 100;
    return { p, score };
  }).sort(
    (a, b) =>
      b.score - a.score ||
      latestNode(b.p.chain.nodes)!.business_time.localeCompare(latestNode(a.p.chain.nodes)!.business_time),
  );
  return {
    organization_id: ORG_ID,
    query_text: queryText,
    mode: "semantic",
    hits: ranked
      .slice(0, topK)
      .map(({ p, score }) => collapse(latestNode(p.chain.nodes)!, score, requirementRoot(p))),
  };
}
