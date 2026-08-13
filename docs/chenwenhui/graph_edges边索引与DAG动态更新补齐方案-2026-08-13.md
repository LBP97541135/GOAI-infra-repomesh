# graph_edges 边索引与 DAG 动态更新补齐方案

> 创建时间：2026-08-13
> 模块：repository_intelligence × change_orchestration × task_orchestration × web
> 状态：方案设计（待评审）
> 前置：《DAG动态更新方案-2026-08-08.md》（chenwenhui）

---

## 〇、结论先行

DAG 动态更新**主体闭环已经实现**，但"版本化可观测"这半边缺失。对照 8-08 方案逐条核对：**已实现 7 项、缺 5 项、部分实现 1 项**。最关键的缺口是：

1. **replan 从不保存 v2 快照** —— `plan_snapshots` 里永远只有 materialize 写的 v1，"可查询可展示的版本历史"不存在；
2. **没有 v1↔v2 diff、没有人类审批 checkpoint** —— 重规划一步到位自动 supersede，不可审查；
3. **影响分析用的是 AutoCard 结构边，不是计划自身的依赖边** —— 两套图不一致，影响集可能漏判/误伤；
4. **graph_edges 恒为 []** —— 快照"完整 DAG"名不副实。

本方案以 **graph_edges 升级为边索引** 为切入点，分 5 个阶段把上述缺口补齐，每阶段一个独立 PR、单一 owner、可独立验收上线。

---

## 一、现状盘点（"以为已完成" vs 实际）

### 1.1 已实现（8-08 方案的主体框架）

| # | 8-08 方案条目 | 代码位置 | 状态 |
|---|---|---|---|
| ✓1 | `POST /api/v1/bridge/replan` 端点 | `repository_intelligence/api/router.py:384` `replan_plan` | ✅ |
| ✓2 | Step 1 影响分析（反向依赖遍历） | `change_orchestration/application.py:550` `_compute_affected_repos` | ✅ |
| ✓3 | 反向依赖查询服务 | `repository_intelligence/application/dependency_graph.py:60` `reverse_dependencies` | ✅ |
| ✓4 | Step 2 局部重规划（LLM + 稳定性约束） | `application.py:579` `_local_replan` | ✅ |
| ✓5 | Step 3 版本迁移（supersede 旧任务） | `application.py:629` `_supersede_affected_tasks` + `task_orchestration` `supersede` | ✅ |
| ✓6 | Step 3b 启动新执行批次 | `application.py:688` `_start_replan_batch` | ✅ |
| ✓7 | Step 3c 重新生成 handoff 文档 | `application.py:484-514` | ✅ |
| ✓8 | `TaskStatus.SUPERSEDED` | `task_orchestration/contracts.py:14` | ✅ |
| ✓9 | `TaskReport.plan_version` / `plan_revision_needed` | `task_orchestration/contracts.py:135-136` | ✅（仅报告侧） |

> 这 9 项撑起了完整骨架：**触发 → 影响分析 → 局部重规划 → supersede → 重启批次 → handoff 重生成**。这就是"以为 DAG 动态更新已完成"的错觉来源。

### 1.2 缺口（对照 8-08 方案逐条核对）

