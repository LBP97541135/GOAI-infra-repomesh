# Room-Native Codex Bridge 最终验收标准

- **状态**：目标与验收口径已冻结，待实现与执行
- **日期**：2026-08-27
- **适用范围**：Room-Native Bridge、RepoMesh、AgentTeams、Matrix、RepoMesh Console、GitHub 三仓交付
- **关联执行计划**：[room-native-bridge-execution-plan-20260826.md](room-native-bridge-execution-plan-20260826.md)
- **当前交接基线**：[room-native-bridge-handoff-20260827-pr5.md](room-native-bridge-handoff-20260827-pr5.md)

> 本文定义最终目标和 PASS/FAIL 判据，不代表当前实现已经达到这些标准。若本文与早期执行计划在
> “是否支持 Repository Leader、前端展示、真实三仓 E2E”上存在范围差异，以本文作为最终验收口径。

## 1. 一句话目标

让本地 Codex CLI 在不使用成员容器的前提下，分别作为 RepoMesh Repository Leader 和
Worker 加入 AgentTeams/Matrix；完整走通 RepoMesh 从前端创建 Issue、仓库分析、计划、派活、
受治理执行、Leader 审查、ChangeSet 到三个真实 GitHub Draft PR 的产品链，并在 RepoMesh
前端正确展示身份、房间消息和交付事实。

## 2. 验收范围

### 2.1 纳入本期

1. **Codex Worker 核心工作流等价**：能进入房间、被提及、连续对话、自动接收标准派活，
   并通过 RepoMesh Runner/worktree 完成代码修改、commit 和结果回传。
2. **Codex Repository Leader 核心工作流等价**：能接收 Organization Manager 的仓库级任务，
   生成本仓 Engineering Spec 和任务 DAG，向 Worker 派活，审查证据并向 Manager 汇总。
3. **RepoMesh 前端匹配**：成员、角色、团队、房间消息、任务状态、commit、ChangeSet 和 PR
   在现有 Console 中可见；不建设 Bridge 专用页面。
4. **真实三仓交付**：从前端创建 Issue 开始，最终产生三个真实 GitHub Draft PR。

### 2.2 不纳入本期

1. Organization Leader 本地化；现有 AgentTeams Manager 继续承担该角色。
2. Claude Code、Kimi 等其他 CLI adapter。
3. Linux/POSIX Bridge 宿主；本期真实执行宿主为 Windows。
4. External Agent 的平台在线状态、heartbeat、uptime 和容器 ready 等价。
5. RepoMesh Room 页面内自由聊天输入框；自由对话仍在 Matrix 中进行。
6. 自动故障恢复成功、会话历史 backfill 和 `input_required` 问答恢复闭环。
7. 专项安全测试。
8. 等待或判定 GitHub Actions、代码质量和三仓联调质量。
9. 自动合并 Draft PR。

## 3. 固定验收环境与团队拓扑

### 3.1 运行边界

- RepoMesh API、数据库、Matrix、AgentTeams Controller 等平台服务可以使用 Docker。
- 六个成员的 Bridge 与 Codex CLI 必须直接运行在本机 Windows，不进入成员容器。
- 六个成员均为 `containerManaged: false`。
- 六个 Bridge 可以共享本机同一个 Codex 登录态，但 enrollment、Matrix 身份、状态目录和
  Codex thread 必须彼此隔离。
- 验收期间禁止使用 fake/mock coding adapter 代替真实 Codex。

### 3.2 最低团队规模

| RepoMesh 角色 | 数量 | 运行形态 | 职责 |
|---|---:|---|---|
| Organization Leader | 1 | 现有 AgentTeams Manager | 需求接收、跨仓规划和项目汇总 |
| Repository Leader | 3 | 本地 Codex + 独立 Bridge | 本仓规划、拆解、派活、审查、汇总 |
| Worker | 至少 3 | 本地 Codex + 独立 Bridge | 执行一个受治理的代码任务 |

最终验收时，三个 Leader Bridge 与三个 Worker Bridge 必须同时运行；不得用一个 Bridge
轮流更换身份来模拟六名成员。

## 4. 固定三仓场景

### 4.1 GitHub 仓库

| 位置 | 仓库 | 责任 |
|---|---|---|
| 上游 | `catbobyman/repomesh-e2e-pricing-core` | 共享报价契约与计价实现 |
| 下游一 | `catbobyman/repomesh-e2e-billing` | 发票渲染 |
| 下游二 | `catbobyman/repomesh-e2e-checkout` | 订单摘要 |

### 4.2 依赖图

```text
repomesh-e2e-pricing-core
        ├── repomesh-e2e-billing
        └── repomesh-e2e-checkout
```

`pricing-core` 是共享契约生产者；`billing` 与 `checkout` 是两个下游消费者。RepoMesh 必须
识别并展示这两条跨仓依赖，而不是把三个仓库当成三个互不相关的任务。

### 4.3 固定需求

> 完成多币种报价，并支持币种精度：USD/EUR 保留两位小数，JPY 等零小数币种按整数计价
> 和展示。`pricing-core` 扩展报价契约与舍入规则；`billing` 按真实币种精度计算和渲染发票；
> `checkout` 透传币种并返回正确币种和总额。

