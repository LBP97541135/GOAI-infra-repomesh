# 2026-09-02 从新建 issue 到提交链的实机验证（接在从零启动之后）

## 0. 目的与起点

**目的**：在 `2026-09-02-from-zero-windows.md` 起好的平台上，用用户 GitHub 名下的四个公开仓库
（`catbobyman/repomesh-e2e-pricing-core`、`repomesh-e2e-checkout`、`repomesh-e2e-billing` 三个业务仓 +
`repomesh-test-assets` 测试资产仓）走产品路径：建组织 → 接入仓库 → 新建 issue → 发现链 → 物化 →
盯房间 → 看是否到达最终提交链（候选分支 / PR）。每一步记命令、预期、实际、状态；卡点单列。

**起点（04:50）**：
- 平台：postgres / api / web / bootstrap healthy；`agentteams-controller` + `agentteams-manager` 在跑；
  `setup/status` 五项必需全 true，`ready_for_project_creation=true`。
- 数据：`identity_access.organizations` **0 行**（用户以为已建，实际没有）；`local_human_accounts` 1、
  `local_human_sessions` 1；`repository_intelligence.repositories` 0 行。
- 交付配置（api 容器 env）：`REPOMESH_DELIVERY_AUTO_ENABLED=false`、`REPOMESH_GITHUB_APP_ID=0`、
  无 `REPOMESH_DELIVERY_GITHUB_TOKEN`。即产品默认配置下**不会自动交付到 GitHub**。
- 本机 gh CLI 已登录 `catbobyman`（scope 含 repo）。

**鉴权边界（决定了谁来点）**：发现链（`POST /issues`、`discovery/*`、`materialize`）、`/bridge/*`、
`/console/organizations`、`/repositories/scan-repo`、房间读模型都只要平台的 action token
（`.secrets/platform.env` 里的 `REPOMESH_AGENT_ACTION_TOKEN`）；`POST /repositories/{id}/agent-team`、
`/setup/repositories/onboard`、`/agents/native` 要管理员会话。本记录由 AI 用 action token 驱动 API，
不使用用户密码；需要管理员会话的步骤会写明由谁操作。

## 1. 建组织：`POST /api/v1/console/organizations`（action token）

- **时间**：06:40:23（本机时钟；下同）
- **命令**：`{"name":"repomesh-e2e","leader_resource_name":"repomesh-e2e-manager","idempotency_key":"e2e-org-20260902-a1b2c3"}`
- **预期**：201，返回组织 id 与组织 Leader 的 agent id（契约 v0.3 §2.3：Leader 只是「期望态」目录行，不是运行中的 agent）。
- **实际**：201。`organization_id=7ce7e70e-f501-5ece-b998-ce2f4c4cd550`，`leader_agent_id=703b1dfa-024d-41f0-ab10-ce3ebec025c1`。
- **状态**：**PASS**
- **备注**：用户以为控制台里已经建过组织，实际库里是空的；这一步是 AI 用 action token 补建的。控制台的
  workspace 切换器调的是同一个接口。

## 2. 接入四个仓库：`POST /api/v1/repositories/scan-repo` × 4（action token）

- **时间**：06:40:23 → 06:40:40（每仓 3–5 秒，匿名 GitHub API）
- **实际**：四仓各 `registered=1`：

  | 仓库 | repository_id |
  |---|---|
  | repomesh-e2e-pricing-core | `dfb8a4cd-a6f7-4ee7-95e4-197963151308` |
  | repomesh-e2e-checkout | `39ae6814-9755-4568-b6d4-96ab3d9e36d7` |
  | repomesh-e2e-billing | `d47d566d-ad2a-41dd-bad5-ea8ea9553220` |
  | repomesh-test-assets | `22c4d38e-465c-42c8-b93b-20ae97cc250d` |

  扫描出来的 `test_commands` / `test_paths` 都是空的（扫描不推断测试命令）。
- **状态**：**PASS**
- **补配置**：`PATCH /repositories/{id}/verification` 给三个业务仓填 `python scripts/run_tests.py` + `tests/**`
  （各仓 README 写的入口），测试资产仓填 `python environments/e2e-fixture-joint/run_round.py` + `evidence/**`
  （沿用 W4 的配方）。结果见下一行。

- 四仓 PATCH 全部 200，`test_commands` / `test_paths` 落库。
- **测试资产仓的 `cross-repo-test-team` 档案开关**：只有控制台 UI（管理员会话）能翻，没有 action token 接口。
  本轮**没有翻**，所以测试团队不会经 S-1 追加进拓扑；它在分类里只会作为普通仓库出现（见步骤 3 的 MAYBE）。

