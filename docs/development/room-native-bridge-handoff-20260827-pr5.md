# Room-Native Bridge 交接文档(至 PR 5 收口)

> 日期:2026-08-27
> 分支:`feat/room-native-agent-bridge`(main 之上 43 提交,未推送)
> 状态:**PR 0–5 全部收口 + 平行轨 P 完成;八条治理验收全部自动化通过;治理路径的活体 E2E 尚未走过**
> 上一份交接:`docs/development/room-native-bridge-handoff-20260827-pr4.md`(PR 4 与完整 Matrix E2E,仍有效)
> 读者:零上下文接手本线的工程师或 agent 会话

---

## 0. 从这里开始(接手第一屏)

```bash
git -C <repo> log --oneline -8
git -C <repo> status --short          # M/?? 都是他线的,别动

# 门禁(全量约 7 分钟;不要带 -p no:warnings)
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest -q            # 期望见 §4 的数字

# 只跑本线快测(秒级)
.venv/Scripts/python.exe -m pytest tests/agent_bridge -q -m "not packaging"
```

- **现在能做什么、还差什么**:先读 §0.5,它是本线对着原始目标的达成度对照,也是回答
  「本地 CLI 是不是已经和容器 Worker 一样了」的唯一权威处。**不要只读 §1–§7 就下结论**:
  那几节讲的是"实现了什么",§0.5 讲的是"哪些被活体证明过、哪些还没有、哪些确实不一样"。
- **尚未做**:治理路径的活体 E2E(§7.1);Bridge 不认平台派活通知(§0.5 C-1)。

---

## 0.5 目标达成度对照(接手必读)

本线的原始目标:**让本地 Coding CLI 以 AgentTeams 外部 Worker(`containerManaged:false`)身份
进 Matrix 房间,可被提及、可连续对话、可重启恢复;正式改码走 RepoMesh 的
Task/worktree/测试/commit 治理。**

对着这句话逐条核对,结论分四类。**A 类可以对外宣称,B 类不可以。**

### A. 已达成,且有活体铁证

| 能力 | 证据 |
|---|---|
| 以外部 Worker 身份在房间里在场 | 真 controller 里 `containerManaged:false`、有 Matrix 身份、无容器;收到邀请后**自己 join**(控制器只 invite)——PR 4 交接 §6.3 |
| 被 @ 后回话、同线程续接 | 真团队房里答 42;线程内追问用同一会话答出,另开顶层提及是另一个 session——**rollout 文件级证据**,PR 4 交接 §6.2/§6.3 |
| 隔离与拒绝真实成立 | 受限进程 probe 六项 verified;要求执行命令时驱动层两次真拒(`Rejected("approval request failed")`),命令从未执行——PR 4 交接 §6.1/§6.4 |

**所以「像成员一样待在房间里说话」是达成了的。**

### B. 已实现,但只有自动化证据,活体没走过

治理执行的全套(PR 5):房间 `start task <uuid>` → 平台校验并启动 → Bridge 以 Worker-scoped
凭据领自己的活 → 受限进程里执行 → 路径/测试/commit 三道硬门 → 生命周期叙事回原线程。
八条验收全部自动化通过(§2),但**Bridge 自己的治理路径一次都没有在真环境上跑过**。

⚠️ 特别注意一个容易读错的地方:§6.5 记的执行面活体**用的是 mock Runner 容器,不是 Bridge
自带的 consumer**。它证明的是「Bridge 所组合的那套执行机器在真环境可用」,**不等于**
「Bridge 组合它的方式在真环境可用」。**在 §7.1 跑完之前,B 类只有纸面。**

### C. 与容器 Worker 确有差异(逐条,均已核对代码)