| # | 8-08 方案要求 | 现状 | 影响 |
|---|---|---|---|
| **G1** | Step 4e："保存 v2 快照到 plan_snapshots（完整 DAG，可查询可展示）" | ❌ `replan()` 全程**没有调用 `snapshots.save()`**，表里只有 materialize 写的 v1 | 无版本历史；时间旅行 / 审计 / 回放全空 |
| **G2** | 质疑 6："重规划后新 DAG 展示给人类审批，MVP 阶段 API 返回 diff" | ❌ 无 diff 契约、无 diff API、无审批 checkpoint | 重规划不可审查；自动 supersede 存在误伤风险 |
| **G3** | Step 4a/4b："图推理反向依赖（基于计划 DAG）" | ⚠️ 影响分析用的是 **AutoCard 结构边**（`DependencyGraphService`），**不是计划 `task_dag` 的依赖边** | 两套图不一致 → 影响集漏判（计划新增边 AutoCard 没声明）或误伤（possible 边） |
| **G4** | `graph_edges` 列承载真实边 | ❌ 恒 `[]`，materialize（`application.py:318`）与 replan 都不写 | 快照"完整 DAG"名不副实 |
| **G5** | "每个 Task 上标记 `plan_version`" | ❌ Task 实体无 `plan_version`（只有 TaskReport 携带） | 版本迁移无法在任务粒度核对 |
| **G6** | Step 6 推模式中断通知 | ⚠️ `replan_plan` 注释称"API 层推送"，但端点内**没有 CollaborationGateway 推送代码**，仅返回 `affected_repos` | 实际只剩拉模式兜底（TaskReport 带版本号） |

### 1.3 结论

- "主体闭环"确实搭完了，所以有"已完成"的错觉；
- 但 RepoMesh 作为**可观测控制平面**的立身之本——**版本化（G1/G4）+ 可审查（G2）+ 影响分析准确（G3）**——这半边没做；
- G5 属于任务粒度追踪（可后置），G6 属于运行时平面（API 层），两者不阻塞版本化闭环，列为第二阶段。

---

## 二、第一性目标

对 RepoMesh 而言，DAG 动态更新的本质是**多版本计划的一致演进**。因此每次 replan 必须留下：

1. 一个**不可变的完整 v2 快照**，且包含真实边（G1 + G4）；
2. 从 v1 到 v2 的**边级 diff**，供人类审批与审计（G2）；
3. 影响分析使用**计划自身的依赖边**而非 AutoCard 结构边（G3）；
4. Task 粒度可追踪版本（G5，第二阶段）；
5. 推模式中断通知（G6，第二阶段）。

---

## 三、方案总览

graph_edges 升级为"边索引"，分 5 个阶段落地：

```
阶段 0  边物化    derive_edges(task_dag) → graph_edges          消灭死列（G4）
阶段 1  v2 快照  replan() 保存 v2 + link execution plan         建立版本历史（G1）
阶段 2  边索引   PlanEdgeIndex + PlanDiff + GET /diff API        可 diff、图源统一（G2 数据面 + G3）
阶段 3  审批     replan preview/commit 分离 + 自动审批开关       可审批（G2 流程面）
阶段 4  原语     InsertPrereq / Rewire（中期可选）               细粒度修复
```

每阶段一个独立 PR，owner 单一，可独立验收、独立上线。

---

## 四、阶段 0：边物化（消灭死列）

**Owner：repository_intelligence**（DAG 语义的拥有者）

### 4.1 `derive_edges` 纯函数

新增于 `repository_intelligence/application/plan_integration.py`（与 `_topological_batches` 并列，同为 DAG 派生态）：

```python
def derive_edges(task_dag: list[TaskNode]) -> list[dict[str, str]]:
    """把 task_dag 的 depends_on 展开为显式边列表（graph_edges 的真值来源）。

    边语义与 DependencyGraphService.GraphEdge 一致：
    {"from": 被依赖仓库(producer), "to": 依赖方(consumer)}。
    不展开 parallelizable_with —— 可并行不是依赖，由 execution_batches 同批表达。
    """
    edges: list[dict[str, str]] = []
    for t in task_dag:
        for dep in t.depends_on:
            edges.append({"from": dep, "to": t.repository})
    return edges
```

规则：
- 顺序稳定（按 task_dag 声明顺序），保证 diff 稳定；
- 不去重（保留每条 `depends_on` 声明，审计可追溯）；
- 与 `_topological_batches` 同一约定：边两端都是已确认仓库（`depends_on` 在解析阶段已被过滤到 repo_set 内，见 `plan_integration.py:380`）。

