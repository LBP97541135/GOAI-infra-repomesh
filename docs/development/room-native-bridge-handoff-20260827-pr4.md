# Room-Native Bridge 交接文档(至 PR 4 收口 + 会话级活体验收)

> 日期:2026-08-27
> 分支:`feat/room-native-agent-bridge`(**main 之上 33 提交,未推送**)
> 状态:**PR 0–4 全部收口;PR 4 的会话级活体验收全项通过(真 codex 进受限进程对话)**
> 上一份交接:`docs/development/room-native-bridge-handoff-20260827.md`(至 PR 3,历史仍有效)
> 读者:零上下文接手本线的工程师或 agent 会话

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

## 6. 活体验收(全部真机)

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

### 6.3 未登录时 gate 真拒绝

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

### 8.3 环境侧(非代码,沿用上一份)

AgentTeams controller `DELETE` 返回 204 但资源不消失;external leader 同样卡 `invite`;
RepoMesh provisioning 会用自己的投影覆盖 worker 的 runtime/skills;Docker socket 复发性损坏。

---

## 9. 下一步

1. **完整 Matrix E2E**(本轮按用户决定顺延):重建阶段 1 全栈(一次性 postgres + 迁移 + seed +
   uvicorn + socat forwarder + appservice login 取 worker token),在真房间 @ 它让**真 codex** 回话。
   会话级已证明 codex 侧全通,E2E 要证的是两者接起来。
2. **平行轨 P 剩余**:WO-P3(mock Runner 镜像构建 + 活体诊断)、WO-S3(真机 smoke 服务端准备);
   materialize 的活体验收(`handoff_doc_ids` 非空)仍未走过。二者是 PR 5 硬前置。
3. **PR 5 — 复用完整 Runner 链的受治理执行**(估 6–10 人日):`GovernedTaskPort` 调
   `start_assigned_task`、Bridge 兼任 Runner consumer、Worker-scoped 凭据、八条治理验收。

**红线不变**:`src/repomesh_runner/**` 零改动(方案 (a));冻结契约改字段=升 v2。

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