| # | 差异 | 依据与后果 |
|---|---|---|
| **C-1** | **不会自己接活(最重要)** | 平台派活时发进房间的正文由 `_assignment_body` 生成(`src/repomesh/modules/task_orchestration/application.py:543-552`):它 @ 该 worker,要求调用 MCP 工具 `repomesh-task-control.start_assigned_task`,并**直接给出 `{"task_id":...,"worker_agent_id":...}`**。容器 Worker 有这个工具(`compose.yaml` 里 `REPOMESH_WORKER_TASK_CONTROL_URL` 非空才会把它投影进 worker CRD),**会自己调用把活接下来**。Bridge 的命令语法只认 `start task <uuid>`(`supervisor.py` 的 `_GOVERNED_COMMAND`),**这条通知不匹配**,于是落进会话轨、只回一句话。**后果:今天必须有人读了通知再手敲一遍命令,Bridge 才会动。** |
| C-2 | 只能在 Windows 上真干活 | 受限进程适配器是 Windows-first;非 Windows 上 `probe()` 全项 unsupported、`spawn` 拒绝,故只能 `--inert`(PR 4 记账 N) |
| C-3 | 只支持 codex | claude-code / kimi 无真适配器,**明确拒绝启动并指路 `--inert`**,不静默降级(H-6,PR 4 记账 O) |
| C-4 | 平台上看不到它"在线" | 契约有意为之:本档只提供本机 health probe,不承诺没有接收端的远程心跳,AgentTeams 也不得为外部 Worker 伪造容器就绪(`contracts/agent-bridge/v1/README.md` Liveness 段)。容器 Worker 在控制器里有 `readyWorkers` 之类的就绪态,它没有 |
| C-5 | 离线过久会漏掉旧提及 | 无 backfill:离线超过 timeline limit(100)的历史提及被静默跳过(PR 4 记账 D) |
| C-6 | 卡住了不能在房间里问人 | 需要人补充信息时只标 blocked;`input_required` 的房间问答闭环明确排到档位 C |

### D. 有意不同,不算差距

普通 Worker 被要求在房间里发一段 `repomesh.agent-report.v1` JSON 汇报完成
(`_assignment_body` 的另一分支,`application.py:556-567`,由 collaboration 模块的
`ProcessMatrixTaskReport` 消费,能推进 Task)。**Bridge 从不这么做**:它的终态只走 Runner
事件通道,且有 pinning test 钉死其渲染输出**永远不可能**被解析成该报告(J-17)。这是「房间
文本不得推进 Task」这条信任模型的直接体现,比聊天汇报更强,不要当成缺口去"补齐"。

### 一句话结论

**在场与对话:已达成并经活体验证。改码:实现完成、自动化验收全过,但活体未验;且即便验通,
它仍不会自己接活(C-1),需要人代为触发。**

---

## 1. PR 5 的五个提交

| 提交 | 工单 | 内容 |
|---|---|---|
| `9e94e68a` | WO-A | 缺口 P:observer 计数 PERMISSION_REQUEST,`TurnOutcome.denied_tool_requests`,completed 回合披露拒绝计数并指路 `start task`(=验收 1) |
| `560ed928` | WO-B | Worker-scoped 凭据:`REPOMESH_RUNNER_WORKER_TOKENS`(JSON: workerAgentId→token),`_authorize_runner` 返回主体,lease 派生/403,event 按 runId 反查归属(`RunnerGatewayForbidden`),binding 路径断言,start-worker-task 双授权,全线 `compare_digest`(=验收 2/7) |
| `c800de21` | WO-C | 治理唤醒:`GovernedTaskPort`(第四 seam)+ 无重试 HTTP adapter,supervisor `start task <uuid>` 命令分支(与会话轨互斥),anchor 先于 send,state v3(`run_anchors` + RUN_LANE 显式 ordinal),J-17 pinning(=验收 6) |
| `ac50007f` | WO-D | Bridge 兼任 Runner consumer:`runner_consumer.py` 纯组合 `repomesh_runner`(零改动),`GovernedDriver` 六键 env 注入,`NarratingExecutor` 打 Low 标签 + 锚定叙事(ordinal 1/2/3),terminal body 纯 evidence + 白名单 reason,`--workspace-root` 一开关两半,TaskGroup 双 loop(=验收 3/4/5/8) |
| (本文档) | WO-E | 收口与交接 |

**执行模式**:fable 主脑定界/裁决/验收,opus 子代理按工单实现;逐单主脑亲审 diff、独立复跑、按路径提交。设计文档 `output/bridge-team/014-mainsession-pr5-design.md`(gitignored):裁决 **J-1~J-18**、文件级范围、验收矩阵。

## 2. 八条验收 → 测试落点(全部自动化,全绿)

