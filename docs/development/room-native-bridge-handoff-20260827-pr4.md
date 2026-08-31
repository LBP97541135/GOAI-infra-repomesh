# Room-Native Bridge 交接文档(至 PR 4 收口 + 完整 Matrix E2E)

> 日期:2026-08-27
> 分支:`feat/room-native-agent-bridge`(**main 之上 35 提交,未推送**)
> 状态:**PR 0–4 全部收口;会话级验收 + 完整 Matrix E2E 均已通过(真 codex 在真团队房回话)**
> 上一份交接:`docs/development/room-native-bridge-handoff-20260827.md`(至 PR 3,历史仍有效)
> 读者:零上下文接手本线的工程师或 agent 会话

---

## 0. 从这里开始(接手第一屏)

```bash
# 1) 确认你在本线分支上,且工作区干净(下面这些 M/?? 都是他线的,别动)
git -C <repo> log --oneline -6
git -C <repo> status --short

# 2) 门禁(约 5–7 分钟;不要带 -p no:warnings)
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest -q            # 期望 1674 passed / 21 skipped

# 3) 只跑本线快测(秒级)
.venv/Scripts/python.exe -m pytest tests/agent_bridge -q -m "not packaging"
```

- **代码在哪**:`src/repomesh_agent_bridge/`(Bridge 全部)、`src/repomesh/modules/agent_runtime/`(服务端 preflight 与 provisioning)。Bridge 内部结构见 §4 与上一份交接 §3.1/3.2 的行号地图。
- **现在能做什么**:真 codex 以外部 Worker 身份进 Matrix 房间、被 @ 后回话、同 thread 可续,**但只会说话**(任何工具调用一律被拒)。
- **下一步是什么**:见 §9 —— 平行轨 P 剩余项(PR 5 硬前置)与 PR 5 受治理执行。
- **要复现活体环境**:见 §7.5 的完整命令配方。

**PR 4 的四个提交**(分支 `feat/room-native-agent-bridge`):

| 提交 | 内容 |
|---|---|
| `42483424` | W1:`ensure_ready` 门禁、失败三级分级、outbox 分 lane、schema v2(了结 A/B/C 三笔账) |
| `f20872f1` | W2:受限 `ProcessFactory`(Low IL token + Job 全树 + env allowlist)+ 可验证 `IsolationReport` |
| `d008ee9e` | W3:`DriverCodingSession`(deny-all、三段 gate、五路映射、取消传播)+ CLI 真实装配 |
| `6b82acf2` / `1acf8720` | 本交接文档与 E2E 记录 |

---

## 1. 这条线到哪了(30 秒版)

把本地 Coding CLI 以 AgentTeams **外部 Worker**(`containerManaged:false`)身份接进 Matrix 房间。
PR 3 结束时它「能稳定进房聊天」但不会编码(inert stand-in)。**PR 4 让真 codex 进来了**:
CLI 跑在一个本机可验证的受限进程里(低完整性 token + Job 全树 + env allowlist),
房间只看到驱动层挑出的最终答复,任何工具调用一律被拒。

**尚未做的是 PR 5**:受治理的真实改码(Task/worktree/测试/commit)。PR 4 的会话**只会说话**。

---

## 2. 关键文档(按此顺序读)

| # | 文档 | 作用 |
|---|---|---|
| 1 | `docs/adr/0004-room-native-agent-bridge.md` | **冻结的裁决**(独立进程、四 seam、两段式启动、协作式 deny-all、Bridge 兼任 Runner consumer) |
| 2 | `contracts/agent-bridge/v1/README.md` + 三个 schema | **已冻结**,改字段=升 v2 |
| 3 | `docs/development/room-native-bridge-execution-plan-20260826.md` | 执行计划(档位 B+,PR 0–5 + 平行轨 P) |
| 4 | `output/bridge-team/013-mainsession-pr4-design.md` | **PR 4 施工设计与 14 条裁决 H-1~H-14**(gitignored) |
| 5 | `output/bridge-team/012-mainsession-pr3-design.md` | PR 3 设计与 G-1~G-13(gitignored) |
| 6 | `.superpowers/sdd/progress.md` | **全程台账**(gitignored),含每批工单、门禁数字、活体证据链 |

> `output/` 与 `.superpowers/` 都是 gitignored,`git clean -fdx` 会清掉;恢复只能靠 `git log` 与本文档。

---

