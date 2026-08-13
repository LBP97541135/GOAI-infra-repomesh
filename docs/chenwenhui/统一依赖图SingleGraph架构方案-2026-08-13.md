# 统一依赖图（Single Graph）架构方案

> 版本：v1（初稿，待评审）
> 日期：2026-08-13
> 状态：方案设计
> 取代：《graph_edges 边索引与 DAG 动态更新补齐方案-2026-08-13.md》
> 相关：《DAG动态更新方案-2026-08-08.md》
> 模块：repository_intelligence × change_orchestration × task_orchestration × web

---

## 〇、结论先行

**问题**：RepoMesh 中"仓库依赖关系"这一件事被实现成了**四份互不相认的副本**——① 扫描结构图（`DependencyGraphService`）、② LLM 独立生成的 `task_dag.depends_on`、③ LLM 独立生成的 `contracts` 契约边、④ 存储层 `graph_edges` 死列。各阶段各拿一份用：批次用①、前端展示用①+②拼、replan 影响分析用①、契约落库用③。副本之间零校验，导致 **"决策的图 ≠ 执行的图 ≠ 展示的图"**。

**方案**：收敛为**一张统一依赖图（Unified Dependency Graph）**——唯一实体、版本化进快照、所有消费方读它的投影（`execution_batches` / `contracts` / `task_dag` / `diff`）。图分两个图层：**世界层**（v0，扫描候选边）与**方案层**（v1…vn，确认边 + 语义，版本化）。

**依据（第一性原理）**：任何被多个消费者使用的状态，必须有单一事实来源，否则消费者必然不一致。"可观测控制平面"的第一性是"任何时刻都能回答发生了什么、为什么"——决策、执行、展示必须共享同一份图数据。

**落地**：5 个 PR，每步单一 owner 模块、可独立验收、可独立上线。

---

## 一、现状：图被拆成了四份副本

| 副本 | 载体 | 写入方 | 消费者 | 问题 |
|---|---|---|---|---|
| ① 结构图 | `DependencyGraphService`（内存） | scan 提取 `AutoCard.deps` 名字匹配 | discovery 信号、confirmation 上下文、integration 拓扑、replan 影响分析 | 不落库、不版本化；`possible` 误匹配边参与拓扑 |
| ② 任务依赖副本 | `task_dag.depends_on` | LLM 独立生成 | 前端依赖标签、supersede 语义 | 与①零校验，可能不一致 |
| ③ 契约边副本 | `contracts`（producer→consumer） | LLM 独立生成 | materialize 落库、Worker 约束、handoff | 与①无映射 |
| ④ 物化列 | `graph_edges` | 硬编码 `[]` | 无 | 死列 |

**乱象的具体表现**：

1. **前端一个组件拼两份数据**：`PlanFlowTimeline` 的批次泳道来自①的拓扑、依赖标签来自②的 `depends_on`——两份不一致时展示自相矛盾（"流程图解析不出依赖边"的深层原因不是解析问题，是来源本身有两份）；
2. **replan 影响分析用世界状态回答方案层问题**：用①（扫描候选，含 possible 边）决定 supersede——拿"怀疑"当"判决"；
3. **执行批次/PR 合并顺序与计划语义脱节**：批次来自①拓扑，而"LLM 认为 B 依赖 A"写在②——**该串行的被并行，PR 可能按错误顺序合并**（详见第七章）；
4. **快照名不副实**：④恒空，无版本历史、无 diff、无审批，"完整 DAG 可查询可展示"落空。

---

## 二、已实现与缺口（对照《DAG 动态更新方案-2026-08-08》）

### 2.1 已实现（主体闭环，即"以为已完成"的来源）

| # | 条目 | 位置 |
|---|---|---|
| ✓1 | `POST /api/v1/bridge/replan` 端点 | `repository_intelligence/api/router.py:384` |
| ✓2 | 影响分析（反向依赖） | `change_orchestration/application.py:550` |
| ✓3 | 反向依赖查询服务 | `repository_intelligence/application/dependency_graph.py:60` |
| ✓4 | 局部重规划（LLM + 稳定性约束） | `application.py:579` |
| ✓5 | supersede 旧任务 | `application.py:629` + task_orchestration |
| ✓6 | 启动新执行批次 | `application.py:688` |
| ✓7 | handoff 文档重生成 | `application.py:484-514` |
| ✓8 | `TaskStatus.SUPERSEDED` | `task_orchestration/contracts.py:14` |
| ✓9 | `TaskReport.plan_version` / `plan_revision_needed` | `task_orchestration/contracts.py:135-136` |
| ✓10 | **PR 合并批次门禁（delivery-gated mode）** | `task_orchestration/application.py:614-649` |

### 2.2 缺口