### 4.2 写入

- `change_orchestration/application.py:318`：`graph_edges=[]` → `graph_edges=derive_edges(plan.task_dag)`；
- `replan()` 写入点（阶段 1 引入）同样传入。

> 模块边界：`derive_edges` 定义在 repository_intelligence，change_orchestration 经由其导出的 `IntegratedPlan`/`TaskNode`（既有依赖）同路径使用，不新增跨模块 import。

### 4.3 存量回填（读时推导）

老快照 `graph_edges` 为空。方案：`PlanSnapshotStore.get_latest / get_by_version` 返回时，若 `graph_edges` 为空且 `task_dag` 非空，则用 `derive_edges` 补算——**读时推导**，无需停机、天然幂等、不碰老数据。

### 4.4 验收

- `derive_edges` 单测：无依赖 / 链式依赖 / 并行 / 循环 / 空 dag / 输出顺序稳定；
- materialize 后查库：`graph_edges` 与 `task_dag.depends_on` 一一对应；
- 老数据（`graph_edges=[]`）读出后仍返回完整边。

---

## 五、阶段 1：v2 快照 + link（建立版本历史）

**Owner：change_orchestration**

### 5.1 `replan()` 保存快照

在 `replan()` 的 Step 3b 之后（`new_plan` 非空时）追加：

```python
if new_plan is not None and self._snapshots is not None:
    await self._snapshots.save(
        project_id=project_id,
        plan_version=new_plan_version,            # 统一取 next_version（见 5.2）
        engineering_spec=new_plan.engineering_spec or requirement,
        contracts=[c.to_dict() if hasattr(c, "to_dict") else dict(c) for c in new_plan.contracts],
        task_dag=[t.to_dict() if hasattr(t, "to_dict") else dict(t) for t in new_plan.task_dag],
        execution_batches=[list(b) for b in new_plan.execution_batches],
        graph_edges=derive_edges(new_plan.task_dag),
        created_by_agent_id=leader_agent_id,
        execution_plan_id=<_start_replan_batch 返回的 plan.id>,
        requirement_text=requirement,
        integration_method="local_replan",
    )
```

配套改动：
- `_start_replan_batch` 返回值透传 `started.plan.id` 给快照的 `execution_plan_id`（当前该值被丢弃）；
- `ReplanResult` 增加 `plan_id: UUID | None`；`ReplanResponse` 同步增加；
- cancel-only 场景（`new_plan=None`）：不写快照，`plan_id=None`。已知限制：可能跳版本号（见对抗性审查 Q5）。

### 5.2 版本号唯一事实（消除双源）

现状：`new_plan_version = 调用方传入的 plan_version + 1`，与 `snapshots.next_version()`（查库最大值 +1）**可能不一致**（并发、漏存时）。

**统一原则：`snapshots.next_version()` 是唯一事实。** `replan()` 内直接用它作为 `new_plan_version`；调用方传入的 `plan_version` 仅用于校验与日志（`ReplanRequest.plan_version` 语义改为"期望当前版本"，与最新不符时 409 拒绝，天然防并发重放）。

### 5.3 验收

- 一次 replan 后：`plan_snapshots` 出现 v2 行，`graph_edges` 非空，`execution_plan_id` 指向新批次 plan；
- 连续两次 replan：版本 1→2→3 严格递增，无跳号；
- `GET /api/v1/bridge/plans/{project_id}/snapshots`（已有端点）能看到全部版本；
- 并发 replan 测试：同一 project 两个并发请求，一个 409、一个成功。

---

## 六、阶段 2：边索引 + PlanDiff（可 diff、图源统一）

**Owner：repository_intelligence**

### 6.1 契约（先定契约再实现，符合 AGENTS.md）

新增 `repository_intelligence/contracts/plan_diff.py`：