## 3. PR 4 的 14 条裁决落在哪(H-1~H-14)

| # | 裁决 | 落点 |
|---|---|---|
| H-1 | `CodingSessionPort.ensure_ready()` 启动门禁,**preflight 之后、`open_state` 之前** | `ports.py`、`application.py:247`;`SessionNotReady` in `contracts.py` |
| H-2 | 房间传输词汇表迁入 `ports.py`,matrix 保留 re-export | `ports.py:118/132/143`、`adapters/matrix.py:34-50` |
| H-3 | 失败分级:**sync 拒绝=结束 run** / **send 拒绝=dead-letter** / **join 拒绝=跳过** | `supervisor.py`(`_sync`/`_drain`/`_accept_invites`) |
| H-4 | outbox 分 lane(`turn`/`note`),唯一键 `(trigger, lane, ordinal)` | `outbox.py`、`state.py:140` |
| H-5 | `SCHEMA_VERSION` 1→2;v1 文件拒绝启动 | `state.py:62` |
| H-6 | `run` 默认真会话,`--inert` 保留 stand-in;无真 adapter 的 profile 拒绝而非静默降级 | `cli.py` |
| H-7 | workspace 不打标签(只读)、codex-home/tmp 打 Low(可写);重置保留 codex-home | `restricted_process.prepare_session_dirs` |
| H-8 | 受限四件套:env allowlist(**不合并 os.environ**)、Low IL token、Job 全树、**读隔离不做且不宣称** | `restricted_process.py` |
| H-9 | `probe()` 真实 spawn 逐项验证,`required_ok` 空集或有 unsupported 一律 False | `restricted_process.IsolationReport` |
| H-10 | `ensure_ready` 三段:二进制 → probe → **受限下真握手 + auth.json** | `coding_session.py:202` |
| H-11 | DriverResult→TurnOutcome 五路映射;diagnostics 只进日志 | `coding_session._to_outcome` |
| H-12 | driver 跑独立 task,取消先 cancel/await 子 task 再重抛 | `coding_session.respond` |
| H-13 | driver 窗口 180/840 < supervisor 900;**env allowlist 最终 6 键** | `coding_session.py` |
| H-14 | `check` 不扩(仍 spawns nothing) | `cli.py` 未动 |

---

## 4. 新增代码地图

| 文件 | 行数 | 角色 |
|---|---|---|
| `adapters/restricted_process.py` | ~1089 | Windows-first 受限 `ProcessFactory`/`ProcessHandle` + `IsolationReport` + `prepare_session_dirs`,纯 ctypes 零依赖 |
| `adapters/coding_session.py` | ~460 | `DriverCodingSession`:消费 `ProtocolDriver.execute`,deny-all、三段 gate、五路映射 |
| `tests/agent_bridge/test_process_isolation.py` | 313 / 11 例 | 全部**真实 spawn**,Windows-only |
| `tests/agent_bridge/test_coding_session.py` | — | scripted 假进程重放 JSON-RPC + 真 codex 冒烟(`smoke_codex` marker) |

**三个结构性安全属性**(不靠读分支):
- `_DenyAllPolicy.decide` 只有一条 `return DENY`;
- `_SessionObserver` 只留 `SESSION_STARTED` 的 session id,**连 TEXT 都丢弃** —— THINKING/TOOL/LOG 没有任何路径变成 `RoomObservation`;
- 观测只由 `_to_outcome` 从 `result.summary` 或三条 canned 文案构造。

---

## 5. 门禁与测试

```
$ .venv/Scripts/python.exe -m ruff check .
All checks passed!

$ .venv/Scripts/python.exe -m pytest -q
1674 passed, 21 skipped, 7538 warnings in 402.17s
```

| 时点 | passed / skipped |
|---|---|
| PR 3 收口 | 1612 / 21 |
| **PR 4 收口** | **1674 / 21**(+62,skip 数未增) |

> 不要带 `-p no:warnings`。输出里 aiosqlite 的 `Event loop is closed` 是服务端测试 teardown 噪声,
> 与 Bridge 无关(Bridge 只用 stdlib `sqlite3`)。

**额外手段**:W1 对 supervisor/outbox 做 11 处变异(11 杀,M10 首轮存活已补测);
独立复核方另做 6 处只读变异(4 杀,2 处为等价变异,并据此补了 write-order 不变量测试)。

---

## 6. 活体验收(全部真机:会话级 + 完整 E2E)

