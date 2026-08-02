# RepoMesh 并行开发任务分工

- 文档状态：可执行基线
- 基线版本：0.1
- 更新日期：2026-08-02
- 适用范围：MVP 第一条端到端交付链

## 1. 当前基线

已经完成：模块化单体、模块边界测试、PostgreSQL/Alembic、StateEvent/Audit/Outbox、
Repository Catalog 基础实现、Agent Runtime Port、Scenario Mock、CI 和团队协作模板。

当前目标不是同时填满 13 个模块，而是跑通：

```text
PRD -> Engineering Spec -> 仓库发现 -> 人工确认范围 -> Contract
    -> Task DAG -> Mock Agent -> Validation -> ChangeSet
```

## 2. 团队与目录

| 工作组 | Owner 标签 | 主要目录 | 第一责任 |
| --- | --- | --- | --- |
| 仓库智能组 | `repository-intelligence` | `modules/repository_intelligence` | 扫描、Profile、发现证据 |
| 项目规格组 | `project-planning` | `modules/project`、`specification`、`change_control` | PRD、Spec、范围、Contract |
| 编排组 | `orchestration` | `task_orchestration`、`context`、`collaboration` | Task、上下文、提问路由 |
| Runtime 组 | `runtime-integrations` | `agent_runtime`、`integrations/agentteams`、`coding_agents`、`workspace` | AgentTeams、执行器、工作区 |
| 质量交付组 | `quality-delivery` | `review_validation`、`delivery`、`integrations/scm`、`ci` | 四级验证、PR、ChangeSet |
| 平台组 | `platform` | `persistence`、`observability`、`identity_access`、`bootstrap` | 数据、权限、事件、运行底座 |

实际 GitHub 账号由项目负责人写入 CODEOWNERS；Owner 标签表示职责，不代表已分配到具体人。

## 3. 依赖主线

```text
PLAT-01 公共契约与认证上下文
   |------------------|-------------------|
   v                  v                   v
RI-01/02 仓库画像   PP-01/02 项目规格   RT-01/02 Runtime
   |                  |                   |
   +-------> PP-03 仓库范围确认 <---------+
                      |
                  PP-04 Contract
                      |
             ORCH-01 Task DAG/TestPlan
                      |
       CTX-01 Context + RT-03 TaskRun
                      |
                QA-01/02 四级验证
                      |
                DEL-01 ChangeSet/PR
```

Observability 从第一天旁路消费事件，不得成为业务命令入口。

## 4. 第一批：立即并行

### 仓库智能组

| ID | 任务 | 依赖 | 交付物 | 完成标准 |
| --- | --- | --- | --- | --- |
| RI-01 | Repository Provider 与 GitHub 接入 | PLAT-01 标识符 | Provider Port、GitHub Adapter、仓库注册命令 | 可注册仓库；重复命令幂等；凭据不落库 |
| RI-02 | Baseline Scan 与 Profile Version | RI-01 | ScanRun、ProfileVersion、Evidence、迁移 | 记录 source SHA、扫描器版本、证据；旧版本保留 |
| RI-03 | Profile 新鲜度 | RI-02 | stale 判定、刷新命令和事件 | 关键文件变化后旧 Profile 标记过期 |
| RI-04 | Discovery MVP | RI-02、PP-02 | 候选召回、置信度、理由和证据 | 单/双/多仓库及模糊需求用例通过 |

### 项目规格组

| ID | 任务 | 依赖 | 交付物 | 完成标准 |
| --- | --- | --- | --- | --- |
| PP-01 | Project 与 PRD Version | PLAT-01 | Project 状态机、PRD 版本、CreateProject | PRD 创建 DRAFT Project；原文和作者可追踪 |
| PP-02 | Engineering Spec Version | PP-01 | Spec 模型、发布命令、验收项 | Spec 版本不可覆盖；至少一个可验证验收项 |
| PP-03 | Repository Scope | RI-04、PP-02 | 候选、ScopeReview、确认命令 | PM 确认前编码命令被拒绝；分类完整 |
| PP-04 | Contract 生命周期 | PP-03 | Owner 分配、版本、审批、冻结 | 生产者起草、消费者审批、PM 冻结 |

### Runtime 组

| ID | 任务 | 依赖 | 交付物 | 完成标准 |
| --- | --- | --- | --- | --- |
| RT-01 | 固定 AgentTeams 版本与映射 | PLAT-01 | Team/Manager/Worker/Skill 映射表和契约测试 | RepoMesh ID 可稳定映射；不以消息为事实源 |
| RT-02 | Adapter Registry | 现有 Runtime Port | Manifest、注册表、健康探测、能力声明 | 空/重复 ID 被拒绝；缺 CLI 返回类型化错误 |
| RT-03 | Workspace Adapter 基础 | 现有 Scenario Mock | Create/Destroy/Preserve/Restore | 脏目录普通删除被拒绝；新增/修改文件可恢复 |
| RT-04 | Mock Runtime 生命周期 | RT-02 | Run、Session、事件、取消/中断/恢复 | 七类 Scenario 都产生确定状态和证据 |

