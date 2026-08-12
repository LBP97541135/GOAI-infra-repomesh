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

### 3.2 步进器「走到哪」的判定（唯一实现在读模型）

GUI 步进器（1..4）与管线步（Step 0..4）的映射，本契约以**管线编号**描述行为、
以 **GUI 编号**作 `step` 字段取值：

| GUI 步 | 管线步 | 产生它的写触发 |
| --- | --- | --- |
| 1 需求分析 | Step 0 | `POST …/discovery/analysis` |
| 2 候选评分 | Step 1 | `POST …/discovery/candidates` |
| 3 分档审批 | Step 2（生成三档）**+** 人工审批 | `POST …/discovery/classification` 与 `POST …/discovery/approval` |
| 4 生成计划 | Step 3 | `POST …/discovery/plan` |
| （步进器外）物化开工 | Step 4 | 批次 C-3，不在本文 |

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
