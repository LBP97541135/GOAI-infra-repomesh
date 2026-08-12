# RepoMesh Repository-Native Delivery PRD v0.1

- 文档状态：Draft
- 更新日期：2026-08-11
- 产品名称：Repository-Native Delivery（仓库原生交付）
- 用户入口名称：RepoMesh Repository Agent / `@RepoMesh`
- 目标版本：MVP v0.1
- 关联能力：Repository Intelligence、Project、Specification、Task Orchestration、
  AgentTeams、RepoMesh Runner、Review And Validation、Delivery

## 1. 背景

RepoMesh 已经具备从需求分析、仓库发现、规格冻结、任务编排、Runner 执行，到
Validation Snapshot、ChangeSet、PR、CI、治理决策和顺序合并的交付基础。目前这些能力
主要通过 RepoMesh API、脚本和交付控制台进入，用户仍需离开日常使用的代码仓库来发起
和跟踪任务。

CodeBuddy NPC 等仓库原生 Agent 证明了一种低摩擦交互方式：用户在 Issue 或 PR 中提及
Agent，即可异步获得分析、代码修改和 PR。RepoMesh 应吸收这种入口体验，但不能退化成
单仓库 Bot，也不能把 Issue、评论或外部 Agent 消息变成业务事实源。

本功能为 RepoMesh 增加一个可选的仓库原生入口。用户可以在代码托管平台中通过
`@RepoMesh` 发起需求、确认仓库范围、启动执行并查看状态；后续仍由现有 RepoMesh
控制面完成治理和交付。

## 2. 产品目标

### 2.1 核心目标

1. 用户无需打开 RepoMesh 控制台，即可从 Issue 发起一次受治理的交付请求。
2. Issue 所在仓库仅作为需求入口和发现种子，不被默认视为全部交付范围。
3. RepoMesh 使用现有仓库发现机制，在授权仓库池内给出候选仓库、证据和依赖关系。
4. 用户确认不可变的仓库范围与计划版本后，才允许创建正式 Project 和执行任务。
5. 执行、验证、PR 和交付复用现有 AgentTeams、Runner、Validation 和 Delivery 链路。
6. 仓库评论只作为传输和展示投影；PostgreSQL 继续作为业务事实源。
7. 功能默认关闭，关闭或卸载仓库 Agent 后不影响现有 API、控制台和进行中的 Delivery。

### 2.2 产品成功定义

对于一个已注册、已授权的 GitHub 仓库，具备 Developer 权限的用户能够：

```text
创建 Issue 并 @RepoMesh plan
→ 收到带证据的候选仓库范围和计划版本
→ 通过显式命令确认范围并启动执行
→ 在同一 Issue 中查看进度
→ 获得 RepoMesh 创建的候选 PR
→ 由人完成最终评审和合并
```

整个过程不得绕过现有 Task、Runner、Validation、ChangeSet 和治理记录。

## 3. 非目标

MVP 不包含：

- 使用 Issue 评论代替 RepoMesh Project、Task 或 Delivery 状态；
- 未经范围确认直接修改候选仓库；
- 由 Coding Agent 直接持有 GitHub Push、PR 或 Merge 凭据；
- 自动合并生产分支；
- 任意互联网仓库搜索；
- Fork PR 的代码写入；
- 在 Issue 中完成最终治理合并审批；
- 多代码托管平台同时首发；
- 复刻一个常驻 CodeBuddy NPC Runtime；
- 让 LLM 根据模糊自然语言自行决定是否进入写模式。

## 4. 核心术语

### 4.1 WorkRequest

仓库原生入口收到的需求请求。它存在于正式 Project 之前，用于保存来源、需求、发现结果、
范围确认和后续业务对象绑定。

### 4.2 Origin Repository

Issue 所在仓库。它是需求对话锚点和仓库发现种子，不等于最终修改范围。对于专门的需求
入口仓库，该仓库可以只接收需求而永不进入交付范围。

### 4.3 Candidate Repositories

Repository Intelligence 根据需求、入口仓库、仓库画像、依赖图和发现证据推荐的候选仓库。

### 4.4 Confirmed Repositories

用户确认后进入 Project Scope、Specification、ExecutionPlan 和 ChangeSet 的仓库集合。

### 4.5 Repository Thread

一个 Issue 或 PR 对话与 WorkRequest、Project、Plan、Delivery 之间的持久化绑定。

