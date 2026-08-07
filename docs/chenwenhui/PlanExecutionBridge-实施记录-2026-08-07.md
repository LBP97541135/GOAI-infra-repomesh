# 实施记录：PlanExecutionBridge + API 端点对接

> 创建时间：2026-08-07
> 模块：repository_intelligence × specification × task_orchestration × bootstrap

---

## 一、本次改动概述

本次改动完成了"计划制定"（repository_intelligence）与"计划执行"（specification + task_orchestration）的对接，并暴露了 HTTP API 端点供系统测试使用。

### 改动范围

| 文件 | 操作 | 说明 |
|------|------|------|
| `application/plan_execution_bridge.py` | 新增 | PlanExecutionBridge 对接层 |
| `application/__init__.py` | 修改 | 导出 confirmation/plan_integration/plan_execution_bridge 的全部公开类型 |
| `api/models.py` | 修改 | 新增 Confirmation/Integration/Materialize 的 Pydantic models |
| `api/router.py` | 修改 | 新增 3 个 API 端点 |
| `bootstrap/container.py` | 修改 | 新增 4 个工厂方法 + topology adapter |
| `tests/test_plan_execution_bridge.py` | 新增 | 9 个单元测试 |

---

## 二、PlanExecutionBridge

### 设计原则

PlanIntegrationService 保持不变，单独写对接层消费 IntegratedPlan。制定计划和执行计划是两个不同的关注点，分离各自可测、各自可变。

### 数据流

```
IntegratedPlan
  ├─ engineering_spec  → SpecificationService.create(kind=ENGINEERING)
  ├─ contracts[]       → SpecificationService.create(kind=CONTRACT) × N
  └─ task_dag[]        → TaskOrchestrator.assign() × N
```

### 仓库名 → UUID 映射

bridge 内部通过两个数据源完成映射：

```
catalog.list() → {name: RepositoryProfile(id=UUID)}  → name_to_repo_id
topology.repository_teams → {repository_id: RepositoryTeamView}  → repo_id_to_team

TaskNode(repository="ts-order-service")
  → name_to_repo_id["ts-order-service"] → UUID("aaa...")
  → repo_id_to_team[UUID("aaa...")] → team.leader_agent_id
  → assign(repository_id=UUID("aaa..."), assignee_agent_id=team.leader_agent_id)
```

### Protocol 解耦

为遵守架构边界规则（跨模块只 import contracts），bridge 定义了两个 Protocol：

```python
class SpecificationCreator(Protocol):
    async def create(self, command, *, idempotency_key) -> SpecificationView: ...

class TaskAssigner(Protocol):
    async def assign(self, command, *, idempotency_key) -> TaskView: ...
```

SpecificationService 和 TaskOrchestrator 天然满足这两个 Protocol（duck typing），无需修改队友代码。

### MVP 限制

- `tasks=None` 时跳过 Task 分派（需 AgentTeams Matrix 提供 CollaborationGateway）
- Batch 等待逻辑未实现（所有 Task 一次性 assign）
- acceptance 从 plan 推断，非精确

---

## 三、Container 接线

### 新增工厂方法

| 方法 | 用途 |
|------|------|
| `topology_reader()` | 把 ProjectTopologyStore 适配为 ProjectTopologyReader（domain.to_view()） |
| `confirmation_service(llm)` | 构建 ConfirmationService（async，内部调 catalog.list() 构建 dict） |
| `plan_integration_service(llm)` | 构建 PlanIntegrationService（只需 llm） |
| `plan_execution_bridge()` | 构建 PlanExecutionBridge（注入 specification_service + topology_reader + catalog） |

### topology adapter

```python
class _Adapter:
    async def get_view(self, project_id):
        topology = await store.get(project_id)
        return topology.to_view() if topology else None
```

ProjectTopologyStore.get() 返回 domain 对象，domain.to_view() 转成 contract 的 ProjectAgentTopologyView。

---

## 四、API 端点

### 新增端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/confirmation` | POST | Team Manager 确认 + 产出结构化方案 |
| `/api/v1/integration` | POST | 总 Manager 整合方案 |
| `/api/v1/bridge/materialize` | POST | 创建 Engineering Spec + Contract Spec + Task |

### 完整调用链路

```
1. POST /api/v1/repositories          ← 注册仓库（已有）
2. GET  /api/v1/repositories           ← 列出仓库（已有）
3. POST /api/v1/discovery              ← 仓库发现（已有）
4. POST /api/v1/confirmation           ← Team Manager 确认（新增）
5. POST /api/v1/integration            ← 总 Manager 整合（新增）
6. POST /api/v1/bridge/materialize     ← 创建 Spec + Task（新增）
7. POST /api/v1/coding-runs/mock       ← Mock Agent 执行（已有）
```

### 数据传递

```
/confirmation 返回 ConfirmationSummaryView
  → 调用方将其作为 /integration 的 body.confirmation 传入
    → /integration 返回 IntegratedPlanView
      → 调用方将其作为 /bridge/materialize 的 body 传入
```

---

## 五、测试

### 测试覆盖

| 测试文件 | 测试数 | 覆盖内容 |
|----------|--------|---------|
| test_plan_execution_bridge.py | 9 | Spec 创建、Contract 创建、Task 映射、跳过逻辑、空 plan、拓扑未找到、幂等键 |
| test_plan_integration.py | 16 | 拓扑排序、JSON 解析、Contract 过滤、fallback、LLM 调用 |
| test_confirmation.py | 20 | 确认状态解析、prompt 构建、missing_dependencies、confidence |
| 总计 | 45 | 全部通过 |

### 架构边界测试

`test_module_boundaries.py` 验证 bridge 不直接 import 队友的 application 模块，只通过 contracts + Protocol 交互。✅ 通过。

---

## 六、已知限制

| 限制 | 原因 | 后续 |
|------|------|------|
| Task 分派需要 Matrix | TaskOrchestrator 需要 CollaborationGateway | Matrix 环境就绪后在 container 注入 |
| Batch 等待未实现 | 需要异步轮询 progress() | 后续版本 |
| confirm/integrate 是同步调用 | 会阻塞 FastAPI 事件循环 | 后续改为 async 或用 BackgroundTasks |
| acceptance 是推断的 | 没有 Worker Agent 反馈 | 后续从 Task report 补充 |
