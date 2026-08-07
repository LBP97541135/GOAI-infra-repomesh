# feat/plan-execution-loop 工作报告（2026-08-07）

- 作者：catmem（主脑编排：Fable 5；并行实现：6 个 Opus 5 subagent 工作流 W1-W5b）
- 分支：`feat/plan-execution-loop`（W1-W4 已推 GitHub 共 6 个提交；W5 已验收、待提交）
- 关联文档：`docs/development/closed-loop-gap-analysis-20260807.md`（开工前的差距分析）、
  `docs/test-results/live-plan-loop-20260807.md`（实机验证证据）、
  `docs/test-results/live-e2e-repro-20260807.md`（前一日环境复现）

---

## 1. 一句话总览

**把"计划"接上"执行"**：改动前，`POST /bridge/materialize` 产出的计划止步于文档
（Spec 落库、任务要么不创建要么不可执行）；改动后，一次 materialize 调用即触发
按依赖批次推进的真实执行——Leader 任务分解为 Worker 子任务、执行许可（TASK Spec）
同步产出、Runner 终态事件自动推进下一批，全程可观测、幂等、可重放。
当日已在本机活体验证到 `plan COMPLETED`。

## 2. 改动前的断点（为什么要做）

开工前经代码核实（非文档转述）确认五个断点：

| # | 断点 | 位置 | 后果 |
|---|---|---|---|
| A | `plan_execution_bridge()` 硬编码 `tasks=None` | `bootstrap/container.py` | materialize 永不创建任务，`task_dag` 全部进 `skipped_repos` |
| B | 任务 assignee 是 Leader，但 `start_assigned_task` 只准 Worker 执行且校验 assignee | `worker_execution.py:110-121` | 即便接上 A，产出的任务**谁都执行不了** |
| C | 批次形同虚设 | bridge 旧任务段 | 全部批次一次性 assign，`depends_on` 无效 |
| D | 同任务重复 start 产生重复 Run | `worker_execution.py` | 前日活体验证中 1 个任务跑了 3 遍（3 倍算力浪费） |
| E | materialize 不产出 TASK Spec | （W5 期间由实机验证确诊） | Worker 调 MCP 被 `SpecificationNotFound` 拒——执行许可缺失 |

断点 E 的成因值得记录：它不是被砍掉的功能，而是**两条开发线的会师点没对齐**——
前段线（需求→计划）的完成定义是"Spec/Task 落库"，后段线（MCP→Runner）的入口要求
approved+frozen 的 TASK Spec；唯一跑通过的路径是手工脚本，它悄悄补了这四步
（create→submit→approve(freeze)→publish），把缺口掩盖了。

## 3. 做了什么：五个工作流

### W1 编排内核（`modules/task_orchestration`，修断点 B/C 的机制部分）

- **`DecomposeRepositoryTask`**：把 assignee 为 Repository Leader 的仓库级任务分解为
  Worker 子任务（`parent_task_id` 两级结构，复用 `TaskOrchestrator.assign` 获得任务包
  发布 + Matrix 通知），含任务粒度在途去重（存在非终态子任务则复用不重派）。
- **`ExecutionPlan` 领域模型 + 存储**：批次蓝图（`PlannedRepositoryTask`：仓库、指令、
  验收、tests、leader_task_id）持久化为 `execution_plans`（JSONB batches）+
  `execution_plan_tasks` 映射表（leader_task→plan 反查），迁移 `20260807_0008`。
- **`AdvanceExecutionPlan` 批次推进器**：`start` 幂等持久化并只派首批；
  `on_task_terminal` 做子任务→父任务聚合（全部成功→父 SUCCEEDED；任一失败→父 FAILED）、
  批次门控（当前批全 SUCCEEDED 才派下一批）、失败熔断（plan→FAILED，永不派后批）、
  乐观锁竞态让位。**这就是此前讨论的"DAG 引擎"的 MVP 形态**——拓扑排序已由计划生成层
  完成，运行期只需批次门控，因此是一个挂在事件回流上的推进器，不是独立引擎。
- `TaskStore.list_by_parent`。

### W2 执行面去重（`integrations/runner` + `agent_runtime`，修断点 D）

- 从真实写入方推导 Dispatch 状态集合（在途 `queued/leased/accepted` vs 终态
  `completed/failed/interrupted/input_required`），新增按 (task, worker) 查在途 Dispatch。
- `start_assigned_task` 在 **workspace 准备之前**短路：存在在途 Dispatch 则原样返回该
  Run（从 dispatch payload 重建 RunnerTask），零状态写入、不再新建 worktree/Bundle/Run。

### W3 接线（组合根 + bridge + gateway + API，修断点 A/C 的连接部分）

- `container.plan_execution_bridge()` 注入真实编排服务（Matrix 未配置时保留 skip 语义）；
  组合根新增 `AdvanceExecutionPlanStarter` 适配器——模块边界规则（跨模块只许 import
  contracts）由组合根消化，bridge 只讲本模块方言。
