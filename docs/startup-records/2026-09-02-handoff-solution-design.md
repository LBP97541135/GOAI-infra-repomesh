# 交接：下一会话讨论「从零启动 + issue→提交链」暴露的设计问题怎么解（2026-09-02）

写于 2026-09-02 07:30（本机时钟）。上一会话做了三件事：整拆环境、按 README 从零起平台、用四个 GitHub 夹具仓走
issue→开工→房间→提交链的产品路径。结论：**平台能起、编制能造、房间能派，但没有任何进程真正干活，提交链没到。**
下一会话的目的是**讨论并拍板解法**，不是继续实走。环境还活着，需要取证随时可查。

术语按 `CONTEXT.md`（Manager / 仓库团队 / 开工（materialize） / 测试团队 / 测试资产仓 / 档案开关）。

---

## 1. 必读文件（按顺序，先读前四个再谈方案）

| # | 文件 | 读什么 |
|---|---|---|
| 1 | `CONTEXT.md` | 术语表，59 行 |
| 2 | `docs/startup-records/2026-09-02-defect-summary.md` | **本次交接的核心**：18 条问题的分级总表、6 个设计问题各自的现象/根因/解法、横切建议、修复顺序 |
| 3 | `docs/startup-records/2026-09-02-issue-to-commit-chain.md` | issue→提交链实走的逐步证据（V-1…V-8 的原始出处），§5–§7 是房间与执行面的现场 |
| 4 | `docs/startup-records/2026-09-02-from-zero-windows.md` | 从零启动实走（#1…#10 的原始出处），§10 是问题清单 |
| 5 | `docs/startup-records/2026-09-02-issue-to-commit-chain.rooms.md` | 8 间房的消息摘录（脱敏），看 copaw worker/队长在房里怎么推理的 |
| 6 | `docs/architecture/runtime-planes.md` | 架构对「谁跑 runner」的原始承诺（Command Flow 第 5 步） |
| 7 | `compose.yaml`、`scripts/start-platform.sh`、`scripts/start-platform.ps1` | #2 的 external 声明（`:256-263`）、setup-plane 分支、写运行时配置与不重启 api 的段落 |
| 8 | `src/repomesh/api/worker_mcp.py` | V-1 的 `_authorize`（只认 action/gateway token）与 V-4 的返回值处理 |
| 9 | `src/repomesh/integrations/agentteams/principal_registration.py` | `with_task_control()`：给 worker 投影 MCP server 时只给 URL 不给凭据 |
| 10 | `src/repomesh/modules/agent_runtime/api/router.py` | runner 协议：`/runtime/runner-tasks/next`、`/runtime/runner-events`、`_authorize_runner` 与 `REPOMESH_RUNNER_WORKER_TOKENS`（V-1 解法可复用的机制，`:333`） |
| 11 | `src/repomesh/integrations/bootstrap/executor.py` | bootstrap 容器如何在容器内跑安装器、写运行时配置、`docker restart` api（横切建议的落点） |
| 12 | `src/repomesh/modules/repository_intelligence/application/discovery_materialization.py` | 开工的顺序：确保拓扑 → 投运行时（建房）→ 外部成员就绪门禁 → 铸任务 |
| 13 | `docs/development/local-cli-readiness-live-acceptance-20260830.md`（及同目录 `local-cli-launch-readiness-*-20260829.md`） | V-5 选项 3「本地 CLI 外部成员」已验收的形态 |
| 14 | `docs/development/module-test-team/module-test-team-handoff-20260901.md` §3.4 | 以往跑到 PR 的活体是怎么在宿主起 bridge 的（对照 V-5 为什么产品路径没有它） |
| 15 | `docs/clean-startup-guide-20260831.md` | 08-31 写的「当前可用启动方式」，与本次实走结果对照 |
| 16 | `.env.example` | 三处默认值害人的地方：`:21,105` `REPOMESH_DIRECT_WORKER_MCP_ENABLED=false`、`:52` `REPOMESH_WORKER_RECOVERY_ENABLED=false`、全文重复 |

**不必读**：`docs/development/test-team-tiered-route-spec-20260831.md`（另一条线）；`output/` 下的历史证据。

---

## 2. 环境实况（07:30 全部活着）

### 2.1 代码

- 工作树 `D:\Project4work\GOAI-infra-repomesh`，分支 `feat/module-test-team-v1`，HEAD `fd26f09f`（= GitHub main）。
- 本次**没有改任何源码**。新增文件全在 `docs/startup-records/`（未提交）：`README.md`、两份记录、`.rooms.md`、
  七份 `step*.log`、`defect-summary.md`、本文件。
- 会话 scratchpad（`%LOCALAPPDATA%\Temp\claude\...\scratchpad`）里的驱动脚本、发现链 JSON、房间 jsonl 会随会话消失；
  有价值的内容已抄进上述记录。

