# E1 六实例编排（Room-Native Bridge 环境轨）

终局验收要六个 `containerManaged:false` 的 external 成员——3 个 Repository Leader
+ 3 个 Worker——各自一个独立 Bridge 进程跑在 Windows 宿主上。这个目录是**开通、
启动、对账、拆除**这六个身份的全套脚本与操作顺序。

先做子集：`-Subset m7`（一 leader + 一 worker）就是 M7 smoke 要的最小编制，
六实例 soak 再上全量。

> **本目录不含任何产品代码。** 脚本只调既有 HTTP 路由与既有 Python 入口；
> 路径推导、铸名公式、契约校验一律取自产品代码本身（下文每处都标了出处）。

---

## 0. 名词与文件

| 文件 | 作用 |
|---|---|
| `members.example.json` | 六成员配置样例（tracked） |
| `members.json` | 真实配置（**gitignored**，本目录 `.gitignore` 已收） |
| `e1_config.py` | 花名册读取 + 唯一一处路径推导；PowerShell 通过它问 codex-home |
| `seed_members.py` | 建/核对六个 `AgentPrincipal`（唯一写 RepoMesh 库的脚本） |
| `provision_members.py` | runtime v2 `PUT provision` + `GET binding`（唯一改控制面的脚本） |
| `fetch_matrix_tokens.py` | appservice login 逐成员取 Matrix token → gitignored env 文件 |
| `make_enrollments.py` | 由 binding 应答生成六份 enrollment v2 |
| `copy_codex_auth.ps1` | D-10：把已登录的 `auth.json` 复制进六个 codex-home |
| `start_members.ps1` / `stop_members.ps1` | 启停（PID 文件收尾） |
| `../start-local-cli.ps1` | 面向操作者的一键启动入口；统一 Worker workspace root 后复用 `start_members.ps1` |
| `preflight_bindings.py` | **R8 只读对账**，任一不符 exit 非零 |

产出目录（binding / enrollment / pid / log）与 env 文件**必须 gitignored**：
建议 `output/bridge-team/e1/`（仓库根 `.gitignore` 已含 `output/`），
或本目录下的 `out/`（本目录 `.gitignore` 已含）。

完成下文步骤 1～8 后，日常启动不必再拼长命令：

```powershell
powershell -NoProfile -File .\scripts\start-local-cli.ps1
```

这个入口只拉起现有 external member 进程，不 provision、不建 Team、不写数据库。它优先使用
`REPOMESH_RUNNER_WORKSPACE_ROOT`；未设置时使用仓库同级的 `.repomesh-e1\workspaces`，并把
同一根传给全部 Worker。可用 `-WorkspaceRoot <控制面根>` 显式覆盖，或先加 `-DryRun` 查看
将要执行的命令。Leader 永远不会收到 `--workspace-root`。

---

## 1. 操作顺序

### 步骤 0 —— 环境（照 PR 4 交接 §7.5，本轮不重复）

1. controller forwarder：`docker run ... socat TCP-LISTEN:8090 → agentteams-controller:8090`，
   发布到 `127.0.0.1:18090`。
2. 一次性 postgres：`127.0.0.1:15547`，然后 `alembic upgrade head`
   （**M8 硬前置**：0039 未应用则 timeline ingest 全 500）。
3. 后端 uvicorn `127.0.0.1:8077`，环境变量见 §2。
4. admin：`local_account_service().bootstrap_admin(...)`（仅账户表为空时可用），
   再 `POST /api/v1/auth/login` 拿 `access_token`。
5. 组织 leader（`AgentRole.ORGANIZATION_LEADER`）必须已在库里且 ACTIVE
   —— `seed_members.py` 会先核它，不存在就停工。

### 步骤 1 —— 签发六个成员 token（人工，一次）

`credentialRefs.repomesh` 装的是**该成员自己的 external member token**（裁决 D-6），
不是全局 runner token。签发即写两处，**同一批值**：

* 后端进程环境：`REPOMESH_RUNNER_WORKER_TOKENS`，形如
  `{"<agentId>":"<token>", ...}` 六条；
* Bridge 端 env 文件：`E1_<KEY>_REPOMESH_TOKEN=<token>`，
  `<KEY>` = 花名册 `key` 转大写、`-` 换 `_`（例：`alpha-leader` → `E1_ALPHA_LEADER_REPOMESH_TOKEN`）。