- bridge 任务段重写：构建 `ExecutionPlan` → `advancer.start`（只派首批+自动分解）；
  不可执行仓库（不在 catalog / 无团队）过滤进 `skipped_repos`；返回值新增 `plan_id`。
- `RunnerControlGateway` 终态回写后触发 `on_terminal`（推进批次）；推进异常吞掉记日志，
  **不污染事件摄取**；验收时补了一处：重放同一终态事件会重试推进（否则推进失败后
  plan 永久卡死，与 docstring 承诺矛盾——W4 发现，我修复并加回归测试）。
- 新端点 `GET /api/v1/bridge/plans/{plan_id}`：批次×任务×状态聚合视图。

### W4 全链路 e2e（`tests/test_plan_loop_e2e.py`）

真实对象串链（真 TaskOrchestrator / AdvanceExecutionPlan / RunnerControlGateway /
sqlite 存储，只 fake 外部世界）：双仓两批次 runner 事件驱动 0→1→COMPLETED、
失败熔断不派下批、分解去重；含 mutation check（拔掉 `on_terminal` 两场景必挂）。

### W5 执行许可（修断点 E，实机验证确诊后追加）

- **W5a**：`TaskSpecificationAuthor` 端口；`PlannedRepositoryTask.tests` 字段；
  分解时为每个 Worker 子任务确保 TASK Spec（dedup 重放路径也调用——自愈缺失的许可）；
  `allowed_paths` 取 Worker 的 `responsibility_paths`（空则 `("**",)`）。
- **W5b**：`tests`（验收命令）从 API `TaskNodeView` → `TaskNode` → 计划批次 → Spec →
  Runner `test_commands` 全程穿线（此前产品路径**没有任何测试命令来源**）；
  组合根实现 `ApprovedTaskSpecificationAuthor`：create → submit → approve(freeze=True) →
  publish_to_context，重放状态容忍；permit 守卫按 `(task_id, repository_id)` 键控而非
  幂等键——两个 subagent 交叉验证时抓出"不同幂等键重放会铸造第二份 approved Spec、
  触发 `SpecificationConflict`"的真缺陷，已在源头修复并被测试钉死。

## 4. 在哪一层做的

```text
┌─ 计划生成层（chenwenhui 领域：LLM 发现/确认/整合，未来图推理定边）
│      产出 IntegratedPlan(task_dag, execution_batches, tests)
│                      │  ← 两层接缝 = materialize 入参（图推理接入点，本分支未动语义）
┌──────────────────────▼───────────────────────────────────────────┐
│ 编排层（本分支主战场）                                              │
│  bridge 任务段 + ExecutionPlan/推进器/分解器/TASK Spec + 观测端点    │
└──────────────────────┬───────────────────────────────────────────┘
┌──────────────────────▼───────────────────────────────────────────┐
│ 执行层（触及一处）：start_assigned_task 在途去重                     │
│  （MCP→worktree→Runner→claude-code→事件回流 本身是既有能力）         │
└──────────────────────┬───────────────────────────────────────────┘
└─ 交付层（未动，仍为空壳）：review_validation / delivery / scm / ci
```

## 5. 输入/产出对比

| 维度 | 改动前 | 改动后 |
|---|---|---|
| materialize 输入 | IntegratedPlan + project_id + leader_agent_id | 同左；`task_dag` 节点可带 `tests`（验收命令） |
| 产出：Spec | Engineering + Contract | 同左 + 每个 Worker 子任务一份 **approved+frozen TASK Spec**（含 allowed_paths/tests） |
| 产出：任务 | 无（`tasks=None` 全 skip）；更早版本为"全部仓库任务一次性派给 Leader，不可执行" | **只派首批**：每仓库 1 个 Leader 任务 + 1 个 Worker 子任务（两级 `parent_task_id`），Worker 可直接执行 |
| 产出：计划实体 | 无 | 持久化 `ExecutionPlan`，返回 `plan_id` |
| 批次推进 | 不存在 | Runner 终态事件驱动：聚合→门控→自动派下批 / 失败熔断 |
| 可观测性 | 无 | `GET /bridge/plans/{id}` 全景视图 |
| 重复 start | 每次新建 Run/worktree（实测 3 倍浪费） | 复用在途 Run |
| 测试命令来源 | 无（Spec 靠手工脚本） | API 一路穿线到 Runner `test_commands` |

## 6. 爆破范围（两次 Scope Freeze）

**第一波 W1-W4**：`modules/task_orchestration/**`（W1）｜`integrations/runner/worker_execution.py`
+ `agent_runtime` 契约与 store（W2）｜`bootstrap/container.py`、bridge、`repository_intelligence/api`、
`integrations/runner/gateway.py`（W3）｜`tests/test_plan_loop_e2e.py`（W4）。
**第二波 W5**：`modules/task_orchestration/**`（W5a）｜`repository_intelligence` 穿线 +
`bootstrap/container.py` 适配器（W5b）。

**明确不做**：push/PR/merge 交付尾巴、trace_id 全链路审计、Leader LLM 真实分解
（本次确定性分解）、Matrix sync 超时、mcporter 同步缺陷（AgentTeams 运行时问题，
已挂独立修复任务）、图推理（计划生成层，接缝已留在 materialize 入参）。

