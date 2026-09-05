# 活体验证：runner 镜像重建后带 `adapter=mock` 领活 + §8.16 worker `docker restart` 对照（2026-09-05）

写于 2026-09-05 08:15Z（本机 UTC−7，01:15）。分支 `feat/hosted-native-wave1`，工作树 `HEAD = 2ac657fd`。
本会话**只观察、只探测**：没有改 `src/`、`tests/`、`contracts/`、`migrations/`、`docs/development/` 下任何文件，没有提交、没有推送，
四个 `catbobyman/repomesh-e2e-*` 夹具仓零推送；除了 §3 的那一次 `docker restart agentteams-worker-agt-worker-dfb8a4cda6f7`
和新建/重建 `goai-infra-repomesh-runner-1`，没有停/删/重启任何容器。所有原始输出在会话 scratchpad `live-verify/`（不入库），
文末 §6 列了文件名。凭据只读进 shell 变量，本文与 scratchpad 文件里都没有值。

上一份记录：`docs/startup-records/2026-09-04-handoff-wave1-pr-a.md`（其「没做的 ①」「T3」「T8」即本文两件事）。

---

## 0. 一句话

**runner 半边通、api 半边没验到**：重建后的 runner 每次轮询都带 `adapter=mock` 并拿到 204/200，但一键栈里跑着的 api
镜像是 09-02 10:46Z 构建的、**没有 `bdd04406` 的 adapter 过滤**（不带 `adapter` 也 204 而不是 400），于是 mock runner 把
09-02 遗留的 `claude-code` 调度也领走了；mock 链走到 `runner.accepted → runner.failed`（夹具仓基线单测本身是红的），
任务终态 `failed` 而非 `succeeded`。**§8.16 候选 (a) 基本不可用**：控制器 REST 根本不投影 `lastHeartbeat/lastActiveAt`，
`phase` 全程 `Running`，`containerState` 只在 2 秒采样里露了一次 `stopped`（可见窗口 ≤ 4.3 s），10 s 观察器命中概率 ≤ 43%。

## 1. 环境实况（08:03Z 前后）

| 组件 | 状态 |
|---|---|
| compose `goai-infra-repomesh-{postgres,api,web,bootstrap}` | Up 11 min（healthy）；**api 容器 09-02 11:25Z 创建、镜像 09-02 10:46Z 构建** |
| `goai-infra-repomesh-runner-1` | 会话开始时**不存在**（本机也没有旧 runner 镜像）；本会话构建并启动 |
| `agentteams-controller` / `agentteams-manager` | Up；控制器 API 只在 `agentteams-net` 内的 8090，宿主经 demo 的 socat 转发 `127.0.0.1:18090` 可达 |
| 8 个 `agentteams-worker-agt-*` | Up（`restart=no`，`agt-worker-dfb8a4cda6f7` 基线 `StartedAt=07:40:16.04Z`） |
| 控制器 `GET /api/v1/workers` | 22 个：8 个 `agt-*` Running + **14 个 `repo-*` Pending**（旧遗留 CR，`containerManaged=false`，控制器每轮记 「container management disabled for member, skipping」） |
| `setup/status` | 九项除 `github_app` 全 true |
| RepoMesh 库基线 | 4 任务（3 assigned / 1 in_progress）、1 尝试、**1 调度 `15431819…` queued `adapterId=claude-code`（09-02）**、1 预留 running（租约 09-02 已过期）、0 runner_events |
| 无关容器 | `coagenthub-smoke-pg` 仍在 crash-loop；两个 `agentteams-manager-*` 停在 Created（已知 V-2）；未动 |

api 容器实际环境与 `compose.yaml` 的差（容器创建早于 compose 后来加的行）：容器里**没有**
`REPOMESH_WORKER_DEFAULT_ADAPTER_ID`（→ `settings.py:120` 默认 `claude-code`）、`REPOMESH_AGENTTEAMS_MANAGER_IMAGE`、
`REPOMESH_EMBEDDING_*`、`REPOMESH_OPERATIONS_ALERT_ACTION`；`REPOMESH_DIRECT_WORKER_MCP_ENABLED=false`（来自 `.env`）。
本会话没有重建 api（禁止重启），所以下面所有「api 侧」的结论都是对这个 09-02 镜像说的。