```python
class EdgeView(BaseModel):
    """计划依赖边。from_ = producer（被依赖），to = consumer（依赖方）。"""
    from_: str = Field(..., serialization_alias="from", validation_alias="from")
    to: str

class PlanDiff(BaseModel):
    from_version: int
    to_version: int
    added_edges: list[EdgeView]      # v2 有、v1 无
    removed_edges: list[EdgeView]    # v1 有、v2 无
    added_repos: list[str]
    removed_repos: list[str]
    affected_repos: list[str]        # 变更边的两端 ∪ 变更仓库
```

### 6.2 `PlanEdgeIndex`（从快照边重建图）

新增 `repository_intelligence/application/plan_edge_index.py`：

```python
class PlanEdgeIndex:
    """从快照 graph_edges 构建前向/反向索引。

    与 DependencyGraphService 同构，但输入是"计划自身的边"（task_dag 物化），
    用于执行/重规划阶段；AutoCard 结构边仍只用于仓库发现阶段。
    """
    def __init__(self, edges: list[EdgeView]) -> None: ...
    def reverse_dependencies(self, repo: str) -> list[EdgeView]: ...   # 谁依赖我（受影响集）
    def forward_dependencies(self, repo: str) -> list[EdgeView]: ...   # 我依赖谁
```

### 6.3 `diff_plans` 服务

```python
def diff_plans(v1: PlanSnapshotRecord, v2: PlanSnapshotRecord) -> PlanDiff:
    """边集合对称差 + 仓库集合对称差 + affected 推导。"""
```

- 边比较基于 `(from, to)` 元组，与 instruction 措辞无关 → diff 稳定；
- `affected_repos` = added/removed 边的 `to` 端点（依赖方被影响）+ added/removed 仓库。

### 6.4 API

```
GET /api/v1/bridge/plans/{project_id}/diff?from=1&to=2   # to 缺省 = latest
```

- 版本不存在 → 404；from==to → 全空 diff；
- 响应模型 `PlanDiffView`（复用 6.1 契约，加 project_id）。

### 6.5 影响分析图源切换（修复 G3）

`change_orchestration/application.py:426` `_compute_affected_repos` 的图源改为：
1. **优先**：从最新快照 `graph_edges` 构建 `PlanEdgeIndex` → `reverse_dependencies`；
2. **兜底**：无快照时退回现状（调用方传入的 AutoCard `DependencyGraphService`）。

理由：执行中被中断的是**任务**，任务间依赖（`task_dag` 边）才是中断传播路径；AutoCard 结构边是仓库发现期的语义，用它做 replan 会漏掉"计划新增但 AutoCard 未声明"的边。

### 6.6 验收

- `PlanEdgeIndex` 单测：前向/反向查询、空图、自环；
- `diff_plans` 单测：新增边 / 删除边 / 仓库增删 / 无变化 / 版本缺失 404；
- 一次真实 replan 后 `diff v1→v2` 输出与预期一致（如"发现缺失前置"场景：新增 1 边 + 1 受影响仓库）；
- replan 的 `affected_repos` 用 PlanEdgeIndex 计算后，与 task_dag 手推结果一致。

---

## 七、阶段 3：人类审批 checkpoint（可审批）

**Owner：change_orchestration（bridge 改造） + repository_intelligence（API） + web（UI）**

### 7.1 preview / commit 分离

现状 replan 一步到位（supersede + 启动批次）。改造为两种模式：

| 模式 | 行为 | 副作用 |
|---|---|---|
| `preview` | 影响分析 + 局部重规划 + 计算 PlanDiff，返回待审批内容 | **无**（纯计算，可重放） |
| `commit` | 在 preview 结果上执行 supersede + 启动批次 + 写 v2 快照 + 重生成 handoff | 有 |