## 3. 新建 issue 并跑发现链（action token，真 LLM）

- **时间**：06:41:46 → 06:42:09（23 秒，五步）
- **issue**：`POST /issues`，需求文本：「报价支持多币种：pricing-core 的 quote() 增加 currency 参数并按币种规则计算
  （零小数币种如 JPY 必须取整为整数金额）；checkout 的订单摘要与 billing 的发票渲染要按新契约展示带币种的金额。
  三个仓都要改并保持各自单测通过。」`created_by_agent_id` = 组织 Leader。→ 201，
  `issue_id=ff6a9f90-1e0c-5fe7-8a9f-9b354c1aa754`。
- **analysis**（6 s）：`sufficient=false, confidence=0.7`，追问 3 条（业务场景 / 展示方式 / 非零小数币种规则）；
  按 W4 的做法 `force_continue=true` 放行（脚本自动做，与控制台「忽略追问继续」等价）。
- **candidates**（3 s）：pricing-core / checkout / billing 各 score 1.0，rationale 引用了需求原句；test-assets 也被列出。
- **classification**（3 s）：REQUIRED = 三个业务仓（confidence 1.0）；MAYBE = repomesh-test-assets（0.6，
  理由是它有 `scenarios/multi-currency-joint` 场景可能要更新）；EXCLUDED 空。
- **approval**：以组织 Leader 身份 `approved`，`adjustments=[]`，evidence_version 与分类一致 → 200。
- **plan**（9 s）：`step=4 state=done`，`integration={"task_dag_count":4,"batch_count":3,"contract_count":3}`，
  `effective_tiers`：三仓 required、test-assets maybe。
- **状态**：**PASS**
- **观察**：`GET /issues/{id}/discovery` 不回传 plan 正文（只有 integration 计数与 tiers），W4 的驱动脚本按旧形状读
  `plan.task_dag` 会得到 null——读模型形状变了，脚本没跟上，不影响链路。需求文本在 Git Bash → Python 的 env 传递里
  控制台回显是乱码，但落库是正确的 UTF-8（已核对）。

## 4. 物化：`POST /issues/{id}/discovery/materialize`（action token）

- **时间**：06:44:06 → 06:44:50（3 次尝试，44 秒）
- **实际**：
  1. 第 1 次 503：`the execution plane has no rooms for this project's teams (the AgentTeams controller has not
     created rooms for repomesh-team-… ×4 yet); nothing was started`。
  2. 第 2 次（+20 s）503：`has not provisioned Matrix identities for agt-leader-…/agt-worker-… yet`。
  3. 第 3 次（+42 s）200：`plan_id=6f438ac3-28a5-4284-abe3-f40ebf69267c`，4 个 task，`team_count=4`，
     `repositories=[billing, checkout, pricing-core, test-assets]`。
- **状态**：**PASS**（两次 503 是设计内的「再按一次」，README/控制台文案一致）
- **物化落地的东西**：
  - 目录：4 个 `repository_leader` + 4 个 `worker`（`agt-leader-<repo12>` / `agt-worker-<repo12>`），全部 active。
  - AgentTeams：8 个 `agentteams-worker-agt-*` 容器（镜像 `agentteams-copaw-worker:v1.2.0`）30–60 秒内全部 Up；
    组织 Leader 的 `agentteams-manager-repomesh-e2e-manager` 容器只到 **Created 没启动**（待查）。
  - 4 支团队 `runtime_status=ready`，各有 team room + leader room（8 间房，id 见盯房脚本的 rooms.txt），
    `decomposition_mode=server`。
  - 计划批次：`[[pricing-core, test-assets], [checkout], [billing]]`，第 0 批两支团队的队长任务与 worker 任务都
    是 `assigned`。test-assets 以 MAYBE 档进了计划，被当成普通业务仓派了改代码任务。
- **观察方式**：`scripts/module-test-team/room_watch.py` 改指本栈（Matrix 18080 + `@admin` token、API 8000 +
  action token），4 秒一拉 8 间房，落 `scratchpad/rooms/e2e1/`（会话结束前摘要抄进本记录）。

## 5. 房间与执行面观察（06:44:50 → 06:48）

**房间里发生了什么**（Matrix 原始事件，`@admin` 为平台服务端发信身份）：
- 06:44:49 队长房 `leader-197963151308`（pricing-core）：`@admin` → `@agt-leader-dfb8a4cda6f7`，正文是 LLM
  生成的仓内任务描述（「Modify quote() to accept a mandatory currency parameter (ISO 4217)…」）。