**执行方式**：并行 subagent 按冻结契约开发（端口签名/字段形状先由主脑写死在任务书里），
文件域零交集；每波完成后主脑逐 diff 验收 + 全量测试 + 实机验证。
测试量变化：545 → **560 passed**（全程唯一失败是 `test_register_and_discover_repository`
存量问题，已确认在干净 main 上就失败，另有独立会话在修）。

## 7. 实机验证全过程

### 7.1 前置（约 40 分钟）

1. 重建 API 镜像（迁移自动跑到 `0008`）。
2. 新建两套夹具仓库：`live-checkout-e2e-20260807`（折扣/税错误作用于含运费金额，4 测试
   3 失败）、`live-billing-e2e-20260807`（credit note 未 clamp 到 0，4 测试 1 失败）。
3. 新开两套 AgentTeams 团队（checkout/billing 各 1 Leader + 1 Worker，挂
   `repomesh-task-control` MCP）——不能复用前日 pricing 团队，因为撞上三个唯一约束
   （AgentTeams 资源一对一绑定、仓库单 Leader、团队单 topology）。
4. 新脚本 `scripts/setup-two-repo-plan-scenario.py` 播种拓扑（materialize 需要项目与
   组织 Leader 已存在——项目入驻至今无 HTTP 入口；`REUSE_LEADER_KEY`/`REUSE_TEAM_KEYS`
   处理与前日数据的幂等键冲突）。

### 7.2 四次尝试（每次失败都是一层真实问题）

| 尝试 | 结果 | 暴露的问题 |
|---|---|---|
| a | **批次门控首次活体验证通过**：batch 0 有 Leader+Worker 任务，batch 1 `leader_task_id: null` 一个未派。但 Worker 收到 Matrix 通知后报 "no MCP servers configured" | mcporter 配置 Controller 异步写 MinIO **滞后约 3 分钟**，Worker 处理消息时配置未到 |
| b | 配置到 MinIO 后仍失败，Worker 报 `cat: ./config/mcporter.json: No such file` | 同步**落点缺失**：CoPaw 实际读工作目录 `workspaces/default/config/mcporter.json`，同步只写 `agents/<w>/config/`；前日 pricing 能用是反复重启后凑巧生成。手工 `cp` 补齐（已挂独立修复任务） |
| c | **Worker 自主完成 MCP 三步**（initialize→tools/list→tools/call，API 侧 9 次 200），但被拒：`SpecificationNotFound: approved task specification not found` | **断点 E 确诊**——测试路径（W4 e2e 在业务任务层模拟事件）绕过了这道校验，只有产线路径能暴露。当场立项 W5 |
| d | **全程贯通**（W5 落地后，见下） | — |

### 7.3 尝试 d：完整贯通时间线（单次 API 调用后零人工干预）

| 本地时间 | 事件 |
|---|---|
| 03:16:4x | `POST /bridge/materialize`（`live-plan-20260807d`，带 tests）→ plan `da15fadc`；batch 0 派发 checkout Leader+Worker 任务与 TASK Spec |
| 03:18:32 | checkout Worker 经 MCP 启动 → 宿主 Runner 调真实 claude-code 修 `pricing.py`（4 行）→ 测试过 → commit `e009929` → `runner.completed` |
| 03:18:33 | 子→父聚合成功，**批次自动推进 0→1**，billing 任务+Spec 自动派发并通知 |
| 03:19:23 | billing Run 被 Runner 领取 |
| 03:20:46 | claude-code 修 `invoice.py`（1 行）→ 测试过 → commit `7d87f4f` → plan **completed** |

**独立复验**：两个 commit（作者 `RepoMesh Worker`，标题含任务 ID）存在于各自仓库镜像；
在临时 detached worktree 各自重跑 `python scripts/run_tests.py` 均 OK；任务 evidence
（changedFiles/commitSha/runId/testResults）完整落库；`GET /bridge/plans/da15fadc` 终态
`completed`，两批次 leader/worker 全部 `succeeded`。治理边界未松：claude-code 两次尝试
自跑测试均被权限层拒绝，验收测试由 Runner 受控执行。

## 8. 当前边界与下一步

本分支之后，闭环图为：**①→⑦ 全通（含产线路径活体验证），⑧⑨⑩ 交付尾巴仍空**。

1. **交付尾巴**（最大缺口）：commit 仍停在仓库镜像的 detached HEAD——push 分支 → PR →
   验收证据消费 → 合并/回滚，约 3-5 天。
2. **过程审计**：trace_id 贯穿、MCP 正文、Coding Agent 逐轮事件、Worker→Leader→Admin
   汇报链；与另一条会话进行中的 OTel 埋点工作互补。
3. **计划生成质量**：Contract/DAG 边的可靠生成待图推理引擎（chenwenhui），接入点即
   materialize 入参，与本分支并行不冲突。
4. **环境硬化**：mcporter 同步落点（已立项）、项目入驻 HTTP 入口、Matrix sync 超时噪声。