### 2.2 进程与端口

| 组件 | 状态 | 地址 |
|---|---|---|
| compose 项目 `goai-infra-repomesh`：postgres / api / web / bootstrap | healthy | 5432 / 8000 / 5280 / – |
| `agentteams-controller`（`agentteams-embedded:v1.2.0`，PS 安装器装的） | Up | 18080 网关（Matrix 客户端入口）、18001 Higress、18088 Element |
| `agentteams-manager`（安装器的 Manager 容器） | Up | 18888 |
| `agentteams-manager-repomesh-e2e-manager`（组织 Manager 的运行时投影） | **Created，未启动**（18888 端口冲突，V-2） | – |
| 8 个 `agentteams-worker-agt-{leader,worker}-<repo12>`（copaw v1.2.0） | Up 2 小时 | 各随机映射 8088 |
| dashboard | **没装**（PS 安装器不装） | 13000 不通 |
| 房间盯档脚本 | 已停（STOP 文件） | – |

`setup/status`：model / database / agentteams / matrix / internal_auth / administrator / agent_directory / repositories 全 true，
只有 `github_app=false`（可选）。`/health/ready` 200。

### 2.3 配置与凭据（位置，不是值）

- `.env` = `.env.example` + 用户自己的 `REPOMESH_MODEL_API_KEY`（DeepSeek）+ 追加一行
  `AGENTTEAMS_REGISTRY=higress-registry.cn-hangzhou.cr.aliyuncs.com`。其余全是 example 默认，
  即 `REPOMESH_DIRECT_WORKER_MCP_ENABLED=false`、`REPOMESH_WORKER_RECOVERY_ENABLED=false`、
  `REPOMESH_DELIVERY_AUTO_ENABLED=false`、无 GitHub 凭据。
- `.secrets/`：`platform.env`（action / runner-control / gateway 三枚 token）、`platform-runtime.env`（1147 字节，
  controller token + Matrix `@admin` token + MinIO 账密）、`platform-credentials.key`、`browser-action-token`、
  `startup.env`（5432/8000/5280）、`agentteams-manager.env`（安装器 env 的副本）。