### 4.6 Control Comment

RepoMesh 在 Issue 中维护的一条状态评论。系统优先更新这条评论，而不是为每次状态变化
新增评论。

## 5. 用户角色

| 角色 | 需求 |
| --- | --- |
| 产品经理 | 在不知道具体代码仓库的情况下提交需求并确认业务范围 |
| 开发者 | 在发现问题的仓库中直接要求分析、规划或实现 |
| Repository Leader | 审核仓库范围、任务和候选变更 |
| Organization Leader | 处理跨仓库范围、风险和最终治理决策 |
| 平台管理员 | 安装 GitHub App、绑定组织、配置权限和预算 |

## 6. 产品原则

1. **入口与范围分离**：Issue 在哪里发，不决定代码改在哪里。
2. **先读后写**：默认命令只读；写入必须使用显式命令和已确认版本。
3. **先发现后建项目**：范围未确认时只创建 WorkRequest，不创建正式 Project。
4. **外部平台不是事实源**：评论丢失或 GitHub 暂时不可用时，RepoMesh 状态不能丢失。
5. **复用现有闭环**：仓库入口不得创建第二套 Runner、Task 或 Delivery 流程。
6. **失败关闭**：权限、配置、范围或版本不明确时拒绝执行并给出可操作原因。
7. **可选增强**：仓库 Agent 未启用时，当前所有行为保持不变。

## 7. Issue 应发在哪里

### 7.1 业务仓库 Issue

用户可在任意已注册且安装 RepoMesh App 的业务仓库中发 Issue。当前仓库作为高置信度发现
种子，RepoMesh 仍可将范围扩展到同一组织内的其他授权仓库。

适用场景：

- 从某个前端、API 或服务暴露出来的问题；
- 单仓需求可能存在跨仓影响；
- PR 或 CI 失败后的修复请求。

### 7.2 专用需求入口仓库

组织可配置 `repomesh-inbox`、`engineering-requests` 等无业务代码仓库。产品需求统一在该仓库
创建 Issue，RepoMesh 在组织授权仓库池中执行完整发现。

入口仓库可配置为 `intake_only`，表示它可以承载 WorkRequest 和对话，但不能成为交付目标。

### 7.3 PR 对话

PR 中的 `review` 或 `fix` 默认锁定当前 PR 仓库和当前 Head SHA。如果分析发现跨仓库影响，
RepoMesh 必须创建范围变更请求，不得在后台静默扩展写权限。

### 7.4 MVP 决策

MVP 支持业务仓库 Issue。专用需求入口仓库的数据模型在 v0.1 中预留，但其管理界面和自动
创建流程可在后续版本实现。

## 8. 仓库发现范围

候选仓库池必须满足：

```text
Candidate Pool
  = Organization 已注册仓库
  ∩ GitHub App 可见仓库
  ∩ Repository Agent 配置允许参与的仓库
  ∩ 发起人有权启动交付的范围
```

RepoMesh 不得因为某个必需仓库缺少写权限而从结果中删除它。系统应保留发现证据，并将该
仓库标记为阻塞项。

示例回复：

```text
已发现 3 个必需仓库：

✓ order-api：已授权，提供价格原因字段
✓ dashboard：已授权，展示价格原因
✗ payment-app：未安装 RepoMesh App 或无写权限

当前计划不可执行。完成授权后重新运行：
@RepoMesh plan refresh
```

## 9. 用户命令

### 9.1 MVP 命令

| 命令 | 是否写入代码 | 行为 |
| --- | --- | --- |
| `@RepoMesh ask <问题>` | 否 | 回答仓库、架构或交付状态问题 |
| `@RepoMesh plan <需求>` | 否 | 创建 WorkRequest 并生成候选范围与计划 |
| `@RepoMesh approve-scope plan:vN` | 否 | 确认指定计划版本的仓库范围 |
| `@RepoMesh work plan:vN` | 是 | 启动已确认计划的现有执行闭环 |
| `@RepoMesh status` | 否 | 返回当前 Thread 关联的交付状态 |

### 9.2 后续命令

- `@RepoMesh review`
- `@RepoMesh fix`
- `@RepoMesh retry`
- `@RepoMesh cancel`
- `@RepoMesh revise plan:vN <反馈>`
- `@RepoMesh evidence`

### 9.3 命令安全规则

