# 交付读模型契约 v0.4（发现链读投影 / 写触发 / 分档审批）

- 状态：**已裁决 · 生效**（Q1~Q18 按建议案，2026-08-12 主脑裁决）。B 批后端已按本文实施，
  正文与实现最终形状一致；实施中与草案有出入之处逐条列在 §6.1，**以本文为准**。
- 版本：0.4（**增量**：v0.1/v0.2/v0.3 全文继续有效，本文件只定义发现链的一个读端点、
  四个写触发端点、一个审批写端点，以及承载它们的快照版本语义）
- 基线：`docs/contracts/delivery-read-model-v0.3.md`（截至 §6 S-8）
- 对应批次：`docs/development/full-loop-plan-20260812.md` 批次 B（B-1/B-2/B-3）
- 界面定稿：`docs/development/full-loop-gui-design-20260812.md` ②（发现面板）
- 消费方：`frontend/` issue 详情页「发现」面板
- 体例沿用：诚实数据、状态映射唯一实现在读模型、写端点幂等与审计（v0.1 §4.4 风格）、
  开放问题列 Q 表（§6，已全部裁决）

---

## 0. 定位与边界

v0.2 §0 语义等式不变（issue = Project、工作区 = Organization、**零新展示实体**）。
v0.3 把「从界面发起需求」的入口打通到「建 issue = 落 `plan_version=1` 草稿快照」为止；
本增量接着往下走一段：**让草稿快照上的需求文本真正跑完发现链**（需求分析 → 候选评分 →
三档分类 → 人工审批 → 生成计划），并把每一步的结果与状态变成可读投影。

| 缺口 | 本文契约 | 事实源与 Owner |
| --- | --- | --- |
| B-1 建 issue 后发现链一步也触发不了 | §4 四个写触发端点 | `repository_intelligence`（`plan_snapshots` 唯一生产方） |
| B-1 面板不知道「走到哪」、看不到追问/评分/分档 | §3 读投影 + 步进器判定 | 读模型（`api/read_models`），唯一实现 |
| B-2 分档结果无处审批 | §5 审批面 | 见 §5.1 三套既有机制实测比较 |
| B-3 以上语义无契约 | 本文件 | — |

### 0.1 主脑已裁决的前提（本文遵守，不重开）

1. **审批 v1 必经**：分档结果必须经 organization leader 审批放行才能进 Step 3 集成；
   全自动模式进 backlog（`full-loop-plan-20260812.md` §3）。
2. **clarify 可强行继续但必须留痕**：留痕内容为「忽略 N 条追问继续」（GUI 裁决 2）。
3. **零新展示实体延续**：发现各步结果挂 issue 的快照（PlanSnapshot 系），不建新展示实体。
   ⚠ **零新实体 ≠ 零新列**——见 §2 与 Q1。这一条起草时被单独拎出来请求裁决，已裁为「加一列 `discovery`」。
4. **LLM 失败显示错误原文摘要**，不显示假进度；**rationale 原样展示**，读模型不摘要不截断。
5. 写请求**幂等键 + 花名册派生主体**（v0.3 §1.2 同款）；鉴权维持 `Authorization: Bearer`
   动作 token（v0.2 Q1）。

### 0.2 明确不做

物化开工（`bridge/materialize` 的 GUI 触发）属批次 C-3，本文只定义**衔接语义**（§2.4）
不定义端点；图形化 DAG（C-2）已落地并消费 v0.2 §5.4，本文不改其形状（只在附录提两条
勘误）；全自动模式（跳过审批）、SSE 推送、主体化凭据、issue 级归档：沿用既有 backlog。

---

## 1. 管线现状（起草前逐一核实的事实，契约据此设计）

### 1.1 五步端点与真实形状

全部挂 `repository_intelligence` 的 `router.py`，前缀 `/api/v1`
（`src/repomesh/api/router.py:24`），每个写路由带 `dependencies=[ACTION_TOKEN]`
（`router.py:130` 定义，`:104-126` 是检查本体：未配置 token → 503，不匹配 → 401）。

| 管线步 | 端点 | 定义处 | 请求模型 | 响应模型 | LLM 调用 |
| --- | --- | --- | --- | --- | --- |
| Step 0 需求分析 | `POST /requirement-analysis` | `router.py:469` | `RequirementAnalysisRequest`（`models.py:221`：只有 `requirement`） | `RequirementAnalysisView`（`models.py:225`：`sufficient` / `confidence` / `missing_dimensions` / `questions` / `extracted_keywords`） | 1 次 |
| Step 1 候选评分 | `POST /discovery` | `router.py:443` | `DiscoveryRequest`（`models.py:206`：`requirement` / `limit` / `entry_point`） | `list[DiscoveryCandidate]`（`models.py:212`：`repository_id` / `repository_name` / `score` / `matched_terms` / `rationale` / `is_entry_point`） | 1 次（可回退，见 §1.4） |
| Step 2 三档分类 | `POST /confirmation` | `router.py:505` | `ConfirmationRequest`（`models.py:238`：`requirement` / **`candidate_repos`** / **`discovery_evidence`**） | `ConfirmationSummaryView`（`models.py:267`：`required` / `maybe` / `excluded` / `supplemented_repos` / `final_repos`） | **N 次**（每候选一次） |
| Step 3 集成 | `POST /integration` | `router.py:566` | `IntegrationRequest`（`models.py:275`：`requirement` + **整块 `confirmation`**） | `IntegratedPlanView`（`models.py:296`：`engineering_spec` / `contracts` / `task_dag` / `execution_batches`） | 1 次 |
| Step 4 物化 | `POST /bridge/materialize` | `router.py:602` | `MaterializeRequest`（`models.py:303`：**整块计划** + `project_id` / `leader_agent_id` / `idempotency_prefix`） | `MaterializeResponse`（`models.py:322`） | 0 次 |

三档取值是**大写字符串**：`ConfirmationResult.status ∈ {"REQUIRED","MAYBE","EXCLUDED"}`
（`application/confirmation.py:86`、`:253-255`），不是枚举类型；GUI 的行内下拉展示小写
（`required/maybe/excluded`），大小写映射必须只有一处实现。

### 1.2 步骤之间传什么：**服务端一无所有**

`scripts/run_pipeline.py` 是当前唯一驱动者，它把每一步的响应**原样回传给下一步**：

- `:150-158` 从 discovery 响应拼出 `candidate_names` 与 `evidence`，
- `:163-173` 把这两样连同 requirement 一起 POST 给 `/confirmation`，
- `:192-199` 把整个 confirmation 响应体塞进 `/integration` 的请求体，
- `:222-235` 把整个 integration 响应体塞进 `/bridge/materialize` 的请求体。

**五步端点全部无状态**：没有任何一步把中间结果写进数据库，`project_id` 直到 Step 4 才
第一次出现在请求里。这就是 B-3 必须存在的根本原因，也是本契约与现有端点最大的语义差别：

> **GUI 面不能照抄脚本的无状态串法。**浏览器持有中间态等于把事实源搬进前端——刷新即丢、
> 换标签页即丢、两人同看即分叉。发现链的中间结果必须落服务端（§2）。

计划文档 §0「管线五步 API 全在且有鉴权」属实，但没写这一层，容易被读成「GUI 照着
run_pipeline 调一遍即可」。

### 1.3 快照的存储能力（决定 §2 的可选项）

`PlanSnapshotRecord`（`infrastructure/models.py:36-59`）的全部列：
`id` / `project_id` / `plan_version` / `created_at` / `created_by_agent_id` /
`engineering_spec`(Text) / `contracts`(JSONB) / `task_dag`(JSONB) /
`execution_batches`(JSONB) / `graph_edges`(JSONB) / `execution_plan_id` /
`requirement_text`(Text) / `integration_method`(String(20))；
唯一约束 `(project_id, plan_version)`（`:38-41`）。

**没有任何一列能诚实地容纳发现中间态**：追问问题列表、候选评分+rationale、三档分类、
审批记录，在上表里都无处可去。把它们塞进 `contracts` 或 `task_dag` 就是让一列有两个
语义，属「同一字段两个事实源」，本项目已就此吃过亏。→ Q1。

`PlanSnapshotStore` 文档串称快照 **write-once**（`plan_snapshot_store.py:1-6`），但
`link_execution_plan()`（`:135-147`）在 materialize 之后回填 `execution_plan_id`，
即**实现早已不是 write-once**。这条不实描述须与 Q2 一并勘正。

### 1.4 诚实必须写进契约的三件事（现状核实）

1. **候选评分可能不是 LLM 出的**。`RepositoryDiscoveryService.discover()`
   （`application/discovery.py:39`）在 `self._llm` 为空或 LLM 抛错时静默回退到关键词匹配
   （`:49-52`、`:82-84` 的 `except ... return []`），两条路径产出的
   `DiscoveryCandidate` **形状完全相同**，`llm_used` 只进了一个 span attribute
   （`:50` 算出、`:54` 落 span），不进响应体。消费方无法分辨「模型评的 0.62」与「词频算的 0.62」。
   → 本契约要求响应自述 `llm_used`（§3.1），Q11。
   ⚠ 不要用「`matched_terms` 是否为空」去反推：那是**信号对≠原因对**，同一形状可以来自
   别的原因，判据必须是产出机制本身的自述。
2. **LLM 调用是同步阻塞的**。`ConfirmationService.confirm()`
   （`application/confirmation.py:333`）与 `PlanIntegrationService.integrate()`
   （`application/plan_integration.py:475`）都是 `def` 不是 `async def`；
   `RequirementAnalyzer.analyze()` 同理；`discover()` 虽是 `async def`，但内部
   `self._llm.chat(...)`（`discovery.py:80`）是同步调用。从 `async def` 端点直接调用它们
   会**阻塞整个事件循环**——一次 10 候选的 confirmation 期间，8100 上所有请求（包括读端点
   轮询）都排队。→ 直接决定 §4.2 的取舍，Q6。
3. **LLM 未配置时三个端点行为不一致**。`/requirement-analysis` 判 `analyzer is None` 后
   返 503（`router.py:477-482`）；`/confirmation`（`:511-513`）与 `/integration`
   （`:570-574`）直接把可能为 `None` 的 `container.llm_client` 传进服务，最终在
   `chat()` 处炸成 500。→ Q12。

### 1.5 核实中发现的「管线现状 vs 计划/设计文档」不一致（诚实清单）

| # | 文档说法 | 核实到的现状 | 影响 |
| --- | --- | --- | --- |
| 1 | 计划 B-2「分档结果**进决策夹**审批」 | 控制台决策夹＝**轮次粒度治理决策**（`frontend/src/api/decisions.ts` 取 `/deliveries/{round_id}/decisions`；写端点 `modules/delivery/api/deliveries.py:27` 必须带 `change_set_id`+`repository_id`+`head_sha`）。发现期没有 ChangeSet、没有 delivery_id、没有 head_sha | **按字面不可实现**，须另定机制，见 §5 与 Q4 |
| 2 | 设计稿 ②「审批主体沿用花名册派生（org leader）」 | 语义最贴的既有机制（检查点决策 `api/human_control.py:261`）主体是**人类账户**：`actor = await _account(request)`（`:265`），走 session ticket 不走动作 token | 两套主体模型冲突，见 §5.1 |
| 3 | 设计稿 ② 步进器四步 `1 需求分析 → 2 候选评分 → 3 分档审批 → 4 生成计划`；计划 B-1 写「Step 0 需求分析 → Step 1 候选评分 → Step 2 三档分类」 | 管线自身编号（`run_pipeline.py:98/127/161/190/209`）是 Step 0..4。GUI 的「3 分档审批」把**管线 Step 2（生成三档）**与**人工审批**并成一格，GUI 的「4 生成计划」＝管线 Step 3 | 两套编号并存，契约须给映射表（§3.2），Q14 |
| 4 | 计划 §0「管线五步 API 全在且有鉴权」 | 属实，但五步**无任何服务端状态**（§1.2） | GUI 不能照抄脚本串法；B-3 的根本理由，计划未写 |
| 5 | 计划 C-3「物化并开工 → plan loop 自动转起」 | `materialize` 内部**已有一道人工检查点 gate**：`change_orchestration/application.py:200-206` 对 `ProjectCheckpoint.REPOSITORY_SCOPE` 求值，不通过即 `WorkflowBlocked` → 409（`router.py:649-650`） | 受控模式下「物化开工」会 409，用户可能被要求批两次，见 §5.4 与 Q5 |
| 6 | — | `confirm_repositories` 的 `raise ValueError("No valid candidate repositories found in catalog")`（`router.py:518`）**没有 except 转换**，逃出成 500 而非 422 | 既有缺陷，Q13 |
| 7 | — | `run_pipeline.py:56` 只接受 `status_code == 200`，A-2 的 console 面返 202 | 提醒：脚本不能直接切到 console 面 |
| 8 | — | `plan_snapshot_store.py:1-6` 称快照 write-once，`link_execution_plan()`（`:135`）事后改行 | 文档不实，须随 Q2 勘正 |

---

## 2. 事实源：发现链状态存在哪

### 2.1 裁决：`plan_snapshots` 增一列 `discovery`（JSONB，nullable）

零新**实体**（不建表、不建 Project 行、不建 Discovery 聚合）；代价是零新**列**做不到——
§1.3 已核实无处可放。新增一列，Alembic 迁移编号接现链尾 `20260811_0022_task_origin`
之后：

```text
repository_intelligence.plan_snapshots
  + discovery  JSONB NULL      -- 本轮发现链的全部中间态与审批记录
```

选一列 JSONB 而非四列的理由：四步是**一条链的四个阶段**，永远一起读、一起作废
（§4.4 上游重跑作废下游），拆成四列只会让「作废下游」变成四次写且可能写一半。

### 2.2 `discovery` 块的形状（契约形状，服务端唯一生产方）