### 6.1 隔离 probe(本机 required_ok=True)

```
[verified] low_integrity_token          child token integrity RID = 0x1000 (Low)
[verified] env_allowlist                extra=[] missing=[] value_mismatch=[]
[verified] workspace_read_only          read=ok, write=denied:PermissionError
[verified] out_of_bounds_write_denied   repo-shaped=denied, profile-shaped=denied
[verified] low_dir_writable             wrote
[verified] process_tree_terminated      grandchild alive before=True, after=False
[unsupported] read_isolation_restricted_sids   (H-8④,如实记账,不计入放行门)
```

### 6.2 会话级四条(真 codex 0.149.1)

| 验收 | 证据 |
|---|---|
| 同 thread resume | 回合一开 thread `01a04410-2fb8-...b537e`;回合二用同句柄 → 答出暗号 `FABLE-PR4-7391` |
| deny-all 拒工具 | 要求 `echo SENTINEL_TOOL_RAN` → 审批被拒 → 房间只见「命令未能运行,没有产生 shell 输出」,sentinel 在所有 observation 中**缺席** |
| 取消传播 | 回合中途 cancel → `CancelledError` 穿出 `respond`(H-12) |
| 残留与仓库 | 新增 node/codex pid = **NONE**;`git status` 前后完全一致 |

**旁证**:回合三属于另一个 thread,拿到的是**不同 session id**(`...29305`)——跨会话不串味的活体佐证。

### 6.3 完整 Matrix E2E(真房间 + 真 codex)

复用阶段 1 遗留的 `repomesh-preflight-{probe,leader,team}`(probe 本就是 `containerManaged:false`、
有 Matrix 身份与房间),**本轮零新增 controller 残留**。一次性 postgres @15547 迁到 head,
seed 出 admin + org leader + worker principal,uvicorn 8077,socat 18090 → controller 8090。

> **关键手法**:worker principal 的 id 钉成 `4d1e6f00-0000-4000-8000-000000000004` ——
> 与 §7.1 已登录 codex 的会话目录同一个 UUID,于是 `session_root()` 直接落在已认证的
> `CODEX_HOME` 上,`ensure_ready` 无需二次登录即通过。

链路:`admin PUT` → 200 `{containerManaged:false}`;`GET binding` → 200 完整 binding.v1、
两个 allowedRoomIds;无 token → 401;`bridge check` → exit 0(profile=codex);
`bridge run` → 三段 gate 全过(含受限下真 codex 握手)→ `bridge ready ... profile=codex rooms=2`。

房间里的最终画面(同一个团队房,两代 build 的回答上下相邻):

```
@repomesh-preflight-probe  | [note] I am in this room and I can hear you, but this build cannot run a coding session yet.   ← 阶段 1(inert)
@repomesh-preflight-leader | @repomesh-preflight-probe What is 17 plus 25? ... which coding CLI you are running as.
@repomesh-preflight-probe  | [note] 42. I'm running as the Codex CLI.                                                      ← 本轮(真 codex)
@repomesh-preflight-leader | (线程内)Which number did you just give me in this thread?
@repomesh-preflight-probe  | [note] 42                                                                                     ← rel_type m.thread
```

**resume 的 rollout 级证据**:两条提示词落在**同一个 rollout 文件**(session `01a04429-8284-…`),
而后来的顶层提及另开了 session(`01a0442a-fba0-…`)—— `thread/resume` 确实复用了会话,
不同 thread 也确实不共享。仓库前后不变;拆环境后进程归零。

### 6.4 deny-all 活体:结论正确,但首读是个陷阱

在房间里要求执行 `echo E2E_TOOL_SENTINEL_9931` 并报告 stdout,房间回了
`[note] E2E_TOOL_SENTINEL_9931` —— **看起来像工具跑了**。rollout 说的是反面:codex 调了两次
`exec`(第二次还带 `sandbox_permissions=require_escalated` 与理由),**两次都被拒**:

```
exec_command failed for `powershell.exe -Command 'echo E2E_TOOL_SENTINEL_9931'`:
CreateProcess { message: "Rejected(\"approval request failed\")" }
```

命令从未执行,那串 sentinel 是模型**猜**出来的(`echo` 的输出太好猜)。**隔离成立**。
测试设计教训:输出可被 LLM 猜到的 deny-all 探针,**单看房间文本什么也证明不了** ——
要么读 rollout,要么用模型无法预测的输出。