- 没有明确命令时默认进入 `ask`，不得默认执行写操作。
- `work` 必须引用已确认且仍为最新版本的 `plan:vN`。
- 评论编辑不自动重新执行原命令；需要产生新的明确命令事件。
- Bot 自己的评论不得再次触发 RepoMesh。
- 一个 Repository Thread 在 MVP 中最多存在一个活跃 Delivery。

## 10. 用户流程

### 10.1 从 Issue 发起交付

```text
用户创建 Issue 并输入 @RepoMesh plan
  → 验证 Webhook 签名、App Installation 和用户权限
  → 创建 WorkRequest
  → 注册或读取 Origin Repository
  → Repository Intelligence 发现候选仓库并保存证据
  → 生成不可变 Plan Version
  → 在 Issue 回复范围、风险、缺失权限和确认命令
  → 用户 approve-scope
  → 用户 work
  → 创建 Project、冻结 Specification、物化 ExecutionPlan
  → AgentTeams / Runner 执行
  → Validation Snapshot / ChangeSet / PR
  → 更新 Control Comment 并通知用户评审
```

### 10.2 状态查询

`@RepoMesh status` 不读取历史评论推断状态，而是查询 RepoMesh 交付读模型，并将结果投影到
Issue。

### 10.3 失败处理

- 发现失败：保留已发现证据，返回可重试原因。
- 权限不足：拒绝写操作，说明缺少的权限或 App Installation。
- 计划版本过期：返回最新版本，不执行旧计划。
- Runner 失败：由现有 Task/Recovery 流程处理，Issue 仅展示投影。
- GitHub 回复失败：RepoMesh 状态继续推进，Outbox 后续重试。
- GitHub 暂时不可用：不得将 Delivery 标记为失败，除非超过明确重试策略。

## 11. WorkRequest 状态模型

```text
RECEIVED
  → DISCOVERING
  → AWAITING_SCOPE_CONFIRMATION
  → SCOPE_CONFIRMED
  → MATERIALIZED

任意非终态可进入：REJECTED / CANCELLED
```

`MATERIALIZED` 表示已经创建正式 Project/Plan；此后的执行状态由现有业务模块拥有，
WorkRequest 不复制 Task 或 Delivery 状态。

## 12. 概念数据模型

### 12.1 WorkRequest

| 字段 | 说明 |
| --- | --- |
| `id` | RepoMesh UUID |
| `organization_id` | 所属组织 |
| `requirement_text` | 规范化后的需求文本 |
| `origin_provider` | `github`，后续可扩展 |
| `origin_repository_id` | 入口仓库；Inbox 场景可为空 |
| `origin_thread_ref` | Issue/PR 外部引用 |
| `requested_by` | 已验证的外部用户引用 |
| `discovery_scope` | organization/project/explicit pool |
| `seed_repository_ids` | 发现种子 |
| `confirmed_repository_ids` | 用户确认范围 |
| `confirmed_plan_version` | 已确认计划版本 |
| `linked_project_id` | 物化后填写 |
| `linked_delivery_id` | 产生交付后填写 |
| `status` | WorkRequest 生命周期 |

### 12.2 RepositoryThread

保存 Provider、外部仓库、Issue/PR 编号、Control Comment ID，以及关联的 WorkRequest、
Project 和 Delivery。

### 12.3 RepositoryCommand

保存 Provider Event ID、Comment ID、Actor、命令类型、正文哈希、处理结果和错误信息。
Provider Event ID 必须唯一。

### 12.4 ActorAuthorizationSnapshot

保存命令发生时外部用户身份、仓库角色、授权来源、配置版本和决策结果。后续用户权限变化
不能改写历史证据。

### 12.5 RepositoryReplyOutbox

保存待发送的确认评论、状态更新、Check Run 和错误通知。外部回复必须可幂等重试。

## 13. 幂等与并发

```text
入站事件键：provider + installation_id + provider_event_id
命令键：repository_id + thread_id + comment_id + command_revision
回复键：interaction_id + reply_kind + revision
执行键：work_request_id + confirmed_plan_version
```

必须覆盖：

- Webhook 重放；
- 用户重复发送 `work`；
- 两个用户同时批准不同计划版本；
- 相同 Issue 同时启动两个执行；
- 回复成功但本地确认前进程崩溃；
- PR Head SHA 在评审或修复期间漂移。

## 14. 权限与安全

### 14.1 配置来源

仓库策略从默认分支的固定 Commit SHA 读取：

