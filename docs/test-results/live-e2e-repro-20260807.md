# RepoMesh 真实端到端本机复现报告（2026-08-07）

## 结论

在本机（catmem，Windows 11 + Docker Desktop + Git Bash）成功复现 2026-08-06 报告的完整执行闭环。
用户只发布初始修复需求，AgentTeams Worker 自动领取任务、调用 RepoMesh Worker MCP、触发宿主
Runner，由真实 Claude Code 完成代码修改；Runner 执行验收测试、创建 Git commit 并回写任务成功。

## 测试对象

- 需求：修复结算价格计算，折扣和税费只作用于商品小计，运费最后原值加入。
- 夹具仓库：`.repomesh-workspaces/fixtures/live-runner-e2e-20260807-local`（本机新建，带缺陷基线）
- 基线提交：`9e2509345a8ae807feafeec5cf3f7dbb00ec5ffc`
- 有效 Run（对象存储链路完全正确的一次）：
  - Worker Task：`559e39af-a323-492a-a842-dcc389fe399b`
  - Runner Run：`3bb524ce-306f-4876-a5d6-fbaba595ab6d`
  - 生成提交：`8825f6bb19fc0b41b972e05430ba13289d2857e5`（作者 `RepoMesh Worker`）
- 另有两个 Run（`0ceba590` / `db3687d0`，任务 `8cb63914` / `c72866a7`）同样 succeeded，
  源自早期 Worker 对 MCP 的重复调用与首轮文件系统发布的遗留任务。

## 环境拓扑

| 组件 | 位置 | 说明 |
|---|---|---|
| AgentTeams Controller/Matrix/MinIO | `agentteams-controller` 容器 | 既有安装，healthz 通过 |
| AgentTeams Manager | `agentteams-manager` 容器 | `default`，SOUL 未改动（避免影响本机其他项目） |
| RepoMesh API | `goai-infra-repomesh-api-1` | compose `platform` profile，迁移至 `20260806_0007` |
| PostgreSQL | `goai-infra-repomesh-postgres-1` | repomesh 库 |
| Repository Leader | `repomesh-pricing-leader`（copaw, deepseek-v4-pro） | 本次新建 |
| Worker | `repomesh-pricing-worker-01`（copaw, deepseek-v4-pro） | 本次新建，挂 `repomesh-task-control` MCP |
| Team | `repomesh-pricing-team`（Active） | Team Room `!IDC4AnNw1eaWPgvCvI:...` |
| 宿主 Runner | `uv run python -m repomesh_runner`（Git Bash 后台进程） | 真实 `claude-code` Adapter |

## 实际执行链路（2026-08-07 北京时间 15:18–15:25）

1. 补丁版 `run-live-worker-e2e.py` 在 API 容器内发布任务 `559e39af`，任务包以 MinIO 对象写入
   `teams/repomesh-pricing-team/shared/tasks/559e39af.../{meta.json,manifest.json,spec.md}`。
2. Leader → Worker 的定向通知经 Matrix 送达（`collaboration.messages` 三条记录均 `delivered`，含 Event ID）。
3. Worker 自动调用 `repomesh-task-control.start_assigned_task`（API 日志多次 `POST /api/v1/mcp/worker 200`）。
4. API 准备隔离 worktree（`.repomesh-workspaces/w/a9958bed.../a586734c...`）并创建 Runner Dispatch。
5. 宿主 Runner 长轮询取到任务（`runner.accepted` 15:23:49），启动真实 Claude Code。
6. Claude Code 仅修改 `src/checkout_fixture/pricing.py`（折扣/税基改为商品小计，运费税后原值加入）。
7. Runner 执行 `python scripts/run_tests.py`，退出码 0（`testResults` 持久化于 `runner.completed`）。
8. Runner 创建 commit `8825f6bb` 并上报终态（15:25:00）；Task=`succeeded`，Dispatch=`completed`。
9. 人工复验：worktree 内 4 项测试全部通过；diff 仅 4 行改动、1 个文件。

治理面观察：Claude Code 曾三次尝试自行运行测试被权限层拒绝（其总结中如实声明"未自证测试通过"），
验收测试由 Runner 以受控方式执行——与设计意图一致（Coding Agent 无自由命令执行权）。

## 复现过程中发现并修复的问题

1. **任务包被写成 MinIO 后备目录的普通文件**：compose 未给 API 传
   `REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT/ACCESS_KEY/SECRET_KEY`，发布走了文件系统 publisher，
   Worker `mc mirror` 报 Access Denied。修复：本地 `compose.override.yaml` 注入对象存储凭据。
2. **`scripts/run-live-worker-e2e.py` 硬编码文件系统 publisher**：脚本直接构造
   `AgentTeamsTaskPublisher(storage_root)`，绕过了 `bootstrap/app.py` 中按 env 选择
   `AgentTeamsObjectTaskPublisher` 的逻辑。本次以补丁副本运行；脚本本体待修。
3. **Git Bash (MSYS) 路径转换破坏 Runner 前缀映射**：`REPOMESH_RUNNER_WORKSPACE_PATH_FROM=/runner-workspaces`
   被 MSYS 转成 Windows 路径，Runner 报
   `workspace path does not match the configured execution-plane prefix`。修复：启动 Runner 时
   `export MSYS2_ENV_CONV_EXCL="REPOMESH_RUNNER_WORKSPACE_PATH_FROM"`。
4. **幂等短路遮蔽发布失败**：同一 `LIVE_E2E_RUN_KEY` 重跑不会重新发布任务包，需换新 run key
   （`LIVE_E2E_IDENTITY_KEY`/`LIVE_E2E_PROJECT_KEY` 可固定以复用主体与拓扑）。
5. **Worker 重复调用 MCP 产生重复 Dispatch**：三条 `start_assigned_task` 调用创建了三个
   workspace 与 Run，全部独立执行成功。幂等在 Run 粒度成立，但任务粒度的去重值得跟进。

## 与 2026-08-06 报告一致的已知边界（本次未扩展）

- 提交保留在隔离 worktree 的 detached HEAD，未 Push、未创建 PR。
- Worker MCP 请求/响应正文、Claude 逐轮 Prompt/Tool Call 未独立持久化。
- Worker → Leader 结果回报未摄取为结构化 Collaboration Message。
- Matrix `/sync` 长轮询超时产生周期性告警噪声（`AgentTeamsUnavailable("Matrix sync failed")`）。

## 关键运行文件

- Runner 日志：`.test-tmp/runner2.log`
- Runner 环境：`.test-tmp/runner-env.sh`（另需 `MSYS2_ENV_CONV_EXCL`）
- 本地覆盖：`compose.override.yaml`（含 MinIO 凭据，勿提交）
- 补丁脚本副本：scratchpad `run-live-worker-e2e-patched.py`（已复制到 API 容器 `/app/run-live-e2e-patched.py`）