## 2. 任务 1：重建 runner，证明带 `adapter=mock` 仍能领活

### 2.1 构建与启动

| 时间（UTC） | 动作 | 结果 |
|---|---|---|
| 07:52:07 → 07:57:53 | `docker compose --profile platform build runner` | 成功，`goai-infra-repomesh-runner:latest c1bcfab4b26f`（拉 `python:3.13-slim` + apt 占大头） |
| 07:58:23 | `docker compose --profile platform up -d --no-deps runner` | 容器创建；**runner 每次 `GET …/runner-tasks/next?wait=30.0&adapter=mock` 都 401** |
| 07:58:2x | 查因 | runner 环境里 `REPOMESH_RUNNER_CONTROL_TOKEN` 长度 0：控制令牌在 `.secrets/platform.env`，`start-platform.ps1:116-134` 把它读进进程环境再调 compose；裸 shell 直接 `compose up` 拿不到 |
| 07:59:26 | `set -a; . .secrets/platform.env; set +a` 后再 `up -d --no-deps runner` | 重建（只重建 runner；`--no-deps` 是为了不让 compose 顺手重建配置已漂移的 api） |
| 07:59:29 | runner 起来 | `starting runner: … adapters=mock`；第一次轮询 **200**（领到 09-02 的 `claude-code` 调度，见 2.3）；随后稳定 **204** |

`--no-deps` 的理由：`docker compose up -d runner` 会把 `depends_on` 的 api/postgres 一并对账，api 容器的环境已与 compose.yaml 漂移
（§1），不加 `--no-deps` 会触发 api 重建 = 重启。

### 2.2 证据行

runner 自己的日志（`docker logs goai-infra-repomesh-runner-1`）：

```text
2026-09-05 07:59:29,191 INFO repomesh_runner.main starting runner: workspace_root=/workspace state_dir=/home/runner/.runner-state labels=- adapters=mock
2026-09-05 07:59:29,552 INFO httpx HTTP Request: GET http://api:8000/api/v1/runtime/runner-tasks/next?wait=30.0&adapter=mock "HTTP/1.1 204 No Content"
2026-09-05 08:01:59,692 INFO httpx HTTP Request: GET http://api:8000/api/v1/runtime/runner-tasks/next?wait=30.0&adapter=mock "HTTP/1.1 200 OK"
2026-09-05 08:01:59,696 INFO repomesh_runner.task_source accepted task run=e76cebc5-2877-4833-9bb2-853e57224762 attempt=1
```

api 访问日志（uvicorn 带查询串，`docker logs goai-infra-repomesh-api-1`）：

```text
INFO:     172.19.0.6:40790 - "GET /api/v1/runtime/runner-tasks/next?wait=30.0&adapter=mock HTTP/1.1" 401 Unauthorized   ← 07:58 无令牌
INFO:     172.19.0.6:45646 - "GET /api/v1/runtime/runner-tasks/next?wait=30.0&adapter=mock HTTP/1.1" 200 OK             ← 07:59:29 有令牌
INFO:     172.19.0.6:45646 - "GET /api/v1/runtime/runner-tasks/next?wait=30.0&adapter=mock HTTP/1.1" 204 No Content
```

会话结束（08:12Z）runner 仍每 30 s 一次 204，无异常。

### 2.3 api 侧没有过滤：契约的另一半在活体上验不到

从 runner 容器内用它自己的令牌探三种查询（`15-adapter-probe-from-runner.txt`）：

| 查询 | 期望（`contracts/runtime/README.md`，2026-09-04 起生效） | 09-02 镜像的 api 实际 |
|---|---|---|
| `wait=1`（无 `adapter`，control token） | **400** | **204** |
| `wait=1&adapter=mock` | 204/200 | 204 |
| `wait=1&adapter=claude-code` | 204/200 | 204 |

