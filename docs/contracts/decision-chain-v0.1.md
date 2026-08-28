# 决策链契约 v0.1（决策单 / 决策链 / 事件投影）

- 状态：**已裁决 · 生效**（Q1~Q6 按建议案，2026-08-28 主脑裁决；裁决前不进入实现）
- 修订：2026-08-28 Phase 1 实施期——§3.2 `TasksPlanned.upstream_step` 由 `confirmation` 更正为 `integration`（与 §2.1 五节点顺序、§6.1 trace 示例一致；原值为早期四节点草稿遗留）；Phase 3 实施期——新增 §6.4 追溯 API（`GET /api/v1/decision-chains/{project_id}`，Bearer token 鉴权，404/空链语义），§5 端口与 §6.1 输出形状不变；Phase 4 实施期——新增 §6.5 相似检索 API（`GET /api/v1/decision-chains/{project_id}/similar`）；§5.1 `find_similar_structural` 落地修订：项目为聚合单元（任一节点携带目标仓库即命中，折叠该项目最新决策单），仓库作用域为空时返回空列表（无法证明共享不声称相似，红线 7 延伸）；Phase 4b 实施期——§5.1 `same_repository_ids` 由 `tuple[UUID, ...]` 修订为 `tuple[str, ...]`（仓库 name/slug，与 `affected_repository_ids` 实际形态一致，注入方直接把候选名单原样传入）；§6.5 补充 pipeline 注入消费方（经 `DecisionHistoryPort`，增强非前置）；**L3（Phase 4b 后续）实施期——§6.5 新增 `mode=semantic` 语义检索（`query_text` + cosine Top-K + 项目折叠 + 同仓硬过滤，命中携带 `score`），§6.5.1 新增 embedding 刷新端点（`POST /api/v1/decision-chains/embeddings/refresh`，B8：批量异步、永不进写路径）；`DecisionHistoryPort.find_similar` 新增可选 `query_text` 钩子（结构性适配器接受并忽略，语义适配器用它生成查询向量）**
- 版本：0.1（首版）
- 基线：`docs/contracts/delivery-read-model-v0.4.md`（issue = Project、零新展示实体、幂等键 + 花名册派生主体的体例沿用）
- 依据：`docs/chenwenhui/决策链与历史上下文-功能板块文档-2026-08-28.md`（已确认方案）、
  `docs/chenwenhui/决策链与历史上下文-第一性原理对抗审查与落地方案-2026-08-28.md`（两遍审查收敛，本文是其 Phase 0 产物）
- 消费方：`decision_chain` 模块投影器（第一消费者）；追溯 API（Phase 3 已交付，§6.4）；相似决策检索（Phase 4 结构化检索已交付，L3 语义检索已交付，§6.5）；pipeline 注入（Phase 4b 已交付，经 `DecisionHistoryPort` 注入确认 prompt 参考证据段，L3 起可透传 `query_text` 走语义检索）
- 体例沿用：诚实数据（§1 事实逐一核实）、写端点幂等与审计、裁决记录列 Q 表（§9）、红线（§10）

---

## 0. 定位与边界

### 0.1 一句话

让 RepoMesh 的每个需求（`project_id`）从分类到 PR 的**五个决策点**以"决策单"形态沉淀为一条可追溯的链；
各 producer 模块在决策发生时发射事件，`decision_chain` 模块订阅事件、幂等投影出只读链视图。

### 0.2 明确不做（两遍审查已裁决）

1. **不新建 requirement 实体**——requirement 实体已存在（= issue = `project_id`，见 §1 E1）；
2. **决策链表不存完整 payload**——只存链字段 + 轻量摘要 + 源指针，payload 留在源模块（唯一事实源，不双写）；
3. **RAG / embedding 是可选加分层，不进写路径**——L3 已并入本契约（§6.5 `mode=semantic`、§6.5.1 刷新端点）；embedding 批量异步生成（B8），写决策单路径永不调用 embedding 服务；未配置 `REPOMESH_EMBEDDING_BASE_URL` 时语义检索整体禁用、端点诚实返回结构结果；
4. **AGE / 图数据库不涉及**——决策链是线性链，关系表足够；图的锚点是代码依赖图，触发条件满足才另立契约；
5. **不引入 semantica 重量级模块**（ontology / SPARQL / 推理引擎）；
6. **图谱（若建）只读投影**——真值源在本契约的链记录 + 各源模块，投影可随时重建。

