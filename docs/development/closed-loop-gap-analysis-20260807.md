# 闭环全流程记录与差距分析（2026-08-07）

- 作者：catmem（本机复现操作方）
- 关联：`docs/test-results/live-e2e-20260806.md`（Bohan 首次联调）、
  `docs/test-results/live-e2e-repro-20260807.md`（本机复现证据）、
  `docs/development/team-handoff.md` 第 6 节（目标全流程）

---

## 1. 本次全流程记录（本机复现操作序列）

### 阶段 A：基础设施拉起

| 步骤 | 操作 | 结果 |
|---|---|---|
| A1 | 启动 Docker Desktop，等待引擎就绪 | 28.0.4 / linux 引擎 |
| A2 | 确认 AgentTeams 既有安装 | `agentteams-controller`（Controller+Matrix+MinIO 同容器）与 `agentteams-manager` 均 Up，`/healthz` ok |
| A3 | `docker compose up -d postgres` | repomesh 库就绪 |
| A4 | 凭据采集 | Controller JWT 与 MinIO 凭据取自 `docker inspect` 容器 env；Matrix admin token 用 login API 换取 |
| A5 | 写 `.env`（runner/action/gateway 三个随机 token + 上述凭据） | `direct_worker_mcp_enabled=true`（development） |
| A6 | `docker compose --profile platform up -d --build api` | 迁移跑到 `20260806_0007`，`/health/ready` 200 |

### 阶段 B：AgentTeams 运行面开设

| 步骤 | 操作 | 结果 |
|---|---|---|
| B1 | `agt create worker repomesh-pricing-leader / repomesh-pricing-worker-01`（copaw, deepseek-v4-pro，SOUL 取自 `scripts/provision-repomesh-team.ps1`） | 两者 Running（约 40s） |
| B2 | `agt create team repomesh-pricing-team` | Active；Team Room `!IDC4AnNw1eaWPgvCvI:...` |
| B3 | `agt apply -f`（JSON-as-YAML manifest）给 Worker 挂 `repomesh-task-control` MCP → `http://host.docker.internal:8000/api/v1/mcp/worker` | Controller 生成 `config/mcporter.json`（自动注入 gateway Bearer） |
| B4 | 补回被 `agt apply` 清掉的 SOUL；重启 Worker 容器同步配置 | mcporter 配置落到 Worker 本地 |

刻意不做：不改共享 `default` Manager 的 SOUL（本机还有其他项目在用）。

### 阶段 C：夹具与 Runner

| 步骤 | 操作 | 结果 |
|---|---|---|
| C1 | 建缺陷夹具仓库 `.repomesh-workspaces/fixtures/live-runner-e2e-20260807-local`（折扣/税错误作用于含运费金额） | 基线 `9e25093`，4 测试 3 失败（红） |
| C2 | `uv sync --extra dev`；写 `.test-tmp/runner-env.sh`；宿主启动 `python -m repomesh_runner` | 长轮询 `/runtime/runner-tasks/next` 正常 204 |

### 阶段 D：发布任务与自动执行（三次迭代，两个坑）

| 步骤 | 现象 | 处置 |
|---|---|---|
| D1 | 第 1 次发布：Worker `mc mirror` Access Denied——compose 未传 MinIO 凭据，任务包被文件系统 publisher 写成后备目录普通文件（非对象） | `compose.override.yaml` 注入 `REPOMESH_AGENTTEAMS_STORAGE_*`；清理脏文件 |
| D2 | 第 2 次发布：同 run key 幂等短路，未重新发布 | 换 run key，固定 identity/project key 复用主体 |
| D3 | 仍写文件系统——`run-live-worker-e2e.py` 硬编码 `AgentTeamsTaskPublisher`，绕过 bootstrap 的对象存储选择 | 补丁副本改用 `AgentTeamsObjectTaskPublisher` 重跑（r3） |
| D4 | 任务包成为真 MinIO 对象；Matrix 通知送达；Worker 自动调 `start_assigned_task`（API 多次 200） | 链路通 |
| D5 | Runner 拒收：`workspace path does not match the configured execution-plane prefix`——Git Bash MSYS 把 `/runner-workspaces` 前缀转成 Windows 路径 | `MSYS2_ENV_CONV_EXCL="REPOMESH_RUNNER_WORKSPACE_PATH_FROM"` 重启 Runner |
| D6 | 15:20–15:25 三个 Dispatch 依次执行，全部 succeeded | 有效 Run：Task `559e39af` / Run `3bb524ce` / commit `8825f6bb` |