在 api 容器里 `inspect.getsource(repomesh.modules.agent_runtime.api.router)` 没有 `_adapter_filter`，
`repomesh.modules.agent_runtime.api.runner_store` 模块不存在——就是 `bdd04406` 之前的代码。后果已经发生了一次：
07:59:29 runner 第一次轮询就领到了 09-02 遗留的调度 `15431819…`（`adapterId=claude-code`），执行到 `DriverError: claude-code: binary_not_found`，
`runner.failed` 回投后 **任务 `b6e0bc59…`（09-02 pricing-core worker 任务）→ `failed`、预留 `0e41307c…` → `failed`、旧计划 `6f438ac3…` → `failed`**。
这正是 `bdd04406` 要堵的「无主体 runner 抢别人队列」；新 api 镜像能否堵住，要重建 api 后再看（本会话不做）。

### 2.4 mock 链逐步（issue `7bbe605f-b7e8-5776-beb3-d81786cdc316`）

驱动方式：全部 curl/`urllib` 打本地 API（action token），没有碰 web UI；脚本 `drive_issue.py` 在 scratchpad。
需求文案：pricing-core `quote()` 加 `max_discount_ratio`（默认 0.3）上限截断并标 `discount_capped`，只改一个仓。

| # | 步骤 | 时间（UTC） | 结果 | 关键 id / 备注 |
|---|---|---|---|---|
| 1 | `POST /issues` | 07:59:40 | **PASS** 201 | `issue_id=7bbe605f…`，org `7ce7e70e…`，created_by = 组织 Leader `703b1dfa…` |
| 2 | `discovery/analysis` | 07:59:40→43 | **PASS**（`sufficient=False, confidence=0.7, 3 问`；`force_continue` 后 replayed 到 step 2） | 真 LLM `deepseek-chat` |
| 3 | `discovery/candidates` | 07:59:43→46 | **PASS** | 唯一候选 pricing-core，score 1.0 |
| 4 | `discovery/classification` | 07:59:46→49 | **PASS** | REQUIRED = pricing-core（risk low），无 MAYBE |
| 5 | `discovery/approval` | 07:59:49 | **PASS** 200 | 按原样批 |
| 6 | `discovery/plan` | 07:59:49→55 | **PASS** step 4 done | 1 任务 / 1 批次，`tests=["python scripts/run_tests.py"]` |
| 7 | `discovery/materialize` | 08:00:52→53 | **PASS** 200（一次过，团队已存在无需建房） | `plan_id=af09b1e4-5093-4769-b69c-4de21923de47`；任务：队长 `7ef54f26…`（assignee `10f3ec71…`）、worker `1cd91edb…`（assignee `87fdc9c2…`）；`team_count=1` |
| 8 | 任务包与房间派工 | 08:00:53 / 08:01:19 | **PASS** | 团队房 `!3IU075BSWiAQORHR4e` 08:00:53 `@admin` 发 `start_assigned_task` 说明；MinIO `teams/repomesh-team-dfb8…/shared/tasks/1cd91edb…/` 08:01:19（v1：manifest/meta/spec） |
| 9 | copaw worker 自己调 MCP | 08:01:0x | **BLOCKED（V-1 复现）** | api 日志 `POST /api/v1/mcp/worker` 401 ×5、`GET` 405 ×6、oauth 发现 404；worker 08:01:31 在房里 @队长报 `BLOCKED: 1cd91edb…` |
| 10 | 代 worker 起任务：`POST /agent-actions/start-worker-task {task_id, worker_agent_id, adapter_id:"mock"}` | 08:01:45 → 08:01:50（4.6 s） | **PASS** 202 | `run_id=e76cebc5-2877-4833-9bb2-853e57224762`，workspace `/runner-workspaces/w/f13db2c7e7edbea53ed0/20b712cc4c33daf2811d`，`base_sha=882231dd`；**必须显式 `adapter_id=mock`**（这个 api 的默认是 `claude-code`，见 §1） |
| 11 | runner 领活 | 08:01:59.69 | **PASS** 200（起任务后 14 s，长轮询窗口内） | `accepted task run=e76cebc5…` |
| 12 | `runner.accepted` 回投 | 08:01:59.70 | **PASS** 202 | payload `{"adapterId":"mock"}`，`executionId=4662536d…`，`assignmentAttemptId=ef5305b7…` |
| 13 | mock 编码代理执行 | 08:01:59→08:02:00 | **PASS**（scenario success，会话 `mock-success-15c24030bf7e`，不改文件） | ledger `task-ledger.json` 记了 key |
| 14 | 计划测试命令 `python scripts/run_tests.py` | 08:02:00 | **FAIL exit 1** | 见 2.5 |
| 15 | 终态事件与投影 | 08:02:00.02→03 | `runner.failed` 202 → dispatch `failed`、预留 `4662536d…` `failed`、**任务 `1cd91edb…` `failed`**、队长任务 `7ef54f26…` `failed`、计划 `af09b1e4…` `failed` | `runner_events` 成对：accepted + failed（两轮各一对，共 4 行） |
| 16 | 交付 / 房间回执 | — | **未到达**（任务没 succeeded；`/deliveries` 里该 issue 无 delivery；团队房里平台没发失败回执） | 两条尝试记录（`3e4afc19…`、`ef5305b7…`）在任务失败后仍 `state=active`、`finished_at` 空 |