```powershell
# 六个随机 token 写进 gitignored env 文件；同时按 agentId 拼后端要的 JSON
$env:E1_OUT = "D:\Project4work\GOAI-infra-repomesh\output\bridge-team\e1-members.env"
# （值不要回显到终端；用编辑器或脚本直接落盘）
```

### 步骤 2 —— seed 六个 AgentPrincipal

```bash
MSYS_NO_PATHCONV=1 \
REPOMESH_DATABASE_URL="postgresql+asyncpg://postgres:e2e@127.0.0.1:15547/postgres" \
  .venv/Scripts/python.exe scripts/bridge-e1/seed_members.py \
    --members scripts/bridge-e1/members.json --dry-run
# 看清楚要建哪几个，再去掉 --dry-run
```

幂等：已在库的成员逐字段核对后跳过；**与花名册不一致就报错停工，不修不覆盖**。
Leader 先插、Worker 后插（`leader_agent_id` 是自引用外键）。

### 步骤 3 —— provision external members（PUT）

```bash
E1_REPOMESH_ADMIN_TOKEN=... \
  .venv/Scripts/python.exe scripts/bridge-e1/provision_members.py \
    --members scripts/bridge-e1/members.json --out output/bridge-team/e1 \
    --stage provision
```

断言 `containerManaged:false` 与 `role` 与 `workerName`；PUT 无请求体（角色由
RepoMesh 目录决定，调用方说了不算）。

### 步骤 4 —— 让 Team 存在

`GET binding` 会拒绝「不属于任何 Team」的成员（`ResolveExternalMemberBinding`），
而 Team 必须在六个 worker 资源都存在之后才建得起来。所以这一步夹在中间。

**Team 只由 materialize 的正式拓扑 reconcile 建立/采用（`ReconcileProjectAgentTopology`），
不走 `agt create team` 旁路**（主脑裁决 W-E1-D2）：验收标准 §5 禁止脚本代替产品链业务步骤，
建团正是产品链的一步；且 reconcile 对既有 Team 做逐字段奇偶性核对（A-8 adopt），手工建的
Team 与投影稍有出入就是 409，错误会记在操作者头上。由此 R8 预检分两段读：materialize
**前**只有 controller 半边可核（资源存在 / 名字逐字相符 / containerManaged:false / skills），
binding 半边预期「不属于 Team」失败属知情；materialize **后**再跑一次，六行全绿才算 R8 过。
Team 名与 room 号见 §4。

### 步骤 5 —— 取 binding（GET）

```bash
E1_ALPHA_LEADER_REPOMESH_TOKEN=... E1_ALPHA_WORKER_REPOMESH_TOKEN=... ... \
  .venv/Scripts/python.exe scripts/bridge-e1/provision_members.py \
    --members scripts/bridge-e1/members.json --out output/bridge-team/e1 \
    --stage binding
```

用**成员自己的 token** 读（D-6），顺带验一遍 `REPOMESH_RUNNER_WORKER_TOKENS`
条目没配错。应答原样落盘 `binding.<key>.json`。

### 步骤 6 —— 取六个 Matrix token

```bash
E1_CONTROLLER_TOKEN=... E1_APPSERVICE_TOKEN=... \
  .venv/Scripts/python.exe scripts/bridge-e1/fetch_matrix_tokens.py \
    --members scripts/bridge-e1/members.json \
    --out output/bridge-team/e1-members.env
```

external 成员没有容器，取不到容器 env，只能用 appservice
（`m.login.application_service`）登录（PR 4 §7.5 第 6 步）。
身份取自 controller worker 文档的 `matrixUserID`，不靠资源名猜。
**token 只写 `--out`，不进 stdout、不进日志**；文件里已有的其它行（比如步骤 1
写的 `*_REPOMESH_TOKEN`）会被保留。

### 步骤 7 —— 生成六份 enrollment

```bash
.venv/Scripts/python.exe scripts/bridge-e1/make_enrollments.py \
  --members scripts/bridge-e1/members.json \
  --bindings output/bridge-team/e1 --out output/bridge-team/e1/enrollments
```

`allowedRoomIds` **以 binding 应答为权威**，一个字都不手填：leader 的 DM 房必须
同时出现在 enrollment 与 binding 里，否则 stage 2 拒绝启动（wave-2 交接 §5 M7-4）。
`credentialRefs` 一律 `env:` locator（Bridge 的解析器只认这一种）。

### 步骤 8 —— 复制 codex auth.json（D-10）

