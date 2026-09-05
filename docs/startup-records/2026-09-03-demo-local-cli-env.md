# 录 demo 用的本地 CLI（Bridge）隔离环境：从零起到三仓跑通（2026-09-03）

> 场景：在旧的托管原生波次 0 活体（compose `goai-infra-repomesh` 8000/5280/5432 + `agentteams-controller` + 8 个 copaw）**不停、不动**的前提下，
> 再起一套只服务于 demo 视频的环境，施工模式只用本地 CLI（Bridge，codex）。
> 被验对象：`main` 头 `c4a630f5`（本会话先把本地 main 从 `fd26f09f` 快进到这里）。
> 机器：Windows 11 + Docker Desktop 28 + Git Bash + PowerShell 5.1；操作者 AI，判定人用户。
> 时间：2026-09-03 02:05Z – 03:15Z（本机 09-02 19:05 – 20:15，UTC−7）；文内时间戳一律 UTC。
> 产物（全部 gitignored）：`output/demo-local-cli/`——`README.md`（环境与拆除）、`DEMO-RUNBOOK.md`（幕表）、`setup/`（脚本）、`logs/`（后端/前端/启动器/六桥原始日志）、`bootstrap/`（发现链与物化回执）。
> 术语按 `CONTEXT.md`；状态口径按本目录 `README.md`。

## 0. 结论先行

**环境建成并预热通过：一条三仓 issue 从发现链到 Leader（codex）拆解、worker（codex）施工、复验、Leader 审阅，三仓各得一个候选提交，7 个任务 succeeded；GitHub 三仓分支原样未动。**
路上撞到 11 个问题，5 个是项目缺陷（§4 P-1 … P-5），其中 P-1（桥的计划预检查）不修则本地 CLI 模式一个计划都交不出去，已在工作树打补丁、**未提交**。
建环境净耗时约 70 分钟，其中 45 分钟花在诊断 P-1 … P-4。

## 1. 起步前的实况与隔离方案

| 项 | 实况 |
|---|---|
| 已在跑（不碰） | compose 四件 `goai-infra-repomesh-{postgres,api,web,bootstrap}`、`agentteams-controller`（18080/18001/18088）、`agentteams-manager`、8 个 `agentteams-worker-agt-*`、`multica-*`、`coagenthub-smoke-pg` |
| 已占端口 | 5432 / 8000 / 5280 / 18080 / 3000 / 8080 |
| 隔离方案 | 新库 `repomesh-demo-pg`(15549) + 宿主 uvicorn 8077 + vite 5281 + Local Launcher 8121 + 工作区 `D:\Project4work\.repomesh-demo\workspaces`，即 R6 验收（08-30）的同款配方 |
| 唯一共用 | AgentTeams controller 的 Matrix（AgentTeams 装不了第二套；控制器 8090 未发布到宿主，用 socat 转发到 18090）。demo 只追加自己的 team / worker / Matrix 用户 / 房间 |
| 不走 compose 的原因 | `compose.yaml` 不透传 `REPOMESH_RUNNER_WORKER_TOKENS`，容器化 api 认不了 external 成员的 token；bootstrap 容器持 docker socket，两个 bootstrap 会争同一个 controller |
| 凭据复用 | 控制台 action token 复用旧栈 `.secrets/platform.env` 的值（`frontend/vite.config.ts` 无条件把该文件的 token 注入 `VITE_API_TOKEN`，第二实例只能同值）；服务端 Matrix 身份 `@admin` 的 token 复用 `.secrets/platform-runtime.env`（whoami 验证有效） |

## 2. 步骤记录

### 步骤 1 — 库与转发器

- **命令**：`docker run -d --name repomesh-demo-pg --label repomesh.demo=local-cli-20260903 -p 127.0.0.1:15549:5432 postgres:17-alpine`；
  `docker run -d --name repomesh-demo-controller-fwd --network agentteams-net -p 127.0.0.1:18090:8090 --entrypoint sh alpine/socat:latest -c "socat TCP-LISTEN:8090,fork,reuseaddr TCP:agentteams-controller:8090"`