```yaml
# .repomesh/agent.yml
version: 1

repository_agent:
  enabled: true
  modes: [ask, plan, work]
  work_requires_role: developer

  discovery:
    scope: organization
    can_be_entrypoint: true
    can_be_delivery_target: true
    intake_only: false

  allowed_base_branches: [main]
  allowed_paths:
    - src/**
    - tests/**
  denied_paths:
    - .github/workflows/**
    - .repomesh/**
    - deploy/production/**

  limits:
    max_attempts: 2
    max_parallel_runs: 1
    max_cost_usd: 10

  delivery:
    create_pull_request: true
    auto_merge: false
    required_checks: [test]
```

### 14.2 强制规则

- Agent 不得修改 `.repomesh/**` 或本次权限策略。
- PR 分支中的策略修改不影响当前 Run。
- Issue、评论、仓库文件和测试日志均按不可信输入处理。
- Prompt 内容不能扩大 Repository、Path、Tool、Network 或 Secret 权限。
- Fork PR 默认无 Secret、无写权限。
- Secret 只通过引用解析，不进入评论、Runtime v1 或日志。
- Coding Agent 无远程 Push、PR 和 Merge 凭据。
- MVP 始终 `auto_merge=false`。

## 15. 系统设计

### 15.1 新模块

建议新增业务模块：

```text
src/repomesh/modules/repository_interaction/
├─ contracts.py
├─ domain.py
├─ application.py
├─ ports.py
├─ infrastructure.py
├─ README.md
└─ module.toml
```

该模块拥有 WorkRequest、RepositoryThread、RepositoryCommand、外部 Actor 授权快照和回复
Outbox。它不拥有 Project、Task、Run、Validation 或 Delivery。

### 15.2 Provider Adapter

```text
src/repomesh/integrations/repository_hosts/
├─ github.py
├─ cnb.py                 # 后续
└─ in_memory.py           # 行为测试
```

GitHub/CNB 细节不得进入业务模块。

### 15.3 深模块 Interface

仓库事件入口保持小而稳定：

```python
class RepositoryInteractionHandler(Protocol):
    async def ingest(
        self,
        event: RepositoryInteractionEvent,
    ) -> InteractionReceipt: ...

    async def get_thread(
        self,
        thread: RepositoryThreadRef,
    ) -> RepositoryInteractionView | None: ...
```

命令解析、身份验证、去重、范围确认、工作流调用和回复生成隐藏在该 Interface 后面。

### 15.4 现有模块复用

| 功能 | 生产模块 |
| --- | --- |
| 仓库注册、画像、发现证据 | Repository Intelligence |
| 正式项目与仓库范围 | Project |
| Engineering/Repository/Task Spec | Specification |
| Task DAG、批次、重试 | Task Orchestration |
| Agent 身份和层级 | Agent Directory |
| Worker 生命周期与通信 | AgentTeams |
| Worktree、CLI、测试、Commit | RepoMesh Runner / Agent Runtime |
| 验证快照 | Review And Validation |
| PR、CI、Merge Gate、Recovery | Delivery |
| 仓库内状态回复 | Repository Interaction 投影 |

新模块跨模块调用只能依赖生产模块的 `contracts.py`。如现有能力尚未暴露合适的 Interface，
应先由生产模块定义契约，再由组合根注入实现。

## 16. 仓库内展示

### 16.1 Control Comment

每个 Repository Thread 默认维护一条可更新评论：

```text
RepoMesh Delivery RM-1042

阶段：执行中
计划：v3
范围：order-api、dashboard
任务：2/3 完成
当前：运行集成测试
阻塞：无
控制台：查看完整证据
最后更新：2026-08-11 12:31 UTC
```

评论包含机器标记，用于恢复绑定但不作为事实源：

```html
<!-- repomesh:thread=<uuid>;revision=7 -->
```

### 16.2 Check Run

GitHub MVP 创建名为 `RepoMesh Delivery` 的 Check Run，展示当前阶段并链接到 RepoMesh
控制台。Check 状态是业务状态的投影，删除 Check 不影响 Delivery。

## 17. 概念 HTTP 接口

```text
POST /api/v1/repository-interactions/github/webhook
GET  /api/v1/repository-interactions/{interaction_id}
POST /api/v1/repository-interactions/{interaction_id}/reconcile
```

Webhook 在完成签名检查和持久化后快速返回 `202`，不得同步等待规划或 Runner 执行。