| # | 8-08 方案要求 | 现状 | 影响 |
|---|---|---|---|
| G1 | Step 4e 保存 v2 快照 | ❌ replan 从不调 `snapshots.save()` | 无版本历史、无法审计/回放 |
| G2 | 质疑6：API 返回 v1↔v2 diff + 人类审批 | ❌ 无 diff、无审批 checkpoint | 重规划不可审查 |
| G3 | 影响分析基于**计划** DAG | ⚠️ 用的是**世界层**结构图 | 候选边当判决依据，漏判/误伤 |
| G4 | `graph_edges` 承载真实边 | ❌ 恒 `[]` | 快照"完整 DAG"名不副实 |
| G5 | 每个 Task 标记 `plan_version` | ❌ 仅 TaskReport 携带 | 任务粒度无法核对版本 |
| G6 | Step 6 推模式中断通知 | ⚠️ 端点无 CollaborationGateway 推送 | 仅剩拉模式兜底（第二阶段） |

---

## 三、第一性原理：为什么必须是一张图

RepoMesh 的本质是**多仓库交付控制平面**，它所有核心问题都围绕同一个概念——仓库间的依赖关系：

| 系统要回答的问题 | 依赖图的角色 |
|---|---|
| 哪些仓库要改？ | 图的候选边（发现） |
| 按什么顺序改？ | 图的拓扑（调度） |
| 冲突了怎么重排？ | 图的反向依赖 + 图的新版本（重规划） |
| 现在进行到哪？ | 图的节点状态（可观测） |

**推论**：

1. **单一事实来源**：任何被多消费者使用的状态必须唯一，否则消费者必然不一致——这就是现在"乱"的根源（同一张图存四份没人对账的副本）；
2. **可观测控制平面的第一性**："任何时刻能回答发生了什么、为什么"，要求决策、执行、展示共享同一份图数据；
3. **单图 ≠ 简单化**：多图方案需要"自洽校验"（对账系统）这种补丁；单图方案只需要投影系统（派生）。**副本被消灭，补丁自然不需要。**

---

## 四、对抗性审查（对单图方案本身的攻击与回应）

**Q1：契约边和任务边语义不同，硬合并会丢语义吗？**
A：不合并语义，合并载体。边 = `(from, to)` + 属性（`status` / `interface` / `agreement`）。契约是边的**元数据**，执行顺序是边的**拓扑投影**。且实践中"契约影响 ⇒ 必须串行"恒成立（B 消费 A 的接口，A 变更，B 必须等 A 验证兼容性），一条边承载两个语义不会冲突。

**Q2：AutoCard 名字匹配的低精度候选边（possible）会不会污染拓扑？**
A：会。所以边必须有 `status` 属性，**只有 confirmed 边参与拓扑**；candidate 边只服务 discovery 候选。单图 ≠ 所有边等权——`dependency_graph.py:86` 目前把所有边（含 possible）排进批次，这是要修的点。

**Q3：扫描常驻图（v0）和方案快照（v1）是不是又回到"两张图"？**
A：不是两张图，是**同一张图实体的两个图层**：世界层（常驻、候选、可被扫描刷新）与方案层（版本化、confirmed、进快照）。分层的依据是**确认状态**（candidate vs confirmed），不是两个对象。

**Q4：快照里存"图"还是存投影？**
A：**存图，投影写时物化**。`graph_edges` 列升级为完整图（nodes+edges+属性），`task_dag`/`contracts`/`execution_batches` 三列保留但降级为派生投影。存储契约不变（消费方不破），但"谁是真值"彻底归一。

**Q5：replan 影响分析用哪张图？**
A：用**最新方案层快照（v1）**做反向依赖——只有方案内的任务会被 supersede，候选集从宽（+世界层 possible 提示）、中断集精确（confirmed 边）。新发现的方案外依赖触发"候选确认"流程（可能拉新仓库进 v2），不直接中断。

**Q6：前端进度展示凭什么保证和图一致？**
A：前端只消费"图的投影"：批次=confirmed 边拓扑、依赖标签=边、契约徽章=边元数据、节点状态=任务进度。**删除现在"批次来自①、依赖来自②"的拼凑**，单源后自然一致。

**Q7：组织拓扑（repository_teams）也是图，要不要并入？**
A：不要。那是**组织归属**（谁负责执行），不是**依赖关系**（谁依赖谁）。图只管依赖，组织归 topology 模块。这是唯一允许存在的第二张图，跨模块、语义正交。

**Q8：单图比"多图+校验"复杂还是简单？**
A：严格更简单。多图需要自洽校验器、图源切换、两套边索引；单图只需要**一个图实体、一个拓扑算法、一个 diff、一个反向依赖查询**。复杂性从"对账系统"降为"投影系统"。

---

## 五、统一依赖图：两个图层