- **预期**：两个容器 Up。
- **实际**：postgres 秒起；`alpine/socat` 本地没有，`docker pull` 走 Docker Desktop 里四个死掉的 registry mirror，2 分钟超时。改 `docker pull docker.m.daocloud.io/alpine/socat:latest` 10 秒到。转发器起来后 `curl -H "Authorization: Bearer <controller token>" http://127.0.0.1:18090/api/v1/workers` → 200。
- **状态**：WORKAROUND（换镜像源，环境问题 E-1）
- **耗时**：4 分钟

### 步骤 2 — 迁移到链尾

- **命令**：`REPOMESH_DATABASE_URL=postgresql+asyncpg://repomesh:repomesh@127.0.0.1:15549/repomesh .venv/Scripts/python.exe -m alembic upgrade head`
- **预期**：head `20260902_0053`。
- **实际**：第一次炸在 `get_settings()`：`github_app_id: Input should be a valid integer, input_value=''`——仓库根 `.env`（由 `.env.example` 生成）里 `REPOMESH_GITHUB_APP_ID=` 是空串，pydantic 不把空串当未设；compose 靠 `${REPOMESH_GITHUB_APP_ID:-0}` 掩住了这一点。显式 `REPOMESH_GITHUB_APP_ID=0` 后 5 秒到 head，69 张表。
- **状态**：WORKAROUND（P-3）
- **耗时**：2 分钟

### 步骤 3 — 凭据、管理员、后端

- **命令**：`setup/gen_secrets.py`（六个成员 token、runner control token、controller token 与 as_token 取自容器、写 `backend.env` / `members.env` / `launcher.json`）→ `setup/seed_admin.py`（`bootstrap_admin`，账密取 `admininfo.txt`）→ `setup/start_backend.ps1`（PowerShell `Start-Process` 起 `uvicorn repomesh.main:app --port 8077`，日志与 PID 落 `output/demo-local-cli/{logs,pids}`）
- **预期**：`/docs` 200；`setup/status` 五项必需检查 true。
- **实际**：2 秒 `/docs` 200；`checks: model/database/agentteams/matrix/internal_auth` 全 true。`admininfo.txt` 第二行键名是 `password` 不是 `pass`，解析器改了一次。
- **状态**：PASS
- **耗时**：3 分钟

### 步骤 4 — 组织、三仓、花名册

- **命令**：`setup/bootstrap_platform.py`：`POST /api/v1/console/organizations`（idempotency_key 派生 org id）→ 三次 `POST /repositories/scan-repo`（真扫 GitHub）→ 三次 `PATCH /repositories/{id}/verification`（`python scripts/run_tests.py`，`tests/**`）→ 写 `members.json`（6 成员，agent id 沿用 E1 的 `4d1e6f00-…-a1/a2/b1/b2/c1/c2`，资源名 `repo-<repo12>-leader / -worker-01`，`responsibilityPaths ["**"]`）
- **预期**：201 / 200 / 200。
- **实际**：第一次打 `/api/v1/organizations` 404，真实路径是 `/api/v1/console/organizations`。改后 201；三仓扫描各 3–5 秒，`test_commands` 落库。
- **状态**：PASS（路径一次纠错）
- **耗时**：3 分钟

### 步骤 5 — 前端与启动器

- **命令**：`setup/start_frontend.ps1`（`npm run dev -- --port 5281`，`REPOMESH_API_TARGET=http://127.0.0.1:8077`）；`setup/start_launcher.ps1`（`python -m repomesh_local_launcher launcher.json`，`allowedOrigins` 只放 5281）
- **预期**：5281 首页 200，`/api` 代理到 8077；8121 `/v1/status` 列出 6 个成员。
- **实际**：全部一次通过。
- **状态**：PASS
- **耗时**：1 分钟

