# 批次编排链路本机活体验证（2026-08-07）

- 分支：`feat/plan-execution-loop`
- 目的：验证 `POST /bridge/materialize` → 批次编排 → Worker 自主执行这条**产线路径**，
  而不只是 `tests/test_plan_loop_e2e.py` 证明过的测试路径
- 结论：**W5 补齐 Task Spec 后，产线路径已全程贯通到 plan COMPLETED（见 §6）。**
  首轮验证（§2-§3）曾在 `start_assigned_task` 处被缺失 Task Spec 挡住，该缺陷当天由 W5 修复。

---

## 1. 场景

| 项 | 值 |
|---|---|
| 项目 | `31686a0a-91f5-4894-9cc8-a60d872c1fac` |
| 组织 Leader | `442905a3-4d24-4579-92e5-43aa8d129ddb`（复用 AgentTeams Manager `default`） |
| 仓库 A | `checkout-live`，夹具 `live-checkout-e2e-20260807`，缺陷：折扣与税错误作用于含运费金额 |
| 仓库 B | `billing-live`，夹具 `live-billing-e2e-20260807`，缺陷：credit note 未 clamp 到 0 |
| 批次 | `[[checkout-live], [billing-live]]`，B 依赖 A |
| 团队 | `repomesh-checkout-team`、`repomesh-billing-team`（均新建，避开既有唯一约束） |

前置拓扑由新增的 `scripts/setup-two-repo-plan-scenario.py` 播种——materialize 需要项目和组织
Leader 已存在，而项目入驻至今没有 HTTP 入口。

## 2. 已验证通过的部分

### 2.1 批次门控在真实环境生效（本次改动的核心价值）

`POST /bridge/materialize` 返回 `plan_id` 与 2 个任务，`GET /bridge/plans/{id}` 显示：

```json
{"status": "in_progress", "current_batch_index": 0,
 "batches": [[{"repository_id": "fa418459…", "leader_task_id": "de252a6f…",
               "leader_status": "assigned",
               "worker_tasks": [{"task_id": "4c9d654a…", "status": "assigned"}]}],
             [{"repository_id": "b0145234…", "leader_task_id": null,
               "leader_status": null, "worker_tasks": []}]]}
```

batch 1 的 `leader_task_id` 为 `null`——**billing 一个任务都没有被派发**。改动前的 bridge 会把
两个仓库的任务一次性全部派给 Leader，`depends_on` 形同虚设。

### 2.2 Leader→Worker 分解与通知

每个批次的仓库产出一个 Leader 任务 + 一个 Worker 子任务（`parent_task_id` 两级结构），
Matrix 定向通知送达 Worker，Worker 的 CoPaw 会话可见完整任务包提示。

### 2.3 Worker 自主调用 MCP

配置到位后 Worker 自主完成 `initialize` → `tools/list` → `tools/call start_assigned_task`
（API 侧 9 次 `POST /api/v1/mcp/worker` 全部 200）。

## 3. 阻断点：materialize 不产出 Task Specification

Worker 调用返回：

```text
SpecificationNotFound: approved task specification not found
```

`PlanExecutionBridge.materialize` 只创建两类 Spec：
`SpecificationKind.ENGINEERING`（项目级）与 `SpecificationKind.CONTRACT`（跨仓契约）。
它**从不创建 `SpecificationKind.TASK`**。

而 `start_assigned_task`（`integrations/runner/worker_execution.py`）要求目标任务存在一份
**已批准（approved）且冻结（frozen）** 的 Task Spec，用来产出允许路径、验收命令与 Coding Package。

2026-08-06 的联调之所以能跑通，是因为手工脚本 `run-live-worker-e2e.py` 显式做了四步：
`create(kind=TASK)` → `submit` → `approve(freeze=True)` → `publish_to_context`。
产品路径缺的正是这四步。

`tests/test_plan_loop_e2e.py` 没有暴露它：该测试在业务任务层面模拟 Runner 事件，
不经过 `start_assigned_task` 的 Spec 校验。

### 修复方向

