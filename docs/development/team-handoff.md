# RepoMesh 团队交接与后续开发总览

- 更新日期：2026-08-03
- 当前分支：`main`
- 架构基线提交：`d5e9775`
- 目标：从需求和产品原型出发，完成可审计、可恢复、可验证的多仓库 Agent 代码交付

## 1. 一句话架构

RepoMesh 决定做什么、谁可以做和结果是否合格；AgentTeams 管理多 Agent 的运行关系和
生命周期；RepoMesh Runner 在 Worker 内调用 Coding Agent 写代码和运行测试。

```text
RepoMesh Product Control Plane
    -> AgentTeams Runtime Control Plane
        -> RepoMesh Runner
            -> Codex / Claude Code / Cursor / other coding CLIs
```

这三个部分属于同一个 RepoMesh 产品和仓库，但保持独立的进程、语言和数据边界。

## 2. 项目目录

所有成员都在同一个仓库开发，不复制另一套业务代码：

```text
infra/
├─ src/repomesh/                    # Python 产品控制面
│  ├─ api/                          # 对外 API
│  ├─ modules/                      # 业务模块
│  ├─ integrations/                 # AgentTeams、Coding Agent、SCM、CI 等适配器
│  ├─ persistence/                  # 平台数据库、事务和 Outbox
│  ├─ bootstrap/                    # 组合根和启动
│  └─ shared/                       # 公共基础类型和事件
├─ src/repomesh_runner/             # Python Worker 执行器
├─ components/agentteams/           # 第一方 Go Runtime 源码
├─ components/repomesh-runner/      # Runner 组件描述
├─ contracts/runtime/v1/            # Python/Go/Runner 跨进程契约
├─ migrations/                      # PostgreSQL 迁移
├─ tests/                           # 行为、契约、架构和集成测试
└─ docs/                            # 架构、规范和开发计划
```

## 3. RepoMesh 与 AgentTeams 的边界

| 工作 | RepoMesh | AgentTeams | Runner / Coding Agent |
| --- | --- | --- | --- |
| PRD、规格、契约 | 事实源和审批 | 不负责 | 可辅助起草，不保存事实 |
| 仓库发现和范围确认 | 负责 | 不负责 | 可提供扫描证据 |
| Task DAG、并行和重试策略 | 负责 | 不负责 | 执行单个已授权任务 |
| Agent 身份、项目成员和授权 | 事实源 | 接收运行投影 | 按授权执行 |
| Manager、Worker、Team 生命周期 | 发出期望状态 | 创建和协调 | 在 Worker 中运行 |
| Matrix 房间和消息传输 | 决定允许的路由 | 创建并传输 | 发送问题和结果 |
| 代码修改和本地测试 | 定义任务和门禁 | 不负责 | Runner 调用 Coding Agent 执行 |
| 验证、PR、合并和回滚 | 负责 | 不负责 | Coding Agent 无远程写权限 |

RepoMesh PostgreSQL 是企业交付状态的事实源。AgentTeams 资源和 Matrix 消息只是运行投影，
不能代替 Project、Task、Context、Validation 或 Delivery 记录。

## 4. 业务模块与 Owner

| 工作组 | 主要目录 | 第一责任 |
| --- | --- | --- |
| 仓库智能组 | `src/repomesh/modules/repository_intelligence` | 注册、扫描、依赖证据、仓库推荐 |
| 项目规格组 | `modules/project`、`specification`、`change_control` | 项目范围、PRD、Spec、Contract、变更审批 |
| 上下文权限组 | `modules/context`、`identity_access` | 可见性、Bundle、Delta、权限和审计 |
| 编排组 | `modules/task_orchestration`、`agent_directory`、`collaboration` | Task DAG、Agent 选择、租约、重试和提问路由 |
| Runner 组 | `modules/agent_runtime`、`src/repomesh_runner`、`integrations/coding_agents`、`workspace` | Run、Session、Worktree、CLI、测试和结果 |
| AgentTeams 组 | `components/agentteams`、`integrations/agentteams` | Worker/Team/Matrix、资源投影和生命周期 |
| 质量交付组 | `modules/review_validation`、`delivery`、`integrations/ci`、`scm` | 测试门禁、证据、ChangeSet、PR 和回滚 |
| 平台组 | `persistence`、`observability`、`bootstrap`、`migrations` | 数据库、事件、日志、配置和服务启动 |