- `POST /bridge/replan` 增加字段 `mode: "preview" | "commit"`（缺省 `commit`，保持向后兼容）；
- 新增 `POST /bridge/replan/preview` 便捷别名；
- commit 幂等：沿用 `idempotency_prefix` 机制；preview 天然幂等（无副作用）；
- 审批开关：`REPOMESH_REPLAN_AUTO_COMMIT=true|false`。`false` 时 replan 端点强制走 preview，由调用方显式 commit（**默认 true，行为与现状一致**）。

### 7.2 审批界面（web）

在"执行流程"视图（`PlanFlowTimeline` 视觉语言）上叠加 diff 态：
- 新增边：绿色高亮 + `+` 徽标；
- 删除边：红色删除线；
- 新增任务卡片：`NEW` 徽标；
- 操作按钮：**批准并执行（commit）** / **驳回（丢弃 preview）**；
- 复用现有 `flow-*` / `taskflow-*` 样式体系。

### 7.3 验收

- preview 无任何写操作（查库验证无新快照、无 supersede、无新批次）；
- commit 幂等：同一 `idempotency_prefix` 重放只生效一次；
- `REPOMESH_REPLAN_AUTO_COMMIT=true` 时行为与现状完全一致（回归测试保障）。

---

## 八、阶段 4：GraSP 原语（中期可选）

在边索引 + diff 就绪后，把"局部重规划 = 整仓替换"细化为原语：

| 原语 | 语义 | diff 体现 |
|---|---|---|
| `InsertPrereq` | 为受影响仓库插入缺失前置任务 + 边 | 新增 1 节点 + 1 边 |
| `Rewire` | 修改 `depends_on` | 删 1 边 + 增 1 边 |
| `Substitute` / `Bypass` | 替换/删除任务 | 节点与边增减 |

- 实现位置：repository_intelligence（原语语义）调用现有 bridge 机制（supersede / 启动批次 / 快照）；
- 不阻塞阶段 0-3，作为中期里程碑独立立项。

---

## 九、数据契约汇总

### 9.1 graph_edges 条目（阶段 0 生效）

```json
{"from": "ts-verification-code-service", "to": "ts-auth-service"}
```

### 9.2 PlanDiff 示例（阶段 2 生效）

```json
{
  "from_version": 1,
  "to_version": 2,
  "added_edges": [{"from": "ts-verification-code-service", "to": "ts-auth-service"}],
  "removed_edges": [],
  "added_repos": ["ts-auth-service"],
  "removed_repos": [],
  "affected_repos": ["ts-auth-service"]
}
```

### 9.3 API 变更汇总

| 方法 | 路径 | 阶段 | 说明 |
|---|---|---|---|
| POST | `/api/v1/bridge/replan` | 1 / 3 | 增加 `mode` 字段；响应增加 `plan_id` |
| GET | `/api/v1/bridge/plans/{project_id}/diff` | 2 | 版本间边级 diff |
| GET | `/api/v1/bridge/plans/{project_id}/snapshots` | 已有 | 阶段 1 后可见多版本 |

---

## 十、实施 PR 序列与验收

| PR | Owner 模块 | 内容 | 依赖 | 验收 |
|---|---|---|---|---|
| PR-1 | repository_intelligence | `derive_edges` + 读时回填 + materialize 写真实边 | - | 阶段 0 验收 |
| PR-2 | change_orchestration | replan 存 v2 快照 + link + `ReplanResult.plan_id` + 版本号唯一事实 | PR-1 | 阶段 1 验收 |
| PR-3 | repository_intelligence | `PlanEdgeIndex` + `diff_plans` + diff API + 影响分析切图源 | PR-2 | 阶段 2 验收 |
| PR-4 | change_orchestration | preview/commit + `REPOMESH_REPLAN_AUTO_COMMIT` | PR-3 | 阶段 3 验收 |
| PR-5 | web | diff 展示 + 审批 UI | PR-4 | 端到端走查 |

约束（AGENTS.md）：
- 每个 PR 一个 owning module；PR-1/PR-3 先落契约（`contracts/`）再实现；
- PR-1/PR-3 补 ports 契约测试；每个 PR `ruff check .` + `pytest` 全绿；
- 跨模块 import 只允许 `repomesh.modules.<producer>.contracts`。