| 维度 | **世界层（v0）** | **方案层（v1…vn）** |
|---|---|---|
| 内容 | 扫描出的**候选边**（AutoCard.deps 名字匹配） | 确认激活 + LLM 补充的**确认边** + 语义（instruction/interface/agreement） |
| 边状态 | `candidate`（confirmed/possible 都是候选） | `confirmed` |
| 生命周期 | 常驻、可被扫描刷新、**不版本化** | **版本化**、随 plan_version 进快照 |
| 覆盖 | 全部仓库 | 方案确认集 |
| 用途 | 发现候选、TM 确认上下文 | 执行批次/顺序、契约、影响分析、diff、前端展示 |

**判定标准（一句话）**：边被"确认"就进方案层，否则留在世界层。

**演进管线（一条边的一生）**：

```
扫描        AutoCard.deps 名字匹配 → 世界层 candidate 边（status=candidate）
  ↓
确认        TM 激活/剔除节点；声明补边（来源=tm）
  ↓
集成        LLM 语义写回图：instruction 挂节点、contract 挂边(interface/agreement)；
            LLM 发现的图上没有的依赖 → 新增边（来源=llm, status=confirmed）
  ↓
投影        confirmed 边拓扑 → execution_batches；边元数据 → contracts；
            节点语义 → task_dag；全部写入时物化，读时从图重算
  ↓
执行        图 → 前端进度展示（单一来源）；批次门禁 → PR 合并顺序
  ↓
重规划      图的反向依赖 → 影响分析 → 局部重规划 → 图 v2 快照 → diff/审批
```

---

## 六、PR 合并顺序：交付门禁的现状与致命细节

### 6.1 现状：批次门禁已实现（✓10）

```
图拓扑 → execution_batches（plan_integration.py:524,537）
  → 任务按批次执行（task_orchestration）
  → 批次全部成功 → on_batch_deliver 创建/undraft PR（application.py:624）
  → _delivery_gate：当前批次所有 PR 已 merged 才放行（application.py:638）
  → merge 事件(webhook/15s 重放) → reconsider_task 驱动推进（application.py:590）
  → 自动合并开关 delivery_auto_enabled（container.py:362）
```

**合并顺序 = 批次顺序 = 图的拓扑顺序**：同批次内（可并行仓库）PR 并发合并，跨批次强制串行。这块实现完整（端口解耦、幂等、重放），**不需要改机制，只需换图源**。

### 6.2 致命细节：批次的图源会坑了合并顺序

1. **possible 边参与拓扑**（`dependency_graph.py:86` 不过滤 confidence）→ 误匹配边强行拉长批次 → 过度串行；
2. **LLM 补充的依赖进不了批次**（`plan_integration.py:537`：批次永远来自结构图）→ 计划认为 B 依赖 A 但被排进同一批 → **PR 并发合并，B 可能基于 A 的旧接口合并 → 交付破坏**。

第 2 条是"两套副本不一致"在**交付**环节的爆雷点。

### 6.3 修法

**批次只由方案层 confirmed 边生成**（含 LLM 补充边、滤掉 possible）：

```
方案层图（v1）confirmed 边
  → 唯一拓扑算法 → execution_batches
  → 执行 + PR 合并门禁（复用现有 delivery-gated，机制不改）
```

合并顺序从此 = 方案层图拓扑 = 执行顺序 = 展示顺序 = 影响分析图源，**五者同一份数据**。

---

## 七、图契约定义（先定契约再实现，符合 AGENTS.md）

新增 `repository_intelligence/contracts/graph.py`（`contracts.py` 升级为 `contracts/` 包，`__init__.py` 再导出；`RepositorySelected` 移入 `contracts/repository.py`，无破坏——当前无任何导入方）：