### 阶段 E：证据核验

- `task_orchestration.tasks`：三个 Worker Task 均 `succeeded`。
- `agent_runtime.runner_events`：每个 Run `runner.accepted` + `runner.completed` 有序持久化；
  completed payload 含 status、changedFiles（仅 `pricing.py`）、commitSha、testResults（exit 0）、
  Claude 摘要与 nativeSessionId。
- `collaboration.messages`：三条 Leader→Worker 通知均 `delivered` 且有 Matrix Event ID。
- 人工复验：worktree 内 4 测试全过；diff 仅 4 行。
- 治理面：Claude Code 三次尝试自跑测试被权限层拒绝（其摘要如实声明），验收测试由 Runner 受控执行。

---

## 2. 当前实际执行链路（已验证部分）

```text
[人工/脚本] 造 Org/Project/Repo/Team 拓扑 + 父任务
      │  run-live-worker-e2e.py（本应由产品前段生成）
      ▼
Specification：create → submit → approve(freeze) → publish_to_context
      ▼
TaskOrchestrator.assign
      ├─ 任务包发布 → MinIO teams/<team>/shared/tasks/<id>/{meta,manifest,spec}
      └─ SendCollaborationMessage → Matrix Team Room（mention 唤醒 Worker）
      ▼
Worker(copaw LLM) 读通知 → mcporter 调 repomesh-task-control.start_assigned_task
      ▼
Worker MCP(API)：鉴权 → WorkerExecutionService
      ├─ GitWorktreeManager 备镜像+隔离 worktree（相对 gitdir，宿主可用）
      ├─ Context Bundle / Coding Package / 能力与权限投影
      └─ Runner Dispatch（runner_dispatches, leased）
      ▼
宿主 Runner：长轮询领取 → 路径前缀映射 → 上下文清单校验
      ├─ runner.accepted ↑
      ├─ claude-code CLI（结构化指令+允许路径+禁止工具；权限回调白名单裁决）
      ├─ 变更采集（仅允许路径）→ 验收命令受控执行 → git commit
      └─ runner.completed ↑（终态+证据）
      ▼
API：Task→succeeded，Dispatch→completed
      ▼
（终止于此：commit 停在 detached-HEAD worktree）
```

## 3. 目标完整闭环（team-handoff §6 对照）

```text
用户一句话需求
  → Specification 保存 PRD / Engineering Spec
  → Repository Intelligence 推荐仓库并保留证据
  → Organization Leader 确认仓库范围
  → Specification 冻结跨仓 Contract 与验收
  → Task Orchestration 生成 Task DAG
  → Context 生成每 Worker 的 VisibilitySnapshot 与 Bundle
  → Agent Runtime 创建 CodingRun（AgentTeams 投影 Team/房间）
  → Runner 隔离执行 + 有序事件回传          ←—— 本次已验证段
  → Review Validation 保存测试证据并验收
  → Delivery 创建 ChangeSet / PR / 合并顺序 / 回滚记录
  → Leader 逐级汇报，Admin DM 收到最终交付证据
```

## 4. 差距清单：还缺什么才算闭环

### 4.1 前段 —— 需求入口没有接上执行面（最短板之一）

