/** 发现链 replay 夹具（批次 B-1/B-2）。
 *
 *  形状唯一来源是契约 v0.4 §2.2/§3.1/§4.5，类型在 `api/contract.ts`——本文件只有
 *  数据，不另抄一份字段表（同 data/issueDetail.ts 的分工）。
 *
 *  **为什么夹具要覆盖十种形态**：按「哪些分支会走出不同界面」逐个建，而不是只建一个
 *  好看的成功态——只测成功态等于没测。失败态、回退评分、证据漂移这些分支，正是出事
 *  时才会被看到的那几屏。
 *
 *  ⚠ 夹具**不模拟状态机**：`step` / `step_state` 在每份夹具里都是**写死的常量**，
 *  正如 live 下它们是读模型算出来的常量。前端在任何模式下都不许按 §3.2 自行推导
 *  走到了哪一步（红线：状态映射唯一实现在读模型）。同理，回放模式下四个写触发与
 *  审批一律**如实拒绝**而不是就地改夹具——就地改就等于在前端实现了一份步进器判定。
 */
import type {
  ConfirmationResultView,
  DiscoveryCandidateItem,
  DiscoveryTaskView,
  DiscoveryView,
} from "../api/contract";

/** 与 data/issueDetail.ts 同一个 issue 世界（#7f3d2a10 结账价格修改原因） */
const ISSUE_ID = "7f3d2a10-93d0-4c8e-9b21-5aa1c0de0042";
const LEADER = "a7c9e2f1-5b3d-4e8a-9f01-2c4d6e8a0b13";
const REPO_API = "b1c2d3e4-0001-4a2b-9c3d-4e5f6a7b8c01";
const REPO_WEB = "b1c2d3e4-0002-4a2b-9c3d-4e5f6a7b8c02";
const REPO_DOCS = "b1c2d3e4-0003-4a2b-9c3d-4e5f6a7b8c03";
/** 评分到但最终没进交付范围的候选。它在 catalog 里（否则评分阶段就被过滤掉了，
 *  §2.2），只是分档判到 maybe——所以详情页的关联仓库里没有它。 */
const REPO_STORE = "b1c2d3e4-0004-4a2b-9c3d-4e5f6a7b8c04";

const REQUIREMENT =
  "运营侧需要在订单结账时记录价格被修改的原因（促销、议价、纠错），原因随订单落库并在后台订单详情页展示。价格修改入口不变，新增原因必填校验与审计字段。";

const AT = "2026-08-12T02:14:05Z";

/** 空审批块。契约 §3.1 里 `approval` **不可为 null**（三块可以为 null，它不行），
 *  所以「还没人批」的形态是 state=not_requested，而不是整块缺席。
 *  `evidence_version` 此时为 **null**——它记的是「上一次决定绑在哪份证据上」，
 *  还没决定过就没有值（§3.1 的两字段分工表）。 */
const APPROVAL_NONE = {
  state: "not_requested" as const,
  evidence_version: null,
  decided_by_agent_id: null,
  reason: "",
  decided_at: null,
};

/** 当前分档指纹（顶层 `classification_evidence_version`）。分档存在即非空，
 *  与审批与否无关——提交审批回填的就是它。 */
const EVIDENCE = "sha256:9f2c4a17b83e0d5641ca77b0e9d2183c6a5b4e0f7c1d9a2b3e4f5061728394ab";

/** 需求分析：不充分 + 两条追问（原型 ② 的那两条）。 */
const ANALYSIS_INSUFFICIENT = {
  sufficient: false,
  confidence: 0.62,
  missing_dimensions: ["行为描述", "数据迁移范围"],
  questions: ["「原因」指枚举选项还是自由文本？", "是否涉及历史订单的原因字段回填？"],
  extracted_keywords: ["结账", "价格修改", "原因", "审计"],
  answers: [],
  analyzed_requirement: REQUIREMENT,
  forced_continue: null,
  ran_at: AT,
  by_agent_id: LEADER,
  error: null,
};

const ANALYSIS_SUFFICIENT = {
  ...ANALYSIS_INSUFFICIENT,
  sufficient: true,
  confidence: 0.88,
  missing_dimensions: [],
  questions: [],
  answers: [
    { question: "「原因」指枚举选项还是自由文本？", answer: "枚举四选一 + 可选备注" },
    { question: "是否涉及历史订单的原因字段回填？", answer: "不回填，只对新单生效" },
  ],
  analyzed_requirement: `${REQUIREMENT}\n\n补充：原因为枚举四选一 + 可选备注；历史订单不回填。`,
};