- 家目录：`~/agentteams-manager.env`、`~/agentteams-manager/`、`~/agentteams-install.log`。
- 整拆前的旧 `.env` / `.secrets` / `agentteams-manager*` 在 `D:\Project4work\repomesh-wipe-backup-20260902\`，没有回填任何 token。
- 本机 gh CLI 已登录 `catbobyman`（scope 含 repo）；本次没用它推过任何东西。
- **宿主残留**：`.repomesh-workspaces/`（8 月留下，含旧镜像与 43 个旧 worktree）整拆时没清；本次新建的
  `repositories/dfb8a4cd….git` 与 `w/503a658525d7c7b5861a/20b712cc4c33daf2811d` 也在里面。

### 2.4 数据库里的关键 id

| 对象 | id |
|---|---|
| 组织 `repomesh-e2e` | `7ce7e70e-f501-5ece-b998-ce2f4c4cd550` |
| Manager（organization_leader，resource `repomesh-e2e-manager`） | `703b1dfa-024d-41f0-ab10-ce3ebec025c1` |
| 仓库 pricing-core / checkout / billing / test-assets | `dfb8a4cd-…-197963151308` / `39ae6814-…-96ab3d9e36d7` / `d47d566d-…-ea8ea9553220` / `22c4d38e-…-20ae97cc250d` |
| issue | `ff6a9f90-1e0c-5fe7-8a9f-9b354c1aa754`（需求：报价支持多币种，三仓都改） |
| 执行计划 | `6f438ac3-28a5-4284-abe3-f40ebf69267c`，`in_progress`，批次 `[[pricing-core, test-assets], [checkout], [billing]]` |
| 任务 | pricing-core 队长 `9ca46162…` assigned / worker `b6e0bc59…` **in_progress**；test-assets 队长 `b4980c68…` assigned / worker `54250ad9…` assigned |
| runner dispatch | run `15431819-61cd-4ce3-b813-e8eb6d04a031`，`queued`，`lease_until` 空 |
| 预留 | `worker_execution_reservations` 1 行 `running`，租约 13:55:20Z 已过期无人回收 |
| 四支仓库团队的房间 | 见 `project.repository_agent_teams`（`room_id` / `leader_room_id`），或 issue-to-commit-chain.md §4 |

test-assets 仓的档案开关**没翻**，它是以 MAYBE 档普通仓进计划的；测试团队在本轮不存在。

### 2.5 怎么查（下一会话可直接用）

```bash
# action token（发现链、开工、房间读模型、/bridge/*、建组织都用它）
T=$(sed -n 's/^REPOMESH_AGENT_ACTION_TOKEN=//p' .secrets/platform.env | tail -1)
curl -sS -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/v1/bridge/plans/6f438ac3-28a5-4284-abe3-f40ebf69267c
```

```bash
# 数据库
docker exec goai-infra-repomesh-postgres-1 psql -U repomesh -d repomesh -At -c "select status from agent_runtime.runner_dispatches"
```

```bash
# Matrix 原始事件（@admin 的 token 在 platform-runtime.env 的 REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN）
# 盯房：复制 scripts/module-test-team/room_watch.py，把 MATRIX 改成 http://127.0.0.1:18080、API 改成 http://127.0.0.1:8000/api/v1、
# ACTION_TOKEN 与 matrix_token 改成从 .secrets 读；rooms.txt 每行「标签 房间id」。
```

```bash
# MinIO 任务包
docker exec agentteams-controller sh -c 'mc ls -r agentteams/agentteams-storage/teams/ | grep tasks/'
```

AI 侧的边界：建账号、输密码、在页面里填 API key 不由 AI 做；需要管理员会话的接口
（`/repositories/{id}/agent-team`、`/setup/repositories/onboard`、`/agents/native`、档案开关）由用户在 5280 控制台操作。

---

## 3. 待裁决项（下一会话要拍板的）

1. **runner 放哪**（V-5，唯一门槛）：① 进 Worker 镜像（自有 `agentteams-repomesh-worker` + 默认 runtime 改
   `repomesh-runner`）；② 平台 sidecar `runner` 服务（control token 领所有 dispatch）；③ 本地 CLI 外部成员为默认。
   三者对「编码 CLI 凭据在哪」的答案不同，这是决定性的分歧点。
2. **worker 凭据模型**（V-1）：平台签发 per-worker token 并投影进 MCP header（复用 runner 协议的机制），还是只走网关注入；
   dev 直连开关在 `.env.example` 里默认开还是留空。
3. **就绪的定义**（#6/V-6/回执）：`/health/ready` 是否区分 `ready` / `degraded`；运行时配置改落库 + 进程退出重启；
   worker 的 `result.md` / `meta.json.status` 要不要进平台、映射成什么状态；recovery 默认开。
4. **启动逻辑收进 bootstrap 容器**（横切）：宿主脚本退化成 `docker compose up`，是否接受；与 #2 的解法（幂等建网络/卷 vs 拆 profile）一起定。
5. **服务端拆解模式下队长的协议**（V-3）：不发消息 / 发「仅知会」结构化消息。
6. **凭据主体化**（V-8）：service account 的粒度与策略表。
7. **环境去留**：讨论完是否整拆重来验证修复；或保留现场供取证。若整拆，照 from-zero-windows.md §0 的清单，
   并把 `.repomesh-workspaces/` 一起清。
8. **夹具仓**：本轮没有推任何分支或 PR，四个 GitHub 仓干净，无需清理。

---

## 4. 坑（下一会话别再踩）

- Git Bash 里 `export` 中文再交给 Python，控制台回显是乱码但落库是正确 UTF-8；打印用 `PYTHONIOENCODING=utf-8`。
- `docker exec … cat /var/run/…` 在 Git Bash 要 `MSYS_NO_PATHCONV=1`，否则路径被改写；`tasklist /FI` 要写 `//FI`。
- Docker Desktop 的四个镜像加速器全死，任何 `--build` 每个基础镜像多等 1–5 分钟；本机 Windows 时区是 Pacific，
  按时区猜地域的脚本都会误判。
- `GET /issues/{id}/discovery` 不回传 plan 正文，只回 `integration` 计数与 `effective_tiers`；plan 正文在
  `repository_intelligence.plan_snapshots`（`task_dag` / `execution_batches` 列）。
- 开工首两次 503（房间/身份未建好）是设计内的，等 20 秒重按即可。
- `start_assigned_task` 回 500 但副作用已发生（V-4），别据此重复调用。
- compose 看不到 `.secrets/` 文件变化，改完运行时配置要 `docker restart goai-infra-repomesh-api-1`（或 `up -d --force-recreate api`）。
- README 三条 Windows 启动命令都不能原样跑；能跑的拼法在 from-zero-windows.md §3、§6、§7。

---

## 5. 如果想先看到 PR 再讨论

两条路，都不是产品路径：
- 照 `module-test-team-handoff-20260901.md` §3.4 起宿主 bridge 外部成员（W4 的做法），能在 2 分钟内看到候选分支与 PR；
- 或 `.env` 改 `REPOMESH_DIRECT_WORKER_MCP_ENABLED=true` 后 `docker compose --profile platform up -d --force-recreate api`，
  让 copaw worker 能过 MCP 门禁——但随后仍卡在 V-5（没有 runner），只能证明 V-1 的判定。