```json
{
  "schema_version": 1,
  "requirement_analysis": {
    "sufficient": false, "confidence": 0.55,
    "missing_dimensions": ["行为描述"],
    "questions": ["希望改成什么行为？"],
    "extracted_keywords": ["通知", "邮件"],
    "answers": [ { "question": "…", "answer": "…" } ],
    "analyzed_requirement": "string",
    "forced_continue": { "ignored_question_count": 2,
                         "by_agent_id": "uuid", "at": "..." },
    "ran_at": "...", "by_agent_id": "uuid",
    "error": null
  },
  "candidates": {
    "items": [ { "repository_id": "uuid", "repository_name": "string",
                 "score": 0.82, "matched_terms": ["..."],
                 "rationale": "原样，不摘要", "is_entry_point": false } ],
    "llm_used": true,
    "limit": 10, "entry_point": null,
    "ran_at": "...", "by_agent_id": "uuid", "error": null
  },
  "classification": {
    "required": [ ConfirmationResultView ],
    "maybe":    [ ConfirmationResultView ],
    "excluded": [ ConfirmationResultView ],
    "supplemented_repos": ["repo-x"],
    "adjustments": [ { "repository": "repo-a", "from": "MAYBE|null", "to": "REQUIRED",
                       "by_agent_id": "uuid", "at": "..." } ],
                       // from=null：模型从未给该仓分档、审批人自行加入（实现
                       // discovery_chain.py 的 original.get(...) or None）——
                       // 前端渲染为「未分档」而非空串（2026-08-12 勘正）
    "ran_at": "...", "by_agent_id": "uuid", "error": null
  },
  "approval": {
    "state": "not_requested|approved|changes_requested",
    "evidence_version": "sha256:...|null",
                       // null=尚无任何审批决定。与 §3.1 分工表一致（本 JSON 块
                       // 原漏写 |null——同一事实两处只改一处的复发，2026-08-12 勘正）；
                       // 审批请求必须带的是顶层 classification_evidence_version，
                       // 不是这个字段
    "decided_by_agent_id": "uuid|null", "reason": "string",
    "decided_at": "...|null"
  }
}
```

- `ConfirmationResultView` 复用 `api/models.py:255` 既有形状（`repository` / `status` /
  `confidence` / `reason` / `plan_summary` / `plan` / `missing_dependencies`），**不另写
  第二套序列化**（v0.3 §1.4 同一条红线）。
- 每个步块的 `error` 为 `null` 或 `{ "message": "服务端错误原文摘要（≤500 字）",
  "at": "..." }`。**不设计假进度**：一步失败就是这一步 `error` 非空、后续步块保持 `null`。
- `adjustments` 是**审批人改档的留痕**（GUI「行内分档下拉」），与 LLM 原判并存：
  `classification.required/maybe/excluded` 保留 LLM 原始分档，**生效分档 = 原始分档
  叠加 `adjustments` 后的结果，派生规则唯一实现在读模型**（§3.1 的 `effective_tiers`）。
  覆盖写会抹掉「模型说什么、人改成什么」的对照，属删证据。
- **`candidates.items[].repository_id` 不可为 null**（实施定死；草案原文误写
  `uuid|null`）。两条产出路径（LLM / 关键词）都只能从 catalog 里已存在的 profile 取
  `repository_id`，评分时不在 catalog 的候选会被直接过滤掉，故本块内该字段恒为真实 id。
  既有 `DiscoveryCandidate` pydantic 模型的不可空 UUID **是对的，不放宽**。消费方无需为
  「catalog 未解析」渲染分支。（注意区分：§5.4 plan 端点的 `dag.nodes[].repository_id`
  **确实可为 null**——那是名字解析，不是本块。）
- **两个拼写是有意的，不是笔误**：`force_continue` 是**请求标志**（§4.6 的写端点字段，
  祈使：「忽略追问继续」），`forced_continue` 是**留痕记录**（本块内的对象，过去式：
  「已忽略 N 条追问」）。请求里不存在 `forced_continue`，读投影里不存在 `force_continue`。
- **`analysis.forced_continue` 为 `null`** 表示未强行继续；非空时形如
  `{ "ignored_question_count": 2, "by_agent_id": "uuid", "at": "..." }`。强行继续
  **不改写 `sufficient`**——模型的判断仍是事实，覆盖它就没人知道曾被绕过。

### 2.3 承载 `discovery` 的是「当前草稿快照」

定义（读模型与写端点共用，唯一实现）：

> **当前草稿快照** = 该 `project_id` 下 `execution_plan_id IS NULL` 的**最高
> `plan_version`** 快照；不存在时由 Step 0 写触发按 `next_version()` 新建一份。

发现链的四步与审批**全部写进这一张快照的 `discovery` 列，不涨版本**。

### 2.4 快照版本语义（与既有裁决的衔接）

| 时点 | 版本行为 | 依据 |
| --- | --- | --- |
| 建 issue | 落 `plan_version=1`、`execution_plan_id=null` 的草稿快照 | v0.3 §1.1，**既有裁决不动** |
| Step 0/1/2 与审批 | **不涨版**，写当前草稿快照的 `discovery` 列 | 本文 §2.3 |
| Step 3 集成 | **不涨版**，把 `engineering_spec` / `contracts` / `task_dag` / `execution_batches` 写回同一张草稿快照 | 本文 §4.3 |
| Step 4 物化 | 见下两案 | Q3 |
| 下一轮发现 | 当前草稿已被消费（`execution_plan_id` 非空）→ 在 `next_version()` 上新建草稿，`discovery` 从空开始 | 本文 §2.3 |
| 「回到分档重新生成」（GUI 裁决 3 的调整动线，发生在物化**之前**） | 覆盖同一张草稿的 `classification` 块 + 作废下游（§4.4），**不涨版** | 本文 §4.4 |

**为什么不能「每步涨一版」**（这不是洁癖，是当前实现的直接后果）：
`GET /issues/{id}/repositories/{repo}/plan`（v0.2 §5.4）取 `snapshots[0]`
（`api/read_models/service.py:1120`），而 `for_project` 的契约是「newest plan_version
first」（`api/read_models/sources.py:69-71`）。若发现中间态各占一版，最高版就是一张
`execution_batches` 为空的快照——**C-2 刚落地的 DAG 面板会渲染成空图**
（`frontend/src/components/PlanDagPanel.tsx` 按 `batch_index` 分列，无节点即无列）。

**materialize 的衔接（两案，见 Q3）**：

- 案 (a) 维持现状：`change_orchestration/application.py` 在物化时按
  `next_version(project_id)` **另写一份新快照**并带 `execution_plan_id`。于是一轮留下
  两份：草稿 v1（发现档案 + 集成产物）+ 执行 v2（计划）。零改动，但版本号翻倍，
  v0.2 §3 的 `rounds[].plan_version` 从 2 起跳。
- 案 (b) **建议**：物化复用当前草稿快照——`link_execution_plan()`（既有方法，
  `plan_snapshot_store.py:135`）回填 `execution_plan_id`，不新增版本。版本号语义干净
  （v1 = 第一轮），发现档案与该轮计划天然同行。代价：要改 `application.py` 落快照的
  分支，并同步核 `replan` 的 `plan_version` 语义（`router.py:664`、
  `models.py:331-350`）。

### 2.5 草稿快照的可变性（必须明说）

按 §2.3，草稿快照在被物化消费前是**可变的**。这与 `plan_snapshot_store.py:1-6` 的
「write-once」描述冲突——但那句描述**当前就已不实**（`link_execution_plan` 事后改行）。
本契约的口径：

> **已消费的快照（`execution_plan_id` 非空）不可变；当前草稿快照（`execution_plan_id`
> 为空）在被消费前可变，且同一 `project_id` 至多只有一张。**

已按 Q2 同批勘正该 docstring（`plan_snapshot_store.py` 模块头），未留「同一事实两处、只改一处」。

---

## 3. 读投影

### 3.1 `GET /api/v1/issues/{issue_id}/discovery`（新读端点）

落位：**读模型** `api/read_models/router.py` 的 `issues_router`（`:8`，前缀 `/issues`）。
路径占用已查：该 router 现有 `""`、`/{issue_id}`、`/{issue_id}/rooms`、
`/{issue_id}/repositories/{repository_id}/plan`（`router.py:100/120/128/138`），
`/{issue_id}/discovery` 空闲。鉴权同其余读端点（v0.2 §1，动作 token）。

```json
{ "issue_id": "uuid",
  "plan_version": 1,                       // 承载 discovery 的草稿快照版本
  "step": 3,                               // 1..4，GUI 步进器当前步（§3.2 唯一实现）
  "step_state": "idle|running|failed|done",
  "running_task_id": "uuid|null",          // 有在跑的长任务时非空（§4.2）
  "requirement_text": "string",            // 快照 requirement_text（issue 标题的事实源）
  "analyzed_requirement": "string|null",   // 实际送进 LLM 的全文（含已拼入的答复）
  "analysis": { ... }|null,                // §2.2 requirement_analysis 直投影
  "candidates": { ... }|null,              // §2.2 candidates 直投影
  "classification": { ... }|null,          // §2.2 classification 直投影
  "classification_evidence_version": "sha256:...|null",  // 当前分档指纹，审批必带
  "effective_tiers": [                     // 派生：原始分档叠加 adjustments
    { "repository": "repo-a", "tier": "required|maybe|excluded",
      "adjusted": true, "original_tier": "maybe|null" } ],
  "approval": { ... },                     // §2.2 approval 直投影
  "integration": { "task_dag_count": 6, "batch_count": 3,
                   "contract_count": 2 }|null   // 集成产物已落草稿快照时的计数
}
```

**`classification_evidence_version`（实施新增，草案缺）**：审批端点要求请求带
`evidence_version`（§5.3），审批人必须有地方拿到「当前这份分档的指纹」。
`approval.evidence_version` 回答的是**另一个问题**——「上一次决定绑在哪份证据上」，
未审批时为 `null`，拿它去提交审批必然 409。两个字段不可互相替代：

| 字段 | 含义 | 未审批时 |
| --- | --- | --- |
| `classification_evidence_version` | 服务端当前分档的指纹，**提交审批时回填这个** | 分档存在即非空 |
| `approval.evidence_version` | 已记录的那次决定绑定的指纹（审计用） | `null` |

**`effective_tiers[].original_tier` 取值定死**：**`adjusted` 为 `false` 时恒为 `null`**。
该字段的存在意义是描述「改动」，未改动却回显当前档位，会诱使面板渲染出「原为 required，
现为 required」。两种 `null` 靠 `adjusted` 区分，不会混淆：

| `adjusted` | `original_tier` | 含义 |
| --- | --- | --- |
| `false` | `null` | 模型分档，审批人未动 |
| `true` | `"maybe"` 等 | 模型分了档，审批人改成了 `tier` |
| `true` | `null` | 模型从未给该仓库分档，审批人自行加入 |

改档后又改回原档 → `adjusted: false`、`original_tier: null`（没有净改动就不宣称有）。

`integration` 的三个计数取自草稿快照的 `task_dag` / `execution_batches` / `contracts`
实际长度。注意 `batch_count` **由依赖图决定，不等于 LLM 提议的批次数**：有依赖图时
集成只用图的拓扑分批，LLM 仅负责语义内容（`plan_integration.py:_integrate_with_graph`）。

诚实条款（消费方约束，随实现验收）：

- 未跑过的步为 `null`，**不填空对象冒充「跑过但没结果」**；
- `analysis.error` / `candidates.error` / `classification.error` 非空时前端显**服务端
  detail 原文**（设计稿 ② 末条），不显进度条；
- `candidates.llm_used === false` 时必须显「关键词回退（未调用 LLM）」，**禁止呈现为
  模型评分**；
- `rationale` 原样透传，读模型不摘要不截断（诚实数据原则的审批场景延伸）；
- `effective_tiers` 是**唯一的生效分档来源**，前端禁止自己把 `adjustments` 叠到
  `classification` 上（状态映射唯一实现在读模型，红线延续）。

**issue 存在但从未发起发现** → HTTP 200 且 `analysis/candidates/classification` 全 `null`、
`step: 1`、`step_state: "idle"`（**不是 404**；沿用 v0.2 §7.2「未建团的 issue 返 200 空集」
的同一口径）。issue 本身不存在（无任何快照）→ 404。

#### 3.1.1 `materialization`：把 §8.3 的物化收据投影出来（**已裁决 · 2026-08-12**，B-12）

> 主脑裁决通过，随修复合并生效（分支 `feat/retry-materialize-entry`）。
> 本节只动 §3.1 的响应形状，不动 §8 任何一节的语义。

核实事实（B-12）：物化自 `7659c89` 起是**可重入**的——一轮跑到一半（计划起了、任务或
房间没齐）再调一次 materialize 就能补完，同键异键皆可（§8.3）。收据也确实按 §8.3 落进了
草稿的 `discovery.materialization`。**但 `GET …/discovery` 从不投影它**：实测该端点顶层只有
`issue_id / plan_version / step / step_state / running_task_id / requirement_text /
analyzed_requirement / analysis / candidates / classification /
classification_evidence_version / effective_tiers / approval / integration`，没有
`materialization`。

后果是「跑砸了一半的一轮」与「压根没试过」在读模型里**长得完全一样**——`step` / `step_state`
/ `integration` 三者逐字相同。GUI 于是只能拿轮次数去猜，而失败的那一轮往往**已经有轮次行**，
就被判成「已物化」，物化入口从此消失。服务端早已准备好受理的重试，界面上没有任何入口。
按本项目验收口径（**GUI 走不通即缺陷**），这是缺陷而非待办。

响应新增一栏（`approval` 之后、与 `integration` 并列）：

```json
{ "materialization": { "status": "materialized|failed",
                       "at": "iso8601",
                       "by_agent_id": "uuid",
                       "error": "string|null",
                       "plan_id": "uuid|null" } }
```

**缺席口径**：从未物化过 → `"materialization": null`（**键在、值为 null**，与 `integration`
同一写法）。不填空对象冒充「试过但没结果」，沿 §3.1 诚实条款。

**只投影五栏，其余一律不投影**，理由分两类：

| 不投影的字段 | 理由 |
| --- | --- |
| `idempotency_key` / `prefix` / `plan_fingerprint` | §8.3 的**重放账本**，服务端独占。发给客户端等于邀请它伪造一个与真键撞车的键，而 §8.3 的全部保证都建立在服务端独占这个命名空间上 |
| `task_ids` / `team_count` / `repositories` / `skipped_repos` | 轮次、团队、房间**各自已经投影过**（§5.1、§3.3）。第二份副本只是多一次自相矛盾的机会 |

**`error` 原文照登，不摘要不归类**（§3.1 诚实条款在物化场景的延伸）。