| # | 验收 | 测试 |
|---|---|---|
| 1 | 普通聊天要求改代码 → 拒绝并提示走 Task | `test_coding_session.py`:披露语系列(denied>0 → body 含计数与 `start task`) |
| 2 | 非 assignee 不能启动 | 服务端 `worker_execution` 既有校验 + `test_governed_wakeup.py` refusal 入房 + `test_runner_scoped_auth.py` HTTP 形状 |
| 3 | allowed path 之外的改动失败 | `test_governed_execution.py::test_a_run_that_writes_outside_its_allowlist_fails_and_commits_nothing`(真 git 仓,HEAD 不动) |
| 4 | 测试失败不创建 commit | `::test_a_run_whose_tests_fail_is_a_failure_however_the_model_finished` |
| 5 | 相同 Task 重投复用 in-flight Run | 服务端 `test_worker_execution.py` 既有 + `::test_the_same_lease_delivered_twice_executes_once_and_narrates_once`(ledger 单执行 + ordinal 重放 no-op) |
| 6 | 房间文本「完成」不推进 Task | `test_governed_wakeup.py`(非命令文本零触 port)+ J-17 pinning(`[label] ` 前缀使渲染输出永不可解析为 collaboration task report) |
| 7 | 伪造 workerAgentId / 投递他人 run event → 401/403 | `tests/api/test_runner_scoped_auth.py` 全矩阵 + `test_gateway.py` 真 store 归属守卫 |
| 8 | 双 loop 并发,任一失败无残留不丢 cursor | `::test_a_consumer_that_dies_takes_the_room_loop_down_and_unwinds_cleanly` + `::test_a_homeserver_that_refuses_the_sync_stops_the_consumer_too` |

## 3. 关键裁决(细账在 014 设计文档,红线级的复述在此)

1. **契约零改动**:v1 的 13 kind + taskId/runId/testExitCode 等字段是 PR 0 预留的,直接用;
   `TurnOutcome` 是内部类型,加 `denied_tool_requests` 不触冻结。
2. **`src/repomesh_runner/**` 真零改动**:consumer 自组 `DriverExecutor`,连计划允许的
   `build_default_executor` 小改都没用上。`tests/runner` 原样全绿是提交门禁的一部分。
3. **J-12(隐藏拦路虎)**:`DriverExecutor` 构造 `DriverRequest` 不传 environment(空 dict),
   受限 factory 又不合并 `os.environ` ⇒ 治理 CLI 会以空环境起不来。修法=`GovernedDriver`
   在 execute 前 `replace(request, environment=六键)`,与会话轨共用 `session_environment`。
4. **J-13**:平台工作树执行前打 Low 标签(否则 Low IL CLI 只读不可写)。代价与 codex-home
   相同:标签期间任何 Low 进程可写,记账于 PR 4 交接 §7.4 的延伸。
5. **叙事只锚定房间触发的 run**(RUN_LANE ordinal:0=accepted 由 supervisor 写,1=started/
   2=test/3=terminal 由 consumer 写);无锚 run 静默执行,结构化真相走 runner events。
6. **terminal body 纯 evidence**:改动文件数/短 sha/测试退出/工具计数;`summary` 只在非成功
   且命中平台笔迹白名单(`changed_path_denied:`/`test_command_failed:`/
   `context_verification_failed:`)时逐字入房;`commit_failed` 只报名目;driver diagnostics
   (CLI stderr)**永不入房**——这条是主脑验收时抓到并修掉的缺陷(WO-D 首版把 stderr 截
   200 字符入房),连同 pinning test `test_a_drivers_own_diagnostics_never_reach_the_room`。
7. **无重试的 start action**:服务端 in-flight 复用使「人再 @ 一次」成为唯一安全重试。
8. **`RunnerGatewayForbidden` 刻意不继承 `ValueError`**:否则被 `record_event` 与路由既有的
   except 吞成 409,403 语义就丢了。异常继承层次决定 HTTP 语义,改它前先看 except 链。

## 4. 门禁

```
$ .venv/Scripts/python.exe -m ruff check .
All checks passed!

$ .venv/Scripts/python.exe -m pytest -q
1777 passed, 21 skipped, 7967 warnings in 398.86s
（全量;PR 4 收口基线 1674/21,+103 全为本线新测试,skip 未增）
```

分项:`tests/agent_bridge` 366(不含 packaging)、`tests/runner` 246/10(未动)、
`tests/api` scoped-auth 20 新测试、`tests/contracts` 205(冻结 schema 未触)。
合并门禁扫描:Bridge 源码零 `repomesh.` 服务端 import(只组合 `repomesh_runner`);
无 token 样式串;无私有绝对路径。

## 5. 新增配置与操作面