### 平台组

| ID | 任务 | 依赖 | 交付物 | 完成标准 |
| --- | --- | --- | --- | --- |
| PLAT-01 | 公共标识符与命令上下文 | 无 | ID 类型、Command Context、错误契约 | actor 不从正文读取；UTC/UUID 规则统一 |
| PLAT-02 | 幂等命令中间件 | 当前数据库基础 | reserve/replay/complete 流程 | 同 scope/key 返回首次结果；请求 hash 冲突返回 409 |
| PLAT-03 | Outbox Publisher | 当前 Outbox | claim、发布、重试、失败记录 | 至少一次投递；并发 Publisher 不重复持有租约 |
| PLAT-04 | Audit/Timeline 查询 | PLAT-03 | 项目/聚合时间线查询 | 可按 correlation、project、task、run 查询且脱敏 |
| PLAT-05 | 基础认证与授权 | PLAT-01 | actor 注入、Policy Port、拒绝审计 | 403 与 409 分离；密钥只保存引用 |

## 5. 第二批：范围和 Contract 稳定后

| ID | Owner | 任务 | 前置条件 | 完成标准 |
| --- | --- | --- | --- | --- |
| ORCH-01 | orchestration | Task/TaskSpec 状态机 | PP-04 | 规格版本不可变；非法迁移返回机器码 |
| ORCH-02 | orchestration | Task DAG、READY、路径冲突 | ORCH-01 | 跨仓库可并行；同仓库冲突路径被阻止 |
| ORCH-03 | orchestration | Lease、Retry、Checkpoint | RT-04 | 单 Task 只有一个有效 Lease；崩溃可恢复 |
| CTX-01 | orchestration | ContextObject/Version/Bundle | PP-02、PP-04 | 每个 Bundle 固定版本和 hash |
| CTX-02 | orchestration | Context Workspace 与访问审计 | CTX-01、RT-03 | 只读物化；按需读取；每次读取可审计 |
| COL-01 | orchestration | Worker 提问与 Manager 回答 | RT-04 | 提问暂停 Run；答案不静默修改 TaskSpec |
| QA-01 | quality-delivery | TestPlan 与四级 TestCase | PP-04、ORCH-01 | 编码前发布；缺少门禁不得执行 |

## 6. 第三批：Candidate 可以稳定产出后

| ID | Owner | 任务 | 前置条件 | 完成标准 |
| --- | --- | --- | --- | --- |
| QA-02 | quality-delivery | Candidate 与 Repository Review | RT-04、ORCH-03 | Candidate hash 唯一；Manager 可请求修改 |
| QA-03 | quality-delivery | Task/Repository Validation | QA-01、QA-02 | 证据版本化；失败退回正确 Task/Workstream |
| QA-04 | quality-delivery | ValidationSnapshot、Joint/Regression | QA-03 | 输入 SHA/Contract/TestPlan 固定；变化使旧结果失效 |
| DEL-01 | quality-delivery | ChangeSet 与 RepositoryDelivery | QA-04 | 汇总多仓库候选、验证、依赖和回滚计划 |
| DEL-02 | quality-delivery | GitHub PR Coordinator | DEL-01 | 重复创建返回原 PR；Coding Agent 无远程写权限 |
| CC-01 | project-planning | ChangeRequest 与影响传播 | ORCH-03、QA-01 | 目标/范围/Contract 变化进入 CHANGE_PENDING |
| OBS-01 | platform | 六类查询视图和 SSE | 上述事件稳定 | 前端不需要跨模块拼事实表 |

## 7. 集成检查点

| 检查点 | 必须同时通过 |
| --- | --- |
| G1 仓库可发现 | RI-01~04、PP-01~03、Profile/Scope 公共契约测试 |
| G2 任务可执行 | PP-04、ORCH-01~03、CTX-01~02、RT-01~04、QA-01 |
| G3 修改可验证 | QA-02~04、固定 ValidationSnapshot、失败回流 |
| G4 结果可交付 | DEL-01~02、幂等 PR、ChangeSet、审计时间线 |

未通过上一个检查点，不得把下一个阶段标记为可验收；可以开发内部实现，但不能冻结其公共契约。

## 8. 每个任务的完成定义

每个 Issue/PR 必须包含：

1. Owner 模块和对应规范/验收编号。
2. Command、Query、Event 的新增或变化。
3. 数据迁移、约束和回滚方式。
4. 状态前置条件、409/403/422 等失败行为。
5. 幂等、并发、超时、重试和取消行为。
6. 单元测试、契约测试；涉及方言时增加 PostgreSQL 集成测试。
7. 可观察事件和证据；不得记录密钥或隐藏思维过程。
8. 生产方 Owner 和受影响消费方 Owner 的 Review。