**读模型不给判断，只给 `status`。** 不投影 `stuck` / `retryable` 之类的派生栏——那是把
「要不要重试」的判断从人手里拿走，塞进一个消费方无法复核的布尔里。前端只按 `status` 一条
分支，`error` 原样摆给人看。红线延续：读模型载事实、载服务端原话，不造派生的区分。

**消费方约束（随实现验收）**：

- `status === "failed"` → 必须重新给出物化入口（措辞如「重试物化」），**并同屏显 `error` 原文
  与 `at`**，如实归因为「上次物化失败」。点它走的必须是**首次物化那同一个确认弹窗**（弹窗按
  §8.3 自行取新幂等键，服务端重放机制负责其余），不得另起一条平行路径；
- `status === "materialized"` → 维持既有「已物化」留痕，不变；
- **`materialization` 为 null 但已有轮次**（收据机制之前的旧轮次，如种子数据）→ **维持既有
  留痕不变，不得猜测**。「没有收据」不等于「多半没事」。

落点：`api/read_models/service.py` 的 `discovery()`（该端点返回裸 `dict`，无 pydantic 响应
模型，故本次新增不涉及模型类）。

### 3.2 步进器「走到哪」的判定（唯一实现在读模型）

GUI 步进器（1..4）与管线步（Step 0..4）的映射，本契约以**管线编号**描述行为、
以 **GUI 编号**作 `step` 字段取值：

| GUI 步 | 管线步 | 产生它的写触发 |
| --- | --- | --- |
| 1 需求分析 | Step 0 | `POST …/discovery/analysis` |
| 2 候选评分 | Step 1 | `POST …/discovery/candidates` |
| 3 分档审批 | Step 2（生成三档）**+** 人工审批 | `POST …/discovery/classification` 与 `POST …/discovery/approval` |
| 4 生成计划 | Step 3 | `POST …/discovery/plan` |
| （步进器外）物化开工 | Step 4 | `POST …/discovery/materialize` —— 见 **§8**（批次 C-3 已实施） |

`step` 派生，**按序判定、首个命中即返回**：

1. `discovery` 为空 或 `analysis` 为 `null` → **1**；
2. `analysis` 非空，且 **未通过**（`sufficient == false` 且 `forced_continue` 为 `null`）
   → **1**（停在追问，等回答或强行继续）；
3. `candidates` 为 `null` → **2**；
4. `classification` 为 `null` → **3**（分档尚未生成，仍属「分档审批」格）；
5. `approval.state != "approved"` → **3**（等审批 / 被要求改动）；
6. 草稿快照 `task_dag` 为空 → **4**（已放行，等生成计划）；
7. 否则 → **4** 且 `step_state == "done"`。

`step_state`：该步有在跑任务 → `running`；该步块 `error` 非空 → `failed`；
规则 7 命中 → `done`；其余 → `idle`。

**禁止在 issue 层新增第 5 步**：面板只允许呈现这四格 + 物化按钮（v0.2 §2.2「禁止新增
第 9 相」的同一条约束）。

### 3.3 issue 详情聚合加什么

`GET /issues/{issue_id}`（v0.2 §3）**只加两个标量**，供列表/详情页出徽标而不必再取一次：

```json
{ "discovery_step": 3,                       // 同 §3.2 的 step，同一实现
  "discovery_state": "idle|running|failed|done" }
```

**消费时机（实施补写）**：两个标量**恒存在**（issue 从未发起发现时为 `1` / `"idle"`），
供 issue 详情页在**不打开发现面板**时出徽标——例如「发现 3/4 · 待审批」。打开面板后
一律以 §3.1 的专用端点为准，不要拿这两个标量驱动面板内部渲染：它们与 §3.1 的
`step`/`step_state` 同源同实现，但**取不到 `running_task_id`**，无法据以轮询。

其余（追问列表、候选评分、rationale、三档、审批记录）**只在 §3.1 的专用端点里**——
它们是打开面板才看的整块数据，塞进详情聚合会让每次列表刷新都拖着一堆 rationale 长文。

`GET /issues`（v0.2 §2）列表**不加任何发现字段**：列表的每行没有展示发现进度的位置
（设计稿 IA 未给），加了就是无消费方的字段。

---

## 4. 写触发

### 4.1 落位与鉴权

- **写端点挂 `repository_intelligence` 的 `api/router.py`**（`plan_snapshots` 的唯一
  生产方），路径 `POST /api/v1/issues/{issue_id}/discovery/*`，与 v0.3 §1.5 把
  `POST /issues` 挂同一 router 的裁决同风格。路径占用已查：该 router 现有
  `POST /issues`（`:262`），无 `/issues/{id}/...` 任何路由；读模型的 `issues_router`
  只注册 GET，**方法与路径均不冲突**。
  ⚠ 代码库里现存两种落位风格：v0.3 的 `POST /issues`（原生路径）与 A-1 的
  `POST /console/repositories/scan-org`（console 面，`api/console.py:212`）。本文按
  已按 Q10 裁为 **RI router 原生路径**：console 面的存在理由是掐断凭据透传，本批请求体里没有凭据字段，该理由不成立。
- **鉴权**：`ACTION_TOKEN`（`router.py:130`），与本 router 全部写路由一致。
- **主体**：请求体 `created_by_agent_id` / `decided_by_agent_id`，必须是**活跃的
  ORGANIZATION_LEADER**，且其所属组织与该 issue 的工作区一致，否则 403——校验规则与
  取数路径**复用 v0.3 §1.2 的既有实现**（`application/issue_intake.py:77-99`），
  前端沿用 CONS-44 的花名册派生（`decisions.ts` 单点实现），不新增取数路径。
- **幂等键**：每个写请求必带 `idempotency_key`（最短 8 字符，同 v0.3 §6 S-5）；
  **随表单生成**（设计稿 ②），每次逻辑触发新键、重试沿用同键。

### 4.2 同步还是 202：与 A-2 的对照论证

A-2（`api/console.py`）刚落地的模式：写请求返 **202 + task_id**，
`GET /console/repositories/scan-tasks/{task_id}` 轮询（`:296`），任务记录是**进程内 dict**
（`:99`，上限 100 条 `:58`，重启即丢，404 文案明说可以重跑）。

发现链与扫描的**相同点**：耗时不可控、用户可以离开页面回头再看。
**不同点有两个，决定契约不能照抄**：

1. **结果的归宿不同**。扫描的结果是「已注册进 catalog」，任务记录只是计数板，丢了不
   影响事实。发现链的结果**必须落快照**（§2），任务记录**只作进度句柄**：任务丢了，
   前端重新 `GET …/discovery` 就知道这一步到底落没落。→ 复用 A-2 的进程内注册表是
   **可接受的**（Q7），因为这里它比在扫描里更弱耦合。
2. **阻塞形态不同**。扫描是出站 HTTP（`await`，让出事件循环）；发现链的 LLM 调用是
   **同步阻塞**（§1.4 第 2 条）。→ **只加 202 是假异步**：`asyncio.create_task` 里跑一个
   同步的 `chat()` 照样把事件循环占死。契约硬性要求：**长任务体必须在线程池里跑**
   （`asyncio.to_thread` 或等价），否则 202 只是把「浏览器等 3 分钟」换成「服务器死
   3 分钟且浏览器还在轮一个不动的端点」。→ Q6。

**建议案**：四个写触发**全部 202 + 轮询**，形状复用 A-2 的 `ScanTaskView` 精神但字段
按发现链改（见 §4.5）。理由：Step 2 是 N 次 LLM 调用（`confirmation.py:333` 且已备好
`on_progress` 回调，与扫描的 `_progress_reporter` 同构），必须异步；其余三步虽只有 1 次
调用，但同一面板里三个端点同步、一个异步，前端要写两套等待逻辑与两套错误路径——
统一的收益大于「单次调用同步更省事」。

### 4.3 四个写触发端点

四者共同的响应形状（**实施定死**，草案只写了 202 那一半）：

```json
{ "task_id": "uuid|null", "step": 1, "status": "accepted|replayed" }
```

| 情形 | HTTP | `task_id` | `status` |
| --- | --- | --- | --- |
| 已受理、任务已起 | `202` | 任务 id | `accepted` |
| 同幂等键重放（§4.4） | `200` | **`null`** | `replayed` |

重放不给 `task_id`：原任务记录是进程内的、可能早已被清掉，编一个回去等于承诺一次
答不上来的轮询。两种情形下前端的下一步是同一个动作——重取 `GET …/discovery`；
只有 `accepted` 需要先轮询 `…/discovery/tasks/{task_id}`。

错误：`403` 主体不合格 / 跨工作区；`404` issue 不存在或主体不存在；`409` 前置未满足
或该 issue 已有在跑任务；`422` 参数非法；`503` LLM 未配置（见 Q12 与 §6.1 第 3 条）。

**Step 0 需求分析** `POST /api/v1/issues/{issue_id}/discovery/analysis`

```json
{ "created_by_agent_id": "uuid",
  "idempotency_key": "string(>=8)",
  "answers": [ { "question": "string", "answer": "string" } ] }
```

- **不收 `requirement`**：需求文本的事实源是快照的 `requirement_text`
  （与 v0.3 §1.2「不收 title」同一条理由——存两份即两个事实源）。
- `answers` 是上一次追问的回答；**答复与需求的拼接规则唯一实现在服务端**，前端拼即
  第二事实源。拼接结果落 `analysis.analyzed_requirement`。
- 是否改写快照的 `requirement_text`：**建议不改**（它是 issue 标题的事实源，改了标题会
  跟着变）→ Q16。

**Step 1 候选评分** `POST /api/v1/issues/{issue_id}/discovery/candidates`

```json
{ "created_by_agent_id": "uuid", "idempotency_key": "string",
  "limit": 10, "entry_point": "string|null" }
```

- 前置：`analysis` 非空，且（`sufficient == true` **或** `forced_continue` 非空）；
  否则 **409**，detail 说明「需求分析未通过且未强行继续」。
- 需求文本取 `analyzed_requirement`，不收。
- **`limit` / `entry_point` 均可选**（实施定死）。`limit` 缺省 **10**、范围 `1..50`；
  `entry_point` 缺省 `null`。前端不送、交服务端缺省即可。
  与既有 `POST /discovery` 的 `DiscoveryRequest.limit` 缺省 5 **有意不同**：那是脚本
  入口，改它会动既有调用方的行为；面板一屏展示 10 条是设计稿的形状。两处缺省各自
  独立，不统一。

**Step 2 三档分类** `POST /api/v1/issues/{issue_id}/discovery/classification`

```json
{ "created_by_agent_id": "uuid", "idempotency_key": "string" }
```

- **不收 `candidate_repos` / `discovery_evidence`**——这是与现有
  `POST /confirmation`（`models.py:238-242`）最大的差别：脚本时代由客户端回传，GUI 面
  由服务端从快照的 `candidates` 块取。让浏览器回传候选与证据，等于让前端成为事实源，
  且用户可以（无意地）提交一份与展示不一致的候选集。
- 前置：`candidates` 非空且 `items` 非空，否则 409。
- **可加进度**：`confirm()` 的 `on_progress` 回调（`confirmation.py:333` 签名）可喂
  「第 n / N 个候选」，与 A-2 的 `scanned/total` 同形。

**Step 3 生成计划** `POST /api/v1/issues/{issue_id}/discovery/plan`

```json
{ "created_by_agent_id": "uuid", "idempotency_key": "string" }
```

- 前置：`approval.state == "approved"`，否则 **409**（**审批 v1 必经**的契约落点）。
- 送进集成的分档 = §3.1 的 `effective_tiers`（含审批人的调整），不是 LLM 原判。
- 产物写回当前草稿快照的 `engineering_spec` / `contracts` / `task_dag` /
  `execution_batches`；`integration_method` 置 `"llm_only"`（与既有物化路径同值，
  `change_orchestration/application.py` 落快照处）。

### 4.4 幂等、并发与「上游重跑作废下游」

- **同键重放 → 200 + 既有结果，不重跑 LLM**。LLM 调用花钱且不确定：重跑同一逻辑请求
  会产出第二份互相矛盾的事实。（这与 v0.3 §1.3「相同 key 重放返回既有 issue」同精神。）
- **不同键 → 重跑该步，并作废其下游步块**（建议案，Q8）：重跑 Step 0 清空
  `candidates`/`classification`/`approval`；重跑 Step 1 清空
  `classification`/`approval`；重跑 Step 2 清空 `approval`。
  不作废的后果是面板同屏展示「新需求的分析」与「旧需求的候选」，两者都标着「已完成」。
  作废写 platform 审计事件（写明作废了哪几步）。
- **同一 issue 同时至多一个在跑任务** → 第二个请求 409，detail 带在跑的 `task_id`。
  两人同时点「重新分析」不该产生两条竞争的写。
- **审批的乐观并发**：见 §5.3 的 `evidence_version`。

### 4.5 轮询端点

`GET /api/v1/issues/{issue_id}/discovery/tasks/{task_id}`（鉴权同上）

```json
{ "task_id": "uuid", "issue_id": "uuid",
  "step": 2,
  "status": "running|succeeded|failed",
  "progress": { "done": 3, "total": 10, "label": "repo-c" },   // Step 2 有；其余 total=1
  "error": "string|null",       // 失败原因原文摘要，与快照里落的同一份
  "started_at": "...", "finished_at": "...|null" }
```

- 任务记录**进程内、重启即丢**（沿用 A-2 的诚实注记，`api/console.py:61-78`）；
  404 的 detail 必须说明「任务状态只活在本进程，重启会丢；请改读
  `GET …/discovery` 判断该步是否已落」——这句在扫描那边是「重跑是安全的」，
  在这边是**更强的保证**：结果在快照里，不必重跑就能知道。
- `status == "succeeded"` 之后**前端仍必须重取 `GET …/discovery`**：任务视图不投影结果
  （避免同一份数据两个序列化，v0.3 §1.4 同一条）。

### 4.6 clarify 强行继续的留痕

设计稿裁决 2：「按钮点击即在**决策记录**留痕『忽略 N 条追问继续』」。
**发现期不存在决策记录实体**（§1.5 #1、§5.1）。本契约落点：

1. 写快照 `discovery.requirement_analysis.forced_continue`
   （`ignored_question_count` / `by_agent_id` / `at`）——**可读、可审、随 issue 永存**；