表中省略的相对路径默认位于 `src/repomesh/`。完整所有权以
`docs/architecture/module-map.md` 和各模块 `module.toml` 为准。

## 5. 上下文与 Worker 可见性

上下文不是只给 Coding Agent 的 Prompt。它控制所有 Manager、Worker、Validator 和交付角色
可以发现、读取、挂载、发布和批准什么。

### 5.1 四类可见性

1. Agent 可见性：A 能否发现 B、查看 B 的职责或向 B 提问。
2. 上下文可见性：能否发现、读取、挂载、发布或批准某份资料。
3. Skill 可见性：能否发现、加载和执行某个 Skill。
4. 运行空间可见性：能否访问仓库、路径、工具、网络和其他 Run。

### 5.2 权限计算

每次 Run 启动前，RepoMesh 应生成不可变的 `VisibilitySnapshot`：

```text
EffectivePermission
  = AgentPolicy
  ∩ ProjectMembership
  ∩ TaskSpec
  ∩ RunDelegation
  - ExplicitDeny
```

Snapshot 编译出该 Worker 可见的 Agent、ContextObjectVersion、Skill、仓库、路径、工具、网络
和有效期。任何一层拒绝都必须拒绝访问并记录 AuditEvent。

### 5.3 运行时执行

1. RepoMesh Context 计算可见性并生成不可变 Context Bundle。
2. RepoMesh 将允许的 Team、Worker、房间成员和 Skill 投影给 AgentTeams。
3. AgentTeams 创建对应运行资源，不得自行扩大成员或权限。
4. Runner 将 `context/` 只读挂载，并执行路径、工具和网络白名单。
5. 查询、读取、挂载、执行、发布和拒绝行为都写入访问审计。

例如 A 的 Skill 只授权给 B、对 C 显式拒绝时，C 必须无法从目录、API、Matrix、文件系统、
日志或错误信息中判断该 Skill 是否存在。不能只依靠 Prompt 隐藏。

### 5.4 动态变化

普通补充事实通过有序 `ContextDelta` 发送给指定 Run。若变化涉及目标、验收、Contract、
仓库范围、base SHA、Skill 或权限，必须创建 ChangeRequest，生成新的 Snapshot 和 Bundle；
安全撤权应立即终止租约和临时凭据。

## 6. 一条需求的完整流程

```text
用户需求
  -> Specification 保存 PRD 和 Engineering Spec
  -> Repository Intelligence 推荐仓库并保留证据
  -> Organization Leader 在临时项目上下文中确认仓库范围
  -> Specification 冻结跨仓 Contract 和验收标准
  -> Task Orchestration 生成 Task DAG 和执行顺序
  -> Context 生成每个 Worker 的 VisibilitySnapshot 和 Bundle
  -> Agent Runtime 创建 CodingRun
  -> AgentTeams 创建 Manager、Worker、Team 和房间
  -> Runner 创建 Worktree、挂载上下文并调用 Coding Agent
  -> Runner 执行检查和测试并回传有序事件
  -> Review Validation 保存测试证据并给出验收结果
  -> Delivery 创建 ChangeSet、PR、合并顺序和回滚记录
```

失败时，Runner 回传标准失败事件；Agent Runtime 保存 Session 和证据；Review Validation
保存失败日志；Task Orchestration 决定恢复原 Session、重试、换 Agent、重新规划或终止。
AgentTeams 负责把反馈送达 Worker 和管理生命周期，不决定业务重试策略。

## 7. 当前完成状态

已经完成的基础：