### 步骤 6 — 种成员、provision

- **命令**：`scripts/bridge-e1/seed_members.py --members output/demo-local-cli/members.json`（先 `--dry-run`）→ `POST /auth/login` 取会话 → `scripts/bridge-e1/provision_members.py --stage provision`
- **预期**：6 个 AgentPrincipal 入库；controller 出现 6 个 `containerManaged:false` 的 worker。
- **实际**：如预期，controller 里 6 个 `repo-*` 资源 Pending，`matrixUserID` 立刻发布。
- **状态**：PASS
- **耗时**：1 分钟

### 步骤 7 — 预热 issue 的发现链（两次失败）

- **命令**：`setup/run_issue.py --run smoke1 --text "演示预热…" --no-materialize --all-required`
- **预期**：分析 → 候选 → 分类 → 审批 → 计划，全部 idle/done。
- **实际**：
  1. 中文需求经 Git Bash 命令行进 Python 后变成 `��ʾԤ��…`，这条 issue 的标题永久乱码。改成 `--text-file`（UTF-8 文件），乱码 issue 从 `plan_snapshots` / `audit_events` / `llm_usage` / `log_entries` / `idempotency_records` 手工删除。
  2. 重跑后分析与分类都 3 秒失败：`Request URL is missing an 'http://' or 'https://' protocol.`。根因同步骤 2：`.env` 里 `REPOMESH_DEEPSEEK_API_KEY / BASE_URL / MODEL` 三项是空串，而 `bootstrap/app.py` 用的是 `settings.deepseek_*`，不是 `REPOMESH_MODEL_*`；compose 用 `${REPOMESH_DEEPSEEK_BASE_URL:-${REPOMESH_MODEL_BASE_URL:-…}}` 兜底，宿主进程没有。把三项按 `REPOMESH_MODEL_*` 显式写进 `backend.env` 后：分析 3 秒、候选 3 秒（LLM 打分 1.0 带理由）、分类 3 秒（三仓 required）、计划 6 秒。
- **状态**：WORKAROUND（T-1 工具问题 + P-3）
- **耗时**：8 分钟

### 步骤 8 — 首次物化：503 → 409（预期中的两段）

- **命令**：`setup/run_issue.py --issue <id> --materialize-only`
- **预期**：团队不存在 → 建团 → 房间未就绪 503 → 稍后 409 `external_members_not_ready`。
- **实际**：02:24:25 503「the execution plane has no rooms for this project's teams … materialize again once AgentTeams answers」；controller 里三支 `repomesh-team-<repo32>` 从 Pending 5 秒转 Active；02:25:18 再打 → 409，六个成员 `offline / no readiness report`。
- **状态**：PASS
- **耗时**：1 分钟

### 步骤 9 — binding、Matrix token、enrollment

- **命令**：`provision_members.py --stage binding`（成员自己的 token）→ `scripts/bridge-e1/fetch_matrix_tokens.py`（appservice 登录）→ `make_enrollments.py --subset demo`
- **预期**：六份 binding 各两个房间；六个 Matrix token；六份 enrollment。
- **实际**：binding 与 enrollment 如预期。**appservice 登录 401 `M_UNKNOWN_TOKEN`**——这套 controller `AGENTTEAMS_MATRIX_APPSERVICE_ENABLED=0`，`AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN` 只是个没注册的值，homeserver 是 tuwunel。控制器给每个 worker 资源（external 也算）在容器 `/data/worker-creds/<resourceName>.env` 里铸了 `WORKER_MATRIX_TOKEN`，直接取用（`setup/fetch_worker_matrix_tokens.py`），六个 token whoami 全对。
- **状态**：WORKAROUND（P-5）
- **耗时**：6 分钟

### 步骤 10 — 起桥、物化 200