```python
class GraphNode(BaseModel):
    """方案层节点：仓库 + 本次方案的语义。"""
    repository: str
    instruction: str | None = None          # LLM 补充
    tests: list[str] = []

class GraphEdge(BaseModel):
    """依赖边。from_ = producer（被依赖），to = consumer（依赖方）。"""
    from_: str = Field(..., serialization_alias="from", validation_alias="from")
    to: str
    status: Literal["candidate", "confirmed"]   # candidate=世界层 only
    source: Literal["scan", "tm", "llm"]        # 审计：边从哪来
    interface: str | None = None                # 契约元数据（LLM 补充）
    agreement: str | None = None

class ContractEdgeView(BaseModel):
    """confirmed 边 + interface/agreement 的投影（避开 delivery.ContractView 命名冲突）。"""
    producer: str
    consumer: str
    interface: str
    agreement: str | None = None

class TaskDagNodeView(BaseModel):
    """方案层节点 + confirmed 依赖列表的投影（避开 api.TaskNodeView 命名冲突）。"""
    repository: str
    instruction: str | None = None
    depends_on: list[str] = []
    tests: list[str] = []

class PlanGraph(BaseModel):
    """统一依赖图的方案层版本（进快照，plan_version 单调递增）。"""
    plan_version: int = Field(ge=1)
    nodes: list[GraphNode]
    edges: list[GraphEdge]                      # 全部 confirmed 边（真值）
    # ↓ 派生投影（构造时物化；读时从 edges/nodes 重算，一致性断言保障）
    execution_batches: list[list[str]]          # 节点集=方案层 nodes，排序=confirmed 边
    contracts: list[ContractEdgeView]           # 边 interface/agreement 投影
    task_dag: list[TaskDagNodeView]             # 节点+依赖投影

def derive_edges(nodes: list[GraphNode], edges: list[GraphEdge]) -> list[GraphEdge]:
    """一致性保证：节点必须覆盖所有边的两端；edges 去重、无悬空边、去自环。"""

def project_batches(nodes: list[GraphNode], edges: list[GraphEdge]) -> list[list[str]]:
    """Kahn 拓扑，节点宇宙=方案层 nodes（每个方案仓库恰好进一个批次），
    只消费 confirmed 边；cycle 落同一批。"""

def project_contracts(edges: list[GraphEdge]) -> list[ContractEdgeView]:
    """只投影带 interface/agreement 的 confirmed 边。"""

def project_task_dag(nodes: list[GraphNode], edges: list[GraphEdge]) -> list[TaskDagNodeView]:
    """方案层节点 + confirmed 依赖列表。"""
```

> 实现备注（与初稿差异）：① 投影视图命名改为 `ContractEdgeView`/`TaskDagNodeView`，避免与 `delivery/contracts.py:394` `ContractView`、`api/models.py:148` `TaskNodeView` 冲突；② `project_batches` 增加 `nodes` 参数——批次必须覆盖方案层全部节点（含无边的仓库），节点宇宙取自 nodes 而非边端点，杜绝 candidate 边把世界层仓库带进批次。

---

## 八、落地 PR 序列与验收

| PR | Owner 模块 | 内容 | 消灭的乱象 | 验收 |
|---|---|---|---|---|
| PR-1 | repository_intelligence | 图契约 + 投影函数（derive/project）+ 单元测试 | 概念归一、投影可测 | 投影函数单测全覆盖；`ruff`+`pytest` 全绿 |
| PR-2 | repository_intelligence | 集成阶段改"图为中心"：结构图 candidate 边 + LLM 语义写回图，confirmed 边投影三列；`graph_edges` 升级为完整图落库（**含 possible 过滤 + LLM 补充边**） | 死列变活；LLM 不再另写一套 depends_on；**批次含 LLM 补充边** | materialize 后 `读图 ≡ 投影列` 断言通过 |
| PR-3 | change_orchestration | replan 影响分析/中断改用**最新方案层快照**；保存 v2 图快照；版本号唯一事实（`next_version`） | 影响分析不再错用世界层；版本历史建立 | 连续 replan v1→v2→v3；影响集=confirmed 边手推一致 |
| PR-4 | repository_intelligence + api | 图版本 diff（边增删 + 属性变化）+ 审批 preview/commit + `REPOMESH_REPLAN_AUTO_COMMIT` | diff/审批可用 | preview 零副作用；commit 幂等；自动模式与现状回归一致 |
| PR-5 | web | 前端单源投影展示 + diff UI（新增边绿高亮、删除边红删除线、NEW 徽标） | 展示不再拼两份数据 | 端到端走查：批次/依赖/契约/diff 全部来自同一图 |

**验收红线**：任何时刻 `读图 ≠ 投影列` 即视为 bug（PR-1 起在 materialize/replan 后做一致性断言测试）。

**约束**（AGENTS.md）：每 PR 单一 owning module；跨模块 import 只允许 `repomesh.modules.<producer>.contracts`；PR-1/2 先落契约再实现；行为测试不测目录结构。

---

## 九、数据契约汇总

### 9.1 graph_edges 条目（PR-2 生效）

```json
{"from": "ts-verification-code-service", "to": "ts-auth-service",
 "status": "confirmed", "source": "scan", "interface": "getCode", "agreement": "..."}
```

### 9.2 PlanDiff（PR-4 生效）

```json
{
  "from_version": 1, "to_version": 2,
  "added_edges": [{"from": "ts-verification-code-service", "to": "ts-auth-service"}],
  "removed_edges": [],
  "added_repos": ["ts-auth-service"],
  "removed_repos": [],
  "affected_repos": ["ts-auth-service"]
}
```

### 9.3 API 变更汇总

| 方法 | 路径 | PR | 说明 |
|---|---|---|---|
| POST | `/api/v1/bridge/replan` | 3/4 | 增加 `mode: preview\|commit`；响应增加 `plan_id` |
| GET | `/api/v1/bridge/plans/{project_id}/diff?from=1&to=2` | 4 | 版本间边级 diff |
| GET | `/api/v1/bridge/plans/{project_id}/snapshots` | 已有 | PR-3 后可见多版本 |