const CANDIDATE_ITEMS: DiscoveryCandidateItem[] = [
  {
    repository_id: REPO_API,
    repository_name: "saleor-core",
    score: 0.86,
    matched_terms: ["价格", "结账", "audit"],
    rationale:
      "近 30 天有结账路由改动；checkout/price_override.py 已有 override_amount 字段但无原因列，订单模型的审计表就在本仓。该仓是价格写入的唯一入口，原因字段必须与金额在同一事务内落库。",
    is_entry_point: true,
  },
  {
    repository_id: REPO_WEB,
    repository_name: "saleor-dashboard",
    score: 0.71,
    matched_terms: ["结账", "后台"],
    rationale:
      "后台订单详情页在本仓；价格修改弹窗组件 PriceOverrideDialog 需要新增原因选择器与必填校验。",
    is_entry_point: false,
  },
  {
    repository_id: REPO_STORE,
    repository_name: "saleor-storefront",
    score: 0.41,
    matched_terms: [],
    rationale: "名称语义相关；无直接依赖证据。",
    is_entry_point: false,
  },
  {
    repository_id: REPO_DOCS,
    repository_name: "saleor-docs",
    score: 0.18,
    matched_terms: ["订单"],
    rationale: "仅文档仓，未见价格相关章节。",
    is_entry_point: false,
  },
];

const CANDIDATES_LLM = {
  items: CANDIDATE_ITEMS,
  llm_used: true,
  // 服务端缺省（§4.3：面板入口缺省 10，与脚本入口的 5 有意不同）
  limit: 10,
  entry_point: null,
  ran_at: AT,
  by_agent_id: LEADER,
  error: null,
};

/** Q11 的诚实自述形态：同样的形状、同样的分数区间，但**不是模型评的**。 */
const CANDIDATES_FALLBACK = {
  ...CANDIDATES_LLM,
  llm_used: false,
  items: CANDIDATE_ITEMS.map((c) => ({
    ...c,
    rationale: `关键词匹配命中 ${c.matched_terms.length} 项：${c.matched_terms.join("、") || "（无）"}`,
  })),
};

const result = (
  repository: string,
  status: ConfirmationResultView["status"],
  confidence: number,
  reason: string,
  plan_summary: string,
): ConfirmationResultView => ({
  repository,
  status,
  confidence,
  reason,
  plan_summary,
  plan: null,
  missing_dependencies: [],
});

const CLASSIFICATION = {
  required: [
    result(
      "saleor-core",
      "REQUIRED",
      0.91,
      "价格写入的唯一入口，原因字段与金额必须同事务落库；不改本仓则需求无法成立。",
      "price_override 表增 reason 枚举列与 audit 字段，写入路径加必填校验",
    ),
    result(
      "saleor-dashboard",
      "REQUIRED",
      0.84,
      "后台订单详情页与价格修改弹窗都在本仓，展示与必填校验的落点。",
      "PriceOverrideDialog 增原因选择器；订单详情页增原因展示行",
    ),
  ],
  maybe: [
    result(
      "saleor-storefront",
      "MAYBE",
      0.44,
      "若买家侧也要看到修改原因则涉及，但需求只说了后台；证据不足以判定必需。",
      "（待定）订单详情组件增原因展示",
    ),
  ],
  excluded: [
    result("saleor-docs", "EXCLUDED", 0.12, "纯文档仓，无价格相关章节，改动无落点。", ""),
  ],
  supplemented_repos: ["saleor-dashboard"],
  adjustments: [],
  ran_at: AT,
  by_agent_id: LEADER,
  error: null,
};

const TIERS_UNADJUSTED = [
  { repository: "saleor-core", tier: "required" as const, adjusted: false, original_tier: null },
  { repository: "saleor-dashboard", tier: "required" as const, adjusted: false, original_tier: null },
  { repository: "saleor-storefront", tier: "maybe" as const, adjusted: false, original_tier: null },
  { repository: "saleor-docs", tier: "excluded" as const, adjusted: false, original_tier: null },
];