---

## 十一、对抗性审查

**Q1：影响分析为什么不用内存里的 `plan.task_dag`，而要物化 graph_edges？**
A：replan 发生在 v1 执行到一半时，内存里早没有 v1 的 `IntegratedPlan`，唯一可靠来源是数据库快照。物化 graph_edges 让"历史版本图"可查询、可重建 `PlanEdgeIndex`——这是可观测控制平面的刚需，运行时影响分析只是顺带受益。

**Q2：diff 为什么以边为主、不是节点或 instruction？**
A：8-08 方案的修复原语（InsertPrereq / Rewire）语义就是"改边"，边级 diff 直接映射到原语；而 instruction 措辞变化会产生大量伪 diff。节点增删是边的投影，作为附带信息返回。

**Q3：审批会拖慢闭环、增加人工负担？**
A：preview/commit 分离 + `REPOMESH_REPLAN_AUTO_COMMIT` 开关，默认自动模式行为与现状完全一致。审批是"可配置门槛"，只对需要人类决策的 BLOCKED 场景启用。

**Q4：版本号有两个来源（调用方 `plan_version` vs `next_version`）会打架？**
A：阶段 1 统一为 `next_version` 唯一事实，调用方参数降级为"期望当前版本"校验（不符则 409），消除双源。

**Q5：cancel-only replan（new_plan=None）不写快照，版本会跳号吗？**
A：会（如 v1 → v3）。已知限制，先记录。若需连续版本，PR-2 可选实现"取消快照"（`integration_method="cancel_only"`，task_dag 保留 v1 受影响外部分）。

**Q6：graph_edges 与 DependencyGraphService 两套边会不会混淆？**
A：职责分离——AutoCard 结构边（DependencyGraphService）只用于仓库发现/确认阶段（"哪些仓库可能有依赖关系"）；计划边（graph_edges）用于执行与重规划阶段（"派发的任务之间真实依赖"）。阶段 2 后 replan 只用计划边，边界清晰，文档同步注明。

---

## 十二、关键文件索引（实施定位用）

| 文件 | 位置 | 用途 |
|---|---|---|
| `change_orchestration/application.py` | `:318` | materialize 写快照 `graph_edges=[]`（PR-1 改） |
| 同上 | `:362` `replan()` | PR-2 加 v2 快照；PR-4 加 mode |
| 同上 | `:426` / `:550-576` | 影响分析（PR-3 切图源到 PlanEdgeIndex） |
| 同上 | `:688-740` `_start_replan_batch` | PR-2 透传 plan_id |
| `repository_intelligence/application/plan_integration.py` | `:408` `_topological_batches` 旁 | PR-1 加 `derive_edges` |
| `repository_intelligence/application/dependency_graph.py` | 全文件 | 结构图服务（保留，供发现阶段） |
| `repository_intelligence/infrastructure/plan_snapshot_store.py` | `:83-107` | PR-1 读时回填 |
| `repository_intelligence/api/router.py` | `:384` / `:564-579` | PR-2/3/4 API 扩展 |
| `repository_intelligence/api/models.py` | `:192` `ReplanRequest` / `:214` `ReplanResponse` | PR-2/4 加字段 |
| `change_orchestration/contracts.py` | `:31` `ReplanResult` | PR-2 加 `plan_id` |
| `task_orchestration/contracts.py` | `:135-136` | G5 后续项（Task 粒度版本标记） |
| `web/src/PlanFlowTimeline.tsx` | 全文件 | PR-5 diff 展示基座 |

---

## 十三、参考

- 《DAG动态更新方案-2026-08-08.md》：阶段划分、Step 4e、质疑 6、GraSP 原语
- `docs/architecture/delivery-loop.md`：可观测控制平面定位