结论：**执行面闭环本身是通的**（起任务 → 带 adapter 领活 → accepted → 执行 → 终态事件 → 任务/预留/调度/计划一致落终态），
指南 §6 说的「任务变 succeeded」没到，卡在夹具仓自身。

### 2.5 为什么是 `failed`

1. **夹具仓基线是红的**：在 runner 容器里对 worktree 跑 `python scripts/run_tests.py`（`48-worktree-test-diagnosis.txt`）：
   `Ran 5 tests … FAILED (errors=2)`，`test_quote_reports_the_requested_currency: quote() got an unexpected keyword argument 'currency'`、
   `'Quote' object has no attribute 'currency'`。`catbobyman/repomesh-e2e-pricing-core` 在 `882231dd` 上 tests 已经在测多币种而 src 没实现
   （像是 09-01/09-03 某次演练只推了测试）。mock 代理不改文件，测试命令必红，`executor.py:313-323` 如实给 `test_command_failed`。
   要让 mock 链走到 `succeeded`，要么用指南 §2 的本地 fixture 仓（`/runner-workspaces/fixtures/checkout-pricing-api`，本机 `.repomesh-workspaces/fixtures/` 里有），要么把该 GitHub 夹具仓修绿。
2. **顺带发现的真缺陷——runner 读不到 worktree 的 `.git` 指针**：api（root）在 `git_worktree.py:158` 对 `.git` 文件 `chmod(S_IREAD|S_IWRITE)` = 0600，
   runner 以 uid 10001 跑，容器内 `git status` → `fatal: error opening '…/.git': Permission denied`（09-02 的 `w/503a…` 和今天的 `w/f13d…` 都是）。
   `executor.py:_git_output` 把非零退出当 `None`，于是 **containerized runner 永远报 `changedFiles=[]`**，真 CLI 改了代码也收不到 diff。
   宿主上看是 644，是容器视角的 0600 决定的（api 容器内 `ls -la` 也是 `-rw-------`）。

## 3. 任务 2：§8.16 候选 (a)——控制器 `WorkerStatus` 能否看出 `docker restart`

### 3.1 方法

- 目标：`agentteams-worker-agt-worker-dfb8a4cda6f7`（控制器资源 `agt-worker-dfb8a4cda6f7`，pricing-core 团队 worker）。
- 端点：`GET http://127.0.0.1:18090/api/v1/workers/agt-worker-dfb8a4cda6f7/status`（Bearer = `.secrets/platform-runtime.env` 的控制器令牌；
  `/workers/<name>` 与 `/status` 返回同一投影）。
- 采样器 `restart_poller.sh`：循环里 curl + `docker inspect` + python 解析 + `sleep 1`，**实际节奏 ≈ 2.0 s**（min 1.93 / 中位 2.03 / max 2.44），
  236 个样本，08:03:16 → 08:11:15Z；每样本记 `phase, state, containerState, lastHeartbeat, lastActiveAt, http, 整个 JSON 的 sha8, docker Status, docker StartedAt`。
- 08:04:06.596Z `docker restart`，09.36 s 后返回；之后继续采样 7 分钟。

### 3.2 先于数据的硬事实：REST 投影里没有 `lastHeartbeat` / `lastActiveAt`

- Go 类型 `WorkerStatus`（`agentteams-controller/api/v1beta1/types.go:371-383`）确有 `LastHeartbeat/LastActiveAt`（`omitempty`），
  但 REST 响应结构 `WorkerResponse`（`internal/server/types.go:56-80`）**没有这两个字段**，`workerToResponse`（`internal/server/resource_handler.go:718-745`）
  只搬 `Phase / ContainerState / MatrixUserID / RoomID / Message / ExposedPorts`。