2. 同时写一条 platform 审计事件（事件类型如 `DiscoveryClarifyOverridden`，
   payload 带 `ignoredQuestionCount`），风格同 v0.3 §1.5。

强行继续是 Step 0 端点上的一个显式字段而不是另一个端点：

```json
{ "created_by_agent_id": "uuid", "idempotency_key": "string",
  "force_continue": true }
```

置 `true` 时**不重跑 LLM**，只在既有 `analysis` 上记 `forced_continue`
（`ignored_question_count = len(analysis.questions)`）；`analysis` 为 `null` 时 409
（没有追问可忽略）。Q15 已确认该落点即「决策记录」的等价物——发现期不存在决策记录实体，快照那份给人看、审计那份给审计看。

---

## 5. 审批面（B-2）

### 5.1 三套既有审批机制的实测比较

| 机制 | 写端点 | 主体 | 绑定对象 | 发现期能否用 |
| --- | --- | --- | --- | --- |
| 治理决策（控制台**决策夹**） | `POST /deliveries/{id}/governance-decisions`（`modules/delivery/api/deliveries.py:27`） | `decided_by_agent_id`（agent）+ 动作 token | `change_set_id` + `repository_id` + **`head_sha`**（`GovernanceDecisionCreate`，`delivery/api/models.py`） | **不能**：发现期没有 ChangeSet、没有候选分支、没有 head_sha |
| 检查点决策 | `POST /projects/{project_id}/checkpoint-decisions`（`api/human_control.py:261`） | **人类账户**（`_account(request)`，`:265`），session ticket | `review_request_id`（PENDING 的 `HumanReviewRequest`） | **不能（三重阻碍）**：① `record()` 要求 topology 存在（`project/checkpoint_control.py:74-76`），草稿 issue 无拓扑；② 主体是人不是 agent，与设计稿冲突；③ PENDING review 只能由 `evaluate()` 生成，而 `evaluate()` 同样要 topology（`:137-139`），且 AUTO 模式下 `requires_human_checkpoint` 恒 False（`project/human_control.py:64-65`） |
| 仓库对接文档决策 | `POST /handoff-docs/{doc_id}/decision`（`repository_intelligence/api/router.py:771`） | `decided_by_agent_id`（agent）+ 动作 token | `doc_id`，文档由 **materialize 时**生成 | **不能**：对象在发现期之后才存在 |

结论：计划 B-2「复用现有审批交互」在**交互形态**上可复用（弹窗 + 幂等键 + 花名册派生
主体 + 服务器 detail 原样展示），在**机制**上三套都不可复用——它们各自绑在发现期尚不
存在的对象上（ChangeSet / topology / handoff doc）。硬接任一套都要先伪造那个对象。

### 5.2 裁决：审批记录落 `discovery.approval` + 专用写端点

`POST /api/v1/issues/{issue_id}/discovery/approval`（落位与鉴权同 §4.1）

```json
{ "decided_by_agent_id": "uuid",            // 活跃 ORGANIZATION_LEADER，校验复用 v0.3 §1.2
  "idempotency_key": "string(>=8)",
  "decision": "approved|changes_requested",
  "reason": "string",
  "adjustments": [ { "repository": "repo-a",
                     "tier": "required|maybe|excluded" } ],
  "evidence_version": "sha256:..." }
```

- **同步端点**（无 LLM 调用），`200`。响应体形状（**实施定死**，草案未写）与四个写触发
  **同形**，是回执而非审批块：

  ```json
  { "task_id": null, "step": 3, "status": "accepted|replayed" }
  ```

  `task_id` 恒 `null`（没有异步任务）；首次记录为 `accepted`，同键重放为 `replayed`。
  **不回投影审批块**——那是 §3.1 已经拥有的数据，回一份就是同一事实两处序列化
  （v0.3 §1.4 同一条红线）。前端写完重取 `GET …/discovery`，与步骤触发后的动作一致。
  重放必须可分辨：否则客户端重试会把 `adjustments` 再追加一遍，分档上凭空多出一条改档。
- `adjustments` 与 `decision` **一次提交**：设计稿是「行内下拉调整后放行」，拆成两个写
  会造出「改了但没批」的中间态，且要多一套并发处理。
- `decision == "changes_requested"` → `approval.state` 置同名，`step` 停在 3；
  它**不清空** `classification`（人只是要求改，模型判断仍是事实）。
- 写 platform 审计事件（同 v0.3 §1.5）；`adjustments` 逐条进 §2.2 的
  `classification.adjustments` 留痕。

### 5.3 `evidence_version`：审批绑在它批的那份分档上

`evidence_version = sha256` of 规范化后的三档结果（排序后的
`{repository, status}` 列表 + `supplemented_repos`），**与 materialize 现有 gate 的取指纹
方式同风格**（`change_orchestration/application.py:188-204` 对
`{repositories, contracts}` 取 `sha256`）。

- 请求带的 `evidence_version` 与服务端当前分档结果不符 → **409**（审批人批的是一份已被
  重跑覆盖的分档）。这是 v0.1 §4.4 head-bound 语义在发现期的等价物：**批准必须绑在它
  实际看到的那份证据上**。
- Step 2 重跑后 `approval` 被作废（§4.4），`evidence_version` 随之更新。

### 5.4 与既有 REPOSITORY_SCOPE gate 的关系（诚实交代）

`materialize` 内部已有一道 `ProjectCheckpoint.REPOSITORY_SCOPE`（「仓库修改范围」，
`project/checkpoint_control.py:52`）人工检查点，不通过即 `WorkflowBlocked` → 409
（`change_orchestration/application.py:200-206`，`router.py:649-650`）。

本文的发现期审批**不替代它**，两者对象不同：本审批批的是**三档分类结果**，gate 批的是
**最终仓库集 + contracts 的指纹**（物化时才成形）。诚实后果：

> **在配置了 REPOSITORY_SCOPE 检查点的受控项目里，用户会被要求批两次**——一次在分档
> 审批，一次在物化开工。

这是既有 gate 的既定语义，本契约不消解它，但 C-3 的「物化并开工」按钮**必须能显示
409 的原因**（`gate.reason` 取值如 `human_checkpoint_pending` /
`project_topology_missing`），不能显示成通用失败。→ Q5。

另一诚实事实：**草稿 issue 没有 topology**，此时 gate 返
`CheckpointGateDecision(False, "project_topology_missing")`（`checkpoint_control.py:137-139`）
→ 物化必然 409。即「建团/建拓扑」是物化的前置，属 C-3 范围，本文只记录该依赖。

---

## 6. 裁决记录（Q1~Q18 全部按建议案 · 2026-08-12 生效）

下表「建议案」列即**最终裁决**，已全部落地。与建议案有出入的实现细节见 §6.1。

| # | 问题 | 裁决（原建议案） | 理由 / 代价 |
| --- | --- | --- | --- |
| Q1 | 发现链中间态落哪：`plan_snapshots` 新增 `discovery` JSONB 列 / 新表 / 复用既有列 | **新增一列 `discovery`（JSONB, NULL）** | 现有列无一能诚实容纳（§1.3）；新表违背零新实体；复用 `contracts`/`task_dag` 是一列两义。**零新实体 ≠ 零新列**，这一条需要主脑明确点头 |
| Q2 | 草稿快照可变性 | **承认「已消费不可变、当前草稿可变」，并同批勘正 `plan_snapshot_store.py:1-6` 的 write-once 描述** | 每步涨版会让 v0.2 §5.4 的 plan 端点取到空 `execution_batches` 的最高版快照，打空 C-2 的 DAG 面板（§2.4）；write-once 描述当前就已不实（`link_execution_plan`） |
| Q3 | materialize 与草稿快照的衔接 | **案 (b)：物化复用草稿快照，`link_execution_plan` 回填，不新增版本** | 版本号语义干净（v1=第一轮）；代价是改 `change_orchestration` 落快照分支并同步核 `replan` 的 `plan_version`。案 (a) 零改动但一轮两份快照、`rounds[].plan_version` 从 2 起跳 |
| Q4 | 分档审批用什么机制 | **专用 `discovery.approval` 记录 + 专用写端点（§5.2）**，不复用三套既有机制 | 三套各绑 ChangeSet / topology / handoff doc，发现期都不存在（§5.1）。硬接要先伪造对象。代价：控制台从此有两个「审批面」，须在 UI 上说清各自对象 |
| Q5 | 与既有 REPOSITORY_SCOPE gate 的关系 | **并存，并在 C-3 如实显示 409 原因**；不做「本审批通过即豁免 gate」 | 豁免等于把受控模式的最后一道人工闸删掉；而「本审批通过即代写一条 checkpoint 决策」做不到——那需要人类主体与 topology（§5.1）。代价：受控项目要批两次，须在文案里讲明白 |
| Q6 | 四步写触发同步还是 202 | **四步全 202 + 轮询**，且**长任务体必须走线程池** | Step 2 是 N 次 LLM 调用必须异步；四步统一可省掉前端两套等待/错误逻辑。**只加 202 不改线程模型是假异步**（§1.4 第 2 条），这条是硬性要求不是建议 |
| Q7 | 任务注册表复用 A-2 的进程内 dict 还是落库 | **复用进程内 dict** | 结果落快照，任务记录只作进度句柄，丢了不丢事实（§4.2）——比在扫描那边更弱耦合。落库任务表是新实体 |
| Q8 | 上游重跑是否作废下游 | **自动作废并写审计** | 不作废会同屏展示互相矛盾且都标「已完成」的两代结果。备选「保留并标 stale」需要每个步块加状态位，且前端要处理「基于旧输入的结果」的展示，复杂度不划算 |
| Q9 | 幂等键粒度 | **每步一个键，随表单生成**（沿设计稿 ② 与 A1/A7 模式） | 全链一个键会让「只重跑 Step 2」无法与「重跑整链」区分 |
| Q10 | 写端点落位：`/api/v1/issues/{id}/discovery/*`（RI router，v0.3 风格）还是 `/api/v1/console/issues/{id}/discovery/*`（A-1 console 面风格） | **RI router**（与 `POST /issues` 同风格） | 代码库现存两种风格并存（§4.1），需要一条统一裁决而不是再加一个特例。console 面的存在理由是「掐断 token 透传」，本批请求体里没有凭据字段，那条理由不成立 |
| Q11 | `candidates` 是否自述 `llm_used` | **加** | 现状关键词回退与 LLM 结果**形状完全相同**（§1.4 第 1 条），消费方无法分辨，等于把词频分数当模型评分展示。实现侧成本近零（`discovery.py:50` 已算出该值，`:54` 只喂给了 span attribute，没进响应） |
| Q12 | `/confirmation` 与 `/integration` 在 LLM 未配置时返 500（现状）是否本批统一为 503 | **本批统一为 503**，与 `/requirement-analysis` 一致 | 属既有缺陷（§1.4 第 3 条），但本文的四个写触发要声明 503 语义，不修则契约与现状不符。改动极小 |
| Q13 | `confirm_repositories` 的裸 `ValueError`（`router.py:518`）逃出成 500 | **本批改 422** | 同上，属顺带修；不修则「候选全不在 catalog」这个正常的用户错误显示成服务器崩溃 |
| Q14 | 契约以哪套步骤编号为准 | **行为描述用管线编号（Step 0..4），`step` 字段取值用 GUI 编号（1..4），映射表进契约正文（§3.2）** | 两套编号都已写进已发布文档，强行统一会让另一份文档变成错的；给映射表比二选一更不容易读错 |
| Q15 | 强行继续的留痕落点 | **快照 `forced_continue` + platform 审计事件双写**（§4.6） | 设计稿说「决策记录里写明」，但发现期没有决策记录实体（§5.1）。快照里那份是给人看的（面板上可见），审计那份是给审的 |
| Q16 | clarify 答复是否改写快照 `requirement_text` | **不改**；答复与拼接后的全文存 `discovery` 块（`answers` / `analyzed_requirement`） | `requirement_text` 是 issue 标题的事实源（v0.2 §0 截断派生），改它会让用户看到标题在自己变。代价：下游步骤用的是 `analyzed_requirement` 而非 `requirement_text`，契约必须把这条说死，否则实现方会随手取错那个 |
| Q17 | Step 2 是否投影每候选进度 | **投影**（`progress.done/total/label`，复用 `confirm()` 已有的 `on_progress`） | N 次 LLM 调用无进度＝几分钟静默，正是 A-2 要解决的那个问题。成本近零（回调已在签名里） |
| Q18 | 审批的 `evidence_version` 漂移是否必须 409 | **必须** | 审批必须绑在它实际看到的证据上（v0.1 §4.4 head-bound 的同一条道理）。备选「以最新分档为准直接放行」＝批准了一份没人看过的分档 |

### 6.1 实施与建议案的出入（逐条，**以本节为准**）

裁决是「Q1~Q18 全按建议案」，下列各条是**实施中发现建议案说不到位或说错**的地方，
均已落地并有测试。