该需求用于证明一次真实的“一上游、两下游”三仓变更。验收不得通过预制完整 Task Spec、
手工填写 Task UUID 或直接修改数据库跳过 RepoMesh 的分析与计划环节。

## 5. 必须走通的产品链

```text
RepoMesh 前端创建 Issue
  → 需求分析
  → 仓库候选扫描与评分
  → 三档分类
  → 前端确认仓库范围
  → 生成三仓计划、契约与依赖图
  → Materialize
  → 创建或绑定三组 Team、Leader、Worker 与房间
  → Organization Manager 向三个 Codex Leader 分派仓库级任务
  → Codex Leader 生成本仓 Spec、任务 DAG、allowed paths、测试命令与 Worker 指派
  → Codex Worker 自动接单
  → RepoMesh 校验身份、assignee、任务状态和权限
  → Runner 准备 worktree，真实 Codex 修改代码并产生 commit
  → Codex Leader 审查并向 Organization Manager 汇总
  → RepoMesh 生成统一 ChangeSet
  → 推送三个候选分支
  → 创建三个真实 GitHub Draft PR
```

所有业务写操作必须从现有 RepoMesh 前端和正式 Agent/Runner 接口推进。验收过程中不得用
`curl`、临时脚本或数据库写入替代 Issue、发现、计划、Materialize、派活或结果上报。脚本只可
用于启动平台、Bridge/Codex 进程以及收集只读证据。

本场景使用 RepoMesh `auto` 执行模式，不增加执行阶段人工监督检查点。发现链已有的仓库范围
确认仍按现有产品流程完成，不能将其删除或绕过。

## 6. 功能完整性验收

### AC-01：真实 External Codex 身份

- 三个 Leader 和三个 Worker 均存在独立 RepoMesh AgentPrincipal、AgentTeams Worker 与
  Matrix 用户映射。
- 六个 binding 均明确返回 `containerManaged: false`。
- Docker 容器列表中不存在这六个成员的 Agent 容器。
- Windows 进程证据能对应到六个 Bridge 实例及其真实 Codex 子进程。

### AC-02：Repository Leader 遵守 RepoMesh 角色边界

- Codex Leader 负责 Spec、任务 DAG、派活、审查和汇总。
- Codex Leader 不领取 Worker coding Task，不修改生产代码，不调用编码执行入口，不创建代码
  commit。
- 只有 RepoMesh `worker` 身份能够进入 Runner 编码执行路径。

### AC-03：标准派活自动接单

- Worker 从 RepoMesh/AgentTeams 的标准任务分配消息取得任务。
- Worker 在正式启动前再次向 RepoMesh 校验 Task、assignee、状态和权限。
- 用户不需要输入 `start task <uuid>`，也不需要点击 Bridge 专用启动按钮。
- Matrix 消息只负责通知和唤醒，RepoMesh 持久化状态仍是任务真相源。

### AC-04：受治理执行

- Worker 只在 RepoMesh 创建的 task/run/worktree 中改动允许路径。
- commit、Run、Task、Worker 与仓库之间可以相互追溯。
- 房间中 Agent 自述“完成”不能直接推进 Task；终态仍由正式 Runner/RepoMesh 路径产生。
- 不关闭、不绕过 RepoMesh 当前已有的测试、路径和提交治理逻辑；但最终验收不单独把测试结果
  或 CI 结果设为 PASS 条件。

### AC-05：真实房间成员与通信层级

- 六个 Codex 成员均至少在授权房间发送一条身份正确的真实消息。
- 人工在 Matrix 中分别提及一名 Codex Leader 与一名 Codex Worker，两者都能在正确线程回复。
- 普通聊天不能触发仓库写入，正式 Task 才能进入执行路径。
- 通信遵循 `Manager → Leader → Worker → Leader → Manager`，Worker 不越级向 Manager 汇报，
  Manager 不越过 Leader 直接给团队 Worker 派活。
- 三个团队不能消费彼此不在 allowlist 内的房间消息。

### AC-06：RepoMesh 前端匹配

- Agents 页面显示三个 Repository Leader 和至少三个 Worker，并正确显示角色、仓库和 Team。
- External 成员的运行形态明确显示为 `External · Codex` 或等价文案。
- 前端不把 External 成员伪装成 `Container Running`，也不伪造 uptime、heartbeat 或 ready。
- 没有在线事实时显示“未提供”或 `—`，而不是将“没有数据”显示为运行故障。
- Teams 页面显示三组正确的 Leader/Worker 成员关系和对应房间。
- Room 页面显示真实 Matrix 消息以及派活、执行、commit、ChangeSet、PR 等控制面投影。
- 真实房间消息应在正常 5 秒轮询机制下于约 10 秒内出现在 RepoMesh Room 页面。
- 非 Matrix 的 Runner/治理投影继续作为系统条目展示，不伪装成某个 Agent 说过的话。
- Room 页面保持只读；自由对话继续在 Matrix 中进行。