```powershell
scripts\bridge-e1\copy_codex_auth.ps1 -Members scripts\bridge-e1\members.json `
  -SourceCodexHome "$env:LOCALAPPDATA\repomesh-agent-bridge\sessions\<已登录的 agentId>\codex-home"
```

先没有已登录目录的话，先做一次：

```powershell
$env:CODEX_HOME="<某个成员的 codex-home>"; New-Item -Path $env:CODEX_HOME -ItemType Directory -Force | Out-Null; codex login
```

**必须在第一次 `start_members.ps1` 之前跑**：`prepare_session_dirs` 会在首次
`ensure_ready` 时给 codex-home 整棵树打 Low 完整性标签，先放进去的文件跟着被
重贴；事后再塞进去的文件仍是 Medium，受限子进程改不动它。
只复制 `auth.json`，不碰 `config.toml`（那份有 Bridge 的 managed block）。

### 步骤 9 —— 启动

```powershell
scripts\bridge-e1\start_members.ps1 -Members scripts\bridge-e1\members.json `
  -EnrollmentDir output\bridge-team\e1\enrollments `
  -EnvFile output\bridge-team\e1-members.env `
  -PidDir output\bridge-team\e1\pids -LogDir output\bridge-team\e1\logs `
  -WorkspaceRoot D:\Project4work\.repomesh-e1\workspaces `
  -Subset m7 -DryRun      # 先看命令行，再去掉 -DryRun
```

* Worker 带 `--workspace-root`，**Leader 不带**——Bridge 对 leader 直接拒绝该参数
  （`cli._governed_workspace_root`，AC-02），花名册也在另一头拦同一条。
* 每成员一个 PID 文件 `<PidDir>\<key>.pid`。
* 同一成员已有活进程时报错退出（实例锁是按 worker 身份独占的，硬起第二个会 exit 3）。

### 步骤 10 —— R8 只读预检

```bash
E1_CONTROLLER_TOKEN=... E1_<KEY>_REPOMESH_TOKEN=... \
  .venv/Scripts/python.exe scripts/bridge-e1/preflight_bindings.py \
    --members scripts/bridge-e1/members.json
```

逐成员两次 GET（RepoMesh binding + controller worker），打对账表；任一不符 exit 1。
详见 §4。

### 步骤 11 —— 拆除

```powershell
scripts\bridge-e1\stop_members.ps1 -Members scripts\bridge-e1\members.json `
  -PidDir output\bridge-team\e1\pids
# 丢了 PID 文件时的兜底：
scripts\bridge-e1\stop_members.ps1 -Members ... -PidDir ... -Sweep
```

杀之前会用 `Get-CimInstance Win32_Process` 核对该 PID 的 CommandLine 确实是
Bridge（PID 会被系统复用，陈旧 PID 文件可能指向别的进程）。
`-Sweep` 用同一手段找出 PID 文件不认领的漏网进程。

⚠️ **Git Bash 下 `pkill -f` 杀不掉这些进程**，也杀不掉 `nohup env ... python` 链；
Windows 上唯一可靠的是 PID + `Stop-Process -Force`，或按 CommandLine 匹配。

最后：`docker rm -f repomesh-e2e-pg repomesh-controller-forwarder`。

---

## 2. 环境变量清单

**这里只写变量名与来源；真实值在 gitignored 的 `output/bridge-team/e0a-live-env.md`
与步骤 6 写的 env 文件里，任何 token 都不得进入 tracked 文件。**

### 后端进程（uvicorn @8077）

| 变量 | 来源 |
|---|---|
| `REPOMESH_DATABASE_URL` | 一次性 postgres @15547 |
| `REPOMESH_AGENTTEAMS_CONTROLLER_URL` | `http://127.0.0.1:18090`（socat forwarder） |
| `REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN` | `docker exec agentteams-controller sh -c 'cat "$AGENTTEAMS_AUTH_TOKEN_FILE"'`（`.env` 里那份**已失效**） |
| `REPOMESH_RUNNER_CONTROL_TOKEN` | 自定；managed Runner 的全局 token，**Bridge 不持有** |
| `REPOMESH_RUNNER_WORKER_TOKENS` | 步骤 1 签发的六条 `{"<agentId>":"<token>"}` |
| `REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN` | M8 需要；缺了 matrix_client 为 None，poller 与 recorder 都不组装 |

### 本目录脚本