- **命令**：`POST http://127.0.0.1:8121/v1/members/start`（与控制台按钮同一条路由）→ 轮询 `GET /issues/{id}/discovery/readiness` → 物化
- **预期**：六桥 ready；物化 200。
- **实际**：六个进程秒起，日志各一行 `bridge ready: … profile=codex rooms=2`（leader `governed=off leader-lane=on`，worker 反之）；**5 秒 6/6 ready**；02:27:09 物化 200，`team_count 3`，三个 Leader 任务 assigned。
- **状态**：PASS
- **耗时**：1 分钟

### 步骤 11 — Leader 拆解：桥 30 秒超时，服务端 500（P-4）

- **预期**：三个 Leader 收到「plans leader-side」通知 → codex 出 Spec/DAG/worker 任务 → `POST …/plan` 200 → worker 子任务派发。
- **实际**：三个 Leader 都收到通知并 POST 了计划；任务数 3 → 6（子任务建了），但桥侧 `POST …/plan failed with ReadTimeout`（`leader_actions.DEFAULT_TIMEOUT_SECONDS = 30`），三个 Leader 在房间里写「I could not reach RepoMesh … Ask me again」。后端 `Exception in ASGI application`：`TaskPublicationUnavailable: HTTPConnectionPool(host='agentteams-controller', port=9000)`——用上了 MinIO 对象发布器，宿主解析不了容器名。
  根因：`modules/platform_config/runtime_config.py::load_runtime_environment` 会把 `.secrets/platform-runtime.env`（默认路径、相对 CWD，即**旧栈**的运行时文件）里的九个 AgentTeams 键填进任何**值为空**的环境变量；我为了选磁盘发布器把 `REPOMESH_AGENTTEAMS_STORAGE_*` 设成空串，正好被旧栈的 MinIO 端点与账密覆盖。
  修法：`REPOMESH_RUNTIME_CONFIG_FILE` 指到 demo 自己的 `secrets/platform-runtime.env`（只含 controller / Matrix 五键），`REPOMESH_CREDENTIALS_ENCRYPTION_KEY_FILE` 指到 demo 自己的密钥；重启后端。
  处置：这一轮的房间里已留下失败记录，选择**换新房间**：`docker rm -f` 重建库、迁移、重种管理员，组织与组织 Leader 两行从 `pg_dump` 快照原样恢复（id 不变），三仓重扫（新 id → 新团队名 → 新房间）。想顺手删掉第一轮在 controller 建的 3 支团队和 6 个 worker：`agt delete team` 打印 deleted、REST `DELETE /api/v1/teams/<t>` 204，但列表里团队仍 Active，worker 删除 409「is a member of team …」——留作孤儿（Pending、无容器）。
- **状态**：BLOCKED → WORKAROUND（P-4；重建 8 分钟）
- **耗时**：15 分钟

### 步骤 12 — 第二轮引导（脚本化）

- **命令**：`bash output/demo-local-cli/setup/bootstrap_round.sh smoke3 bootstrap/smoke-issue.txt`（发现链 → 物化循环 503→409 → binding → worker-creds token → enrollment → 启动器起桥 → readiness → 物化）
- **预期**：一路到物化 200。
- **实际**：02:36:52 分类完成，02:36:58 计划 done（3 节点 2 批：`[pricing-core]`, `[billing, checkout]`），02:36:59 503，02:37:09 409，桥起后 5 秒 6/6，**02:37:25 物化 200**。全程 35 秒。
- **状态**：PASS
- **耗时**：1 分钟

### 步骤 13 — Leader 计划被桥自己拦下（P-1）