---

## 十、关键文件索引

| 文件 | 位置 | 用途 |
|---|---|---|
| `repository_intelligence/application/dependency_graph.py` | `:76-112` | 拓扑（PR-2 改为只消费 confirmed 边） |
| 同上 | `:126-158` | 世界层建边（保留，供发现） |
| `repository_intelligence/application/plan_integration.py` | `:515-538` | 集成（PR-2 改图为中心，批次=confirmed 边拓扑） |
| `change_orchestration/application.py` | `:318` | materialize 写快照（PR-2 写完整图） |
| 同上 | `:362` `replan()` | PR-3 加 v2 快照；PR-4 加 mode |
| 同上 | `:550-576` | 影响分析（PR-3 切到方案层快照） |
| `repository_intelligence/infrastructure/plan_snapshot_store.py` | `:83-107` | PR-2/3 快照读写与读时投影 |
| `task_orchestration/application.py` | `:614-649` | 批次门禁（**机制不动，只换批次来源**） |
| `repository_intelligence/api/router.py` | `:384` / `:564-579` | PR-3/4 API 扩展 |
| `repository_intelligence/api/models.py` | `:192` `ReplanRequest` / `:214` `ReplanResponse` | PR-3/4 加字段 |
| `change_orchestration/contracts.py` | `:31` `ReplanResult` | PR-3 加 `plan_id` |
| `task_orchestration/contracts.py` | `:135-136` | G5 后续项 |
| `web/src/PlanFlowTimeline.tsx` | 全文件 | PR-5 单源投影 + diff 展示基座 |

---

## 十一、后续事项（不在本方案内）

- G5：Task 实体标记 `plan_version`（任务粒度版本追踪）
- G6：推模式中断通知（CollaborationGateway 装配）
- 阶段 4（中期）：GraSP 原语（InsertPrereq / Rewire / Substitute / Bypass）——在边索引 + diff 就绪后，把"局部重规划=整仓替换"细化为原语级操作
- 存量数据：老快照 `graph_edges` 为空 → PR-2 提供读时推导回填（幂等、免停机）

---

## 十二、参考

- 《DAG动态更新方案-2026-08-08.md》：阶段划分、Step 4e、质疑 6、GraSP 原语
- `docs/architecture/delivery-loop.md`：可观测控制平面定位
- 本方案取代的旧版：《graph_edges 边索引与 DAG 动态更新补齐方案-2026-08-13.md》

---

## 十三、PR-2 实施记录（2026-08-13）

### 13.1 交付内容

集成阶段图中心化改造：`graph_edges` 由死列（恒 `[]`）升级为真实的方案层边索引；所有消费方（执行批次 / contracts / task_dag）统一读图投影；批次来源改为图投影。

### 13.2 代码落地

| 文件 | 变更 |
|---|---|
| `contracts/integration.py`（新增） | 契约层承载 `ContractSpec` / `TaskNode` / `IntegratedPlan`；`plan_to_graph`（回填）、`normalize_plan`（图投影重写三列）、`tm_order_edges`（人工顺序边）、`integration_method`（graph_assisted / llm_only） |
| `contracts/__init__.py` | re-export 新契约；`plan_integration.py` 只做 re-export 兼容旧 import |
| `application/plan_integration.py` | `integrate()` LLM-only 分支 → `normalize_plan(llm_plan, plan_to_graph(llm_plan))`；`_integrate_with_graph` → `_build_plan_graph`（scan confirmed→confirmed / possible→candidate / 契约升级 / LLM depends_on 新增边进批次）+ `normalize_plan` |
| `change_orchestration/application.py` | materialize fail-closed 后 0b 图统一化（`plan.graph or plan_to_graph(plan)` → `normalize_plan`）；快照写完整 `graph_edges` + 真实 `integration_method` |
| `infrastructure/plan_snapshot_store.py` | 新增 `plan_graph_from_snapshot`：读图≡投影列；legacy 空 `graph_edges` 行经 `plan_to_graph` 读时回填（与 materialize 路径完全一致） |

### 13.3 关键设计决策

1. **tm 调和（人工顺序保真）**：人工审批的批次顺序本身就是计划决策（`source="tm"`）。`plan_to_graph` 按相邻批次派生 confirmed 边，使投影重现显式批次；与依赖事实冲突时**事实优先**（反向边存在即跳过），矛盾的人工顺序永远不会覆盖真实依赖。
2. **legacy 回填复用 `plan_to_graph`**：读时回填与 materialize 回填走同一函数，保证老行重建的图与新落库一致；修复了原回填中 depends_on 边与契约边重复导致契约 `interface` 被丢弃的问题。
3. **顺带修复潜伏 bug**：快照保存曾用 `dict(c)`/`dict(t)` 序列化 slots dataclass（`ContractSpec`/`TaskNode`）——直接抛 `TypeError` 并被 `except Exception` 吞掉，导致生产上计划快照从未真正落库。改为 `dataclasses.asdict`。
4. **possible 边永不进拓扑**：candidate 边只承载"发现"，不进入执行批次与 task_dag 投影；契约可将其晋升为 confirmed（`source` 保持 `scan`，保留出处）。