---

## 1. 事实基础（起草前逐一核实）

### 1.1 锚点已存在（本文据此设计，不重造）

| # | 事实 | 证据位置 | 对契约的含义 |
|---|---|---|---|
| E1 | requirement 实体 = `project_id`；需求文本已在 `PlanSnapshot(plan_version=1, requirement_text=…)` | `issue_intake.py:153-168`、`infrastructure/models.py:62,76` | 链根直接用 `project_id`，零新实体 |
| E2 | `PlanSnapshotRecord` **无 `organization_id` 列** | `infrastructure/models.py:54-76` | 投影侧自持 L1（org），不依赖源表 |
| E3 | `TaskView` 已带 `organization_id + project_id + repository_id + parent_task_id` | `task_orchestration/contracts.py:121-137` | task 决策单零成本挂链 |
| E4 | `PrepareChangeSetCommand` 带 `organization_id + project_id`；`PullRequestObservationCommand` 只绑 `change_set_id + repository_id + pr_url`，**无 project_id** | `delivery/contracts.py:250-267` | PR 环节缺链，经 change_set 反查或补字段（Q3） |
| E5 | 消息类型确为 6 种，**已有 `DECISION`** | `collaboration/contracts.py:8-14` | 人工确认的过程证据载体已存在 |
| E6 | `ConfirmationSummary` 无 project_id/org；确认结果以 `classification_evidence_version` 指纹存在于 plan_snapshot | `confirmation.py:151-165`、`discovery_chain.py:554` | 分类/确认是字段贯通主战场 |
| E7 | 共享事件骨架 `EventEnvelope` 已存在；intake 已发 `IssueIntakeCreated`；**task_orchestration 未见事件发射痕迹** | `shared/events.py:17-30`、`issue_intake.py:205` | 事件投影路线可行，但需给 producers 补发射点（契约先行，单 PR 单模块） |
| E8 | 审批落点在 `POST /issues/{issue_id}/discovery/approval`，approval 块 `state ∈ {not_requested, approved, changes_requested}`，带 `evidence_version` 防过期审批 | `api/discovery_chain.py:443,473`、`application/discovery_chain.py:993,1074` | `ConfirmationDecided` 的触发点与并发守卫已存在 |

### 1.2 断链清单（本契约要补的）

| 环节 | 现状 | 本契约要求 |
|---|---|---|
| 分类（classification） | 结果在 plan_snapshot，无 project_id 语义贯通 | 发射 `ClassificationDecided`（含 project_id） |
| 确认（confirmation） | approval 块已持久化，无链事件 | 发射 `ConfirmationDecided`（含 adjustments 摘要） |
| 集成（integration） | 状态机已持久化 | 发射 `IntegrationDecided` |
| 任务（task） | 已带 project_id（E3），无事件 | 发射 `TasksPlanned` |
| PR（pr） | PR 观察无 project_id（E4），但 change_set 已可反查（E9） | 发射 `PullRequestObserved`（project_id 经 change_set 反查） |

---

## 2. 核心概念

### 2.1 决策链 = 1 根 + 5 决策 step

```
链根（requirement）= project_id（plan_version=1 快照自带 requirement_text）
  ├─ step=classification   三档分类（LLM）
  ├─ step=confirmation     人工确认（内嵌 adjustments 改档子记录，不设独立改档 step）
  ├─ step=integration      集成方案 / 批次
  ├─ step=task             任务拆分（每 task 一条，带 repository_id）
  └─ step=pr               PR 合入（带 change_set / pr_url）
```