- **预期**：pricing Leader 提交计划。
- **实际**：codex 20 秒出了计划，桥在本地预检查抛 `LeaderDocumentInvalid: worker task 'document-local-verification-and-rounding' allows 'README.md', which is outside the safety envelope roots **, tests/**`，房间里写「I would not submit the decision my own session produced」。
  根因：`src/repomesh_agent_bridge/contracts.py::RepositoryAssignmentPackage.refuse_plan` 用 `allowed.startswith(roots)` 拿**原始 glob 串**做前缀比较；服务端 `task_orchestration/application.py::_within_roots` 会先剥掉根末尾的 `**`/`*`（`**` → 空串 → 全部放行）。所以 `**` 之下任何具体路径都被桥拒，除非 codex 把根原样抄成 allowedPaths（R6 那次就是这么碰巧过的）。`git log -S` 定位到桥的首个提交 `52577d76`。
  修法：在 contracts.py 加与服务端同规则的 `_within_roots`，`refuse_plan` 改调它（+20/−1 行，**未提交**）。ruff 干净；`tests/agent_bridge/test_leader_lane.py` 68 过 5 败，把补丁 stash 掉再跑同样 5 败，是 main 上既有的红。六桥停/起以加载新代码。
- **状态**：WORKAROUND（P-1）
- **耗时**：10 分钟

### 步骤 14 — 唤醒 Leader：re-dispatch 不管用（P-2）

- **命令**：`POST /api/v1/deliveries/f1857407-…/redispatch {"idempotency_key":"smoke3-redispatch-1","scope":"unfinished"}`
- **预期**：Leader 重新收到派单，规划通道再跑一次。
- **实际**：接口 200，`task_ids` 含 Leader 任务，但它**只重发了 `task_assignment`（任务说明）**，没有重发 `decision` 那条「plans leader-side… POST …/plan」通知；桥的 `parse_leader_notice` 认的是通知里的路由末段 + 任务 id，于是 Leader 桥把重发的说明当普通对话，回了一条 `agent-report status=blocked`，规划通道没醒。
  绕法：`setup/resend_leader_notice.py <leader_task_id> <leader DM 房间>`——从时间线表取原通知的 wire body，以 `@admin` 身份、新 transaction id、带 `m.mentions` 再 PUT 一次。20 秒后 `POST …/plan → 200`，worker 子任务派发。
- **状态**：WORKAROUND（P-2）
- **耗时**：6 分钟

### 步骤 15 — worker 秒败：工作树打不上 Low 标签（P-6，已知）

- **预期**：worker 桥租到任务 → codex 在后端准备好的工作树里施工。
- **实际**：worker 桥 `start-worker-task 202` → `runner-tasks/next 200` → 5 秒后 `WorkspaceNotWritable: the prepared worktree could not be labelled Low integrity … grant this machine's user full control of the runner workspace root`。Leader 收到失败回执立刻返工重派，30 秒一轮，**5 次 failed** 后我停了六桥。
  根因：`runner_consumer._label_workspace` 的注释里 08-28 就写过——D 盘、用户 profile 之外的目录只有继承来的 `Modify`，没有 `WRITE_OWNER`，写不了强制完整性标签；E1 的根 `D:\Project4work\.repomesh-e1\workspaces` 上有显式的 `18092:(OI)(CI)F`。手工 `icacls … /setintegritylevel` 复现「拒绝访问」。
  修法：`icacls D:\Project4work\.repomesh-demo\workspaces /grant 18092:(OI)(CI)F /T`（246 个文件），再对一个工作树打 Low 标签成功。重起六桥，重发通知。
- **状态**：WORKAROUND（P-6）
- **耗时**：8 分钟

### 步骤 16 — 三仓跑通

- **预期**：pricing 批次 → 成功 → billing/checkout 批次自动派发 → 全部 succeeded。
- **实际**（UTC）：
  - 02:56 重发通知；Leader 20 秒出计划；worker 02:57 领活，codex `permission allow: commandExecution`，**03:01 finished with status succeeded**（`README.md`、`src/pricing_core/quote.py`、`tests/test_quote.py`，commit `0f95a99`，6 个单测通过）；Leader 审阅 ACCEPT，pricing Leader 任务 succeeded；批次 2 自动派发。
  - billing：Leader 03:02 计划，worker 03:05 succeeded（`README.md`，commit `eabca7c`，7 个单测通过）。
  - checkout：worker 03:05 第一次 succeeded（`README.md`、`src/checkout/order.py`，`c7b9dba`），Leader 审阅要求修订，第二次 03:08 succeeded（另加 `tests/test_readme.py`，`0e1e182`），Leader 任务 succeeded。
  - 交付读模型：phase `validate`「等待交付证据」；`delivery.change_sets` 0 行（delivery 关闭，不推 GitHub）；`gh api …/branches` 三仓分支与开工前完全一致。
  - 观测：`observe/summary` 6 次 LLM 调用全成功；`observe/alerts` 无事件；`POST /issues` 入口 422 而非 503（`pause_intake` 已配但未被触发）。