- 模块边界、Owner、CODEOWNERS 和架构测试；
- PostgreSQL、Alembic、StateEvent、AuditEvent 和 Outbox；
- Repository Intelligence 基础纵向切片；
- Context 不可变版本、六种 scope、权限交集、Bundle、Delta 和访问审计；
- 23 个 Coding Agent 适配声明、统一 Registry 和 Scenario Mock；
- AgentTeams v1.2.0 第一方源码、Controller/Matrix 适配和启动依赖；
- Runtime v1 JSON Schema 和 RepoMesh Runner 核心事件模型。

尚未完成的端到端能力：

- Project、Specification、Task Orchestration 的完整应用服务；
- `VisibilitySnapshot`、角色视图和 Project Space 查询 API；
- Identity/Project/Task 权限提供器；
- S3/MinIO 内容存储和 Context Workspace 物理只读挂载；
- AgentTeams 的 Worker/Team/Matrix/Skill 可见性投影；
- Runner 的真实 Worktree、CLI、测试 Harness 和事件传输；
- Review Validation、CI、Delivery、PR 和回滚闭环。

当前架构底座可测试，但完整产品链路尚未跑通。

## 8. 下一批并行任务

| 流 | 目录 | 首个交付 |
| --- | --- | --- |
| VIS-01 | `modules/context`、`identity_access` | VisibilitySnapshot、角色视图、A/B/C 隔离测试 |
| ORCH-01 | `project`、`specification`、`task_orchestration` | PRD -> Scope -> Contract -> Task DAG |
| ATP-01 | `agent_directory`、`integrations/agentteams` | 持久化资源绑定和幂等 Worker/Team 投影 |
| RUN-01 | `src/repomesh_runner`、`integrations/workspace` | Worktree、只读 Context Workspace 和结果收集 |
| ATG-01 | `components/agentteams` | Runtime 元数据、房间成员和 Skill 策略执行 |
| QA-01 | `review_validation`、`integrations/ci` | TestPlan、测试结果和不可变验证证据 |

推荐先冻结 VIS-01、ATP-01 和 RUN-01 使用的 Runtime/Context 契约，再让实现并行。第一次修改
`components/agentteams` 前，必须建立 RepoMesh 产品 Fork，并记录产品 Fork、官方 upstream 和
Runtime contract 三个兼容版本。

## 9. 分支和 Pull Request 规则

建议分支：

```text
feat/context-visibility
feat/task-orchestration
feat/agentteams-projection
feat/runner-workspace
feat/review-validation
```

开发规则：

1. 开工前阅读目标模块的 `README.md` 和 `module.toml`。
2. 一个 PR 原则上只由一个模块拥有。
3. 跨模块时先改生产方公共契约和契约测试，再分别实现。
4. 跨模块 import 只能指向 `repomesh.modules.<producer>.contracts`。
5. 不得直接查询或写入其他模块的 PostgreSQL schema。
6. 外部副作用必须有幂等键或明确的重试、超时和取消策略。
7. Coding Agent 不得直接 Push、创建 PR 或 Merge。
8. Matrix 消息和 AgentTeams 资源不得成为业务事实源。

提交前执行：

```powershell
uv run ruff check .
uv run pytest
```

## 10. 换电脑后继续开发

```powershell
git clone https://github.com/LBP97541135/GOAI-infra-repomesh.git
cd GOAI-infra-repomesh
git pull --ff-only
uv sync --extra dev
docker compose up -d postgres
uv run alembic upgrade head
uv run pytest
uv run uvicorn repomesh.main:app --reload
```

API 文档默认位于 `http://127.0.0.1:8000/docs`。需要完整 AgentTeams 平台时，在 PowerShell
7+ 和 Docker 环境执行：

```powershell
.\scripts\start-platform.ps1 -InstallAgentTeams
```

明天开始开发前，先确认 `git status` 干净、`main` 已拉取最新提交，再从上述下一批任务创建
独立分支。详细任务编号继续参考 `docs/development/parallel-work-plan.md`。