- **改档不设独立 step**：adjustments 是确认决策单的内嵌子记录（同一 step 的 v2 版本），避免"确认"与"改档"两个链节歧义。

### 2.2 step 枚举与允许 status

| step | status 允许值 | 语义 |
|---|---|---|
| classification | `proposed` / `adjusted` | proposed=LLM 原判生效；adjusted=后续被确认环节改档覆盖（保留原判作对照） |
| confirmation | `confirmed` / `rejected` / `changes_requested` | 对应 approval.state |
| integration | `proposed` / `confirmed` | proposed=方案已生成；confirmed=已物化 |
| task | `proposed` / `confirmed` / `blocked` / `superseded` | 对应任务生命周期（creation/settled/blocked/redo 替代） |
| pr | `proposed` / `merged` / `closed` | 对应 PR 观察 |

### 2.3 链 vs payload 边界

决策单存三层，**payload 全文绝不入表**：

1. **链字段**：`decision_id / project_id / organization_id / step / version / status / actor / upstream_ref / 时间 / event_id`；
2. **摘要**：`payload_summary`（JSONB，决策结果值——三档结果、确认结论、task 标题、PR url，够展示）；
3. **指针**：`evidence_refs`（JSONB，指向源模块记录 id，按需深查）。

---

## 3. 事件契约（producers → decision_chain）

### 3.1 事件总表

| 事件类型 | 生产方模块 | 触发点 | 说明 |
|---|---|---|---|
| `ClassificationDecided` | repository_intelligence | 三档分类完成（confirmation 生成） | 含三档结果摘要 + 指纹 |
| `ConfirmationDecided` | repository_intelligence | 审批落定（E8 approval 块写入） | 含审批结论 + adjustments 摘要；改档追加以同 step 新事件发射（version+1） |
| `IntegrationDecided` | repository_intelligence / change_orchestration | 集成方案生成 / 物化 | 含批次摘要 |
| `TasksPlanned` | task_orchestration | 每个 task 创建 | 每条 task 一条事件（幂等自然） |
| `PullRequestObserved` | delivery | PR 观察落库 | 含 pr_url / change_set_id / repository_id |

事件承载统一为 `repomesh.shared.events.EventEnvelope`（E7），附加约束：

- `organization_id`、`project_id` **必填**（E2 投影自持 L1 的依据）；唯一例外见 §7 回填；
- `payload` 必须含 `occurred_at`（业务发生时间，生产者填写）——双时态的来源；
- `event_type` 是字符串，**事件级 schema 版本**以 `payload["schema_version"]` 表达（本次全为 1）。

### 3.2 payload 形状（契约形状，服务端唯一生产方）

```jsonc
// ClassificationDecided
{
  "schema_version": 1,
  "occurred_at": "ISO-8601",
  "classification": {
    "required": ["repo-a"], "maybe": [], "excluded": ["repo-b"],
    "effective_tiers": {"repo-a": "REQUIRED", "repo-b": "EXCLUDED"},
    "evidence_version": "sha256:...",           // 结果证据（运行时计算指纹）
    "supplemented_repository_ids": ["repo-c"]   // 图预补充的仓
  },
  "affected_repository_ids": ["repo-a", "repo-c"]   // 时点快照：决策当时的 final_repos
}

// ConfirmationDecided
{
  "schema_version": 1,
  "occurred_at": "ISO-8601",
  "approval": { "state": "approved", "decided_by_agent_id": "uuid", "reason": "…" },
  "adjustments": [ { "repository": "repo-b", "from": "EXCLUDED", "to": "MAYBE", "by_agent_id": "uuid", "at": "…" } ],
  "evidence_version": "sha256:...",
  "affected_repository_ids": ["repo-a", "repo-b", "repo-c"]
}

// IntegrationDecided
{
  "schema_version": 1,
  "occurred_at": "ISO-8601",
  "execution_batches": [ { "index": 0, "repository_ids": ["repo-a", "repo-c"] } ],
  "contracts": ["C1"],                       // 契约名摘要，不拷全文
  "affected_repository_ids": ["repo-a", "repo-c"]
}

// TasksPlanned
{
  "schema_version": 1,
  "occurred_at": "ISO-8601",
  "task": { "task_id": "uuid", "repository_id": "uuid", "title": "…", "parent_task_id": "uuid|null" },
  "upstream_step": "integration"            // 链：task 挂在 integration 之后（§2.1 五节点顺序）
}

// PullRequestObserved
{
  "schema_version": 1,
  "occurred_at": "ISO-8601",
  "change_set_id": "uuid",
  "repository_id": "uuid",
  "pull_request_number": 42,
  "pull_request_url": "…",
  "task_ids": ["uuid"]                        // 关联链：PR → task（经 change_set 反查，Q3 已裁决）
}
```