- 06:44:50 团队房 `team-197963151308`：`@admin` → `@agt-worker-dfb8a4cda6f7`：「A verified RepoMesh task package
  is ready. Do not edit code directly… Call the MCP tool `repomesh-task-control.start_assigned_task` with
  `{"task_id":"b6e0bc59…","worker_agent_id":"87fdc9c2…"}`. Task package: teams/repomesh-team-dfb8…/shared/tasks/…」。
- 06:44:50 test-assets 的两间房同样各收到一条（task `54250ad9…`）。
- 06:45:13 起 test-assets 的 worker `@agt-worker-22c4d38e465c` 开始在团队房逐条推理：
  「MCP server is timing out / returning HTTP 405 for SSE tool listing」→「POST 得到 422 不是 405，服务端是
  Streamable HTTP」→「The token from the config is invalid for this endpoint」→ 06:47:13 写回 `result.md`
  `STATUS: BLOCKED`，并把 BLOCKED @ 给队长。它的诊断是准确的（见下）。
- 06:45:14 起 test-assets 的队长 `@agt-leader-22c4d38e465c` 在队长房绕圈：「shared/tasks/b4980c68…（我的任务目录）
  不存在，只有 54250ad9…」「No repository is checked out anywhere on the filesystem」「identity confusion」…
  持续到 06:48 仍未收敛。
- pricing-core 的 worker/队长在 06:48 前**没有任何回复**（同样的镜像、同样的消息；差异待查，可能只是 LLM 延迟）。
- checkout / billing 的房间只有建房与 `room.meta` 心跳，符合「第 1/2 批未开始」。

**MinIO 任务包**（`agentteams-storage/teams/<team>/shared/tasks/`）：
- pricing-core：`b6e0bc59…/{manifest.json, meta.json, spec.md}` 06:44:49 写入 —— 任务包投递链是通的。
- test-assets：`54250ad9…/{spec.md 06:44:50, manifest 06:46:05, meta.json + result.md 06:47:14}`，
  `meta.json.status=submitted`；`result.md` 首行 `STATUS: BLOCKED`。
- 队长任务（`9ca46162…`、`b4980c68…`）**没有任务包**：server 拆解模式下队长只收消息，不领包，是设计；
  但 copaw 队长不知道这一点，把它当故障找了三分钟。

**平台侧状态**：`GET /bridge/plans/6f438ac3…` 第 0 批两支团队的队长任务与 worker 任务始终 `assigned`；
worker 写回的 BLOCKED 回执没有任何东西消费它。

### 卡点 V-1（阻断）：Worker 调不动 `start_assigned_task`——MCP 端点 401

- **证据**：api 访问日志 10 分钟内 `POST /api/v1/mcp/worker` **12 × 401**、`GET` 6 × 405（mcporter 先探 SSE）、
  `GET /.well-known/oauth-protected-resource/...` 4 × 404（mcporter 的 OAuth 发现）。
- **根因**：`src/repomesh/api/worker_mcp.py:_authorize` 只接受两种凭据：Bearer == `REPOMESH_AGENT_ACTION_TOKEN`，
  或 `REPOMESH_MCP_GATEWAY_TOKEN(S)`（Bearer 或 `X-RepoMesh-Gateway-Token`）。而控制器写进 worker 容器
  `/root/.copaw-worker/<worker>/config/mcporter.json` 的 `Authorization: Bearer <64 位>` 与三枚平台 token
  （action / gateway / runner-control，均 43 位）**逐一比对都不相等**（sha256 前 12 位：worker `699188c47bc4`，
  三枚平台 token `47aca625a3e3` / `7a21a9946b4e` / `9a75922951d4`）。`principal_registration.with_task_control()`
  投影 MCP server 时只给 URL 不给 header，header 是控制器自己配的，平台侧没有登记这枚令牌。
- **为什么 dev 编排本该绕开它**：`compose.yaml:64` 把 `REPOMESH_DIRECT_WORKER_MCP_ENABLED` 默认成 `true`
  （development 环境下免鉴权直连），但 **`.env.example:21` 与 `:105` 都写死 `=false`**，`.env` 照抄后把
  compose 的 dev 默认覆盖掉了。也就是说：照 README 复制 `.env.example` 的人，worker 一定 401。