- 而且 vendored 控制器里**没有任何代码给容器托管 worker 写 `Status.LastHeartbeat`**（全仓 grep 只有 `team_controller.go:713` 的读）；
  `LastActiveAt` 只在 `appservice_handler.go:393` 给 standalone worker 写；`worker_controller.go:118` 的 `edgeHeartbeatStale` 只用于 edge worker。
- 活体返回的完整文档只有：`name, phase, containerManaged, state, model, runtime, skills, mcpServers, containerState, matrixUserID, roomID, team, role`。
  `GET /api/v1/teams/<team>` 也只有 `leaderReady / readyWorkers / totalWorkers`，没有成员级心跳。
- 236 个样本 `lastHeartbeat` / `lastActiveAt` 非空次数 = **0**。

所以候选 (a) 在今天的控制器上只剩 `phase` 与 `containerState` 两个字段可读。

### 3.3 时间线

| 时间（UTC） | 来源 | 事件 |
|---|---|---|
| 07:40:16.04 | docker inspect | 容器基线 `StartedAt`，pid 15678 |
| 08:03:16 | poller | 开始采样：`phase=Running state=Running containerState=running`，JSON sha `a1fd299a` |
| 08:03:44 | 团队房 | copaw 队长自发把任务改派成 copaw 原生任务 `repomesh-maxdiscount-20260905-080308-01`（worker 本地 `shared/tasks/` 08:03:48 出现该目录） |
| 08:04:06.60 | shell | `docker restart` 发出 |
| 08:04:07.12 | worker 日志 | copaw 收到 SIGTERM：`Shutting down` → 停 agent、队列（`stop_all: 1 consumer(s) still pending after 5s`） |
| 08:04:07 → 08:04:13 | poller（4 个样本） | **`containerState` 仍 `running`**（进程还在退出中，Docker 还没判 exited） |
| 08:04:12 | 团队房 | worker 最后一条：`Confirmed: … start_assigned_task fails with HTTP 405` 紧接 **`Error: Task has been cancelled!`**（进行中的 LLM 任务被关机钩子取消） |
| 08:04:15.08 | docker inspect | 容器 `FinishedAt` |
| 08:04:15.40 | poller | **`containerState=stopped`**（sha `6d827870`），`phase=Running state=Running` 不变；同一样本里 docker 已 `running`、`StartedAt=08:04:15.65` |
| 08:04:15.65 | docker inspect | 新 `StartedAt`，pid 35432 |
| 08:04:15.90 | worker 日志 | `Starting copaw-worker: agt-worker-dfb8a4cda6f7` |
| 08:04:16.14 | shell | `docker restart` 返回（9.36 s） |
| 08:04:17.67 | poller | `containerState=running`，sha 回到 `a1fd299a`；此后 7 分钟没有任何字段再变 |
| 08:04:20.88 | worker 日志 | `Worker initialized`（从 MinIO 拉文件、桥接配置完成） |
| 08:04:27 | copaw.log | `Workspace started successfully: default`，命令注册、队列管理器起来（≈ 重启后 20 s） |
| 08:05:14 | worker 文件 | `matrix_sync_token` 更新（Matrix 同步已恢复的上界） |
| 08:05:19 → 08:05:20 | 控制器日志 | 对该 worker 的例行对账（MinIO 用户/策略、`openclaw.json`、`AGENTS.md` 推送、一条 `force-leave-room` 管理命令）——**是 08:04 之后控制器唯一提到这个 worker 的地方，距重启 73 s，且没有任何一行记录容器停/起** |
| 08:11:15 | poller | 结束 |

（`docker logs agentteams-controller` 只是 supervisord 输出；控制器自己的日志在容器内 `/var/log/agentteams/agentteams-controller-error.log`，08:04 这一分钟 72 行全是别的 worker 的例行对账。）

### 3.4 分析