### 3.3 幂等与顺序

- 每个事件 `correlation_id` 唯一；投影按 `event_id`（envelope 自身 id）幂等；
- 同 step 多事件（改档、redo）按 `occurred_at` 顺序投影，version 递增，**不覆盖**；
- 投影器必须容忍乱序（event 迟到）：以 `(project_id, step, version)` 唯一约束兜底，缺失前置节点时节点照常落库、链查询时按 version 拼接（Q5）。

---

## 4. 决策链表（decision_chain 模块自持 schema）

### 4.1 `decision_chain_nodes`

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `decision_id` | UUID | PK | 投影器 `new_id()` 生成 |
| `event_id` | UUID | **UNIQUE** | 幂等：重放不重复 |
| `project_id` | UUID | index | 链根（= requirement，E1） |
| `organization_id` | UUID | index | L1 命名空间（E2） |
| `step` | enum | | §2.2 五值 |
| `version` | int | | 同 step 多事件；`(project_id, step, version)` UNIQUE |
| `status` | enum | | §2.2 允许值 |
| `actor` | enum+id | | `{type: llm|human|service, agent_id?}`；`service` 覆盖 PR 观测节点（SERVICE envelope，§3.2） |
| `upstream_ref` | UUID | null | 父决策 id（链的串联）；链根节点此列为空 |
| `evidence_refs` | JSONB | | `{ "result": [...], "process": [...] }`（§6.2） |
| `payload_summary` | JSONB | | 决策结果摘要（§2.3） |
| `affected_repository_ids` | JSONB | | 时点快照（已拍板决策③），**不事后改写** |
| `business_time` | timestamptz | | 事件 `occurred_at`（双时态） |
| `recorded_at` | timestamptz | | 投影落库时间 |
| `source` | enum | `event` / `backfill` / `legacy` | §7 |
| `event_type` | varchar | | 冗余的事件类型（幂等审计/调试，非业务字段） |

链根不单独建表：`project_id` 指向的 plan_version=1 快照即链根实体，trace 时经 `repository_intelligence` 契约端口读取 `requirement_text`。

### 4.2 版本化规则

- 同 step 首事件 version=1；后续同 step 事件 version 递增；
- **不覆盖**：classification 节点保留 LLM 原判（status=`proposed`），改档后的生效值只出现在 confirmation 节点的 `payload_summary.effective_tiers`（由分类有效档 + approval `adjustments` 重建）；
- confirmation 节点状态按 approval 映射：`approved→confirmed`、`rejected→rejected`、`changes_requested→changes_requested`；classification / integration / task / pr 节点以 `proposed` 落库；
- 查询展示按 `(project_id, step, version)` 取最高版本为当前值，历史版本可展开。

---

## 5. 端口（decision_chain 模块 ports）

### 5.1 `DecisionChainStore` —— 决策单存储（读侧唯一写入者）

```python
class DecisionChainStore(Protocol):
    async def append(self, node: DecisionNodeInput) -> DecisionNodeView: ...
    async def latest_node(self, project_id, step) -> DecisionNodeView | None: ...
    async def trace(self, *, organization_id, project_id) -> DecisionChainNodes: ...
    async def find_similar_structural(
        self, *, organization_id, project_id,
        same_repository_ids: tuple[str, ...] = (),
    ) -> list[DecisionChainSummaryView]: ...
```