/** 审批人把 saleor-docs 从 EXCLUDED 提到 REQUIRED 后的生效分档——这也正是详情页
 *  夹具里 saleor-docs 明明被模型排除、却仍在本 issue 交付范围内的原因（同一世界自洽）。
 *  LLM 原判仍留在 classification.excluded 里：两份并存才看得出「模型说什么、人改成什么」。 */
const TIERS_ADJUSTED = [
  TIERS_UNADJUSTED[0],
  TIERS_UNADJUSTED[1],
  TIERS_UNADJUSTED[2],
  { repository: "saleor-docs", tier: "required" as const, adjusted: true, original_tier: "excluded" as const },
];

const base = {
  issue_id: ISSUE_ID,
  plan_version: 1,
  requirement_text: REQUIREMENT,
  running_task_id: null,
  analyzed_requirement: null,
  analysis: null,
  candidates: null,
  classification: null,
  // 分档不存在 → 当前指纹为 null（无档可指纹）
  classification_evidence_version: null,
  effective_tiers: [],
  approval: APPROVAL_NONE,
  integration: null,
} satisfies Omit<DiscoveryView, "step" | "step_state">;

/** 十种形态。名字即 `?discovery=<name>` 的取值。 */
export const discoveryFixtures: Record<string, DiscoveryView> = {
  /** §3.2 规则 1：从未发起 → step 1 / idle，三块全 null（200 而非 404） */
  empty: { ...base, step: 1, step_state: "idle" },

  /** §3.2 规则 2：分析跑过但未通过、也没强行继续 → 仍停在 step 1 */
  clarify: {
    ...base,
    step: 1,
    step_state: "idle",
    analyzed_requirement: REQUIREMENT,
    analysis: ANALYSIS_INSUFFICIENT,
  },

  /** 步块 error 非空 → step_state failed。界面显服务端原文，不显进度条 */
  analysis_failed: {
    ...base,
    step: 1,
    step_state: "failed",
    analysis: {
      ...ANALYSIS_INSUFFICIENT,
      questions: [],
      missing_dimensions: [],
      confidence: 0,
      error: {
        message:
          "LLM 调用失败：Connection error while calling chat completions endpoint (attempt 3/3)",
        at: AT,
      },
    },
  },

  /** §4.6 强行继续后：分析仍不充分，但 forced_continue 非空 → 规则 2 不再命中，
   *  落到规则 3（candidates 为 null）→ step 2 */
  forced: {
    ...base,
    step: 2,
    step_state: "idle",
    analyzed_requirement: REQUIREMENT,
    analysis: {
      ...ANALYSIS_INSUFFICIENT,
      forced_continue: { ignored_question_count: 2, by_agent_id: LEADER, at: AT },
    },
  },

  /** 候选评分失败：analysis 已通过，candidates.error 非空 → step 2 / failed */
  candidates_failed: {
    ...base,
    step: 2,
    step_state: "failed",
    analyzed_requirement: ANALYSIS_SUFFICIENT.analyzed_requirement,
    analysis: ANALYSIS_SUFFICIENT,
    candidates: {
      ...CANDIDATES_LLM,
      items: [],
      error: { message: "LLM 未配置：REPOMESH_LLM_API_KEY 缺失（503）", at: AT },
    },
  },

  /** Q11 形态：分数是词频算的不是模型评的，界面必须自己说出来 */
  fallback: {
    ...base,
    step: 3,
    step_state: "idle",
    analyzed_requirement: ANALYSIS_SUFFICIENT.analyzed_requirement,
    analysis: ANALYSIS_SUFFICIENT,
    candidates: CANDIDATES_FALLBACK,
  },

  /** §3.2 规则 4：候选已出、分档未生成 → 仍属「3 分档审批」格 */
  classifying: {
    ...base,
    step: 3,
    step_state: "running",
    running_task_id: "c0ffee00-1111-4222-8333-444455556666",
    analyzed_requirement: ANALYSIS_SUFFICIENT.analyzed_requirement,
    analysis: ANALYSIS_SUFFICIENT,
    candidates: CANDIDATES_LLM,
  },

  /** §3.2 规则 5：分档已出、尚未审批 → step 3 等人批 */
  approval: {
    ...base,
    step: 3,
    step_state: "idle",
    analyzed_requirement: ANALYSIS_SUFFICIENT.analyzed_requirement,
    analysis: ANALYSIS_SUFFICIENT,
    candidates: CANDIDATES_LLM,
    classification: CLASSIFICATION,
    // 分档在、还没人批：当前指纹非空，而 approval.evidence_version 仍是 null。
    // 这正是两个字段不可互换的形态——拿 approval 那个去提交必然 409。
    classification_evidence_version: EVIDENCE,
    effective_tiers: TIERS_UNADJUSTED,
    approval: APPROVAL_NONE,
  },

  /** 被要求改动：state=changes_requested，**不清空** classification（人只是要求改，
   *  模型判断仍是事实）；§3.2 规则 5 同样命中 → 停在 step 3 */
  changes_requested: {
    ...base,
    step: 3,
    step_state: "idle",
    analyzed_requirement: ANALYSIS_SUFFICIENT.analyzed_requirement,
    analysis: ANALYSIS_SUFFICIENT,
    candidates: CANDIDATES_LLM,
    classification: CLASSIFICATION,
    classification_evidence_version: EVIDENCE,
    effective_tiers: TIERS_UNADJUSTED,
    approval: {
      state: "changes_requested",
      evidence_version: EVIDENCE,
      decided_by_agent_id: LEADER,
      reason: "saleor-storefront 的判据不足，请补充依赖证据后重新分类。",
      decided_at: AT,
    },
  },

  /** §3.2 规则 6：已放行、task_dag 仍空 → step 4 / idle（等生成计划）。
   *  同时是「改档留痕」的形态：LLM 原判 MAYBE 与人改成的 REQUIRED 并存。 */
  approved: {
    ...base,
    step: 4,
    step_state: "idle",
    analyzed_requirement: ANALYSIS_SUFFICIENT.analyzed_requirement,
    analysis: ANALYSIS_SUFFICIENT,
    candidates: CANDIDATES_LLM,
    classification: {
      ...CLASSIFICATION,
      adjustments: [
        { repository: "saleor-docs", from: "EXCLUDED", to: "REQUIRED", by_agent_id: LEADER, at: AT },
      ],
    },
    classification_evidence_version: EVIDENCE,
    effective_tiers: TIERS_ADJUSTED,
    approval: {
      state: "approved",
      evidence_version: EVIDENCE,
      decided_by_agent_id: LEADER,
      reason: "范围认可；saleor-docs 提为必需，价格原因的运营文档同批改。",
      decided_at: AT,
    },
  },

  /** §3.2 规则 7：整链走完 → step 4 / done，集成计数非空 */
  done: {
    ...base,
    step: 4,
    step_state: "done",
    analyzed_requirement: ANALYSIS_SUFFICIENT.analyzed_requirement,
    analysis: ANALYSIS_SUFFICIENT,
    candidates: CANDIDATES_LLM,
    classification: {
      ...CLASSIFICATION,
      adjustments: [
        { repository: "saleor-docs", from: "EXCLUDED", to: "REQUIRED", by_agent_id: LEADER, at: AT },
      ],
    },
    classification_evidence_version: EVIDENCE,
    effective_tiers: TIERS_ADJUSTED,
    approval: {
      state: "approved",
      evidence_version: EVIDENCE,
      decided_by_agent_id: LEADER,
      reason: "范围认可；saleor-docs 提为必需，价格原因的运营文档同批改。",
      decided_at: AT,
    },
    integration: { task_dag_count: 6, batch_count: 3, contract_count: 2 },
  },
};

/** 默认形态。回放世界里这个 issue 已经在发布门禁（phase=release、已有计划快照），
 *  与它自洽的发现链形态就是「整链走完」——默认摆一个半截的发现链会让同一份夹具
 *  自相矛盾。其余形态用 `?discovery=<name>` 取，名字见上表。 */
export const DISCOVERY_FIXTURE_DEFAULT = "done";

/** `classifying` 形态配套的轮询视图：Q17 的逐候选进度（N 次 LLM 调用）。 */
export const discoveryTaskFixture: DiscoveryTaskView = {
  task_id: "c0ffee00-1111-4222-8333-444455556666",
  issue_id: ISSUE_ID,
  step: 3,
  status: "running",
  progress: { done: 3, total: 4, label: "saleor-docs" },
  error: null,
  started_at: AT,
  finished_at: null,
};