为保证现有功能隔离，MVP 使用独立 GitHub App、独立 Webhook Secret 和独立路由，不修改
现有 Delivery Webhook 语义。后续如需合并 GitHub Ingress，应保持旧路由兼容。

## 18. 非功能需求

### 18.1 可靠性

- 入站事件至少一次投递、应用内幂等处理；
- 外部回复使用持久化 Outbox；
- 外部平台不可用不丢失 WorkRequest；
- 重启后可以从 PostgreSQL 恢复未完成交互；
- 所有外部副作用具备稳定幂等键或明确重试策略。

### 18.2 性能

- Webhook 持久化响应目标：P95 小于 2 秒；
- 首条确认回复目标：P95 小于 10 秒；
- 仓库发现和执行为异步长任务，不占用 Webhook 请求连接。

### 18.3 可观测性

每个命令记录：

- Provider Event ID；
- WorkRequest、Project、Plan、Task、Run、Delivery 关联 ID；
- Actor 授权快照；
- 解析出的命令与计划版本；
- 入站、业务处理、出站回复状态；
- 拒绝原因、重试次数和耗时。

### 18.4 数据安全

- 原始 Webhook 正文按保留策略存储或保存内容哈希；
- 日志禁止输出 Token、Webhook Secret 和 Secret 值；
- 外部评论中的个人信息遵循组织数据保留策略；
- 删除外部 Issue 不自动删除 RepoMesh 审计证据。

## 19. MVP 范围

MVP v0.1 包含：

- GitHub；
- 已注册的单个入口业务仓库；
- 组织内授权仓库发现；
- `ask`、`plan`、`approve-scope`、`work`、`status`；
- WorkRequest 和 Repository Thread 持久化；
- 候选仓库证据与范围确认；
- 复用现有执行闭环创建候选 Commit 和 PR；
- Control Comment 和 RepoMesh Delivery Check；
- 独立 GitHub App；
- 功能开关，默认关闭；
- 最终合并仍由现有控制台或 GitHub 人工流程完成。

MVP v0.1 不包含：

- 自动合并；
- Fork 写入；
- PR 内自动修复；
- CI 自动 Repair Loop；
- Issue 内最终治理审批；
- CNB/GitLab Adapter；
- 多个同时运行的 Repository Thread Delivery。

## 20. 验收标准

### 20.1 正常流程

1. 已授权用户在已注册仓库 Issue 中执行 `@RepoMesh plan <需求>`。
2. 系统创建且仅创建一个 WorkRequest。
3. 回复包含候选仓库、每个仓库的发现证据、依赖关系和计划版本。
4. Issue 所在仓库出现在种子证据中，但不被强制纳入最终范围。
5. 用户批准当前版本后可以执行 `work`。
6. `work` 创建正式 Project、Specification 和 ExecutionPlan。
7. Runner 成功后生成 Commit，Delivery 创建 PR。
8. 同一 Issue 的 Control Comment 展示最新状态并链接到控制台。
9. 评论和 Check 被删除后，RepoMesh 仍能从数据库返回完整状态。

### 20.2 权限和失败关闭

1. 未安装 App、仓库未注册或用户权限不足时不得创建执行计划。
2. 旧计划版本执行请求返回冲突，不启动 Runner。
3. 必需仓库缺少权限时保留该仓库及证据，并阻止执行。
4. 模糊命令不得触发代码写入。
5. Fork PR 不得获得写权限或 Secret。
6. Agent 修改 `.repomesh/**` 时 Runner 拒绝提交。

### 20.3 幂等

1. 相同 Webhook 重放不会创建第二个 WorkRequest。
2. 相同 `work plan:vN` 重放不会创建第二个 Project、Run 或 PR。
3. GitHub 评论发送成功后进程崩溃，恢复时不得产生重复评论。
4. 两个并发执行请求只有一个获得活跃 Delivery。

### 20.4 兼容性

1. 功能关闭时现有 API 路由、后台 Worker 和 Delivery 行为不变。
2. 未配置 Repository Agent 的仓库行为不变。
3. 卸载 Repository Agent App 后，已开始的 Delivery 可继续从控制台处理。
4. 现有 GitHub Delivery Webhook 的事件处理语义不变。

## 21. 指标

### 21.1 产品指标