| # | 出入 | 实施结论与理由 |
| --- | --- | --- |
| 1 | **Q6「四步全 202」中，Step 1 在 LLM 未配置时不再 503** | 候选评分**有关键词回退**，没有模型也能真跑，产出自述 `llm_used: false`。对它 503 等于否认一个真实且诚实的能力。`analysis` / `classification` / `plan` 三步没有回退，维持 503。 |
| 2 | **503 在触发时判，不在任务里判** | 建议案只说「503 LLM 未配置」。若在任务体里判，请求会先拿到 202、再轮询出一个从创建起就注定失败的任务。202 的含义是「已受理并已开始」，为跑不起来的活儿发 202 是要靠轮询才能拆穿的谎。 |
| 3 | **`404` 也覆盖「主体不存在」** | 草案 §4.3 只写「404 issue 不存在」。`created_by_agent_id` 查无此人同样是 404（与 v0.3 §1 的 `POST /issues` 同口径），非活跃/非 leader/跨工作区才是 403。 |
| 4 | **`classification_evidence_version` 为新增顶层字段** | 见 §3.1。不加则审批人无处取当前指纹，§5.3 的 409 语义无法被正确使用。 |
| 5 | **`effective_tiers[].original_tier` 在未改档时为 `null`** | 见 §3.1 表。草案示例只给了改档那一种，未定义未改档的取值。 |
| 6 | **写触发/审批响应体形状定死为三字段回执** | 见 §4.3、§5.2。草案只写了 202 那一半，重放的 200 没给形状。 |
| 7 | **`summary_in_force` 会改写 `ConfirmationResult.status`** | §4.3 说「送进集成的分档 = `effective_tiers`」。只把结果换桶不够：集成内部按 `status != "EXCLUDED"` 过滤 `repo_names`，进而决定 **contracts 与分批**。只换桶会让被审批人从 excluded 提上来的仓库「有任务、没契约、不在任何批次」——界面上提上来了，计划里只兑现了一半。已修并有回归测试。 |
| 8 | **Q3 案 (b) 实施时发现 materialize 从来没存过快照** | `ContractSpec` / `TaskNode` 是 frozen+slots dataclass 且无 `to_dict`，原 `dict(item)` 回退对每个真实计划都抛 `TypeError`，被外层 `except Exception` 降级成一行日志：接口返 200、任务照建、DAG 面板要读的那行从未写入。已修（两个 dataclass 各自实现 `to_dict`），并补「快照确实写了且能 JSON 往返」的回归测试。 |
| 9 | **`replan` 的 `plan_version` 无需改动** | Q3 案 (b) 要求「同步核 replan 的 plan_version 语义」。核实结论：`replan` **不写任何快照**，只按请求体的 `plan_version + 1` 派生 handoff 文档与任务版本。案 (b) 之后第一轮是 v1（原为 v2），replan 得到 v2（原为 v3），单调性与语义都更干净，代码零改动。 |

---

## 7. 实现顺序与落点（1~5 **已完成**，2026-08-12 · 分支 `feat/b-backend`）

| # | 内容 | 状态 | 落点 |
| --- | --- | --- | --- |
| 1 | 迁移 + 存储 + store docstring 勘正（Q1/Q2） | ✅ | `migrations/versions/20260812_0023_plan_snapshot_discovery.py`、`infrastructure/models.py`、`infrastructure/plan_snapshot_store.py`、`repository_intelligence/contracts.py` |
| 1b | Q3 案 (b)：物化复用草稿快照 | ✅ | `change_orchestration/{application,ports}.py` |
| 2 | 四个写触发 + 轮询端点（线程池，Q6） | ✅ | `repository_intelligence/api/discovery_chain.py`、`application/discovery_chain.py` |
| 3 | 读投影（§3.1 端点 + §3.3 两标量） | ✅ | `api/read_models/{router,service,sources}.py` |
| 4 | 审批端点（§5.2） | ✅ | 同 2 |
| 5 | 既有缺陷顺带修（Q12/Q13） | ✅ | `repository_intelligence/api/router.py` |
| 6 | 前端发现面板接线 | 前端批次 | `frontend/` |

派生规则（步进器判定、`effective_tiers`、分档指纹、大小写映射）**唯一实现**在
`repository_intelligence/contracts.py` 的纯函数里，读模型与写触发同取一处——
它们必须给出同一个答案，两份实现正是本项目反复吃亏的那类缺陷。

验证：`tests/api/test_issue_discovery.py`（HTTP 全链、鉴权、幂等、作废下游、单飞、
**线程池不阻塞事件循环**、诚实失败、审批漂移 409、改档进计划）、
`tests/test_discovery_contracts.py`（§3.2 七条判定逐条 + 派生纯函数）、
`tests/test_plan_execution_bridge.py::TestMaterializeSnapshot`（Q3 案 (b) 与快照回归）。
全程零真实网络/LLM 调用。

---

## 8. 物化开工端点（C-3，2026-08-11 实施 · 分支 `feat/c3-materialize`）

§3.2 的表格给它留的位置是「（步进器外）物化开工 / Step 4 / 批次 C-3，不在本文」。
本节补齐，与实现一致。

### 8.1 落位、鉴权与形状

`POST /api/v1/issues/{issue_id}/discovery/materialize`，挂 §4.1 的同一个 RI router，
同一个 `ACTION_TOKEN`，主体规则与 §4.1 **同一份实现**（活跃 `ORGANIZATION_LEADER` 且属
该 issue 的工作区；`require_organization_leader()` 被四个写触发与本端点共用——鉴权规则
存两份，就是迟早被放宽一份）。

请求：

```json
{ "created_by_agent_id": "uuid", "idempotency_key": "string(>=8)" }
```

**不收计划的任何字段**——没有 `task_dag`、没有 `contracts`、没有 `engineering_spec`、
没有 `repositories`。这是与既有 `POST /bridge/materialize` 最大的差别，理由与 §4.3
Step 2「不收 `candidate_repos`」同一条，但后果更重：那里浏览器回传的是待审的证据，
这里回传的会直接变成派给 Worker 的任务。`/bridge/materialize` 保持收整份计划不动，
它的调用方是 `scripts/run_pipeline.py`——脚本手里那份是**唯一**的一份，浏览器手里那份
是服务端已经存好的一份的**副本**，两者不是同一种东西。

响应 **200（同步）**：

```json
{ "plan_id": "uuid|null",
  "task_ids": ["uuid"],
  "team_count": 2,
  "repositories": ["ts-notify", "ts-order"],
  "status": "materialized|replayed" }
```

同步而非 202：全程不调模型，只写行然后返回，没有可轮询的东西；给 202 等于让面板多养
一条等待路径和一条错误路径去等一件已经做完的事（与 §4.2 对 A-2 的论证同一把尺子，
结论相反是因为前提相反）。

`plan_id` 可为 `null`：计划里每个仓库都被跳过（都不在编目里、或都没有队）时，规范说明
已建、但没有任何东西被排期。此时草稿**不被消费**（`link_execution_plan` 只在真的起了
执行计划时才回填），这一轮仍可再物化。

### 8.2 三类 409（原因如实，逐条可区分）

| 情形 | detail（原文透传） |
| --- | --- |
| 链未走完（§3.2 判定 `step < 4`） | `the discovery chain is still on step N of 4; there is no plan to materialise yet` |
| 分档生成了但**未审批** | `the classification has not been approved; work cannot start on a repository set nobody released` |
| 审批被**打回**（`changes_requested`） | `the classification was returned for changes; …` |
| Step 3 失败留了 `plan.error` | `the last plan generation failed; re-run it before starting work` |
| 已批但 `task_dag` 为空 | `the classification is approved but no plan has been generated; …` |
| 草稿已被消费（本轮已物化过，且**不是**同键重放） | `this issue's plan has already been materialised; its discovery chain is closed` |
| 计划里的仓库全部不在编目 | `none of the plan's repositories are in the catalog (…); nothing can be assigned` |
| **REPOSITORY_SCOPE 检查点未过** | **bridge 的 `WorkflowBlocked` 原文**，如 `human_checkpoint_pending` |

「未审批」与「已批但没计划」**必须分开说**：它们是两个不同的下一步动作（去审批页 / 去按
生成计划），一句「计划未就绪」会把人送到错的按钮。判定用的是读模型自己的
`discovery_step()` 纯函数，不另立一套——面板上那个按钮是按这个数字亮起来的，服务端换
一把尺子拒绝，就是同一屏幕上两个答案。

另有 **503**：执行面未配置（无 Matrix → 无任务编排）。bridge 在**任何副作用之前**
`raise ExecutionPlaneUnavailable`，所以这是可重试的，草稿仍开着；与
`POST /bridge/materialize` 和 `run_pipeline.py` 对 503 的既有读法完全一致，**降级语义不变**。

运行时投影（§8.7）再补两行 503——控制器未配置 / 不可达 / 房间未就绪，皆发生在
`start_plan` 之前，同样可重试。另有一枚**具名 500**（`RoundNotRecorded`）：计划已启动
但快照记不下它——任务在跑而轮次没有入册，detail 原文明说 `materialize again to finish
recording it`。这是唯一一种**必须重按物化**而不是报障等待的 500：把它吞成 200 会让上表
「已物化」那行 409 永远失灵，下一次点击就为同一轮开出第二个执行计划（缺陷 A-5 的真身；
异常声明在 `change_orchestration/contracts.py::RoundNotRecorded`，脚本路径
`POST /bridge/materialize` 不捕获它，同样以 500 示人而不再假 200）。

### 8.3 幂等与重放

收据落在 `discovery.materialization`（与四个步块并列，同一张草稿）：

```json
{ "idempotency_key": "…", "status": "materialized|failed", "by_agent_id": "uuid",
  "at": "iso8601", "error": null,
  "plan_id": "uuid|null", "task_ids": ["uuid"], "team_count": 2,
  "repositories": ["…"], "skipped_repos": ["…"] }
```

- **同键重放 → 200 + `status: "replayed"`，形状完全相同**，且不再建队、不再起任务
  （bridge 的 `idempotency_prefix` = `disc-{idempotency_key}`，但重放在调 bridge **之前**
  就短路了——bridge 自身幂等只保护规范与任务，挡不住「草稿已被消费 → 另写一版新快照」
  那条分支，那会让一轮长出第二版）。
- **只有 `status == "materialized"` 的收据才重放**。失败的收据是「出了什么事」的记录，
  不是可以交回去的结果；同一个键在故障排除后仍可重试。
- 收据要跨快照找：物化成功即消费草稿，重试到达时持有收据的那一行已经不是当前草稿了。
- **不同键落在已消费的一轮上 → 409**（上表最后第三行），既不重放也不重建：那不是这次
  请求起的计划，也不该给这一轮第二份。

### 8.4 ensure topology：这一步为什么在这里

核实事实：**控制台路径从不创建拓扑**。建工作区只落一个 `ORGANIZATION_LEADER`
（`OrganizationRegistryService` → 一次 `CreateAgent`），扫仓库只落编目行
（`ScanRegistration`），二者都不建 `RepositoryTeam`、不建 `ProjectAgentTopology`。而
`PlanExecutionBridge.materialize` 第一件事就是
`raise ValueError("Project topology not found")`。所以每个项目的第一轮都会撞上它。

裁决：**无拓扑时按草稿 `task_dag` 的仓库集合建一个**（语义照
`scripts/run_pipeline.py::_ensure_topology`：org leader + 每仓一队，一队 = 一个
`REPOSITORY_LEADER` + 一个 `WORKER`），**已有拓扑一律不动**。

走的是既有应用层能力，没有第四条写库路径：

| 层 | 用的东西 | 新增 |
| --- | --- | --- |
| 建 principal | `agent_directory.application.CreateRepositoryAgentTeam`（既有，此前**零生产调用方**，只有脚本用） | 外面包 `ProvisionRepositoryAgentTeam`（*ensure* 语义，见下） |
| 建拓扑 | `project.application.CreateProjectAgentTopology`（既有，此前只有 `POST /projects/topologies` 一个调用方） | 外面包 `EnsureProjectAgentTopology` |
| 跨模块 | `agent_directory.contracts.RepositoryAgentTeamProvisioner`、`project.contracts.ProjectTopologyProvisioner` | 两个 Protocol（**本节即「补最小契约」的记录**） |

三点必须写进契约的差异：

1. **`ensure` 不是 `create`。** 仓库 leader 是目录里的 singleton
   （`singleton_key = "repository:{id}:leader"`，全局唯一而非按项目），所以第二个项目
   碰同一个仓库时 `create` 会 `AgentAlreadyExists` 把这一轮卡死。两个项目共用一个仓库
   是常态不是错误，因此已存在的队**收敛复用**；若那个 leader 属于别的组织、或挂在别的
   org leader 下，则如实报错而不是「修好」它。
2. **组织取的是操作者自己的**，不像 `run_pipeline` 每次 `new_id()` 现造一个——脚本从零
   引导，控制台这一轮发生在一个已经存在的工作区里面。
3. **`execution_mode` / `required_checkpoints` 留缺省（AUTO / 空）。** 顺路建出来的拓扑
   不是决定一个项目监管策略的地方；那仍归 `POST /projects/topologies` 这张管理员面。
   反过来说，**已有拓扑连同它的监管策略一起原样保留**——所以 §8.2 最后一行那个
   REPOSITORY_SCOPE 409 是真会发生的，不会被「顺手重建成 AUTO」绕过。

建队发生在检查点闸门**之前**（bridge 内部才评估闸门），这是本端点唯一一处「先写后拒」。
可以接受的理由很具体：一个没有执行计划的拓扑不给任何人派任何活，而闸门放行后紧接着
就要用它。

### 8.5 与 §2.4 版本语义的衔接

不新增版本。物化走的是 §2.4 已裁决的**案 (b)**：复用当前草稿快照，
`link_execution_plan()` 回填 `execution_plan_id` 即消费该行。于是一轮 = 一版：
v1 同时承载本轮的发现档案（§2.2 的 `discovery` 块）、集成产物（§4.3 写回的四列）与
本节的物化收据，`rounds[].plan_version` 从 1 起跳。下一轮发现在
`next_version()` 上开新草稿（§2.3），`discovery` 从空开始。

`discovery.materialization` 是**收据**，不是计划内容：它写在刚被消费的那一行上，是
§2.5「已消费快照不可变」的唯一例外，明写在此而不是默默做掉。这条例外买到的是「成功
物化之后，同键必然重放、异键必然 409，绝不会静默重建」——因为草稿一旦被消费，任何
后续请求都进不到 bridge。

### 8.6 落点与验证

| 内容 | 落点 |
| --- | --- |
| 端点 + 请求/响应模型 | `repository_intelligence/api/{discovery_chain,models}.py` |
| 服务（读快照、判定、收据、ensure topology 编排） | `repository_intelligence/application/discovery_materialization.py` |
| 物化能力的端口（**避免与 change_orchestration 成环**） | `repository_intelligence/ports/materialization.py` |
| 鉴权规则单一实现 | `repository_intelligence/application/discovery_chain.py::require_organization_leader` |
| 两个跨模块契约 | `agent_directory/contracts.py`、`project/contracts.py` |
| 两个 ensure 实现 | `agent_directory/application/repository_team.py`、`project/application.py` |
| 组装 | `bootstrap/container.py::{project_topology_provisioner,discovery_materialization_service}` |

`ExecutionPlaneUnavailable` 从 `change_orchestration/application.py` 移到该模块的
`contracts.py`（`__init__` 与既有调用方无感）：它是**对外的拒绝**，两个 API 层都要按它
翻 503，放在 contracts 才能被指名而不必 import 别人的 application。