### 6.5 未登录时 gate 真拒绝

对全新会话目录跑 `ensure_ready`:真起了 codex、过了握手,然后因缺 `auth.json` 拒绝,
并打印 `CODEX_HOME=<dir> codex login`。**gate 自 reap,零残留 pid。**

---

## 7. 环境知识(本轮新增)

### 7.1 给 Bridge 的 codex 登录

Bridge 用**自己的** `CODEX_HOME`,不碰操作者的 `~/.codex`。本轮验收目录:

```
%LOCALAPPDATA%\repomesh-agent-bridge\sessions\4d1e6f00-0000-4000-8000-000000000004\codex-home
```

PowerShell(注意:bash 的 `VAR=value cmd` 前置写法在 PowerShell 里是 `CommandNotFoundException`):

```powershell
$env:CODEX_HOME="<codex-home>"; New-Item -Path $env:CODEX_HOME -ItemType Directory -Force | Out-Null; codex login
```

### 7.2 一条排查结论(重要,别重复踩)

登录曾报 `CODEX_HOME ... does not exist`。三组对照证明:**空目录完全没问题**、非空没问题,
**只有目录真不存在**才会报那条。所以那是建目录与登录之间的时间差,
**不是 Low 完整性标签、也不是产品缺陷** —— 这点关键,因为 `ensure_ready` 每次都会建一个空的 codex-home。

### 7.3 受限子进程的 env(最终 6 键)

`SystemRoot`、`windir`、`CODEX_HOME`、`TMP`、`TEMP`、`PATH`。
`PATH` **只含 node 目录与 codex 目录**(npm 的 `codex.CMD` 要 shell out 到 node),
不含操作者 PATH 的任何其它项。活体探测证实 `PATHEXT`/`ComSpec`/`APPDATA`/`USERPROFILE` 都不需要。
加 PATH **不削弱隔离**:写隔离靠 Low IL token,与 PATH 无关。

### 7.4 一条需要知情的属性

`codex-home` 打了 Low 标签(必须,否则受限子进程写不了 rollout),而 `auth.json` 就在里面。
这意味着**任何低完整性进程都能写它**,与操作者 `~/.codex`(Medium)的信任级别不同。
这是"让受限 CLI 能维护自己会话状态"的必然代价,记账于此。

### 7.5 复现完整 E2E 的命令配方(照此可重跑 §6.3)

> 台账 `.superpowers/sdd/progress.md` 是 gitignored 的,所以这套配方**必须留在这里**。

```bash
# 1) controller forwarder(后端跑宿主时必需;controller 8090 未发布到宿主)
NET=$(docker inspect agentteams-controller --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')
docker run -d --name repomesh-controller-forwarder --network "$NET" \
  -p 127.0.0.1:18090:8090 --entrypoint sh \
  alpine/socat:latest -c "socat TCP-LISTEN:8090,fork,reuseaddr TCP:agentteams-controller:8090"

# 2) 真凭据(.env 里的多个已失效,必须从容器取)
CTL=$(docker exec agentteams-controller sh -c 'cat "$AGENTTEAMS_AUTH_TOKEN_FILE"')        # 641 字节
AS=$(docker exec agentteams-controller sh -c 'printf %s "$AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN"')  # 64 字节

# 3) 一次性 postgres + 迁移到 head
docker run --rm -d --name repomesh-e2e-pg -e POSTGRES_PASSWORD=e2e -p 127.0.0.1:15547:5432 postgres:17-alpine
REPOMESH_DATABASE_URL="postgresql+asyncpg://postgres:e2e@127.0.0.1:15547/postgres" \
  .venv/Scripts/python.exe -m alembic upgrade head

# 4) seed(见下方要点)、5) 后端
REPOMESH_DATABASE_URL="postgresql+asyncpg://postgres:e2e@127.0.0.1:15547/postgres" \
REPOMESH_AGENTTEAMS_CONTROLLER_URL="http://127.0.0.1:18090" \
REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN="$CTL" \
REPOMESH_RUNNER_CONTROL_TOKEN="live-runner-token" \
  .venv/Scripts/python.exe -m uvicorn repomesh.bootstrap.app:create_app --factory --host 127.0.0.1 --port 8077

# 6) 取 worker / leader 的 Matrix token(appservice login;external worker 没有容器 env 可取)
curl -X POST -H "Authorization: Bearer $AS" -H "Content-Type: application/json" \
  -d '{"type":"m.login.application_service","identifier":{"type":"m.id.user","user":"<worker-name>"}}' \
  http://127.0.0.1:18080/_matrix/client/v3/login
```