### 13.4 验收门禁

- `ruff check .`：0 错误
- `pytest`：758 passed, 13 skipped（新增 23 个用例）
  - `test_plan_integration.py`：tm 调和 / 事实优先 / 三批链 / 契约升级去重 / normalize 幂等 / 图辅助集成（scan 权威、possible 过滤、契约晋升、LLM 新边进批次）
  - `test_plan_graph_snapshot_consistency.py`（新增）：新行读图≡投影列、legacy 回填一致、手工批次经 tm 边恢复、`integration_method` 分类
  - `test_plan_execution_bridge.py`：materialize 落快照断言 `graph_edges` 真实边 + `integration_method`

### 13.5 遗留（后续 PR）

- PR-3：replan 消费最新方案层快照；影响分析切到方案层
- PR-4：diff API（preview / commit）
- PR-5：web 单源投影展示（`PlanFlowTimeline.tsx` 改读图投影）

## 十四、PR-3 实施记录（2026-08-13）

### 14.1 交付内容

replan 从"世界层扫描图做影响分析"切到"**最新不可变快照的方案层图**"：confirmed 边精确驱动受影响集合（candidate 边永不扩中断），版本铸造与快照持久化全部收敛到快照存储（`next_version`），`ReplanResult`/`ReplanResponse` 透出持久化快照 `plan_id`。

### 14.2 代码落地

| 文件 | 变更 |
|---|---|
| `change_orchestration/ports.py` | 新增 `PlanSnapshotReader` 协议：`get_latest_graph(project_id) -> PlanGraph \| None`；`PlanGraph` 改为从 `repository_intelligence.contracts` 导入 |
| `change_orchestration/contracts.py` | `ReplanResult` 新增 `plan_id: UUID \| None`（持久化快照 id） |
| `repository_intelligence/infrastructure/plan_snapshot_store.py` | 新增 `get_latest_graph`：读最新快照行 → `plan_graph_from_snapshot`（legacy 空 `graph_edges` 读时回填，读图≡投影列）；无快照返回 `None` |
| `change_orchestration/application.py` | `replan()`：① fail-closed 后尽力加载 `plan_graph`；② 影响分析改走 `_compute_affected_repos(plan_graph=...)`，**confirmed 边权威，candidate 边忽略**，无快照时回退世界层图、再退化为仅 change_source 本身；③ 版本铸造改 `snapshots.next_version()`（存储为唯一事实，`plan_version+1` 仅作无存储回退）；④ 产出新计划时持久化 v+1 不可变快照（完整 `graph_edges` + `integration_method`），`plan_id = saved.id` 返回 |
| `repository_intelligence/api/models.py` | `ReplanResponse` 新增 `plan_id` |
| `repository_intelligence/api/router.py` | `replan_plan` 转发 `plan_id=result.plan_id`；docstring 更新为方案层语义 |
| `bootstrap/container.py` | `plan_execution_bridge` 工厂注入 `snapshot_reader=self.plan_snapshot_store()` |

### 14.3 关键设计决策

1. **confirmed 边权威，candidate 边永不扩中断**：受影响集合 = `{change_source} ∪ {confirmed 边指向的消费者}`。扫描层 candidate 边只承载"发现"，不构成计划依赖，绝不扩大中断面。
2. **失败关闭 + 尽力而为分层**：无 superseder 时 replan 在任何副作用前抛 `ExecutionPlaneUnavailable`；快照加载/持久化失败仅告警回退（世界层图 / cancel-only），不阻断 supersede 主流程。
3. **版本铸造归存储**：`next_version()` 从最新行 `+1` 单调递增（无行从 1 起），`plan_version+1` 只服务无快照存储的部署；版本号不再是调用方传入值。
4. **cancel-only replan 不落新快照**：没有新计划就没有新图可记录，保留上一快照；版本仍推进（cancel 也是一个计划变更事件），`plan_id` 为 `None`。
5. **快照回填一致性**：replan 落库与 materialize 走同一条 `model_dump(by_alias=True)` / `asdict` 序列化路径；读图 ≡ 投影列在每条路径上都成立。

### 14.4 验收门禁