验证：`tests/api/test_issue_materialize.py`（15 例，全程零真实网络/LLM/Matrix；唯一替身
是 `start_plan` 这个 Matrix 相关接缝，其余快照存储、拓扑建队、规范服务、检查点闸门、
bridge 全是生产代码）。反证已做：拆掉就绪判定 → 三条 409 用例按**具体原因**红；
拆掉重放查找 → 重放用例红；再叠加「草稿不被消费」 → 重放用例与异键 409 用例同时红，
证明「不重建」那组断言（`start_plan` 调用计数、拓扑行 id 与队数、principal 集合）
真的咬得住，而不是只在数数。

### 8.7 运行时投影：建完队还要给队一个能说话的地方（**已裁决 · 2026-08-12**，B-11）

> 主脑裁决通过，随修复合并生效（分支 `feat/runtime-provision`）；§8.2 的 503 段已含
> 本节两行与 `RoundNotRecorded` 具名 500。

核实事实（B-11）：**§8.4 的 ensure topology 只写行，不建运行时**。
`ProvisionRepositoryAgentTeam` 落 principal，`CreateProjectAgentTopology` 落
`ProjectAgentTopology`，两者都不碰 AgentTeams 控制器。而真正把 Manager/Worker 注册进
控制器、把 Team 建出来、把 `room_id` / `leader_room_id` / `runtime_status` 回填到拓扑行的
是 `RegisterNativeAgent` + `ReconcileProjectAgentTopology`——**它们在 `src/` 下零调用方**，
只有 `scripts/run_pipeline.py` 用。后果是控制台建的项目在控制器里根本不存在，`room_id`
恒为 NULL，`collaboration.SendCollaborationMessage._route` 必然
`CollaborationRouteUnavailable`。§8.2 那条「AgentTeams room is not ready → 503」不是偶发
时序，是这条路径的**必然终点**：房间没有任何人去建，重试多少次都一样。

裁决：**物化在调 bridge 之前、同步做一次运行时投影**，语义照
`run_pipeline.py::_ensure_topology` 的后半段：

1. 按拓扑逐个把 principal 投进控制器——org leader 走 `ensure_manager`，仓库 leader 与
   worker 走 `ensure_worker`，资源名取 principal 已有的 `agentteams_resource_name`
   （**不是**新建：目录里的行是 §8.4 刚建的，`CreateAgent` 是 create 语义，再建会撞
   singleton；这里只补 `RegisterNativeAgent` 的控制器那一半）；
2. `ReconcileProjectAgentTopology` 逐队 `ensure_team`，把房间号回填进拓扑行；
3. **回填后仍有队缺 `room_id` 或 `leader_room_id` → 拒绝**，不让这一轮开工。

三点必须写进契约：

1. **投影的字段必须与脚本逐字段一致**（model / runtime / skills / MCP server）。控制器对
   已存在资源做**逐字段比对**，不一致答 409；一个仓库先被 `run_pipeline.py` 配过、后被
   控制台碰到，只要 skills 列表拼法不同就会冲突。因此 skills 常量与
   task-control MCP 注入在两条路径上**共用同一份实现**。**`runtime` 自 §8.7.1 起不再靠
   「抄一份」维持一致，改为两条路径读同一个设置**。
2. **投影的幂等键不含本轮的 idempotency key**：注册键是
   `project:{project_id}:agent:{agent_id}:agentteams`，建队键沿用既有的
   `project:{project_id}:repository:{repository_id}:team`。所以异键重试、第二轮、第二个
   项目碰同一个仓库，落的都是**同一个**副作用。这与 §8.3 的 `prefix` 机制正交：`prefix`
   保护的是规范与任务，投影不读它。
3. **两个房间都要**：队房间承载 leader↔worker，leader 房间承载 org leader 的派活
   （`_route` 按角色二选一），缺一个就有一半的对话没有落点。

**新增 503（补进 §8.2 的 503 段）**：

| 情形 | detail |
| --- | --- |
| 控制器未配置 | `the execution plane has no rooms for this project's teams (the AgentTeams control plane is not configured, …); nothing was started — materialize again once AgentTeams answers` |
| 控制器不可达 / 拒绝 / 房间未就绪 | 同上包装，括号内是控制器原话（`AgentTeams HTTP …` / `… has not created rooms for … yet`） |

失败语义与 §8.3 完全一致，并且**更干净**：投影发生在 bridge 之前，因此失败时
`start_plan` 从未被调用、规范与任务一行未写、草稿未被消费；只有一张 `status: "failed"`
的收据。同键重试重跑整段投影（失败收据不重放），异键重试按 §8.3 借用同一个 `prefix`
——而投影本身不读 `prefix`，所以借不借都是同一批副作用。

**与 §8.4「先写后拒」的关系**：投影同样发生在 REPOSITORY_SCOPE 闸门**之前**（闸门在
bridge 内部），所以一轮被闸门挡下时房间可能已经建好。理由与 §8.4 同一条，且更弱：房间
不给任何人派任何活，而闸门放行后紧接着就要用它；`ensure_*` 全是幂等的，重复建等于读。

落点：

| 内容 | 落点 |
| --- | --- |
| 端口（**调用方声明**，避免业务模块指名 integration） | `repository_intelligence/ports/runtime_projection.py` |
| 投影实现（复用 `ReconcileProjectAgentTopology`） | `integrations/agentteams/runtime_projection.py` |
| 两路径共用的 MCP 注入 | `integrations/agentteams/principal_registration.py::with_task_control` |
| 调用点（ensure topology 之后、bridge 之前） | `repository_intelligence/application/discovery_materialization.py` |
| 503 翻译 | `repository_intelligence/api/discovery_chain.py` |
| 组装 + 错误族翻译 | `bootstrap/container.py::topology_runtime_projector` |
| 控制器地址 | `REPOMESH_AGENTTEAMS_CONTROLLER_URL`（`settings.agentteams_controller_url`），无硬编码 |
| 运行时取值 | `REPOMESH_AGENTTEAMS_{MANAGER,WORKER}_RUNTIME`（`settings.agentteams_{manager,worker}_runtime`），默认 `copaw`，见下方 §8.7.1 |

验证：`tests/integrations/agentteams/test_runtime_projection.py`（5 例，控制平面是记录型
替身，零网络）+ `tests/api/test_issue_materialize.py` 新增 4 例（顺序、503、异键重试修复、
未配置控制平面时组装出的真投影仍拒绝）。反证已做，见落点分支的施工记录。

### 8.7.1 运行时不是我们能替控制器决定的（**已裁决 · 2026-08-12**，A-6）

> 主脑裁决通过，随修复合并生效（分支 `feat/dispatch-identity-fix`）。本节只改 §8.7 的
> `runtime` 一个字段，其余三点（幂等键、两个房间、先写后拒）不动。
> 三项裁决请求全批；「已知代价」段的误导性 503 另裁如下：**存量 spec 冲突
> （`AgentTeamsConflict`）应是自己的不可重试拒绝（409，原文点名不一致的字段），不得
> 冒充「稍后重试」的 503**——实现入 backlog（本环境仅有的五个存量冲突源已由主脑按
> 下方收敛清单就地改档消除，该 503 无活体实例）；在实现落地前，运维遇到
> 「materialize again once AgentTeams answers」重试不好转时，应先查 spec 冲突。

核实事实（A-6，2026-08-12 活体）：§8.7 落地后房间确实建出来了，轮次也确实开工了，
但**第一次派活就失败**——`matrix.py` 在投递前向控制器要收件人的 Matrix 身份，拿到空值，
抛 `AgentTeamsUnavailable`。原因不在房间，在容器：控制台建的那批 worker 容器全部
`Exited(1)`，入口脚本 `worker-entrypoint.sh` 要 `HICLAW_WORKER_NAME` 而这台控制器从不传，
于是 worker 从未起来，也就从未拿到 Matrix 身份。

再往上一层：**`runtime` 与镜像是控制器侧成对的配置**。这台控制器
`AGENTTEAMS_COPAW_WORKER_IMAGE` 指向本地可用镜像，而 openclaw 那半边
（`AGENTTEAMS_WORKER_IMAGE`）指向的镜像在本环境跑不起来。我们的两条路径却都把
`OPENCLAW` **写死在代码里**——`scripts/run_pipeline.py` 与
`integrations/agentteams/runtime_projection.py` 各写一份。这是个我们无权做的决定：
**一个 runtime 能不能用，是控制器的属性，不是代码的常量。**

裁决请求：

1. **`runtime` 升为设置**：`REPOMESH_AGENTTEAMS_MANAGER_RUNTIME` /
   `REPOMESH_AGENTTEAMS_WORKER_RUNTIME`（`settings.agentteams_manager_runtime` /
   `settings.agentteams_worker_runtime`），**默认 `copaw`**——这是本部署实际具备的配对，
   `repomesh-gh-*` 那批 copaw worker 已连续运行两天并跑完过一整轮活体交付。
2. **两条路径读同一个设置**，于是 §8.7 三点之一的「逐字段一致」对 `runtime` 而言
   **由结构保证，不再由抄写保证**：没有第二个值可漂。`ProjectRuntimeProjection` 由
   composition root 注入（integrations 一律不读 `settings`），`run_pipeline.py` 直接
   `get_settings()`。
3. **设置按 wire enum 定型**（`ManagerRuntime` / `WorkerRuntime`），未知取值在**启动时**
   报错，而不是拖到第一次派活才炸；允许值也因此只有一份。

**已知代价，需一并裁决**：`ensure_worker` / `ensure_manager` 对已存在资源是
**create-or-verify，不是 update**——`control_plane.py:111` 先 GET，命中就拿现有资源逐字段
比对，`runtime` 不一致直接 `AgentTeamsConflict`（`control_plane.py:335-340`），**既不发
POST，客户端也没有 PUT/DELETE**。因此本节生效后，**存量 `openclaw` 资源不会自动收敛**，
而会在下一轮物化时冲突——且该冲突经 `topology_runtime_projector` 会被译成
`RuntimeProjectionUnavailable` → 503「materialize again once AgentTeams answers」，
是一个**重试永远不会好**的 503。这条误导本身值得单独裁一次。

收敛手段（已在部署态控制器上核实，非 mirror 推断）：`agt update worker --runtime` 与
`agt update manager --runtime` **都存在**，容器内 `agt` 经 `AGENTTEAMS_AUTH_TOKEN_FILE`
自动鉴权，因此**无需删除**即可就地改 spec。本环境需收敛的只有 4 个 Worker
（`rm-{leader,worker}-{b-checkout,c-billing}`）与 1 个 Manager（`console-demo-org-leader`）。
`repomesh-gh-*` 与 `e2e-remote` 的 `bohan-*` 均不在其中，不得触碰。

未核实项：改 `runtime` 后控制器是否会用 copaw 镜像重建容器（当前 `containerState=stopped`），
需收敛后实测。本提案**不含**收敛动作本身，只把取值变成设置。

补充勘误（同批）：`AgentTeamsUnavailable` 此前在物化路径上**无人翻译**，裸奔成
`500 text/plain "Internal Server Error"`。已按 §8.2 既有读法并入 503 家族，翻译点在
`bootstrap/container.py::collaboration_routed_messenger`（与
`topology_runtime_projector` 同一个理由：业务模块不得 import integration）。仅翻译
`AgentTeamsUnavailable`；`AgentTeamsResponseError` / `AgentTeamsConflict` 是「答了但答错」，
仍应是故障而非重试。

验证：`tests/integrations/agentteams/test_runtime_projection.py` 增 3 例（默认值即 `copaw`、
未知取值启动即拒、两条路径都不再自带 `runtime` 字面量）+
`tests/collaboration/test_dispatch_identity_translation.py` 4 例 +
`tests/api/test_issue_materialize.py` 增 2 例（503 而非裸 500、派活中途断掉的轮次可被下一次
重放接上）。反证已做：撤掉翻译后端点恢复 `500 text/plain "Internal Server Error"`。

### 8.7.2 Team 属于仓库，不属于拓扑行（**已裁决 · 2026-08-12**，A-8）

> 主脑裁决通过，随修复合并生效（分支 `feat/repo-team-converge`，迁移 0024）。本节改
> §8.7 的 Team 命名与 `ensure_team` 时序两点，并把 §8.7.1 那条「spec 冲突不得冒充
> 503」的裁决从 backlog 变成实现——它有了活体实例。裁决附注：迁移 0024 在共享行存在
> 时 downgrade 诚实失败=预期行为，接受；种子四行幻影队名（§内「10 行非 6 行」一节）
> 随下次投影自然收养，不做迁移期改写，接受。

核实事实（A-8，2026-08-12 活体，只读取证）：同一 org 下三个 issue
（`96896557` / `35e66beb` / `5c1b3567`）都物化到同一对仓库（checkout `579a61c4` /
billing `9dfa78f2`）。仓库 leader/worker 是**目录单例**（这是对的，设计如此：
`CreateAgent` 的 `singleton_key = "repository:{id}:leader"`），三个 issue 的六行
拓扑因此共用同一批 principal——checkout 三行同为 leader `4160c8de`，billing 三行同为
`996dfd64`。但 `project/domain.py:157-162` 用**行自己的 id** 铸 Team 名
（`rm-team-{self.id.hex}`），于是一个 issue 一个名字。

AgentTeams 控制器对 worker 的 Team 归属是**排他**的。第一个投影成功的 issue
（`96896557`，team `rm-team-6c503f02…`）占住了两个 leader，其余每个 issue 的
`ensure_team` 都得到
`AgentTeams HTTP 400: Worker rm-leader-b-checkout is already a member of Team rm-team-6c503f02…`
——一个**确定性冲突**，却经 `bootstrap/container.py::topology_runtime_projector` 被折成
`RuntimeProjectionUnavailable` → 503「materialize again once AgentTeams answers」。
**重试永远不会好**，因为 AgentTeams 早就答了。

这不是新缺陷，是旧假设到期：脚本时代从没让两个项目共用一个仓库，所以「一行一个 Team」
一直成立得很偶然。

补充事实（本次核实，超出初始报告）：该表实为 **10 行**而非 6 行——种子行
`rm-team-b-checkout`（项目 `9129f894`）与 `rm-team-c-billing`（项目 `e94499f9`）
也压在这两个仓库上，且这两个名字在控制器上**根本不存在**（房间 id 是种子脚本直接写库的）。
即同一仓库在库里最多曾有四个「Team 名」，控制器上只有一个真 Team。

