# 交付读模型契约 v0.5（issue 归档）

- 状态：**已实施 · 生效**（2026-09-04，issue 列表归档功能）
- 版本：0.5（**增量**：v0.1/v0.2/v0.3/v0.4 全文继续有效，本文件只定义 issue 归档的
  一个写端点、`GET /issues` 的一个查询参数与两个字段，以及归档的语义边界）
- 基线：`docs/contracts/delivery-read-model-v0.2.md`（§2 issue 列表 / §2.5 计数）
- 先例：交付归档（v0.1 §4 归档端点 + `delivery_archives` 表 + `include_archived`）
- 事实源与 Owner：`repository_intelligence`（`issue_archives` 表唯一生产方）；
  读模型（`api/read_models`）负责过滤与字段投影
- 消费方：`frontend/` issue 列表页（行内归档按钮 + 「已归档」开关）

---

## 0. 定位与边界

v0.2 §0 语义等式不变（issue = Project、零新展示实体）。归档是**列表卫生**，不是
第九相（`phase` 枚举不动）、不是状态改写（`state` 派生规则不动），更不是删除：
**所有与 issue 关联的事实——快照、决策链节点、checkpoint 决策、交付记录、审计——
一律原地保留**。审计轨迹只增加一条 `IssueArchived` 事件，从不减少行。

## 1. 写端点：`POST /api/v1/issues/{issue_id}/archive`

- 鉴权：`Authorization: Bearer <agent_action_token>`（与 `POST /issues` 同源）。
- 语义：写入墓碑行 `(issue_id, archived_at)`；**幂等**——重复调用返回**已有的**
  归档视图（同一 `archived_at`），不产生第二条审计事件。
- 返回体：`{"issue_id": "...", "archived_at": "<ISO8601>"}`。
- 拒绝：
  - `404`：没有任何 PlanSnapshot 佐证该 issue（v0.2 §0——issue 即首份快照）。
  - `409`：该 issue 存在 `IN_PROGRESS` 的执行轮次。归档进行中的交付会把活跃工作
    从拥有它的控制台上藏起来，与交付归档（v0.1）对 in-progress 的拒绝同一条规则。
- 审计：`IssueArchived`（`aggregate_type="Project"`，组织归属取自开票 agent 所在
  工作区，不从调用方猜——v0.3 §6 S-4 同款纪律）。

## 2. 读端点：`GET /api/v1/issues`

- 新查询参数 `include_archived: bool = false`。
- **默认视图**（`include_archived=false`）：已归档 issue 不出现在 `issues` 里，
  且 `open_count` / `closed_count` **同样排除**它们——两个标签回答的是
  「还有什么需要人管」，已归档的 issue 不再需要。
- `include_archived=true`：已归档 issue 按正常排序返回，计数为全量真实值。
- 条目新增两个字段（永远在场，客户端不分支）：
  - `archived: boolean`
  - `archived_at: string | null`（未归档恒为 `null`）
- `GET /api/v1/issues/{issue_id}` 与 `issue_summary`（含 `POST /issues` 响应）同布
  这两个字段：归档不是消失，按 id 直达详情照常可读，决策链
  （`GET /decision-chains/{project_id}`）照常可读。

## 3. 前端行为

- issue 列表页提供「已归档」开关：关= 默认视图；开= `include_archived=true`，
  行内渲染「已归档」徽标（归档行不再提供归档按钮；v1 无「取消归档」——与交付
  归档一致，误归档可按 issue id 直达详情继续工作，后续有需要再加恢复端点）。
- 行内「归档」按钮：确认后调用 §1 端点，成功后刷新当前列表；`409` 的 detail
  原样呈现（含「进行中」措辞），不归并成一句「失败」。
- 回放模式（replay 夹具）不提供归档入口——夹具世界不可篡改（v0.3 §1 同款红线）。

## 4. 归档与决策检索（decision_chain 联动）

归档的「移出」不止于 issue 列表：**已归档 issue 的决策单同时退出历史决策的
检索与推荐语料**，避免废置需求的决策继续作为"相似历史"推荐给新需求。

- 收口点：`decision_chain` 的两个检索服务（结构相似 `find_similar`、语义搜索
  `semantic-search`）在候选阶段过滤掉墓碑项目的决策单。发现链的历史决策证据
  （`DecisionHistoryPort` 适配器）包着结构相似服务，随之一起排除。
- 实现边界：`decision_chain` 通过新增端口 ``ArchivedIssueReader`` 读墓碑集合，
  由组合根用 ``repository_intelligence`` 的 ``issue_archives`` 存储适配——
  模块间不越 schema（AGENTS.md 依赖规则）。端口缺省（None）时退化为
  "无墓碑"，与归档功能之前的行为一致。
- **定向审计不受影响**：按 issue id 直达的链读取（`GET /decision-chains/{project_id}`）
  仍返回完整决策链——那条路就是审计本身。数据一行不删，只是不再主动推荐。
- 语义向量保留不删：若未来引入「取消归档」，向量无需重算。