- `ruff check .`：0 错误
- `pytest`：765 passed, 13 skipped（新增 4 个用例）
  - `test_plan_execution_bridge.py`：
    - `test_replan_affected_set_uses_plan_layer_confirmed_edges`：confirmed 边入受影响集、candidate 边被排除、supersede 只打中确认消费者
    - `test_replan_mints_version_from_store_and_returns_persisted_plan_id`：版本由存储铸造（v1→v2 而非 `plan_version+1`）、v2 快照完整落库、`plan_id` 透出
    - `test_replan_cancel_only_mints_version_without_saving_snapshot`：cancel-only 推进版本但不写快照、`plan_id=None`
  - `tests/persistence/test_plan_snapshot_store.py`（新增）：`get_latest_graph` 无快照→`None`、保存边 round-trip 一致、legacy 空边回填投影一致、`next_version` 单调铸造

### 14.5 遗留（后续 PR）

- PR-4：diff API（preview / commit）+ `REPOMESH_REPLAN_AUTO_COMMIT`
- PR-5：web 单源投影展示（`PlanFlowTimeline.tsx` 改读图投影）

## 十五、PR-4 实施记录（2026-08-13）

### 15.1 交付内容

方案层图的**版本间 diff**（§9.2 PlanDiff）+ replan 的 **preview / commit 双模式** + **`REPOMESH_REPLAN_AUTO_COMMIT`** 服务端配置。diff 由纯函数 `diff_plan_graphs` 统一计算，replan 结果与独立 diff 端点（`GET /plans/{project_id}/diff`）共用同一实现，保证 preview 与 commit 描述的是同一份变更。

### 15.2 代码落地

| 文件 | 变更 |
|---|---|
| `repository_intelligence/contracts/diff.py`（新增） | `DiffEdge`（`from_` serialization_alias `"from"`）、`EdgeChangeView`、`PlanDiff`（from/to version、added/removed/changed edges、added/removed/affected repos）；`diff_plan_graphs(from_graph, to_graph)` 纯计算，任一侧缺失返回 `None` |
| `repository_intelligence/contracts/__init__.py` | 重新导出 `DiffEdge`/`EdgeChangeView`/`PlanDiff`/`diff_plan_graphs` |
| `change_orchestration/contracts.py` | 新增 `ReplanMode = Literal["preview", "commit"]`；`ReplanResult` 新增 `diff: PlanDiff \| None` |
| `change_orchestration/application.py` | `replan(mode="commit")`：先只读铸造版本（`next_version`），`preview` 分支零副作用（不 supersede、不 plan start、不写快照、不 handoff，仅构造内存图副本并算 diff），`commit` 分支走 PR-3 全流程并在快照落库后基于快照图算 diff |
| `settings.py` | 新增 `replan_auto_commit: bool = True`（默认保持 PR-3 行为；设 `false` 时 auto 请求按 preview 运行，需显式 commit） |
| `repository_intelligence/api/models.py` | `ReplanRequest.mode`（`auto`/`preview`/`commit`，默认 `auto`）；`ReplanResponse.mode`（必填）+ `diff` |
| `repository_intelligence/api/router.py` | `_resolve_replan_mode`（auto → 跟随设置）；新增 `GET /plans/{project_id}/diff?from=&to=` 端点（默认 to=最新、from=to-1，缺版本 404，纯读幂等） |
| `repository_intelligence/infrastructure/plan_snapshot_store.py` | **生产修复**：`get_by_version` 与 `list_all` 的 `self._database.session()` → `sessions()`（`Database` 只暴露 `sessions`；该错误先于 PR-4 存在，diff 端点是其首个调用者） |

### 15.3 关键设计决策

1. **diff 单一纯函数实现**：`diff_plan_graphs` 无存储、无副作用，bridge（replan 结果）与 API 端点共用。边身份 = `(from_, to)`；同身份属性不同列入 `changed_edges`（不与增/删重复）；`affected_repos = added ∪ removed ∪ {added/removed 边的 to}`。
2. **preview 零副作用**：preview 仍调用 `next_version()` 只读报告"提交将铸造的版本"，但绝不落库——不 supersede、不 plan start、不写快照、不重生成 handoff；diff 基于内存图副本（`model_copy`）计算。
3. **两种 affected 语义并存**：`ReplanResult.affected_repos` 是**影响分析**（基于旧快照的 confirmed 边，回答"谁被中断"）；`diff.affected_repos` 是**变更足迹**（回答"图如何变化"）。preview 场景下消费者可能只出现在 diff 中，测试分别断言两者。
4. **确定性输出**：所有列表按边键 `(from_, to)` 或仓库名排序，重复调用字节级一致，diff 端点天然幂等。
5. **auto 模式向后兼容**：`REPOMESH_REPLAN_AUTO_COMMIT=True` 时 `auto` 请求等价于 PR-3 的立即提交；设 `false` 则强制 preview 审批往返。显式 `preview`/`commit` 始终覆盖设置。

### 15.4 验收门禁