| 变量 | 用在哪 | 来源 |
|---|---|---|
| `REPOMESH_DATABASE_URL` | `seed_members.py` | 同上 |
| `E1_REPOMESH_ADMIN_TOKEN` | `provision_members.py --stage provision` | `POST /api/v1/auth/login` 的 `access_token` |
| `E1_CONTROLLER_TOKEN` | `fetch_matrix_tokens.py`、`preflight_bindings.py` | 同 `REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN` |
| `E1_APPSERVICE_TOKEN` | `fetch_matrix_tokens.py` | 容器 env `AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN` |
| `E1_<KEY>_REPOMESH_TOKEN` | binding GET、Bridge 运行时 | 步骤 1 人工签发 |
| `E1_<KEY>_MATRIX_TOKEN` | Bridge 运行时 | 步骤 6 脚本写入 |

`<KEY>` 推导：花名册 `key` 转大写、`-` 换 `_`。六个成员共十二个变量，
`start_members.ps1 -EnvFile` 会把它们读进当前会话再拉起子进程。

### 端口

| 端口 | 服务 |
|---|---|
| 18080 | Matrix client-server API（conduit 内置在 controller，**这就是 `matrixHomeserverUrl`**） |
| 18090 | socat forwarder → controller 8090（用完删） |
| 8077 | E2E 的 RepoMesh 后端 |
| 15547 | 一次性 postgres |

`5432 / 55432 / 8080 / 3000 / 5280 / 8100` 是他线，**不碰**。

---

## 3. 三条环境坑

1. **`MSYS_NO_PATHCONV=1`** —— Git Bash 会把 `/api/v1/...`、`/_matrix/...`
   这类以斜杠开头的参数改写成 Windows 路径。所有带 URL 路径或容器内路径的命令
   前面都要带上它。
2. **控制面与 Bridge 同跑 Windows 宿主** —— controller 的 8090 没发布到宿主，
   必须自建 socat forwarder；Bridge 是本机进程，不是容器，所以它看到的
   `repomeshEndpoint`/`matrixHomeserverUrl` 都得是 `127.0.0.1:<发布端口>`。
   另：PowerShell 不支持 bash 的 `VAR=value cmd` 前置写法（`CommandNotFoundException`）。
3. **5432 的活体库不碰** —— 它的迁移谱系与本分支不符，**绝不对它跑本分支迁移**。
   E1 一律用一次性 postgres @15547。

---

## 4. R8：铸名规则与只读预检

### 4.1 权威在哪

**materialize 不铸名。** `ProjectRuntimeProjection._register` 读
`principal.agentteams_resource_name`，拿这个字符串去 controller
`GET /api/v1/workers/{name}`：查到就校验（kind 与 name），查不到才按这个名字创建。
所以规则只有一条：

> 预建的 AgentTeams 资源名，必须**逐字等于**该 principal 在 RepoMesh 目录里的
> `agentteams_resource_name`。

对不上不是「换个名字」，是**多建一个资源**；而名字对上、投影别处不一致
（skills / model / runtime / containerManaged）则是 controller 的
`409 ... differs in: <字段>`，没有重试能清掉。

### 4.2 推导公式（三套既有铸法，按创建路径不同）

| 路径 | Leader | Worker | 出处 |
|---|---|---|---|
| 控制台建团 `POST .../repository-team` | `repo-{repositoryId.hex[:12]}-leader` | `repo-{repositoryId.hex[:12]}-worker-{NN:02d}` | `api/human_control.py` |
| materialize 自动补齐 `ProvisionRepositoryAgentTeam` | `agt-leader-{repositoryId.hex[:12]}` | `agt-worker-{repositoryId.hex[:12]}` | `agent_directory/application/repository_team.py` |
| 通用 `agentteams_resource_name(kind, id)` | `repomesh-manager-{uuid.hex}` | `repomesh-worker-{uuid.hex}` | `agent_runtime/ports/agent_team.py` |

Team 名只有一套，**键在 repository 而不是 topology 行**（修正 A-8）：

```
RepositoryTeam.canonical_agentteams_team_name(repository_id)
  = f"{AGENTTEAMS_NAME_PREFIX}-team-{repository_id.hex}"     # AGENTTEAMS_NAME_PREFIX = "repomesh"
```

例（`members.example.json` 的 alpha 仓）：

```
repositoryId = 1a1f0c37-9d40-4c11-9f01-0000000000a1
  hex        = 1a1f0c379d404c119f010000000000a1
  hex[:12]   = 1a1f0c379d40
Leader 名（控制台铸法） = repo-1a1f0c379d40-leader
Worker 名（控制台铸法） = repo-1a1f0c379d40-worker-01
Team  名（唯一铸法）    = repomesh-team-1a1f0c379d404c119f010000000000a1
```