- `append(node: DecisionNodeInput)`：投影器把 `EventEnvelope` 映射成 `DecisionNodeInput`（status/actor/payload_summary/evidence_refs 在应用层决定）后交给存储；存储负责幂等（`event_id` 唯一约束，重放返回既有行）、`(project_id, step)` 内版本递增、`upstream_ref` 串链（hint → 前一步最新节点 → NULL，见 §4.2）；回填复用同一端口（`event_id` 用确定性派生，见 §7）；
- `latest_node`：投影器重建 confirmation `effective_tiers` 时读取 classification 最新档；
- `trace`：输出 §6.1 的节点集（按 `business_time` 升序）+ legacy gaps；
- `find_similar_structural`：结构化相似检索（同仓库），Phase 4 的 RAG 之前先落这一档（Phase 2 已随存储实现）。`same_repository_ids` 携带仓库 **name/slug**（决策时刻 `affected_repository_ids` 里存的就是名字，不是内部 UUID），保证调用方（Phase 4b 注入）能直接把候选仓库名单原样传入。

### 5.2 投影与装配端口

```python
class DecisionEventSource(Protocol):       # 事件订阅：读 platform.audit_events，排除已投影 event_id
    async def list_chain_events(self, limit: int = 200) -> list[EventEnvelope]: ...

class RequirementReader(Protocol):          # §6.1 链根文本，组合根处适配 PlanSnapshotStore
    async def get_requirement(self, project_id: UUID) -> RequirementView | None: ...
```

- `DecisionChainProjectionService.drain(limit)`：增量投影，返回本次新增节点数；跳过无法证明 org/project 身份的事件（红线 7）；
- `DecisionChainTraceService.trace(organization_id, project_id)`：把存储的节点集与需求根装配成 §6.1 的 `DecisionChainView`。

---

## 6. 追溯语义

### 6.1 `trace` 输出形状

```jsonc
{
  "project_id": "uuid",
  "organization_id": "uuid",
  "requirement": { "text": "…", "plan_version": 1, "snapshot_id": "uuid" },  // 经 producer 契约端口读
  "nodes": [                    // 按 business_time 升序
    { "step": "classification", "version": 1, "status": "proposed",
      "actor": { "type": "llm", "agent_id": "uuid" },
      "payload_summary": { "effective_tiers": {...} },
      "evidence_refs": { "result": ["sha256:..."], "process": [] },
      "affected_repository_ids": [...],
      "business_time": "…", "recorded_at": "…", "source": "event" },
    { "step": "confirmation", "version": 1, "status": "confirmed", ... },
    { "step": "integration", "version": 1, ... },
    { "step": "task", "version": 1, "payload_summary": { "title": "…", "repository_id": "uuid" }, ... },
    { "step": "pr", "version": 1, "actor": { "type": "service" },
      "payload_summary": { "pull_request_url": "…" }, ... }
  ],
  "legacy_gaps": ["confirmation"]   // §6.3
}
```

### 6.2 证据双指针

`evidence_refs` 固定两键，**分开存，不混淆**：

| 键 | 内容 | 来源 |
|---|---|---|
| `result` | 结果证据：`classification_evidence_version` 指纹、扫描产物 id | repository_intelligence |
| `process` | 过程证据：Room 消息 id（`CollaborationMessageRecord`，E5/E8），优先 `DECISION` 类型消息 | collaboration |

### 6.3 legacy 标记

- `source=legacy`：存量数据无法证明与 project_id 的归属（§7），trace 时不伪造；
- `trace().legacy_gaps` 列出存在 legacy 断点的 step，消费方（审计界面）**必须展示"此处证据缺失"**，不得装作完整。

### 6.4 追溯 API（Phase 3 实施期新增）

审计走查的单一入口，挂在 `decision_chain` 模块的 `api/` 包，由 `api_router` 以 `/api/v1` 前缀汇聚：