**seed 要点**(脚本自写,约 40 行):

- 用 `repomesh.bootstrap.app.build_default_container()`(**不是** `ApplicationContainer()`,后者要 10 个位置参数);收尾用 `await container.close()`(不是 `stop()`)。
- admin:`container.local_account_service().bootstrap_admin(user, pass, display)` —— 仅当账户表为空时可用。
- principal:`container.agent_directory.add(principal, idempotency_key=..., request_fingerprint=...)` 直写。
  `AgentPrincipal` **接受显式 `id`**;`organization_id`/`repository_id` **无外键**,只有 `leader_agent_id` 是自引用外键。
  preflight 只要求:**role=WORKER、status=ACTIVE、`agentteams_resource_name` 与 controller 里的 worker 同名**。
- **省一次登录的关键手法**:把 worker principal 的 `id` 钉成**已登录过 codex 的那个 UUID**,
  `session_root()` 就会落在已认证的 `CODEX_HOME` 上,`ensure_ready` 直接过。

**登录接口**:`POST /api/v1/auth/login` → `{"access_token": ...}`,同时下发 `repomesh_session` cookie;
admin 路由认 `Authorization: Bearer <access_token>`。

**拆环境**:`docker rm -f repomesh-e2e-pg repomesh-controller-forwarder`,并**按 PID 杀进程** ——
⚠️ Windows/Git Bash 下 `pkill -f` **杀不掉 `nohup env ... python` 起的进程**,要用
`Get-CimInstance Win32_Process` 按 `CommandLine` 匹配再 `Stop-Process -Force`。

### 7.6 端口与凭据(2026-08-27 实测)

| 端口 | 服务 |
|---|---|
| **18080** | **Matrix client-server API**(conduit 内置在 agentteams-controller,已发布)—— 这就是 `matrixHomeserverUrl` |
| 18090 | 本线自建的 socat forwarder → controller 8090(**用完删掉**) |
| 8077 | E2E 的 RepoMesh 后端(一次性) |
| 15547 | E2E 的一次性 postgres |
| 5432 | 本机活体 postgres —— **谱系与本分支不符,绝不对它跑本分支迁移** |
| 55432 / 8080 / 3000 / 5280 / 8100 | 他线,勿动 |

| 凭据 | 位置 | 状态 |
|---|---|---|
| controller API token | 容器内 `$AGENTTEAMS_AUTH_TOKEN_FILE`(`/var/run/agentteams/cli-token`) | **有效** |
| appservice as_token | 容器 env `AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN` | **有效**,是取 worker token 的钥匙 |
| `.env` 的 controller / matrix token | | **已失效**(栈重建过) |
| `/data/agentteams-controller/admin-token` | | **不是 API token** |

---

## 8. 待处理问题

### 8.1 PR 4 已了结的三笔账(PR 3 遗留 A/B/C)

- **A** 被吊销 token 无限退避 → H-3 分级,sync 拒绝结束 run;
- **B** 超时回合 ordinal 撞车 → H-4 分 lane,两边各自幂等;
- **C** `await_runtime` 启动门禁缺失 → H-1 `ensure_ready`。

### 8.2 仍然记账(不阻塞)

| # | 缺口 |
|---|---|
| D | **无 backfill**(G-6):离线超过 timeline limit(100)的历史提及被静默跳过。实验分支有整套实现可近乎照搬,约 0.5 体量,立项即 C4 |
| E | `turn_count` 只统计产出 outcome 的回合 |
| F | `join` 失败无独立测试 |
| G | `observation_id` 在 `supervisor._note` 与 `outbox` 各派生一次(同源同值,supervisor 那份写时丢弃,**行为上不可测**) |
| H | CLI 第二实例测试若锁逻辑回归会挂死而非失败 |
| I | `MatrixRoomAdapter.start()` 不可重入 |
| J | `RoomInvite` 无房间名;`origin_server_ts` 非 int 归 0 |
| K | `tests/api` 的跨目录 fakes import 依赖 pytest prepend importmode |
| L | `ExternalWorkerProvisioner` docstring 仍点名 adapter 专有异常 |
| **M** | **读隔离(restricted SIDs)本期不做**,`IsolationReport` 如实标 unsupported。要挡"CLI 自发读盘"需给真实用户目录加 ACE |
| **N** | **POSIX 无隔离 adapter**:`probe()` 全项 unsupported、`spawn` 拒绝,故非 Windows 只能 `--inert` |
| **O** | claude-code / kimi **无真 adapter**(H-6 拒绝启动并指路 `--inert`) |
| **P** | **房间分不清「跑了得到 X」与「被拒后猜了 X」**(§6.4 实测):全部工具调用被拒的回合仍报 `completed`,房间只看到一个自信的答案。`DriverResult` 带着 `tool_call_count`、驱动也知道审批被拒,但这些都没进 observation。**归 PR 5 处理**——受治理执行绝不能继承这个:房间文本读起来像命令结果而实际什么都没跑,正是本线要消灭的「四层假绿」 |