| 问题 | 答案 |
|---|---|
| `lastHeartbeat` 有没有断档？ | **没有这个字段可看**（3.2）。 |
| `containerState` 离开过 `running` 吗？ | 有，**恰好一个样本**（08:04:15.40Z，`stopped`），在容器 `FinishedAt` 后 0.32 s、`StartedAt` 前 0.25 s——控制器对 Docker 状态的反映几乎是即时的（应是订阅事件或高频查询），但 Docker 自己在停止阶段（SIGTERM 后 ~8 s）一直报 `running`，所以可见的「非 running」窗口只有 exited→再起之间那 ~2 s。上下界：前一个 running 样本 08:04:13.40 到下一个 running 样本 08:04:17.67，**可见窗口 ≤ 4.3 s**。 |
| `phase` 变了吗？ | **没有**，236 个样本全 `Running`（`state` 也全 `Running`）。与 09-03 S-6 一致。 |
| 各字段多久反应？ | `containerState`：停 → +0.3 s 内变 `stopped`，起 → ≤ 2 s 内回 `running`；`phase`：不反应；`message`：全程空。 |
| 10 s 观察器（`hosted_native_observer_interval_seconds=10`）会看到吗？ | 命中概率 **≤ 43%**（4.3 s / 10 s 的上界，实际更接近 2 s / 10 s ≈ 20%），且看到的只是一个孤立的 `stopped`，无法区分「重启完成」和「正在重启/已挂」。 |
| 控制器有没有把状态变化记下来？ | **没有**：日志里没有任何容器停/起记录；重启后 73 s 才有一轮例行对账。 |
| S-6：`.copaw` 与共享任务目录保住了吗？ | **保住了**：`/root/.copaw-worker/agt-worker-dfb8a4cda6f7/.copaw/` 全在（`config.json`/`providers.json` 起动时按同样大小重写，`skill_pool`、`custom_channels`、`workspaces` 原样）；本地 `shared/tasks/` 六个目录（含 `1cd91edb…` 与 copaw 自建的 `repomesh-maxdiscount-…`）和 `/work/{ca0ef2b0,fb1e42bc,cfe30c99}` 三个工作区都在。**丢的只有会话内存**：08:04:12 的 `Task has been cancelled!` 之后 worker 没有在房里续做。 |

### 3.5 对 §8.16 的判定

候选 (a)「观察器读控制器 `lastHeartbeat`/`containerState` 序列」——**边缘偏无用（marginal→useless）**：
两个心跳字段 REST 不投影且控制器不为托管 worker 写；`phase` 对 `docker restart` 完全盲；`containerState` 虽然反映及时，
但可见窗口只有约 2–4 s，10 s 观察器大概率错过，即便看到也给不出「启动时间晚于 `notified_at`」这个 D-12 ② 真正要的事实。
它至多能作 D-12 ③ 的补充信号（`worker_not_running` 抓的是「长时间不 running」而非「重启过」）。
D-12 ② 的载体只能落在 (b)（verifier/观察器带上 worker 容器 `State.StartedAt`——本次 `docker inspect` 在同一秒就给出了新 `StartedAt=08:04:15.65Z`，
与旧值可直接比较）或 (c)（fork 控制器加 `startedAt`）；若第一阶段不 fork，(b) 需要观察器所在的进程能访问 Docker socket（bootstrap 容器已挂 `/var/run/docker.sock`，是现成的落点）。

## 4. 意外发现（按严重度）

1. **一键栈的 api 是 09-02 镜像**（`docker inspect` 镜像创建 2026-09-02T10:46:09Z），没有 `bdd04406` 的 adapter 过滤：无 `adapter` 的 control token 轮询 204 而非 400；
   mock runner 一上线就领走 `claude-code` 调度并让 09-02 的任务 `b6e0bc59…`、计划 `6f438ac3…` 变 `failed`。**要验证契约的 api 半边必须重建 api**（本会话未做）。
2. **api 容器环境与 `compose.yaml` 漂移**（§1）：缺 `REPOMESH_WORKER_DEFAULT_ADAPTER_ID` → 默认 `claude-code`；copaw worker 若真能调通 MCP 也会入队 `claude-code`，mock runner（新 api 过滤后）永远领不到。
   本会话靠显式 `adapter_id=mock` 绕过。