裁决请求：

1. **Team 是仓库级资源**。规范名由仓库派生：`rm-team-{repository_id.hex}`，不再由拓扑行
   id 派生。凡触及同一仓库的项目，共用同一个控制器 Team 及其两个 Matrix 房间。控制台的
   「rooms」本就是协作消息的读模型投影，不是 Team 的镜像，此变更不影响其语义。
2. **收敛靠认领，不靠外科手术**（`ReconcileProjectAgentTopology`）。`ensure_team` 之前先问
   控制器：**仓库 leader 当前属于哪个 Team**。已核实部署态控制器 v1.2.0 的
   `GET /api/v1/workers/{name}` 响应**直接带 `team` 字段**（另有 `GET /api/v1/teams` 列表
   端点），因此**不需要**去解析 400 那句话——`WorkerRuntimeRef` 增一个 `team` 字段即可。
   读到就**认领**它（用它的名字去 `ensure_team`，拿到它的两个房间），并把认领到的名字
   **回写到拓扑行**（沿用该行已有的房间 id 回写机制）；读不到才用规范名新建。
   以 leader 为锚点：它是 Team 必然包含的那一个 principal，且是目录单例，**谁持有它，
   谁就是这个仓库的 Team**，不管它叫什么。
   代价：每次投影每个 team 多一次 GET，且不缓存——要观察的正是「别的项目刚建了 Team」。
3. **唯一约束改域**（`project/infrastructure.py:74`，迁移 `20260812_0024`）。原
   `UNIQUE (agentteams_team_name)` 读作「两行不得指向同一个 Team」，在共享 Team 之后
   **恰好禁止了正确行为**。改为 `UNIQUE (project_id, agentteams_team_name)`：保留仍然成立
   的那一半——**单个项目内两个仓库必须是两个 Team**（否则两仓流量并进一个房间且无人察觉）。
   **迁移不改任何一行的名字**：存量 stale 名由下一次投影认领后自行回写，收敛是跑出来的，
   不是迁移断言出来的——一个事务内看不见控制器状态，改名就是猜。
4. **翻译收窄**（落实 §8.7.1 第二条裁决）。`topology_runtime_projector` 拆分：
   `AgentTeamsConflict` 与 `AgentTeamsResponseError` 的 **4xx** → 新拒绝
   `RuntimeProjectionConflict`（`repository_intelligence/ports/runtime_projection.py`），
   在 `api/discovery_chain.py` 译为 **409**，原样带上控制器那句话；
   `AgentTeamsRoomsPending`（房间还没建出来）、`AgentTeamsUnavailable`、5xx 仍为 503。
   认领落地后 already-a-member 一支应不再出现，409 是**其余** spec 冲突
   （runtime / skills 不一致等）的诚实归宿。

三个标本的重放推演（认领后，无需碰控制器）：`96896557` 读到 leader 已在
`rm-team-6c503f02…`（正是自己建的），认领＝原地不动，房间不变；`35e66beb` 与 `5c1b3567`
各自读到同一个 `rm-team-6c503f02…`，认领它、拿到同一对房间、把名字回写掉自己那个
从未存在过的 stale 名——三行收敛到一个 Team、一对房间，且**没有任何控制器侧改动**。
这正是验收判据。

**派活兼容性**（`collaboration.SendCollaborationMessage._route`）：`_route` 走
「项目 → 该项目拓扑内按成员匹配 team → 取 `room_id`」，不假设房间在项目间唯一，因此共享
房间不破坏它。多个 issue 的流量在同一个仓库房间里交织**是架构本意**（消息按
delivery/task 键区分，且落库时带 project_id/repository_id）。

验证：`tests/integrations/agentteams/test_runtime_projection.py` 增 5 例
（规范名来自仓库、第二个 issue 共用第一个的 Team、旧行认领既有 Team 并回写、
leader 是唯一被读的锚点、成员真不一致仍是 400）+ `tests/api/test_issue_materialize.py`
增 2 例（refuse-on-merits 得 409 且不含重试话术、composition root 五分支分流）。
反证已做两次：撤掉认领 → 重现活体原句
`AgentTeams HTTP 400: Worker … is already a member of Team rm-team-6c503f02…`；
撤掉 409 分流 → 重现原误导性 503「materialize again once AgentTeams answers」。
迁移 up/down 在一次性 Postgres 上实测：升级后跨项目同名可插入、项目内同名被拒；
降级在存在共享行时**如实失败**（数据已越过 schema），无共享行时干净回滚。

### 8.7.3 任务包进不了对象存储，也是「执行面暂时接不住」（**已裁决 · 2026-08-12**，A-10）

> 主脑裁决通过，随修复合并生效（分支 `feat/storage-publish-503`）。两条观察项的附加
> 裁决：a) **重投不换 transaction_id，维持实现**——该键在 A-10 路径上从未被用过，重投
> 本就落为新事件；换新 id 会在「原消息实际已达」时双发（施工代理的分析采信，主脑
> addendum 中的换 id 建议作废）；b) **「已投递但收件人出生更晚」的点名竞态**（copaw
> 同步自容器启动始，DELIVERED 短路使重放不补投）——先以 425efbbc 重放实测定夺：若
> worker 凭新建的 Worker 任务点名即可开工则无需处置；若 leader 侧滞留点名阻断轮次，
> 立 **A-13** 单独设计（候选修法=重放时对「投递时间早于收件人运行时出生」的消息补投）。

> 与 §8.7 / §8.7.1 同一家族的第三例，也是走得最远的一例。房间有了、身份有了、计划起了、
> **任务行第一次真的落进了 `task_orchestration.execution_plan_tasks`**——然后 Worker 的
> 任务包传不上对象存储，整轮以 `500 text/plain "Internal Server Error"` 逃逸。活体回执：
> `status=failed, error="S3 operation failed; code: InvalidAccessKeyId, message: The Access
> Key Id you provided does not exist in our records., resource: /agentteams-storage,
> bucket_name: agentteams-storage"`（2026-08-12）。凭据本身是 env 配错、已修；此处要裁的是
> **翻译与重放语义**，二者与那次配错无关，仍然成立。

Worker 拿到的活是**文件**，不是一句话：任务包写进 AgentTeams 共享存储，房间里的消息只
指向它。所以一个**不可达 / 未鉴权 / 写不进**的存储，和一个没建好的房间一样能把派活按住，
读法也该一样——请求没错、服务端没坏，执行面**暂时**接不住。

**新增 503（补进 §8.2 的 503 段）**：

| 情形 | detail |
| --- | --- |
| 对象通道（S3/minio）拒收任务包 | `the execution plane could not store this round's tasks (<存储原话，如 S3 operation failed; code: InvalidAccessKeyId, … bucket_name: agentteams-storage>); the tasks are written — materialize again once AgentTeams storage accepts them` |
| 文件通道写不进（磁盘满 / 只读挂载 / 校验不过） | 同上包装，括号内是 `OSError` 原话 |

**这一条与上面两条 503 的关键差别，必须写进 detail**：它**不能**说
`nothing was started`。它触发时任务行已经在库里了，说「什么都没发生」是一句操作员会据以
行动的假话；重按物化做的是**把它们做完**，不是重来一遍。

翻译点：`bootstrap/container.py::storage_backed_task_publisher`，与
`collaboration_routed_messenger`（A-6）、`topology_runtime_projector`（B-11）同一个理由与
同一个位置——业务模块不得 import integration，组合根是两套词汇唯一允许相遇的地方。包的是
**端口**（`TaskAssignmentPublisher`）而不是适配器，所以**文件与对象两个通道一次覆盖**。
翻译的异常族：`minio.error.MinioException`（含 `S3Error` / `ServerError` /
`InvalidResponseError`）、`urllib3` 的连接错误、以及 `OSError`（连接类与文件系统类，含
两个通道各自的「写完读回来对不上」校验失败）。**不翻译 `ValueError`**——文件通道用它表示
「同一路径上已有内容不同的包」，那是「答了且答的是不」，重按永远不会好，冒充 503 只会让
操作员一直按一个按不好的按钮（与 A-6 划的是同一条线）。

**重放（本节的实质，比翻译更重要）**：这次失败落在任务行**之后**，而 7659c89 教会
`AdvanceExecutionPlan.start` 重入的那个判据**恰好在这里失效**——`_assign_batch` 是
「派 leader 任务 → 把 leader id 记进计划行 → 再逐个分解出真正被派活的 Worker 任务」，
上传发生在最后一步，所以失败时批次的 `leader_task_ids` **已经是满的**，旧的
「这批是不是都派完了？」回答「是」，于是重放**什么都不做**。活体证据正是如此：13:51 那次
重放回了 200、账面有任务行，而 `teams/rm-team-…/shared/tasks/` 里只有 `.keep`。

裁决点（两处改动，均已实现并有测试）：

1. `_resume` 去掉「批次已满即跳过」这个早退。数满 leader 任务分不出「已派活」和「只是
   记下来了」，问错了问题；重跑整批、让每一次带键的写自己回答，才是对的。健康批次重跑是
   **便宜**而非禁止：leader 任务按键命中、任务包按 content-hash 原样重写、已投递的房间
   消息被识别而不重发。已结算（failed/completed）的计划仍然原样不动。
2. `DecomposeRepositoryTask.execute` 的 `in_flight` 早退**收窄**为「在飞的 Worker 任务不是
   本键写的」才短路。本键写的那一个要落到 `assign` 上——`assign` 认键、返回既有行而不是
   再写一行，并且**重跑失败掉的那半段投递**（任务包上传 + 房间消息）。旧的无条件短路正是
   第二道闸：即使 `_resume` 重跑了，分解这一层照样会把重发咽掉。

**不重复**：两处重跑的每一次写都落在第一次那个 key prefix 上，所以是**查到**而不是**新建**。
本节的重复性证明一律**按行数断言，不按调用次数**——`test_start_is_idempotent` 原来断言的
「重放没有再调用 assign」正是放走 A-10 的那句话：它把「没试」当成了幂等的定义，而要保的
性质是「没重复」。该用例已改为按任务行断言。

**同键 / 异键**：同键重试重入停在半路的那一批（§8.3 失败收据不重放）；异键重试按 §8.3
借用失败收据的 prefix，因此不会在旁边另开一个执行计划。二者都已有用例。

**一并报给主脑的两条观察（不在本次修复范围，需单独裁决）**：

- **已投递的派活提示不会再发一次**。`SendCollaborationMessage.send` 对
  `status == DELIVERED` 的消息直接短路返回。这是对的（否则每次重放都往房间里灌一条），
  但 copaw Worker 是 mention 驱动、且 sync 从容器**出生**开始
  （`components/agentteams/copaw/src/copaw_worker/matrix_channel.py`），所以一条**早于
  Worker 容器**的 mention 它永远看不见。A-10 这一形态**不受影响**——上传在派活之前失败，
  该 Worker 的消息压根还没建，重放会生成一条全新的 mention；受影响的是「消息发成功、
  之后才失败」的轮次。这是 Worker 启动竞态，不是翻译问题，建议单列。
- **`transaction_id` 就是协作消息的幂等键**，`matrix.py` 直接把它当 Matrix 事务 id 放进
  PUT 路径。因此**重投**同一条 failed 消息会被 Matrix 按事务 id 去重。这在「服务端其实已
  收下、我们这边才失败」时是**正确**的幂等；改成每次换新事务 id 会让原本落地的那条被重发
  成两条。A-10 路径上该键从未被用过，所以新的派活是一枚**全新**事件，不受此影响。是否为
  上一条的竞态另立一条「强制重投」通道，建议与上一条一起裁。

验证：`tests/task_orchestration/test_task_publication_translation.py` 9 例（对象通道拒收 /
不可达 / 校验不过、文件通道写不进 / 正常透传 / 内容冲突不冒充 503，以及任务行留存、
同键重放把包传上去且只有一行、上传没成功就不派活）+
`tests/task_orchestration/test_plan_execution.py` 增 2 例（整批在上传处断掉后被下一次按钮
接上、异键写的在飞任务不被本键碰）并改写 `test_start_is_idempotent` 的断言口径 +
`tests/api/test_issue_materialize.py` 增 3 例（503 而非裸 500 且不含 `nothing was started`、
同键重放接上、异键重放借 prefix 不分叉）。反证已做两次：撤掉组合根翻译 → 端点恢复
`500 text/plain "Internal Server Error"`；撤掉 §8.2 这条 503 分流 → 同样恢复裸 500。

---

### 8.7.4 派工是一次性事件、不是可收敛的状态（**已裁决 · 2026-08-12**，A-13）

> §8.7.3 把 A-13 挂了起来，条件是「先以 425efbbc 重放实测定夺」。实测做完了，答案比
> 当时预设的那条候选修法大：滞留点名不是一个种类，而是**同一个根**结出的三个形状，
> 而 §8.7.3 预设的「重放时对『投递时间早于收件人运行时出生』的消息补投」只够盖住其中
> 一个，且要把补投判断塞进重放路径——那正是 §8.7.3 裁决 (a) 要保住的东西。本节提议
> 换一条路：**不动重放，另立一个人按的收敛入口**。

**根因（裁决原文）**：派工是一次性事件、不是可收敛的状态——agent 那一个回合没干成,
控制台就永远停在 running,没有任何一层会重试、报警或让人在 GUI 上重发。

任务包写进共享存储、房间里发一条点名，之后 Worker 的**那一个 react 回合**就是全部机制。
那个回合没把活干完，没有任何一层会发现。

#### 三个活标本（5533，2026-08-12，均保留未动）

| # | 形状 | 那一回合发生了什么 | 为什么现有机制救不了 |
| --- | --- | --- | --- |
| 1 | issue `96896557…` | 点名被消费，worker 卡在无关缺陷上、审批超时，容器被重建；新 Matrix 会话看不见历史消息 | 回执已是 `materialized`，连「重试物化」入口都消失了 |
| 2 | issue `89ed8942…` | 点名收到、回合跑了，任务包因存储权限缺陷（已修）拉取失败；`Consumer stopped`，processed=1/2 | 任务停在 `running`，包从未被拉走；重放认得旧键，房间里不会多任何东西 |
| 3 | billing leaderDM | 点名投递时收件人容器还不存在 | `SendCollaborationMessage.send` 在 `status == DELIVERED` 上短路，重放永不补发 |