- Issue 到首个范围建议的时间；
- 范围建议被直接接受的比例；
- 从 `work` 到 PR 的时间；
- 首次验证通过率；
- 平均人工交互次数；
- 仓库发现漏选/误选率；
- 每次 WorkRequest 的模型与运行成本。

### 21.2 可靠性指标

- Webhook 重复率与去重成功率；
- Outbox 重试次数；
- 回复失败率；
- 重复 Project、Run、PR 数量，目标为 0；
- 权限拒绝和配置漂移次数；
- 过期计划执行拦截次数。

## 22. 发布策略

### 阶段 0：只读试点

- `ask`、`plan`、`status`；
- 不创建 Worktree 或 Run；
- 验证 Webhook、身份、发现质量和评论体验。

### 阶段 1：单仓受控执行

- 开启 `approve-scope` 和 `work`；
- 只允许单仓 confirmed scope；
- 创建 PR，不自动合并。

### 阶段 2：多仓执行

- 允许确认多个仓库；
- 使用现有执行批次、ChangeSet 和合并顺序；
- 缺少任一必需仓库权限时失败关闭。

### 阶段 3：PR Review 和 Repair

- 增加 `review/fix/retry/cancel`；
- CI 失败创建新的 Rework Task；
- 设置稳定身份和最大重试次数。

### 阶段 4：多平台

- CNB NPC Adapter；
- GitLab Adapter；
- 专用 RepoMesh Inbox 管理体验。

## 23. 功能开关与回滚

默认配置：

```text
REPOMESH_REPOSITORY_AGENT_ENABLED=false
```

功能可以按部署、组织和仓库三级关闭。关闭后：

- 不再接受新的 Repository Interaction；
- 不删除 WorkRequest 和审计记录；
- 不取消已经启动的 Delivery；
- 用户仍可在现有控制台查看和处理进行中的交付；
- Outbox 可配置为完成既有回复或停止投递。

## 24. 风险

| 风险 | 缓解措施 |
| --- | --- |
| Issue 被误认为交付事实源 | 所有状态持久化在 RepoMesh，评论仅投影 |
| LLM 将普通问题误判为写请求 | 写操作要求显式命令和计划版本 |
| 仓库发现跨越权限范围 | 候选池先做组织、App 和策略交集 |
| Prompt Injection 扩权 | 策略在 Prompt 外强制执行，仓库内容视为不可信 |
| Bot 评论刷屏 | 每个 Thread 维护一条 Control Comment |
| Webhook 重复创建任务 | 入站、命令、执行和回复四层幂等键 |
| 多仓库中部分仓库未授权 | 保留证据并失败关闭，不静默删除仓库 |
| 新入口影响现有 Delivery | 独立模块、路由、App、Schema 和默认关闭开关 |
| GitHub 用户无法映射治理主体 | MVP 不允许 Issue 内最终治理审批 |

## 25. 待决策项

1. 产品公开名称使用 `RepoMesh Repository Agent` 还是 `RepoMesh NPC`。
2. MVP 是否允许第一次执行就支持多仓库，或先限制 confirmed scope 为单仓。
3. GitHub App 是否长期与 Delivery App 分离，还是在统一 Ingress 稳定后合并。
4. WorkRequest 全文、原始 Webhook 和评论正文的保留周期。
5. 外部 GitHub 用户与 RepoMesh Human Identity 的正式绑定方式。
6. `cancel` 对已产生 Commit、PR 或部分合并 ChangeSet 的精确定义。
7. 专用 RepoMesh Inbox 是仓库、控制台虚拟入口，还是二者均支持。
8. Control Comment 使用更新模式还是追加事件模式；MVP 推荐更新模式。

## 26. 推荐决策

为最大限度降低风险并快速验证价值，建议采用以下默认方案：

1. 核心采用提供商中立的 `repository_interaction` 深模块。
2. GitHub 作为首个 Adapter，CNB 和 GitLab 后续接入同一 Interface。
3. Issue 所在仓库只作为 Origin Repository 和发现种子。
4. 范围确认前创建 WorkRequest，确认后才创建 Project。
5. MVP 先完成单仓 Issue → Plan → Work → PR，数据模型保留多仓能力。
6. 最终合并继续由人和现有治理链处理。
7. 使用独立 GitHub App、独立 Webhook 路由和默认关闭开关。

该方案获得仓库原生 Agent 的低摩擦入口，同时保留 RepoMesh 在跨仓库发现、事实状态、
权限治理、独立验证和可恢复交付方面的产品差异。