两条要知道的：

* 前缀刻意是 `repomesh` 而不是 `rm`：worker runtime 的危险命令规则用
  `\brm\b` 匹配，`rm-worker-a-api` 里的 `rm` 是独立词，会把 agent 自己的
  `ls` 卡在等人 `/approve` 的审批里（`AGENTTEAMS_NAME_PREFIX` 的 docstring）。
* materialize 自动补齐那一路，**只在该 repository 没有 ACTIVE
  `REPOSITORY_LEADER` 时触发**。E1 先 seed 了 leader，所以走的是收敛分支，
  沿用我们给的名字；`members.example.json` 因此用控制台那套铸法。
* 名字还要过 controller 自己的正则
  `^[a-z0-9](?:[-a-z0-9]{0,251}[a-z0-9])?$`，且**全平台唯一**；
  `e1_config.py` 加载花名册时就按这条拦。

### 4.3 `preflight_bindings.py` 核什么

逐成员两次 GET（只读）：

```
GET {repomesh}/api/v1/runtime/v2/external-members/{agentId}/binding?role={role}   # 成员自己的 token
GET {controller}/api/v1/workers/{resourceName}                                     # controller token
```

| 检查 | 判据 |
|---|---|
| `binding.schemaVersion` | `repomesh.agent-bridge.binding.v2` |
| `binding.role` | 等于花名册 role（RepoMesh 从自己目录确认，不是回显） |
| `binding.containerManaged` | 恒 `false` |
| `binding.workerName` | **等于花名册 `resourceName`**（§4.1 那条规则的一半） |
| `binding.matrixUserId` | 非空 |
| `binding.allowedRoomIds` | ≥ 2（团队房 + 本成员 DM 房） |
| `controller.name` | **等于花名册 `resourceName`**（另一半） |
| `controller.containerManaged` | 恒 `false` |
| `controller.matrixUserID` | 非空，且与 binding 一致 |
| `controller.team` | 非空；同时打印该 repository 的 canonical team 名作对照 |
| `controller.skills` | leader = `code-review, planning`；worker = `coding` |

关于 skills：**v1.2.0 controller 的 GET 文档不带 `skills`**
（2026-08-13 实测，见 `control_plane._assert_worker_matches` 的注释）。
带了就核，不带就如实记为「未上报 + 期望值」，**不当成一致**。
这条是 W-A1 的记账口：leader 若被按 worker 的 `("coding",)` 预建过，
以后每次 `ensure_worker` 都会 409 在 skills 上。

关于 team 名：controller 报的 `team` 与 canonical 名不一致**不判失败**——
A-8 的 reconcile 允许 adopt 一个既有 Team——但会并排打出来给人看。

---

## 5. 契约校验（本环境没有 `jsonschema`）

`make_enrollments.py` 生成后做两道校验，都不新增依赖：

1. **`read_enrollment()`** —— Bridge 自己的读取器（`repomesh_agent_bridge.contracts`）。
   它钉 `schemaVersion`、卡 `role` 枚举、按正则校每个房间号与 matrixUserId、
   拒绝出现在 v1 版本号下的 `repository_leader`，比任何手写检查都严。
2. **schema 声明对照** —— 直接读
   `contracts/agent-bridge/v2/external-member-enrollment.schema.json`，
   核 `required` 全在、无未声明字段（`additionalProperties: false`）。
   与 `tests/contracts/test_agent_bridge_v2_contract.py::assert_fixture_matches_schema`
   同一手法、同一份 schema 文件。

「schema 文件本身是否还诚实」由既有测试负责，需要时单跑：

```bash
.venv/Scripts/python.exe -m pytest tests/contracts/test_agent_bridge_v2_contract.py -q
```

---

## 6. 活体开跑前请连着读

* `docs/development/room-native-bridge-handoff-20260828-wave2.md` **§5** ——
  M7 / M8 逐条预检清单（leader 唤醒是否发生、两个 leader POST 从未对真实路由跑过、
  leader DM 房要同时在 enrollment 与 binding、review_due 要 worker 全终态才发、
  M8 的服务端 Matrix 账号必须**已 join** 目标房间、拓扑 room 号必须已回写）。
* `docs/development/room-native-bridge-handoff-20260827-pr4.md` **§7.5 / §7.6** ——
  环境配方与端口凭据全表。
* `contracts/agent-bridge/v2/README.md` —— enrollment/binding v2 的冻结语义。