#### 一条重要的收窄：执行面本身已经是收敛的

实测另一条证据（2026-08-12）：一次排队中的 runner 派发**等了 100 分钟**，等到 runner 上线
后被正确执行。所以「一次性」的不是整条派工链，而是其中两段——**聊天面的点名**，和
**轮次/批次的推进**。本节的两个范围正对应这两段。

#### 提议的能力：`POST /api/v1/deliveries/{round_id}/redispatch`

落在 `/deliveries` 前缀下，与 §4.6 回滚、§4.5 归档同席——§0 语义等式
`round_id = execution_plan_id = v0.1 的 delivery_id`。**人触发，本迭代不设自动重试**：
一个仅仅是慢的轮次和一个真卡住的轮次在读模型里长得一样，只有人能分辨。

请求体只有两个字段：

| 字段 | 语义 |
| --- | --- |
| `idempotency_key` | 必填。**同时是 attempt 令牌**，见下节 |
| `scope` | `unfinished`（默认）\| `rerun` |

回执：`{round_id, attempt, scope, task_ids[], reopened_task_ids[], settled_task_ids[]}`。
说的是**已经做了什么**，不是将要发生什么；`settled_task_ids` 如实交代哪些被有意没动。

#### transaction_id 的推导，以及它与 §8.7.3 裁决 (a) 的关系

协作消息的幂等键被**逐字**用作 Matrix PUT 的 `transaction_id`
（`SendCollaborationMessage._deliver` → `AgentTeamsMatrixClient.send_task`），而 Matrix 按
transaction id 去重。于是有**两层**吞掉重发：`send` 的 DELIVERED 短路（够不到 messenger），
以及 homeserver 的 txn 去重（够到了也不落新事件）。两层都挂在同一个字符串上，所以提议
**给这个字符串加版本，而不是绕过任何一层守卫**：

```
attempt is None  →  f"{key}:message"                       # 一切既有调用方，含重放
attempt 有值     →  f"{key}:message:rd:{sha256(attempt)[:12]}"   # 一次显式重新派工
                    # 若上式超过 200，退为 f"rd:{sha256(key)[:24]}:{token}"
```

> **勘正（A-20，2026-08-12，本节裁决后）**：上式原文是
> `f"{key}:message:redispatch:{attempt}"`——把调用方的键**逐字**接在后面。首次活体按下
> 即以 `asyncpg StringDataRightTruncationError: value too long for type character
> varying(200)` 裸 500 逃逸。算术此前无人做过：worker 指派键
> `disc-console-discovery-materialize-<uuid>:b0:<uuid>:decompose:worker:<uuid>` 已是
> **165 字**，`:message` 到 173（所以正常派工一直能过），再接控制台的
> `console-redispatch-<uuid>-<uuid>`（92 字）到 245。**零副作用**：库拒了这条 insert，
> 房间也因此没被通知（已核实）。
>
> attempt 的**身份**不需要那 92 个字，只需要「两次不同、一次稳定」，故改摘要承载。
> 但仅改摘要只剩 3 字余量——那是巧合不是余量，所以**再对结果做长度检查**，超限则连
> base 一并摘要，使上界成为构造性质而非观察结果。两条路径都有测试。
> 端点的 `max_length=180` **不是**保证插入合法的东西，不得当作保证依赖。

**这与 §8.7.3 裁决 (a) 不冲突，是同一条裁决的两侧**：那条裁决说的是**重放**不换 id
（换了会在「原消息实际已达」时双发），本节的重放路径一个字符都没改——`attempt is None`
就是原样。换 id 只发生在人明确按下按钮的那一次，此时「可能双发」不是缺陷而是**诉求**
（对已在工作的 agent 表现为一条重复通知，弹窗如实写明）。一次请求内 attempt 恒定，所以
双击是重放；两次请求 attempt 不同，所以是两条真事件。**两侧都做了反证**（见下）。

#### 任务包为什么**不能**同样加版本（实现约束，非选择）

`AgentTeamsTaskPublisher` 把 `idempotency_key` 写进 `meta.json`，而 `meta.json` 进内容哈希；
路径已存在且哈希不同即 `ValueError("published AgentTeams task conflicts with existing
content")`。§8.7.3 **有意不把 `ValueError` 翻成 503**（重试改变不了它）。所以给发布键加版本
会让每一次重新派工都被存储拒绝。**结论：包用原始键重放，只有点名可以加版本**。为此
`TaskStore` 增一个反查 `assignment_key(task_id)`——键的推导者早已是好几轮之前的事了。

#### 两个范围

**`unfinished`（默认）** — 只碰非终态任务，**不写任何任务行**。按错的代价是房间里多一条
重复通知。这是标本 1/2/3 的形状。

**`rerun`** — 2026-08-12 又一条活证据：runner 跑完但**没有测试结果**，
`plan_delivery._candidates_for_batch` 在 `_advance_if_ready` 内抛
`ValueError("Runner evidence has no test results")`，成为**后台静默的崩溃循环**。任务全是
`succeeded`，默认范围会答 409，于是唯一能修的人被告知无事可修。

这一档额外把已完成任务送回去重做（`Task.redo`：状态回 `ASSIGNED`、清掉 `result_summary`）。
**必须写行**：`Task.report` 拒绝终态任务（"a final task cannot be reported again"），只补发
点名的话，重跑的结论永远落不了地，轮次会一直拿着那次坏结果。批次因此重新变成未完成
——这是**诉求**不是副作用：交付被拒的轮次本就不该继续把批次算作完成。

`CANCELLED` / `SUPERSEDED` **不可重做**：那不是「结果错了」，是「决定不做」；重开一个
被新版计划替换的任务等于把退役任务塞回活着的 Worker。

#### 拒绝（与 §8.2 同一家族）

| 码 | 情形 | detail 要点 |
| --- | --- | --- |
| 404 | 该 round 不存在 | — |
| 409 | 轮次有行、无任何任务（物化没走完） | `…has no tasks yet; … materialize the issue instead`——**指向另一个按钮**，不是「等一等」 |
| 409 | 任务全终态且默认范围 | `…a finished round is history, not unfinished work — ask for scope=rerun if a result on file is the thing that is wrong`：拒绝是一句话的开头，不是结尾 |
| 503 | 存储接不住任务包 | 复用 §8.7.3 原话，但**不说 "nothing was started"**，说 `nothing was re-sent` |
| 503 | 房间不可路由 | `…press re-dispatch again once the rooms exist` |
| 503 | 执行面根本未配置 | 无 messenger 即无 orchestrator，是部署问题不是 500 |
| 500 | 其余一切（A-20 补） | **具名**，不裸 500：`…failed unexpectedly (<异常类名>: <原话>); nothing was re-sent`。与 §8.2 给 `RoundNotRecorded` 具名的理由同一条——类名与驱动原话就是全部可行动内容，而 `text/plain "Internal Server Error"` 连「这是 bug 还是故障」都答不了。**仅本端点、与兄弟端点对齐**；全局错误信封是另一议题，未在此裁 |

#### 读模型附带一项：`tasks[].last_dispatched_at`

最近一条该任务 `TASK_ASSIGNMENT` 消息的 `created_at`。**是发出时间不是读到时间**——行上
没有 `delivered_at`，不编。`null` 不是缺数据而是这个字段最响的一句：这条任务压根没被派
出去过（标本 1 的形状）。代价是**整轮一次聚合查询**（`max(created_at) group by task_id`），
不是每任务一次。

#### GUI：零影子判断

入口出现的条件只有「本轮有还能再派的任务」——`display_status` / `backend_status` 都是读
模型原值，界面只转述。**界面不推「卡住了」**（一个仅仅是慢的 Worker 会让这个判断立刻出
错），也不拿 `last_dispatched_at` 减当前时间得结论。「什么时候该用」写在文案里，由人决定。
两个范围是两个显式单选项而非一个默认开关，因为它们是两种不同的风险。

#### 三个标本如何被退役

| # | 退役方式 |
| --- | --- |
| 1 | `unfinished` 补发点名——新 txn id ⇒ 新事件 ⇒ 新容器的 sync 看得见；包按原键重放，内容哈希不变 |
| 2 | 同上；包重新发布（幂等），存储权限已修故这次拉得到 |
| 3 | leader 任务与 worker 任务**一并**重发（这是把 leader 纳入遍历的直接原因）；新键绕开 DELIVERED 短路，而重放路径的短路**原样保留** |
| 4（新） | `rerun`：修好测试命令后让工作真的重来一遍，坏结论清掉、批次重新变成未完成 |

#### 落点与验证

`modules/task_orchestration/{application,contracts,domain,ports,infrastructure}.py`、
`api/round_dispatch.py`、`bootstrap/container.py`、`api/read_models/{service,sources}.py`、
`modules/collaboration/infrastructure.py`；前端 `components/RedispatchModal.tsx` +
`RoundsPanel.tsx` 派工卡。测试 32 例（20 服务级 + 12 端点级）+ 1 例读模型，全 mock。

**两条反证**（按验收纪律各做一次）：

1. **藏掉推导** → `dispatch_attempt=None` 走同一条重发路径，`MatrixDedupingMessenger`
   （模拟真 homeserver 的 txn 去重）吞掉它，房间零新增；换成 `attempt="press-1"` 立刻 +1。
   另有一例把两层守卫拆开单独证，避免把其中一层误当成另一层。
2. **重放仍不重发** → 以 `AdvanceExecutionPlan._resume` 的方式重跑 `assign`（同键），
   命中已有行、房间零新增。被"加版本"的那条守卫在重放路径上**原样成立**——它没有被削弱，
   是被绕开的。

---

## 附录 A：两条既有勘误（**已批准并同批实施**，2026-08-12）

两条都已落地：A.1 的三个自述计数进了 `GET /issues/{id}/repositories/{repo}/plan` 的
`dag` 块（`api/read_models/service.py`，测试
`tests/api/read_models/test_rooms.py::test_the_dag_counts_separate_the_two_reasons_an_edge_is_dropped`），
A.2 的两处文本已在 `docs/contracts/delivery-read-model-v0.2.md` **同批**改完
（§5.4 正文 + §6.1 表格行 + §7.2 裁决行）。
前端消费不受影响：三个字段是可选加法，既有渲染不读也不坏。

### A.1 §5.4 plan 端点增加自述字段（v0.2 §7.2 留的口子）

v0.2 §7.2 原文：「选日志而非响应字段是因为 plan 端点形状已对前端冻结；**如需自述字段
再加**。」

**新事实（本次核实）**：C-2 的图形化 DAG 面板已落地（`frontend/src/components/PlanDagPanel.tsx`），
其契约声明里自己写着——

> 「两端服务端保证已解析且都落在 `nodes` 内（解析不到或不在任何批次里的边被丢弃），
> 故连线时不必判空。**但丢弃只进服务端日志**：消费方不得自称这是完整依赖图。」
> （`frontend/src/api/contract.ts:254-255`）

即：消费方**已经知道**自己可能在画一张不完整的图，却没有任何字段能把这件事告诉用户。
口子该用了。

**已实施**：`GET /issues/{id}/repositories/{repo}/plan` 的 `dag` 块增加三个自述计数
（**向后兼容的加法**，前端 TS 接口加可选字段不破坏现有渲染）：

```json
{ "dag": { "...": "...",
  "unresolved_node_count": 1,          // 名字在 catalog 里查不到的节点数
  "dropped_edge_unresolved_count": 2,  // 端点名解析不到而丢弃的边数
  "dropped_edge_off_batch_count": 1 }} // 端点不在任何批次里而丢弃的边数
```

实现成本近零：三个数在读模型里**已经算出来了**——`unresolved_nodes`
（`api/read_models/service.py:1148`、`:1156`）与 `dropped`（`:1174`、`:1183`、`:1190`）
都是现成的列表，目前只喂给 `_logger.warning`（`:1165-1172`、`:1193-1202`）。

**为什么建议分两个而不是一个 `dropped_edge_count`**：本次核实发现边被丢弃有**两个原因**，
而 v0.2 §7.2 只写了一个——
(1) 名字 catalog 解析不到（`service.py:1180-1184`，即 §7.2 记录的那条）；
(2) **端点不在任何批次里**（`:1185-1191`）——节点来自 `execution_batches`、边来自
`task_dag`，两者本无约束使其一致，这条丢弃原因**契约里从未出现过**。
合成一个计数，用户仍然答不出「为什么少了边」；且 (2) 这条丢弃指向的是**数据本身不自洽**，
与 (1) 的「catalog 缺行」是完全不同的处置路径（前者要查规划产物，后者要补 catalog）。

同时建议：把丢弃原因 (2) 补写进 v0.2 §5.4 正文——它是既有实现的行为，契约至今未述。

### A.2 §6.1「DAG 节点丢失不留痕」条目**已过期**，是勘误不是补齐

v0.2 §6.1 backlog 表最后一行：「边有『丢弃 + warning』，节点只是 `repository_id: null`
且不记日志 → 补齐路径：节点侧补 warning，与 §7.2 口径对齐」。

**实现已经补过了**（应为 M-9/M-10 一批）：`api/read_models/service.py:1147-1172` 收集
`unresolved_nodes`，并在 `:1165-1172` 记 warning，含**条数与全部名字**、附
`issue_id`/`repository_id`，与边侧（`:1193-1202`）完全同一口径。

**已实施（纯文本勘正，实现零改动）**：

1. §6.1 该 backlog 行标为**已闭环**，注明落点 `service.py:1165`；
2. §5.4 正文那句「**已知缺口**：节点侧目前不像边那样记 warning，『N 个节点无法解析』
   不留痕，与 §7.2『不静默截断』的口径不齐」——**是当前的不实描述**，须同批删改。

⚠ 两处必须同批改。v0.2 §7.3 的教训原文已经统计过三次「同一事实出现在两节、只改一节」
的复发；只改 §6.1 不改 §5.4 正文，就是第四次。

（若 A.1 获准，§5.4 正文这段还要再改一次：节点侧不但记了 warning，还会有
`unresolved_node_count` 自述字段。两条勘误建议**合并为一次文本改动**。）