在 bridge 为每个 Worker 子任务补一份 TASK Spec 并走完审批冻结。所需字段都已在手边：
- `goal` / `instruction` ← `TaskNode.instruction`
- `acceptance` ← 现有 `_derive_task_acceptance`
- `allowed_paths` ← 拓扑里该 Worker 的 `responsibility_paths`
- `tests` ← 验收命令

归属上更适合放在 `DecomposeRepositoryTask`（分解出 Worker 任务的同一处），
让"产生可执行任务"与"产生该任务的可执行前提"保持在一个事务语义里。

## 4. 环境侧踩坑（与产品代码无关，但会挡住联调）

1. **唯一约束三连**：`agentteams_resource_name`（Manager `default` 全局唯一）、
   `repository:<id>:leader` 单例、`agentteams_team_name` 全局唯一。
   一个 AgentTeams 团队只能属于一个 project topology，一个仓库只能有一个 Leader。
   因此复现新场景要么复用旧 key，要么开全新的仓库与团队。脚本用
   `REUSE_LEADER_KEY` / `REUSE_TEAM_KEYS` 支持前者。
2. **mcporter 配置同步滞后且落点不全**：`agt apply` 挂 MCP 后，Controller 异步写
   MinIO（本次滞后约 3 分钟），且只落到 `agents/<w>/config/` 与 `.copaw/config/`；
   CoPaw 实际读取的是**工作目录**下的 `workspaces/default/config/mcporter.json`。
   该文件不会自动生成，Worker 会明确报 `No MCP servers configured`。
   本次手工 `cp` 补齐后 MCP 立即可用。这是 AgentTeams 运行时问题，值得单独提 issue。
3. **`teams/<team>/shared/tasks` 对 Worker 是 Access Denied**：Worker 的 MinIO policy 只含
   `agents/<self>` 与 `shared/`。任务包同步失败是**已知噪声**，不阻塞——Worker 从 Matrix
   消息正文即可取得 `task_id` 与 `worker_agent_id`。2026-08-06 那次成功的联调同样报此错。

## 5. 结论

改动本身按设计工作：批次门控、两级任务分解、计划观测端点在真实环境全部验证通过。
链路止步于一个**改动之外的既有缺口**——materialize 不产出 Task Spec，导致产线路径产生的任务
无法被 Worker 执行。补上这一步，`一句话需求 → Worker 自主编码 → 测试 → commit → 批次推进`
即可在产线路径上全程贯通。

---

## 6. W5 修复后的完整贯通（同日复跑）

W5（分解环产出 approved+frozen TASK Spec + `tests` 从 API 穿线到 Runner）落地后，
以 `live-plan-20260807d` 复跑，单次 `POST /bridge/materialize` 之后**零人工干预**：

| 时间(本地) | 事件 |
|---|---|
| 03:16:4x | materialize：plan `da15fadc`，batch 0 派发 checkout Leader+Worker 任务与 TASK Spec |
| 03:18:32 | checkout Worker 经 MCP 启动，宿主 Runner 调 claude-code 修复 `pricing.py`（4 行），测试过，commit `e009929`，`runner.completed` |
| 03:18:33 | 子任务→父任务聚合成功，**批次自动推进 0→1**，billing 任务+Spec 自动派发 |
| 03:19:23 | billing Run 被 Runner 领取 |
| 03:20:46 | claude-code 修复 `invoice.py`（1 行），测试过，commit `7d87f4f`，plan **completed** |

全程约 4 分钟，两个仓库、两次真实 claude-code 执行、一次 API 调用。

### 独立复验

- 两个 commit 均存在于对应仓库镜像（作者 `RepoMesh Worker`，标题含任务 ID）；
- 在临时 detached worktree 各自重跑 `python scripts/run_tests.py`，**均 OK**；
- 任务 evidence（changedFiles/commitSha/runId/testResults）完整落库；
- `GET /bridge/plans/da15fadc-…` 终态：`completed`，两批次 leader/worker 全部 `succeeded`。

### 治理面观察

两次执行中 claude-code 尝试自行运行 Python 均被权限层拒绝（其摘要如实说明），
验收测试由 Runner 受控执行——与 2026-08-06 行为一致，治理边界未因新链路而放松。

§3 的缺陷与修复自此关闭；§4 的环境坑仍然有效（mcporter 落点缺失已另立修复任务）。