- **状态**：PASS
- **耗时**：15 分钟（其中 codex 施工 pricing 4 分、billing 3 分、checkout 两次共 6 分）

### 步骤 17 — 交付给录制

- Chrome（claude-in-chrome）打开 `http://127.0.0.1:5281/`，窗口 1440×900，控制台默认深色；Chrome 自动填了 R6 时代的用户名 `e1admin`，录制前要改。控制台读模型 `issues / console/repositories / console/teams / console/agents / console/organizations / observe/* / deliveries / setup/*` 全 200；团队页三支团队 `ready`，智能体页 7 个（组织 Manager + 6 external，`kind: external, reachable: true`）。
- **状态**：PASS

## 3. 终态账面（03:15Z）

| 对象 | 数量 |
|---|---|
| 任务 | succeeded 7（3 Leader + 4 worker）、failed 6（P-6 修前的返工痕迹） |
| 尝试 / 调度 | attempts active 6 + completed 4；runner_dispatches completed 4 + failed 6 |
| 消息 | task_assignment 14、decision 13、task_report 3，全部 delivered |
| 房间时间线 | 86 行；拓扑 1、团队 3 |
| 候选提交 | pricing `0f95a99`、billing `eabca7c`、checkout `c7b9dba` → `0e1e182`（都在本地 mirror 与工作树，未推） |
| LLM 调用 | 6（deepseek-chat，全成功，4539 tokens） |
| 容器 | `repomesh-demo-pg`、`repomesh-demo-controller-fwd`（label `repomesh.demo=local-cli-20260903`） |
| 进程 | uvicorn 8077、vite 5281、launcher 8121、六个 Bridge（PID 在 `output/demo-local-cli/pids/`） |
| controller 孤儿 | 第一轮的 `repo-{cec32d0b82ea,07d15f89cf7a,67ff485ef897}-{leader,worker-01}` 与三支团队，Pending 无容器 |
| 旧栈 | 未动：14 个容器状态与开工前一致 |

## 4. 问题清单（按严重度）

**P-1（Important，项目缺陷，已打补丁未提交）** `src/repomesh_agent_bridge/contracts.py` `refuse_plan` 用 `str.startswith(roots)` 对 glob 根做前缀匹配，与服务端 `_within_roots` 语义不一致；`responsibilityPaths ["**"]`（E1/R6/本次花名册的默认写法）下任何具体路径都被拒，本地 CLI 模式的 Leader 交不出计划。补丁：同规则的 `_within_roots`。建议连同一条单测一起提交。

**P-2（Important，产品缺口）** `POST /deliveries/{round}/redispatch` 对 Leader 模式的父任务只重发 `task_assignment`，不重发 `decision` 规划通知；桥的规划通道只认通知，所以「重新派发」唤不醒卡住的 Leader，操作者在产品里没有任何手段，只能手工发 Matrix 消息（`setup/resend_leader_notice.py`）。落点：`task_orchestration/application.py::RedispatchRound` 与 `_notify` 系列。

