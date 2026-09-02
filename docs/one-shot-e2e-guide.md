# RepoMesh 一次性启动 + 完整端到端执行指南

更新时间：2026-09-03

本文的目标：从零开始，一条路径跑通 **issue → 发现链 → 计划 → Leader 派单 → Worker 调用 task-control → Runner 租约执行（mock 编码代理）→ 任务 succeeded** 的完整执行链，不依赖任何外部 git 托管或真实编码 CLI。平台启动方式的完整规则见 `docs/clean-startup-guide-20260831.md`，本文不重复那些约束。

## 1. 前置条件

- Windows PowerShell 5.1 或 7+，Docker Desktop（Linux 容器引擎已启动）。
- 仓库根目录 `.env` 中已配置模型三件套（发现链与计划生成需要真实 LLM）：

  ```dotenv
  REPOMESH_MODEL_API_KEY=你的模型密钥
  REPOMESH_MODEL_BASE_URL=OpenAI 兼容接口地址
  REPOMESH_MODEL=模型名
  ```

- 其余执行面凭据（`REPOMESH_RUNNER_CONTROL_TOKEN`、`REPOMESH_AGENT_ACTION_TOKEN`、`REPOMESH_MCP_GATEWAY_TOKEN` 等）由启动脚本自动铸造并持久化到被 Git 忽略的 `.secrets/platform-credentials.env`，无需手工准备。

## 2. 播种本地 fixture 仓库

执行面克隆的仓库来自本机 fixture：脚本在 `.repomesh-workspaces/fixtures/` 下创建两个真实的小代码库（模块 + 通过的测试 + README），幂等可重复执行：

```powershell
python scripts\seed_e2e_fixtures.py
```

输出会打印稍后要在平台注册的仓库地址（容器内侧路径）：

```text
/runner-workspaces/fixtures/checkout-pricing-api
/runner-workspaces/fixtures/checkout-web
```

原理：api 容器以 `git clone --mirror` 克隆该路径（与克隆远端完全同一条代码路径），工作区经 `./.repomesh-workspaces` 宿主目录绑定挂载共享给 runner 容器（挂载点重映射 `/runner-workspaces` → `/workspace`，worktree 的 `.git` 指针被改写为相对路径，一份树两个容器都能用）。

## 3. 一键启动完整平台（含 Runner）

```powershell
.\scripts\start-platform.ps1 -InstallAgentTeams
```

该入口现在会一并拉起 platform profile 的 **runner** 服务：它长轮询 api 的 `/api/v1/runtime/runner-tasks/next` 租约任务、执行后回投 `runner-events`，镜像内置校验用 mock 编码代理（profile id `mock`，无模型无凭据），compose 已把 api 的 `REPOMESH_WORKER_DEFAULT_ADAPTER_ID` 默认为 `mock`。

就绪标志：

```text
RepoMesh is ready at http://127.0.0.1:8000/docs
RepoMesh console is ready at http://127.0.0.1:5280
```

补充确认 runner 在跑：

```powershell
docker compose --profile platform ps runner
```

## 4. 初始化管理员并注册仓库

1. 打开 http://127.0.0.1:5280 ，首次进入按向导初始化本地管理员，然后登录。
2. 侧栏「仓库」→「+ 添加仓库」，分别注册第 2 步打印的两个地址：

   ```text
   checkout-pricing-api   /runner-workspaces/fixtures/checkout-pricing-api
   checkout-web           /runner-workspaces/fixtures/checkout-web
   ```

3. 对每个仓库执行扫描分析，随后完成物化（建团）。物化成功后仓库卡片显示「团队就绪」。

## 5. 新建 issue 并走完发现链

控制台左上「+ 新建 issue」，例如：

```text
为结账服务增加运费满减折扣：订单满 100 元时运费立减 10 元，
并同步更新结账页面的总价展示。涉及 checkout-pricing-api 的折扣计算和 checkout-web 的购物车总价渲染。
```

issue 详情页按顺序推进发现链四步（需求分析 → 候选评分 → 分档审批 → 生成计划）。生成计划后可见计划 DAG（任务节点 + 执行批次）。

## 6. 派发与执行（全自动段）

计划落地后无需人工介入：

1. Leader 在团队房间发出 `task_assignment`，消息携带任务 id 与 `repomesh-task-control.start_assigned_task` 的 MCP 调用说明（Worker 的 `mcporter` 配置由 materialize 从 CR `mcpServers` 推送）。
2. Worker 调用 MCP 工具 → api 校验身份与派单、创建指派尝试、克隆 fixture、准备 worktree、打包任务入队。
3. runner 通过 HTTP 长轮询租到任务 → mock 编码代理执行 → 回投 `runner.accepted` / `runner.completed`。
4. 任务状态变为 **succeeded**，房间与 issue 详情出现本轮交付记录。

机器级验证（可选）：

```powershell
docker exec infra-postgres-1 psql -U repomesh -d repomesh -c `
  "SELECT task_id, status FROM task_orchestration.tasks ORDER BY updated_at DESC LIMIT 5;"
docker exec infra-postgres-1 psql -U repomesh -d repomesh -c `
  "SELECT event_type, COUNT(*) FROM agent_runtime.runner_events GROUP BY 1;"
```

`runner_events` 中每个执行应有 `runner.accepted` + `runner.completed` 成对记录。

## 7. 观测验证

侧栏「观测」四个板块对应完整链路的机器证据：

- **推理轨迹**：会话数 / 事件数 / 覆盖 Agent（trace）。
- **用量大盘**：LLM token、成功率、延迟。
- **日志**：按级别 / 来源 / issue 检索。
- **告警**：阈值规则与触发历史。

## 8. 常见故障

| 现象 | 原因与处理 |
| --- | --- |
| 克隆报 `WorkspacePreparationError` | 仓库 URL 必须是容器内侧路径 `/runner-workspaces/fixtures/<名>`，不是宿主机相对路径，也不是 git 托管地址 |
| `start_assigned_task` 返回 `cannot start task from <状态>` | 该任务已完成或无活跃指派，属结构性拒绝；换新 issue 或看第 5 步是否生成了新计划 |
| 返回 `coding execution is restricted to Worker identities` | 该计划节点指派给了 Leader（协调任务）；演示路径以 Worker 任务为准 |
| runner 起不来 / 401 | `REPOMESH_RUNNER_CONTROL_TOKEN` 未注入；重跑 `start-platform.ps1`（自动铸造并写入 `.secrets/`） |
| Worker 回 `Unauthorized access to model` | 模型密钥/网关配置问题，与执行链无关；检查 `.env` 模型三件套与 Higress consumer |

## 9. 演示素材

按本文流程录制的全流程截图（登录、issue 列表、发现链详情、团队房间、仓库、智能体、观测四板块）在 `tmp/e2e-screenshots/`（Git 忽略，仅本机），可直接用于 PPT。