- `repository_intelligence` 已有 discovery / confirmation / integration /
  `POST /api/v1/bridge/materialize`（PlanExecutionBridge，chenwenhui 2026-08-07 完成），
  能从需求物化 Spec + Task。但本次 E2E 仍靠 `run-live-worker-e2e.py` 手工造拓扑与任务，
  说明 **Bridge 产物与 TaskOrchestrator.assign→发布→通知这条链尚未打通成一个入口**。
  缺一步：materialize 之后自动（或经审批后）触发任务发布与 Worker 通知。
- Organization Leader「确认仓库范围」目前只是 confirmation API，没有接入 Leader Agent 的
  真实决策回路（Matrix 上问答→确认→冻结）。

### 4.2 中段 —— 治理与审计缺口（Bohan 报告已列，均未关闭）

- 无统一 `trace_id` 贯穿 需求→Spec→Task→Dispatch→Runner→commit。
- Worker MCP 请求/响应正文未持久化（只有 Task/Dispatch 状态旁证）。
- Coding Agent 逐轮 Prompt/Tool Call 流式事件未入审计存储（只有终态摘要 + session id）。
- Worker→Leader 的结果回报只存在于 CoPaw 会话日志，未摄取为 `collaboration.messages`。
- Leader→Organization Leader→Admin 的最终汇报链完全未实现（Runner 直接闭环到 DB）。
- `VisibilitySnapshot` 未实现（代码中无实体）；context 模块有 Bundle/Delta/审计，
  但「AgentPolicy ∩ ProjectMembership ∩ TaskSpec ∩ RunDelegation − ExplicitDeny」的
  权限编译尚缺。

### 4.3 后段 —— 交付面基本是空壳（最大缺口）

- `review_validation`、`delivery` 模块只有 README + module.toml；
  `integrations/scm`、`integrations/ci` 只有 README。
- 意味着：测试证据没有独立验收记录、无 ChangeSet、无 push、无 PR、无合并顺序、无回滚。
  成功 commit 永远停在 detached-HEAD worktree，需要人手工捞。
- 最小闭环补法（建议顺序）：
  1. Runner 成功后由 API 把 worktree 分支推到远端（`repomesh/task/<task-id>`）——
     权限留在控制面而非 Coding Agent，符合现有边界；
  2. SCM 适配器创建 PR，回填 `delivery.change_sets`；
  3. Review Validation 摄取 testResults 为不可变验证证据，作为 PR 门禁；
  4. 合并/回滚策略与 Leader 汇报消息。

### 4.4 运行面硬化（不阻塞语义闭环，但阻塞「可日常使用」）

- Worker 对同一任务重复调 MCP → 三个 Dispatch 各自跑一遍（本次 3 倍算力浪费）。
  需要任务粒度的在途去重/合并（当前幂等只在 Run 粒度成立）。
- `scripts/run-live-worker-e2e.py` 硬编码文件系统 publisher（已建后台修复任务）。
- compose 缺 `REPOMESH_AGENTTEAMS_STORAGE_*` 直通（本次靠 override 文件）。
- Matrix `/sync` 读超时噪声（`AgentTeamsUnavailable("Matrix sync failed")` 周期出现）。
- Runner 尚未作为 AgentTeams Worker 镜像交付（当前宿主裸进程）；resume smoke、
  镜像化、产品 fork 对齐仍是 runner-execution-plane 的遗留项。
- Windows/Git Bash 的 MSYS 路径转换需要在启动脚本里固化 `MSYS2_ENV_CONV_EXCL`。

### 4.5 一句话总结

**执行内核已经闭环且可复现（需求包→Worker 自主领取→受治理编码→测试→commit→状态回写），
但它现在是一段「有头无尾的中间件」：头上没接住用户需求（Bridge 未连发布链），
尾上没交出去（无 push/PR/验收/汇报），中间的审计还只覆盖状态与终态、不覆盖过程。**
按性价比排序：① Bridge→assign 打通入口；② push+PR 最小交付；③ trace_id+三处补采；
④ 在途任务去重与 Runner 镜像化。