```
GET /api/v1/decision-chains/{project_id}?organization_id={org}
```

- 响应即 §6.1 的 `DecisionChainView`（`requirement` + 按 `business_time` 升序的 `nodes` + `legacy_gaps`），字段与本节一致，无二次变形；
- 鉴权：**Bearer `agent_action_token`**，与 observability console 同款（缺配 503 失败关闭 / 无效 401）。追溯暴露内部决策出处、payload 摘要与 actor id，是特权审计消费方，不走契约的开放读（`/repositories`、`/plans/*`）；
- `organization_id` 必填查询参数：trace 是 L1 命名空间内的读，调用方必须显式声明范围，链不猜测归属（红线 7）；
- 404 仅当该 org 作用域内"既无节点也无需求根"；有需求根但投影未跑时返回 200 空链——审计界面必须能展示"暂无证据"，而不是假装项目不存在；
- `legacy_gaps` 非空时，消费方必须展示"此处证据缺失"（§6.3）。

### 6.5 相似检索 API（Phase 4 实施期新增；L3 实施期扩为双模式）

```
GET /api/v1/decision-chains/{project_id}/similar
    ?organization_id={org}&top_k=5&mode=structural|semantic&query_text=...
```

- 响应：`{project_id, organization_id, mode, hits: [SimilarDecisionView...]}`——`mode` 回显本次实际执行的检索模式；`SimilarDecisionView` 在 `DecisionChainSummaryView` 形状之上增加 `score: float | null`（语义模式为 cosine 相似度，结构模式为 `null`）；
- **`mode` 参数（L3）**：默认 `structural`；`semantic` 时先走语义检索——用 `query_text` 生成查询向量，在同仓候选（`same_repository_ids` 硬过滤）内做 cosine Top-K，命中携带 `score`。**fail-safe（红线 7 诚实延伸）**：embedding 未配置 / `query_text` 缺失 / embedding 调用失败 / 语义空命中 → 自动回退 `structural` 并如实回显 `mode="structural"`，绝不报错、绝不阻塞分类消费方；
- 语义（Q6 裁决：同仓库 + 最近 N 条）：**项目是聚合单元**——目标项目仓库集合（显式 `same_repository_ids` 优先，否则取目标项目自身链节点）与另一项目任一节点的 `affected_repository_ids` 有交集即命中，该项目折叠出最新节点（`business_time` / `version` 最大者）；两者皆空 → 空 `hits`——无法证明共享不声称相似（红线 7 的诚实延伸），绝不回退成"org 内全部"；`same_repository_ids` 携带仓库 **name/slug**（与 `affected_repository_ids` 同形态）；
- 鉴权与 `organization_id` 必填：与 §6.4 同款（Bearer `agent_action_token`，缺配 503 / 无效 401；L1 显式声明）；
- 空 `hits` 是合法 200：无相似历史是诚实数据；检索不背书目标项目存在性（那是 §6.4 trace 的职责）；
- 消费方：Phase 4 走查界面直接读本端点；pipeline 注入（Phase 4b 已交付）由 `repository_intelligence` 经 `DecisionHistoryPort` 消费本检索——把当前候选仓库名单（slugs）原样传入 `same_repository_ids`，渲染成确认 prompt 的参考证据段，**增强而非前置**：检索失败 → 无历史 → 分类照常；L3 起 `DecisionHistoryPort.find_similar` 接受可选 `query_text`，`DecisionHistoryVectorStore` 混合适配器有 `query_text` 时优先语义、缺失时回退结构（B8 查询向量读时生成）；
- 时间窗（"最近 N 条"的窗口边界）与同 step/status 组合过滤留作后续调优。

### 6.5.1 embedding 刷新端点（L3 实施期新增）

```
POST /api/v1/decision-chains/embeddings/refresh
```