### 8.3 环境侧(非代码,沿用上一份)

AgentTeams controller `DELETE` 返回 204 但资源不消失;external leader 同样卡 `invite`;
RepoMesh provisioning 会用自己的投影覆盖 worker 的 runtime/skills;Docker socket 复发性损坏。

---

## 9. 下一步

1. ~~完整 Matrix E2E~~ —— **已完成,见 §6.3/§6.4**。
2. **平行轨 P 剩余(PR 5 硬前置,建议先做)**:
   - **WO-P3**:构建现有 mock Runner 镜像(`components/repomesh-runner/Dockerfile`)并做执行面活体诊断;
     compose 明确**不增加第二个 Runner 消费者**(R8:Bridge 自己兼任)。
   - **WO-S3**:真机 smoke 的服务端准备。
   - **materialize 活体验收**:`handoff_doc_ids` 非空且无降级 warning —— 0036 迁移已就位、
     `_register` 的 409 已修,但这条**从未在全栈上走过**,需要全栈 + LLM。

3. **PR 5 — 复用完整 Runner 链的受治理执行**(估 6–10 人日)。入手顺序建议:

   1. 先补 **§8.2 缺口 P**(房间分不清"跑了"与"被拒后猜了")。它是 PR 4 刚暴露的、
      且 PR 5 的房间叙事会**放大**它:`DriverResult.tool_call_count` 与审批被拒的事实
      要能到达 observation。**不修它,PR 5 的"进度投影"从第一天起就不可信。**
   2. `GovernedTaskPort`(`adapters/governed_task.py`):房间消息只是**唤醒**,
      Task/assignee/权限/终态**只认 RepoMesh**,调 `start_assigned_task`。
   3. `runner_consumer.py`:组合现有 `HttpLongPollTaskSource` / `serve/ExecuteRunnerTask` /
      `DriverExecutor` / `HttpEventSink` / `TaskLedger` —— **不得复制 Runner 已有的
      `TaskSource`/`RunnerEventSink` seam**。
   4. **Worker-scoped 凭据**:lease 的认证主体绑定 `workerAgentId`,event sink 校验 run 确属该 Worker;
      现有 managed Runner 的全局 token 路径保持兼容,**但 Bridge 不获得它**(当前 preflight 仍用
      runner control token,这正是 PR 5 要收口的)。
   5. 八条治理验收(执行计划 PR 5 节),全部自动化。

**红线不变**:`src/repomesh_runner/**` 零改动(方案 (a));冻结契约改字段=升 v2;
房间只收 `room-observation.v1` 投影,THINKING/协议帧永不入房。

---

## 10. 接手须知(本轮新增)

1. **工作区常年有他线未提交文件**(`.github/workflows/ci.yml`、`docs/architecture/*.html`、
   若干 `docs/development/*.md` 分析文档、`tests/integration/test_runner_gateway_postgres.py`)——
   不读作依据、不改、不删、**不 stage**。提交一律按路径 stage。
2. **本轮出现过编排视野外的并发写者**:两个会话同时改同一批文件。处置方式是让后到者退为
   只读复核方,并由主会话核实工作区实态后裁决归属——**别靠转述判断,先自己看 `git status` 与 mtime**。
   意外收获:独立复核方产出了变异自检与 write-order 补测,质量为正。
3. **测量要有对照组**:第一次数"残留进程"数的是全机 node 总数(基线 64),看起来像大泄漏;
   改成前后 pid 差值才是真相(NONE)。
4. **Windows**:PowerShell 不支持 `VAR=value cmd`;CRLF 警告是常态。