- **影响**：产品路径下任何 copaw worker 都无法启动 governed run，计划永远停在 `assigned`。
- **建议修法**：二选一——`.env.example` 的该项留空或改 `true`（让 compose 的 dev 默认生效）；或让
  `worker_mcp._authorize` 接受控制器签发的 worker 令牌（需要平台侧登记/校验路径）。另外 mcporter 0.9.0 先试
  SSE 再退回 POST 的行为会在日志里制造 405 噪音，与 401 无关。

### 卡点 V-2（缺陷，不阻断）：组织 Leader 的 manager 容器起不来

- `agentteams-manager-repomesh-e2e-manager` 状态 `Created`，`docker inspect` 错误：
  `Bind for 127.0.0.1:18888 failed: port is already allocated`。安装器装的 `agentteams-manager` 已占 18888，
  运行时投影为组织 Leader 再起一个 manager 时用了同一个主机端口。server 拆解不依赖它，本轮没有因此阻断。

### 观察 V-3（设计与 copaw 行为不匹配）：队长无任务包、无仓库检出

- server 拆解模式下队长只收自然语言消息，不发任务包、不给仓库工作区；copaw 队长按 AgentTeams 自己的
  task-management 技能去找 `shared/tasks/<my task id>` 和本地仓库，找不到就反复自查身份，三分钟未收敛。
  平台没有向它说明「你不需要做什么」，也没有任何消息把它拉回。

## 6. 绕过 V-1 继续往下走：由 AI 用 action token 代 worker 调 `start_assigned_task`

- **时间**：06:50:17
- **命令**：`POST /api/v1/mcp/worker`，JSON-RPC `tools/call start_assigned_task
  {"task_id":"b6e0bc59…（pricing-core 的 worker 任务）","worker_agent_id":"87fdc9c2…"}`，Bearer = action token。
  这正是 worker 在 V-1 不存在时会做的那一次调用，用来验证 V-1 之后的链路。
- **实际**：**HTTP 500** `{"detail":"internal server error","error":"AttributeError"}`；api 日志的 traceback 末行
  `AttributeError: 'McpCallResult' object has no attribute 'task'`（`worker_mcp.py` 拿到 `mcp_call_guard().call_gated()`
  返回的包装对象后按裸 `StartedWorkerTask` 取 `.task`）。**但副作用已经全部发生**：
  - `agent_runtime.worker_execution_reservations`：1 行，`status=running`，`lease_owner=ec39f852745c:…`
    （api 容器主机名），`lease_expires_at=13:55:20Z`（5 分钟）。
  - `agent_runtime.runner_dispatches`：1 行，`status=queued`，`adapterId=claude-code`，
    `workspace.path=/runner-workspaces/w/503a658525d7c7b5861a/20b712cc4c33daf2811d`，`baseSha=882231dd`。
  - 宿主 `.repomesh-workspaces/repositories/dfb8a4cd….git` 镜像已克隆（head `882231d`），
    `w/503a…/20b7…` worktree 已检出（README/integration/scripts/src/tests 齐全，仅多一个 `.repomesh/`）。
  - `GET /bridge/plans/6f438ac3…`：该 worker 任务 `assigned → in_progress`。
- **状态**：**WORKAROUND**，且暴露 V-4。

### 卡点 V-4（缺陷）：`start_assigned_task` 成功启动却回 500

worker 侧收到的是错误而不是 run id，会把「已启动」当「失败」重试或放弃；平台侧却已持有预留与 dispatch。
修法：`worker_mcp.py` 按 `McpCallResult` 的真实形状取返回值（或让 `call_gated` 透传原对象）。

### 卡点 V-5（阻断，结构性）：产品部署里没有任何进程消费 runner dispatch

- **证据**：dispatch 入队后 6 分钟仍 `queued`、`lease_until` 为空；api 30 分钟访问日志里
  `GET /runtime/runner-tasks/next` / `POST /runtime/runner-events` **0 次**。
- **为什么**：`docs/architecture/runtime-planes.md`「Command Flow」第 5 步写明 *An AgentTeams-managed Worker starts
  RepoMesh Runner*，即 runner 应跑在 AgentTeams 的 Worker 里（runtime `repomesh-runner`）。但产品路径下：
  - 安装器写的 `AGENTTEAMS_DEFAULT_WORKER_RUNTIME=copaw`，物化出来的 8 个 Worker 全是 copaw 容器；
    容器里没有 `repomesh_runner`、没有任何 coding-agent CLI（`claude`/`codex`/… 全无），只有 copaw 自己的 agent。
  - `repomesh-runner` runtime 需要的镜像 `agentteams/agentteams-repomesh-worker` 仓库里没有 Dockerfile、
    公共仓库里也没有（见 08-15 记录），从零的机器上不存在。
  - api 容器自己也没有 CLI（`claude`/`codex`/`gh` 全 MISSING，只有 git/python），不能在进程内代跑。
  - compose 编排里没有 runner 服务；`.env.example` 也没有任何 runner 进程的启动说明。