- 响应：`{refreshed: int}`——本次批量回填的决策单条数；
- 语义（B8）：`decision_embeddings` 独立存储，**写决策单路径永不调用 embedding**；本端点由运维/定时任务显式触发，幂等（已嵌入节点跳过，重复调用第二次恒为 `{refreshed: 0}`）；
- 未配置 `REPOMESH_EMBEDDING_BASE_URL` 时服务为 `None`，端点诚实返回 `{refreshed: 0}`（no-op，不报错）；
- 鉴权与 §6.4 同款（Bearer `agent_action_token`，缺配 503 / 无效 401）。

---

## 7. 回填规则

| 存量数据 | project_id 可推导？ | 处置 | event_id 派生 |
|---|---|---|---|
| task | ✅ 自带（E3） | 回填，source=`backfill` | `uuid5(ns, f"{org}:{project_id}:task:{task_id}")` |
| PR / change_set | ✅ 经 change_set 反查（E9） | 回填 | `uuid5(ns, f"{org}:{project_id}:pr:{change_set_id}:{pr_number}")` |
| Room 消息 | ✅ 消息带 task_id / correlation_id | 回填 evidence 指针 | 随所属决策单 |
| classification / confirmation 存量 | ❌ 无 project_id 且不可推导（E6） | **不回填**，仅标记存在 | — |

**第一性原理（不猜）**：无法证明归属的环节宁可缺链，不可伪造——伪造的链在审计场景比没有链更危险。回填只对"修复后新产生的数据"保证链完整；存量链完整性在 §9 Q4 裁决展示策略。

---

## 8. 与三项已拍板决策的映射

| 已拍板决策 | 本文落实 |
|---|---|
| ① org_url 只做入口，内部用 organization_id | L1 恒为 `organization_id`（§4.1）；org_url 规范化只在 intake 边界做一次，决策链全链路不碰 org_url |
| ② requirement 实体化 id 随主流程补 | **修正为字段贯通**：requirement 实体已存在（E1），落地动作是 §1.2 的五个事件携带 `project_id`，不建 requirement 表 |
| ③ 影响面时点快照 | `affected_repository_ids` 在事件发生当时定格（payload 必带，§3.2），不事后改写 |

---

## 9. 裁决记录（2026-08-28，Q1~Q6 全部按建议案，契约随之转生效）

| # | 问题 | 裁决 | 落地含义 |
|---|---|---|---|
| Q1 | 分类事件何时发 | **分类完成即发**（proposed），审批再发 `ConfirmationDecided` | 链区分"模型原判"与"人工批准"两个节点，事件量 ×2（规模小无压力） |
| Q2 | 任务事件粒度 | **每条 task 一条** | 幂等自然、直接挂 repository_id、与 task 生命周期一一对应 |
| Q3 | PR 怎么找 project_id | **经 change_set 反查**（E9 已证实 ChangeSetRecord 持久化 org+project，零新增列） | Phase 1 中 delivery 模块**只补事件发射**，不改契约命令 |
| Q4 | 存量链展示 | **标灰展示**（legacy_gaps 如实呈现） | 审计界面必须展示"此处证据缺失"，不得装作完整 |
| Q5 | 投影器形态 | **与 API 同进程**（启动时注册事件处理器） | 事件吞吐/独立扩展需求出现再拆独立 worker |
| Q6 | 相似检索定义 | **同仓库 + 最近 N 条（时间窗）** 起步 | Phase 4 先跑通再调优（同 step/status 组合留作后续） |

---

## 10. 红线（违反即返工）

1. **决策链不存图数据库**——线性链用表；图是给网用的；
2. **决策单只存链+摘要+指针**——payload 全文入表即双写，违反"同一事实单一拼写"；
3. **投影只读，源模块是事实源**——任何"写回源模块以修正链"的路径都是删证据；
4. **影响面是时点快照**——不事后改写（已拍板决策③）；
5. **证据指 RepoMesh 侧持久化记录**——结果证据（指纹）与过程证据（消息镜像）分开存；
6. **跨模块只走契约/事件**——决策链模块不直读他模块 PG schema（AGENTS.md）；
7. **不猜**——无法证明归属的存量数据标 legacy，不伪造链。