- `ruff check .`：0 错误
- `pytest`：775 passed, 13 skipped（新增 10 个用例）
  - `tests/test_plan_diff.py`（新增 5 个）：§9.2 设计示例、边增/删/改、同图空 diff、缺失侧返回 `None`、输出确定性
  - `tests/test_replan_modes.py`（新增 2 个）：auto 跟随服务端设置、显式模式覆盖设置
  - `test_plan_execution_bridge.py`（新增 2 个）：preview 零副作用 + diff 一致、commit 报告快照间 diff（v1→v2，`plan_id` 落库）
  - `tests/persistence/test_plan_snapshot_store.py`（新增 1 个）：两个已保存快照 `get_by_version` → `diff_plan_graphs` round-trip

### 15.5 遗留（后续 PR）

- PR-5：web 单源投影展示（`PlanFlowTimeline.tsx` 改读图投影），将 diff/预览结果呈现为时间线

## 十六、PR-5 实施记录（2026-08-13）

### 16.1 交付内容

前端 `PlanFlowTimeline` 从"拼接 `execution_batches` + `task_dag` 两份数据"改为**单源投影**：只消费 `/integration` 返回的统一图（`plan.graph`），批次泳道取图的 `execution_batches` 投影、依赖标签与契约徽章从图的 `edges` 推导、指令取自图的 `nodes`。叠加 PR-4 的 diff 足迹：**新增边绿色高亮 + NEW 徽标、删除边红色删除线标注、新增仓库 NEW 徽标**。

### 16.2 代码落地

| 文件 | 变更 |
|---|---|
| `repository_intelligence/api/models.py` | `IntegratedPlanView` 新增 `graph: PlanGraph \| None`（统一图随响应下发；顶层三列声明为其投影） |
| `repository_intelligence/api/router.py` | `/integration` 端点返回 `plan.graph` |
| `web/src/api.ts` | 新增 `GraphNode`/`GraphEdge`/`PlanGraph`/`PlanDiff`/`EdgeChangeView` 类型；`IntegratedPlan.graph`；`api.planDiff(projectId, from?, to?)` 封装 PR-4 diff 端点 |
| `web/src/PlanFlowTimeline.tsx` | 重写：只消费 `plan.graph`（graph 缺失渲染空态，杜绝回退拼数据旁路）；新增 `diff?` prop 叠加变更足迹 |
| `web/src/PlanFlowDemo.tsx` | DEMO 数据补齐 `graph`（与顶层三列一致的图）+ 模拟 replan 的 `diff`（v1→v2 新增验证码依赖） |
| `web/src/styles.css` | diff 样式：`.taskflow-dep-chip.added` 绿色高亮、`.taskflow-dep-new` NEW 徽标、`.taskflow-dep-chip.removed` 红色删除线、`.taskflow-diff-badge.new`、契约徽章、图例 |

### 16.3 关键设计决策

1. **前端单源，graph 缺失即空态**：组件不再拼接 `plan.execution_batches` + `plan.task_dag`；只有 `plan.graph` 一个数据源。graph 为空直接渲染提示，杜绝"图没就回退旧拼法"的旁路（旁路正是 §一.1 批驳的自相矛盾来源）。
2. **API 边界一致性断言**：`/integration` 响应同时带顶层投影与 `graph`；测试断言图内嵌 `execution_batches`/`contracts`/`task_dag` 与顶层字段逐项相等——"读图 ≡ 投影列"验收红线延伸到前端契约边界。
3. **diff 是纯叠加层**：diff 不改动图数据，只在渲染时给"消费者任务"叠加足迹——`added_edges` 的 to 节点依赖 chip 绿色 + NEW、`removed_edges` 的 to 节点红色删除线、`added_repos` 节点 NEW 徽标；图例随 diff 出现并显示 `vX → vY`。
4. **后端零新端点**：单源投影复用 PR-2 已产的 `plan.graph`，diff 复用 PR-4 的 `GET /plans/{project_id}/diff`；前端仅新增 `api.planDiff` 封装。

### 16.4 验收门禁

- `ruff check .`：0 错误
- `pytest`：778 passed, 13 skipped（新增 3 个 API 用例）
  - `tests/test_repository_intelligence_api.py`（新增）：`/integration` 返回图且图内投影 ≡ 顶层投影（批次/契约/依赖逐项相等）、边按别名序列化（`from`）、diff 端点投影两个快照间变更（v1→v2 新增边/仓库/受影响集合）、缺版本 404
- `web`：`npm run build`（`tsc -b` + `vite build`）通过
- 端到端走查：`PlanFlowDemo` 演示的批次/依赖/契约/diff 全部来自同一 `graph` 与 PR-4 `diff`

### 16.5 遗留（后续事项）

单图方案主体闭环完成。后续可选事项（不在本方案 PR 序列内）：执行进度真实状态接入 `activeBatchIndex`、多版本时间线选择器（配合快照版本列表）等。