- **结论**：即使 V-1 修掉，产品路径下 dispatch 也永远没人领。之前所有跑到提交/PR 的活体（08-07 plan-loop、
  09-01 W4）都靠宿主上手工起的 bridge/runner 进程（`repomesh_agent_bridge run --enrollment …`），
  不属于 README 的启动路径。
- **租约到期后的行为**：13:55:20Z 租约到期，13:56:24Z 预留仍 `running`、dispatch 仍 `queued`，
  api 日志无 recovery 动作——因为 `.env.example:52` 把 `REPOMESH_WORKER_RECOVERY_ENABLED` 写成 `false`，超期预留没人回收。

## 7. 结论：到最后提交链了吗

**没有。** 从零起的产品路径走到了「任务包投递 + 房间派工 + 镜像克隆 + worktree 检出」，停在 worker 启动 governed run
这一步。到达的最远点：

| 阶段 | 结果 |
|---|---|
| 组织 / 组织 Leader | PASS（action token 建） |
| 四仓接入 + 测试命令 | PASS |
| issue → analysis → candidates → classification → approval → plan | PASS（23 秒，真 LLM，分类正确） |
| 物化：目录 8 个 principal、8 个 copaw 容器、4 团队 8 房间、4 任务、3 批次 | PASS（44 秒，两次设计内 503） |
| 派工消息进房、任务包进 MinIO | PASS |
| copaw worker 调 `start_assigned_task` | **BLOCKED V-1**（401：控制器发的 token 平台不认；`.env.example` 关掉了 dev 直连） |
| 代 worker 调 `start_assigned_task` | 副作用成功、响应 500（**V-4**） |
| 有人执行 runner dispatch（克隆→改码→测试→冻结提交） | **BLOCKED V-5**（产品部署里没有 runner，copaw 容器不是 runner） |
| 候选分支 push / PR | 未到达；且默认 `REPOMESH_DELIVERY_AUTO_ENABLED=false`、无 GitHub 凭据，到了也不会外发 |

**测试资产仓**：只作为 MAYBE 档普通仓进了计划，被派了一个「改代码」任务；`cross-repo-test-team` 档案开关只在控制台 UI，本轮没翻。
**宿主残留**：`.repomesh-workspaces/` 是 8 月留下的（含旧镜像与 43 个旧 worktree），整拆时没清；本轮新建的镜像与 worktree 落在同一目录，
不影响结论，但严格的从零应该把它也清掉。

## 8. 问题清单（按严重度）

| # | 严重度 | 位置 | 现象 | 状态 |
|---|---|---|---|---|
| V-1 | **阻断** | `worker_mcp._authorize` + `.env.example:21,105` `REPOMESH_DIRECT_WORKER_MCP_ENABLED=false` | copaw worker 的 MCP 调用 401；dev 直连被 example 覆盖关掉 | 未修，代调绕过 |
| V-5 | **阻断（结构性）** | 产品编排 / `AGENTTEAMS_DEFAULT_WORKER_RUNTIME=copaw` / 无 runner 镜像 | runner dispatch 永远 `queued` | 未修 |
| V-4 | 高 | `src/repomesh/api/worker_mcp.py` tools/call 返回处理 | run 已启动却回 500 AttributeError | 未修 |
| V-2 | 中 | 运行时投影为组织 Leader 起 manager 容器 | 18888 端口与安装器的 manager 冲突，容器停在 Created | 未修 |
| V-3 | 中 | server 拆解模式 + copaw 队长 | 队长找不到任务包与仓库，绕圈三分钟无人拉回 | 观察 |
| V-6 | 低 | `.env.example:52` `REPOMESH_WORKER_RECOVERY_ENABLED=false` | 租约到期后预留永远 `running`、dispatch 永远 `queued`；recovery 在 example 里默认关 | 配置 |
| V-7 | 低 | `GET /issues/{id}/discovery` 形状 | 不再回传 plan 正文，旧驱动脚本读 `plan.task_dag` 得 null | 文档/脚本 |
| V-8 | 低 | 控制台 | 组织创建、测试团队档案开关都要管理员会话；用户以为建过组织实际没有 | 体验 |

原始房间消息摘录：`2026-09-02-issue-to-commit-chain.rooms.md`。发现链/物化/任务包的原始 JSON 在会话 scratchpad，未入库。
