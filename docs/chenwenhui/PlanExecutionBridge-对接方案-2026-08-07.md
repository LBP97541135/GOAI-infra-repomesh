# PlanExecutionBridge 对接方案

> 创建时间：2026-08-07
> 模块：repository_intelligence × specification × task_orchestration
> 设计原则：PlanIntegrationService 保持不变，对接层消费 IntegratedPlan

---

## 一、问题定义

`PlanIntegrationService.integrate()` 产出 `IntegratedPlan`（engineering_spec + contracts + task_dag + execution_batches），但这个产出物只存在内存和 JSON 文件里。

队友的 specification 和 task_orchestration 模块已经实现了完整的 Spec 生命周期和 Task 分派机制，但 `integrate()` 没有调用它们。

**需要写一个对接层**，把 IntegratedPlan 转成 specification 的 `create()` 调用和 task_orchestration 的 `assign()` 调用。

---

## 二、队友接口分析

### specification 模块

`SpecificationService.create()` 需要：

```python
CreateSpecificationCommand(
    organization_id: UUID,           # 组织 ID
    project_id: UUID,                # 项目 ID
    kind: SpecificationKind,         # ENGINEERING / CONTRACT / REPOSITORY / TASK
    created_by_agent_id: UUID,       # 创建者（ORG_LEADER 的 agent_id）
    goal: str,                       # 目标描述
    title: str,                      # 标题
    acceptance: tuple[str, ...],     # 验收标准
    scope: tuple[str, ...],          # 涉及范围
    dependencies: tuple[str, ...],   # 依赖
    interface_changes: tuple,        # 接口变化
    parent_id: UUID | None,          # 父 Spec
)
```

权限约束（application.py 第 104-108 行）：
- ENGINEERING 和 CONTRACT 只能由 ORGANIZATION_LEADER 创建
- REPOSITORY 只能由 REPOSITORY_LEADER 创建

### task_orchestration 模块

`TaskOrchestrator.assign()` 需要：

```python
AssignTaskCommand(
    organization_id: UUID,
    project_id: UUID,
    repository_id: UUID,             # 仓库 ID（不是仓库名！）
    assigned_by_agent_id: UUID,      # ORG_LEADER 的 agent_id
    assignee_agent_id: UUID,         # REPO_LEADER 的 agent_id
    title: str,
    instruction: str,
    acceptance: tuple[str, ...],
    parent_task_id: UUID | None,
)
```

权限约束（application.py 第 62 行）：
- assignee 必须是 assigner 的直接下属

### project 模块

`ProjectTopologyReader.get_view(project_id)` 返回 `ProjectAgentTopologyView`：

```python
ProjectAgentTopologyView:
    organization_id: UUID
    project_id: UUID
    organization_leader_id: UUID     # ← ORG_LEADER 的 agent_id
    repository_teams: tuple[RepositoryTeamView, ...]
```

```python
RepositoryTeamView:
    repository_id: UUID              # ← 仓库 ID
    leader_agent_id: UUID            # ← REPO_LEADER 的 agent_id
    worker_agent_ids: tuple[UUID, ...]
    runtime_status: ProjectTeamRuntimeStatus  # PENDING / READY / FAILED
```

**关键**：Topology 不包含仓库名，只有 repository_id。需要额外的映射。

### repository_intelligence 模块

`RepositoryProfile` 有 `id: UUID` 和 `name: str`。通过 catalog.list() 可以拿到 name → id 的映射。

---

## 三、对接层设计

### PlanExecutionBridge

```python
class PlanExecutionBridge:
    """把 IntegratedPlan 对接到 specification 和 task_orchestration。"""

    def __init__(
        self,
        specifications: SpecificationService,
        tasks: TaskOrchestrator,
        topologies: ProjectTopologyReader,
        catalog: RepositoryCatalog,       # 用于仓库名 → repository_id 映射
    ): ...
```

### materialize 方法

```python
async def materialize(
    self,
    plan: IntegratedPlan,
    requirement: str,
    project_id: UUID,
    leader_agent_id: UUID,
    *,
    idempotency_prefix: str,
) -> MaterializationResult:
```

**执行流程**：

```
1. 查 topology → 拿到 organization_id + 所有 RepositoryTeamView
2. 查 catalog → 建立 仓库名 → repository_id 的映射
3. 创建 Engineering Spec
4. 为每个 Contract 创建 Contract Spec
5. 按 execution_batches 逐批 assign Task
   - batch 1 全部 assign
   - 等待 batch 1 全部 report SUCCEEDED
   - assign batch 2
   - ...
```

### 数据映射

#### IntegratedPlan → CreateSpecificationCommand (ENGINEERING)

| Spec 字段 | 来源 |
|-----------|------|
| organization_id | topology.organization_id |
| project_id | 参数传入 |
| kind | SpecificationKind.ENGINEERING |
| created_by_agent_id | 参数传入（leader_agent_id） |
| title | 从需求前 60 字截断 |
| goal | plan.engineering_spec |
| acceptance | 从 plan 的 risk 和 scope 推断 |
| scope | 确认的仓库名列表 |
| dependencies | () |
| interface_changes | 从 plan.contracts 转换 |

#### ContractSpec → CreateSpecificationCommand (CONTRACT)

| Spec 字段 | 来源 |
|-----------|------|
| kind | SpecificationKind.CONTRACT |
| title | f"{producer} → {consumer}: {interface}" |
| goal | contract.agreement |
| scope | (contract.producer, contract.consumer) |
| parent_id | Engineering Spec 的 ID |

#### TaskNode → AssignTaskCommand

| Task 字段 | 来源 |
|-----------|------|
| organization_id | topology.organization_id |
| project_id | 参数传入 |
| repository_id | 仓库名 → topology 查 repository_id |
| assigned_by_agent_id | leader_agent_id |
| assignee_agent_id | topology 查 leader_agent_id |
| title | f"Implement changes for {repository}" |
| instruction | task_node.instruction |
| acceptance | 从 task_node 的 risk 推断 |

### Batch 执行逻辑

```python
for batch_index, batch in enumerate(plan.execution_batches):
    # Assign all tasks in this batch
    for repo_name in batch:
        await self._tasks.assign(command, idempotency_key=...)

    # Wait for all tasks in this batch to complete
    # (poll progress() or wait for collaboration messages)
    await self._wait_for_batch_completion(batch, project_id)
```

**注意**：batch 等待逻辑需要异步实现。当前 MVP 可以先只 assign 不等待（一次性全部分派），后续再加等待逻辑。

---

## 四、文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `repository_intelligence/application/plan_execution_bridge.py` | 新增 | PlanExecutionBridge |
| `repository_intelligence/application/__init__.py` | 修改 | 导出 PlanExecutionBridge |
| `tests/test_plan_execution_bridge.py` | 新增 | 单元测试 |

---

## 五、MVP 范围

当前 MVP 对接的范围：

| 功能 | MVP | 后续 |
|------|-----|------|
| 创建 Engineering Spec | ✅ | — |
| 创建 Contract Spec | ✅ | — |
| 按 batch assign Task | ✅ | — |
| Batch 等待（等前一批完成） | ❌ 先跳过 | 后续实现 |
| Task 从 Contract 推导 acceptance | ⚠️ 基础版 | 后续完善 |
| 错误重试 | ❌ | 后续实现 |

MVP 中 `materialize()` 会创建所有 Spec 和 Task，但**不等待 batch 完成**——所有 Task 一次性 assign，由 task_orchestration 和 collaboration 模块自行处理执行顺序。后续版本再增加 batch 等待逻辑。