3. **runner 读不到 worktree `.git`（0600 by root vs runner uid 10001）** → `changedFiles` 恒空（2.5-2）。真 CLI 接入前要修（`git_worktree.py:158` 的 chmod 或 runner 用户）。
4. **裸 `docker compose up` 起 runner 会 401 死循环**：控制令牌只在 `.secrets/platform.env`，launcher 之外要自己 `set -a; . .secrets/platform.env`。
   与 09-02 记录的 P-3/P-4 同源（compose 不透传 `.secrets`）。
5. **夹具仓 `repomesh-e2e-pricing-core@882231dd` 单测红**（tests 有 currency 用例、src 没实现），任何不改代码的执行都 `test_command_failed`。
6. **V-1 仍在**：copaw worker 的 mcporter 打 `POST /api/v1/mcp/worker` 401（`.env` 关了 dev 直连，投影里没有网关令牌）；worker 08:01:31 报 BLOCKED，
   队长 08:03:44 自作主张改派成 copaw 原生任务 `repomesh-maxdiscount-…`（在 MinIO/worker 本地留下目录）。
7. 任务 `failed` 后两条 `task_assignment_attempts` 仍 `state=active`、`finished_at` 空（`3e4afc19…`、`ef5305b7…`）；`worker_recovery_operations` 出现 2 行（恢复开关为 false）。
8. 控制器里 14 个 `repo-*` Pending 的 worker/team CR 是早期遗留，每轮对账刷日志。
9. api 25 分钟内无 ERROR/Traceback；非 2xx 直方图：404 ×15（mcporter OAuth 发现）、401 ×15（runner 无令牌 ~10 + copaw MCP ~5）、405 ×6（mcporter 探 SSE）。
10. 采样器实际 2 s 而非 1 s（每轮 curl + `docker inspect` 的开销），对结论无影响，但 3.4 的窗口估计要按 2 s 粒度读。

## 5. 残留（未清理）

- 新 issue `7bbe605f…`、计划 `af09b1e4…`（failed）、任务 `7ef54f26…`/`1cd91edb…`（failed）、调度 `e76cebc5…`、预留 `4662536d…`、4 行 `runner_events`。
- 09-02 的 `b6e0bc59…`/`6f438ac3…`/`15431819…`/`0e41307c…` 由 queued/in_progress/running 变 failed。
- MinIO `teams/repomesh-team-dfb8…/shared/tasks/1cd91edb…/`；worker 本地 `shared/tasks/{1cd91edb…, repomesh-maxdiscount-20260905-080308-01}`。
- 宿主 `.repomesh-workspaces/w/f13db2c7e7edbea53ed0/20b712cc4c33daf2811d`（worktree）。
- 容器 `goai-infra-repomesh-runner-1`（`restart: unless-stopped`，带令牌，仍在每 30 s 轮询）。
- `agt-worker-dfb8a4cda6f7` 重启过一次（`StartedAt=08:04:15.65Z`）。

## 6. 原始文件（scratchpad `live-verify/`，不入库）

`00-docker-ps-baseline.txt`、`10-runner-build.log`、`11-runner-up.log`、`12-runner-logs-*.txt`、`13*-env-keys.txt`、`14-api-log-runner-401.txt`、
`15-adapter-probe-from-runner.txt`、`20-worker-inspect-baseline.txt`、`21-worker-doc-baseline.json`、`22-workers-list.json`、`24-team-doc-baseline.json`、
`25-worker-fs-{before,after}-restart.txt`（前者的 pid1 命令行已把 MinIO 凭据打码）、`26-drill-timeline.txt`、`28-controller-own-log-*.{txt,jsonl}`、
`29-worker-logs-restart.txt`、`29b-copaw-log-after-restart.txt`、`30-openapi.json`、`31-openapi-paths.txt`、`40-issue-chain.log`、`41-*.json`、
`42-new-tasks.txt`、`43-minio-tasks.txt`、`44-team-room-*.{json,txt}`、`45-start-worker-task.json`、`46-runner-log-mock-run.txt`、
`47-runner-events-e76cebc5.txt`、`48-worktree-test-diagnosis.txt`、`48b-runner-git-perms.txt`、`49-{issue,deliveries}.json`、
`50-probe-analysis.txt`、`60-docker-ps-end.txt`；脚本 `drive_issue.py`、`restart_poller.sh`、`analyze_probe.py`；数据 `restart_probe.csv`（236 行）、`restart_probe_raw.jsonl`。