| 项 | 说明 |
|---|---|
| `REPOMESH_RUNNER_WORKER_TOKENS`(服务端) | JSON object,workerAgentId→token。人工放置(R2),无表无发放路由。格式错=503。与全局 `REPOMESH_RUNNER_CONTROL_TOKEN` 可并存,worker-map-alone 也是合法部署 |
| Bridge 的 `credentialRefs.repomesh` | 现在应装 **该 worker 自己的 token**(不再是全局 runner token);它同时认证 preflight、lease、events、start action |
| `run --workspace-root PATH` | 同时开启治理唤醒与 consumer;与 `--inert` 互斥;须为已存在目录,且应与服务端 `runner_workspace_root` 同一路径(worktree 由控制面在其下准备) |
| 房间命令 | `start task <uuid>`(大小写不敏感,严格 UUID,`\b` 防 `restart task` 误配) |
| state 文件 | SCHEMA_VERSION **3**(v2 拒启,无迁移——操作者删 state 重建基线,同 H-5 政策);新表 `run_anchors`;Runner ledger 在 `<state_dir>/runner/<workerAgentId>/` |

## 6. 记账(不阻塞,接手须知)

| # | 事项 |
|---|---|
| Q1 | **同 run 第二 attempt 撞 ordinal**:ordinal 固定 1/2/3,同 run_id 不同 attempt 的 terminal body 若不同会 `StateRefused`(被 serve 捕获、按失败上报控制面)。修法=把 attempt 编入 ordinal 空间,等真实需求 |
| Q2 | 双 loop **双双真失败**时以 `ExceptionGroup` 逸出 CLI 的类型映射(单失败已解包)——刻意:压平意味着二选一隐瞒一个真原因 |
| Q3 | 叙事延迟 ≤ supervisor sync 轮询窗口(30s);crash 在 terminal 入列前 → 该条叙事丢失(结构化真相不受影响) |
| Q4 | `prepare_session_dirs` 治理启动时跑两次(ensure_ready + governed_environment),幂等,两次多余 icacls |
| Q5 | `ProcessMatrixTaskReport`(collaboration)仍是「房间纯 JSON 上报可推进 Task」的既有产品路径,身份校验齐全、与 Bridge 渲染结构性不相交(J-17 钉死);要不要对 external worker 关闭它=产品裁决,不在本线 |
| Q6 | PR 4 记账 D~O 项继续有效(backfill、POSIX、claude-code/kimi 无 adapter 等);其中 **P 项已了结**(本 PR 主题) |

## 6.5 平行轨 P 已完成(2026-08-27,活体)

**WO-P3 — mock Runner 镜像与执行面诊断:通过,并修掉两个此前无人发现的缺陷**(提交
`5e4fa2bf`,只改 `components/repomesh-runner/Dockerfile`,`src/repomesh_runner/**` 仍零改动)。

| # | 缺陷 | 证据与修法 |
|---|---|---|
| 1 | **镜像根本起不来**:`ModuleNotFoundError: opentelemetry` | 08-07 可观测性线让 `executor→observer` 有了模块级 OTel 导入(`telemetry.py` 刻意让追踪只在**运行期** opt-in,故导入期恒需),而 Dockerfile 仍按「只依赖 httpx」安装。**本机镜像二分**:08-05 构建正确拒绝、08-15 及之后全部导入即死 ⇒ **20 天假绿,因为镜像只被构建、从未被运行**。修法=按 pyproject 的 pin 装上 OTel 两件套 |
| 2 | **执行完才崩**:`PermissionError: /runner-workspaces/.runner-state` | ledger 默认落在 `<workspace_root>/.runner-state`,而 workspace root 按契约由平台挂载;root 属主的卷让非 root 的 runner(uid 10001)写不进去——且是在**任务已执行、事件已投递之后**才发现,run 落了地而「重投即 no-op」的键没留下。修法=ledger 是进程自己的记忆,改落 `/home/runner/.runner-state` |

**执行面全链路活体证据**(真控制面 + 真 worktree):`start-worker-task` 202 → 从夹具真实
clone(base_sha 一致)→ runner 领取 `accepted task run=… attempt=1` → mock agent 经
stream-json 驱动执行 → **仓库自己的测试命令 `python scripts/run_tests.py` 在 worktree 里
exit 0** → `runner.accepted`+`runner.completed` 双 202 → 控制面写回 task=**succeeded**、
dispatch=**completed** → ledger 落盘 → 循环继续长轮询(204)。`changedFiles=[]`/`commitSha=null`
与「mock 不写盘」契约一致。compose **未新增 Runner 消费者**(R8:Bridge 自己兼任)。