**P-3（Important，配置缺陷）** `.env.example` 把 `REPOMESH_GITHUB_APP_ID`、`REPOMESH_DEEPSEEK_{API_KEY,BASE_URL,MODEL}`、`REPOMESH_EMBEDDING_*` 等写成空串；compose 靠 `${VAR:-default}` 掩住，宿主进程（`dev-up`、R6 配方、本次）直接吃亏：空串让 `github_app_id: int | None` 解析炸、把 `deepseek_*` 的默认值清空（发现链全失败，报文只说 URL 缺协议）。修法二选一：`.env.example` 删掉空值行，或 Settings 里给这些字段加「空串视为未设」的 validator。

**P-4（Important，隔离缺陷）** `platform_config/runtime_config.py::load_runtime_environment` 用相对 CWD 的 `.secrets/platform-runtime.env` 填任何空值环境变量，同一个 checkout 起第二个实例会**静默继承另一个栈的 controller / Matrix / MinIO 配置**（本次表现为 dispatch 时 MinIO 500 与桥 30 秒超时）。逃生口 `REPOMESH_RUNTIME_CONFIG_FILE` 没有文档。同类：`crypto.py` 的 `.secrets/platform-credentials.key`、`vite.config.ts` 注入 `.secrets/platform.env` 的 token。建议把「第二实例」写进 `docs/clean-startup-guide` 并让空串不被回填。

**P-5（Normal，脚本假设）** `scripts/bridge-e1/fetch_matrix_tokens.py` 与 `scripts/bridge-e1/README.md` 步骤 6 假定 appservice 开启；本次安装 `AGENTTEAMS_MATRIX_APPSERVICE_ENABLED=0`（`.env.example` 默认），appservice 登录 401。controller 其实已为 external 成员铸好 token，在 `/data/worker-creds/<resourceName>.env`。README 该补这条路径。

**P-6（Normal，已知未自动化）** 工作区根在用户 profile 之外时桥打不上 Low 标签，worker 必败；`runner_consumer._label_workspace` 注释 08-28 就记了，报错信息也给了 icacls 命令，但 Launcher / `start-local-cli.ps1` 都不预检。建议启动器起 worker 前对 `WorkspaceRoot` 做一次 `setintegritylevel` 探针。

**P-7（Minor，controller）** 团队建成后 external worker 删不掉：`DELETE /api/v1/teams/<t>` 204 但团队仍 Active，`agt delete worker` 409。建团失误只能留孤儿。

**P-8（Minor，桥超时）** `leader_actions.DEFAULT_TIMEOUT_SECONDS = 30` 与服务端 `/plan` 的耗时（建子任务 + 克隆镜像 + 发布任务包）贴得很近；磁盘发布器下实测约 20 秒，对象发布器故障时直接超时且不重试，留下「子任务已建、无人派发」的半成状态。

**P-9（Minor，既有）** `tests/agent_bridge/test_leader_lane.py` 在 main 上 5 个失败（`test_a_refused_read_arrives_as_a_typed_exception…`、`test_a_forged_leader_task_is_refused…`），与本次补丁无关。

**T-1（工具，不算项目缺陷）** Git Bash 把含中文的命令行参数以本地代码页交给 Python，需求文本进库即乱码；一律用 `--text-file`（UTF-8）。另观察到 checkout 的 codex 把 `本地验证` 写成 `鏈湴楠岃瘉` 进了 README（worker 侧 CLI 的编码链），pricing/billing 正常——demo 需求里避免要求往文件里写中文。

**E-1（环境）** Docker Desktop 的四个 registry mirror 仍全死，拉新镜像必超时；`docker.m.daocloud.io` 可用。

## 5. 复现与拆除

从零重建：按 §2 顺序跑 `output/demo-local-cli/setup/` 里的脚本（`gen_secrets.py` 已把 P-3/P-4 的覆盖写进 `backend.env`；`bootstrap_round.sh` 把步骤 8–10 串成一条）。ACL 授权与桥补丁不在脚本里，要先做。
拆除顺序与命令见 `output/demo-local-cli/README.md`：停桥 → 杀三个 PID → `docker rm -f` 两个容器 → （可选）清 controller 里的 demo 团队与 worker。旧栈始终不在拆除范围内。