### AC-07：异常最小安全底线

本期不要求 Bridge/Codex 崩溃后自动恢复并完成任务，也不执行完整故障注入套件。但如果执行中
发生异常：

- Task 必须最终显示为 `blocked` 或 `failed`，不能永久静默停留在“执行中”。
- 使用现有重派入口后，不得产生重复 commit、重复 PR 或互相冲突的重复终态。

## 7. GitHub 交付判据

### 7.1 必须产生的结果

本次验收必须通过正常 RepoMesh 流程新建三个真实 GitHub Draft PR，每个目标仓库恰好一个：

1. `catbobyman/repomesh-e2e-pricing-core`
2. `catbobyman/repomesh-e2e-billing`
3. `catbobyman/repomesh-e2e-checkout`

三个 PR 必须能追溯到本次 Issue、ChangeSet、对应 Task/Run、Codex Worker 和候选 commit。旧 PR、
旧分支、手工创建的 PR 或其他仓库中的 PR 不能计入结果。三个 PR 保持 Draft，不自动 merge。

### 7.2 交付结果的成功口径

本次是**功能链路验收**。GitHub 交付结果只判断“三个真实 Draft PR 是否存在且可追溯”：

- 不要求等待 GitHub Actions。
- 不把 GitHub CI 绿灯设为 PASS 条件。
- 不单独把三仓联合测试结果设为 PASS 条件。
- 不据此宣称代码质量、集成质量或生产可交付性已经验证。

RepoMesh 自身既有流程如果因为测试或治理失败而拒绝创建 PR，验收不得绕过该拒绝；此时因为
三个 PR 没有全部产生，最终结果仍然是 FAIL。

## 8. 总体 PASS/FAIL 规则

### PASS

只有同时满足以下两类条件，才可记录为 PASS：

1. **功能完整性**：AC-01 至 AC-06 全部通过；若验收期间触发异常，则 AC-07 同时通过。
2. **GitHub 交付结果**：本次流程产生三个真实、可追溯、保持 Draft 的 GitHub PR。

推荐的最终结论原文：

> Room-Native Codex 的核心工作流已经打通：本地 Codex 能以 RepoMesh Repository Leader
> 和 Worker 身份进入 AgentTeams/Matrix，遵守 RepoMesh 角色与执行治理，从前端 Issue 经三仓
> 分析、计划、派活和执行，最终产生三个真实 GitHub Draft PR；RepoMesh 前端可观察身份、房间
> 消息和交付事实。本结论不包含 GitHub CI、代码质量、自动恢复或自动合并认证。

### FAIL

出现下列任一情况即为 FAIL：

- 任一 Codex Leader/Worker 实际由成员容器或 fake adapter 代替。
- Leader 直接改代码或通过 Worker 执行入口提交代码。
- Worker 仍需人工输入 Task UUID 才能接单。
- 通过脚本、`curl` 或数据库写入代替正式产品链的业务步骤。
- Matrix 中能看到消息，但 RepoMesh Room 页面无法看到真实消息或显示成错误身份。
- 前端无法正确显示 External Codex 的角色、仓库、Team 或运行形态。
- 三个真实 GitHub Draft PR 未全部产生，或其中任一无法追溯到本次 Issue/Task/Run/commit。
- 使用旧 PR、手工 PR 或其他仓库 PR 代替本次产物。

## 9. 验收证据清单

最终验收报告至少保存：

1. 本次 RepoMesh Issue ID、需求原文和前端创建截图。
2. 三仓候选、分类结果、范围确认、计划图和两条依赖边截图。
3. 六个 RepoMesh AgentPrincipal/AgentTeams Worker/Matrix 用户的对应表。
4. 六份 external binding 的 `containerManaged: false` 只读证据。
5. Docker 容器列表与 Windows Bridge/Codex 进程列表。
6. 三组 Team、房间和成员关系截图。
7. Leader 派活、Worker 接单、双方汇报及人工抽验对话的 Matrix 证据。
8. RepoMesh Room 页面中相同消息和执行投影的前端证据。
9. 三个仓库各自的 Task ID、Run ID、commit SHA、候选分支和 PR URL。
10. 统一 ChangeSet ID 与三个 PR 的关联证据。

## 10. 当前实现与最终目标的已知差距

以关联执行计划和 PR5 交接记录为当前基线，最终验收前至少还需要补齐：

1. External Codex Repository Leader 的身份、房间、能力与协调执行路径。
2. 标准 RepoMesh 任务分配消息到 Bridge 自动 `start_assigned_task` 的接线，移除人工
   `start task <uuid>` 依赖。
3. Matrix 真实消息进入 RepoMesh Room 读模型/投影的可靠路径。
4. Agents/Teams 前端对 `External · Codex` 的诚实展示。
5. 六个独立 Bridge 实例的配置、启动与身份隔离。
6. 从前端 Issue 创建到三个真实 GitHub Draft PR 的完整三仓活体 E2E。

在这些差距关闭并按本文完成真实验收前，不得仅凭 PR 0–5 自动化测试通过就宣称最终目标完成。