**materialize 活体验收:通过**。`POST /api/v1/bridge/materialize` → **200**,
`handoff_doc_ids` **非空**(0036 建的表真实落行,`GET /api/v1/handoff-docs` 可读回)、
`skipped_repos: []`、`plan_id` 非空、**日志零 warning/error**(W1「Failed to generate handoff
documents」等降级信号均未出现),且计划批次**真的投进了 Matrix 团队房**(房间里可见发给
worker 的任务包通知)。**关于「需要 LLM」的记录是过度保守**:materialize 本身不调模型,
手写 plan 直接 POST 即可;只有其上游的 requirement/confirmation/integration 才需要 LLM。

**环境坑(必记)**:Git Bash 的 **MSYS 路径转换**会把 `docker run -e VAR=/abs/path` 与脚本
里的 `/abs/path` 一起改写成 `D:/Git/...`,本轮曾因此让 clone 走 ssh 并让 workspace root
变成 `/app/D:/Git/...`。**凡传 Linux 绝对路径给 docker 或种子脚本,一律加 `MSYS_NO_PATHCONV=1`**。
另:Windows 宿主跑 API + Linux 容器跑 runner **无法**靠 `WORKSPACE_PATH_FROM/TO` 打通——
worktree 路径有三层(`w/<run_key>/<repo_key>`),重映射按字符串前缀切分后保留反斜杠,
在 Linux 侧会变成单个畸形目录名。执行面诊断必须两侧同为 Linux。

## 7. 下一步(按此顺序)

~~平行轨 P~~ —— **已完成,见 §6.5**。

### 7.1 治理路径活体 E2E —— 先做这个

**理由**:§0.5 的 B 类(整个 PR 5)在这一步跑完之前全是纸面。后面任何功能补齐都得靠这套
环境来验证,所以它也是最省事的第一步。

配方在 PR 4 交接 §7.5 与本文 §6.5 的基础上组合(§6.5 已证明这套搭法可行):

1. 服务端配 `REPOMESH_RUNNER_WORKER_TOKENS`(JSON: workerAgentId→token);
2. Bridge enrollment 的 `credentialRefs.repomesh` 换成**该 worker 自己的 token**(不再是全局
   runner token);
3. `run --workspace-root <与控制面 runner_workspace_root 同一路径>` 启动 Bridge;
4. 建一个 Task 指派给该 worker(§6.5 的种子路径可直接复用:仓库夹具 + 冻结的 TASK 规格);
5. 房间里发 `start task <uuid>`;
6. **验收对账三处**:worktree 里是否真的改了码并按门禁跑了测试、提交;房间 run lane 是否
   出现 accepted/started/test/terminal 四条且**终态正文只由证据构成**;runner events 与
   rollout 是否与房间叙事一致。

**注意 §6.5 的两条环境坑仍然适用**(MSYS 路径转换、Windows+Linux 混搭不可行)。Bridge 跑在
Windows 宿主上,所以控制面这次也要让 worktree 路径与 Bridge 看到的一致——最省事的做法是
让 RepoMesh 后端也跑在 Windows 宿主(PR 4 §7.5 的 uvicorn 方式),而不是容器里。

### 7.2 让 Bridge 认平台的派活通知 —— 补齐 C-1

做完 7.1 才有验证手段,所以排第二。范围很小:

- 平台派活通知(`task_orchestration/application.py:543-552`)正文里**本就带着**
  `{"task_id":"<uuid>","worker_agent_id":"<uuid>"}`,解析出来直接走现有 `GovernedTaskPort`
  即可,不需要新的服务端接口。
- **安全性不变**,理由要写进代码注释:按冻结契约的信任模型,房间消息本来就只是唤醒;
  任务存不存在、是不是派给这个 worker、有没有权限,全部由 RepoMesh 复核,伪造的通知一样
  会被平台拒掉——这与 `start task <uuid>` 命令是同一条论证。
- 建议保留 `start task <uuid>` 作为人工触发口,两条入口共用同一个 governed 分支与同一套
  幂等键(同一 trigger event 只产生一次 start_task 调用)。

### 7.3 之后

档位 C 立项:`input_required` 房间问答闭环(C-6)、多 CLI 适配(C-3)、backfill(C-5)、
平台在线状态(C-4)。C-2(POSIX 隔离适配器)独立立项。

**红线不变**:`src/repomesh_runner/**` 零改动;冻结契约改字段=升 v2;房间只收
`room-observation.v1` 投影,THINKING/协议帧/未脱敏 stdout/stderr 永不入房。
