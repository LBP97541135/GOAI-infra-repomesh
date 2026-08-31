# Room-Native Bridge 执行计划

> 日期:2026-08-26
> 依据:`docs/development/room-native-coding-agent-bridge-proposal-20260826.md`(提案)、
> `docs/adr/0004-room-native-agent-bridge.md`(待 PR 0 补充裁决)、
> `contracts/agent-bridge/v1/`(草案;PR 0 验收后冻结)
> 分支:`feat/room-native-agent-bridge`
> 范围裁决:**先交付档位 B+(受治理且本机加固的 MVP)= PR 0–5 + 平行轨 P**;
> 档位 C(问答闭环/多 CLI/安装器/平台在线状态)另行立项

## 0. 当前状态

- [x] 提案主体方向核实(2026-08-26);契约缺口与安全承诺转入 PR 0
- [x] Day 1:ADR 0004 + `contracts/agent-bridge/v1/` 初稿(**未提交**)
- [ ] PR 0:修正契约中的身份、连接、验证与安全承诺后再冻结
- [ ] PR 1 起的实现工作

提交规则:只 stage 本线文件;当前工作区遗留的 frontend/ci/命名修复等改动不得混入本线。

PR 0 后固定的裁决(当前是待落实基线):

1. Bridge 独立进程,Runtime v1 零改动;需要改 Runner 语义 = 升 v2,另行 ADR。
2. 外部 Worker 走 `containerManaged: false`,Go Controller 零改动。
3. Codex 先行;一 Worker : 一 Bridge 实例 : 一 CLI profile。
4. Bridge 是一个深模块,外部 interface 只有 `RoomNativeAgent.run(enrollment)`;
   内部真实 seam 是 `WorkerBindingPort`、`RoomPort`、`CodingSessionPort`,以及 PR 5 才出现的
   `GovernedTaskPort`。sqlite 状态是 internal seam;凭据解析是注入函数。
5. `CodingSessionPort` 用方案 (a):Bridge 侧薄 adapter 直接消费
   `ProtocolDriver.execute(request, profile, observer)`(`src/repomesh_runner/drivers/base.py:114`),
   runner 零改动;发现能力缺口才升级方案 (b) 并补 ADR 附录。
6. Bridge 兼任该 external Worker 的 Runner consumer,但复用完整 Runner 执行链;
   不再起第二个本地 Runner 争抢任务,也不在 Bridge 重写执行器/测试/提交逻辑。
7. 房间只收 `repomesh.room-observation.v1` allowlist 投影;THINKING/协议帧/未脱敏输出永不入房。
8. `deny-all` 是协作式权限,不是宿主机隔离。真实 CLI 必须通过受限 `ProcessFactory` 启动;
   若当前平台没有可验证的受限启动 adapter,Bridge 只允许 fake CLI,不得宣称“完全无写权限”。
9. Runtime v1 wire contract、`ProtocolDriver` interface、Go Controller、前端不改;
   允许对 Runner composition root 做保持默认行为的注入式小改。

---

## PR 0 — 契约校正与安全 Spike(估 1–2 人日;所有实现 PR 的硬前置)

**目标**:把当前无法实现或表述不一致的约束改成可验证 contract,再冻结 v1。

**必须裁决并写入 ADR/contract**:

1. Enrollment 增 `workerAgentId` 和 `matrixHomeserverUrl`;`workerName`、`matrixUserId`、
   `allowedRoomIds` 与 RepoMesh/AgentTeams 实际投影必须一致。
2. 启动验证拆成两段:本地 schema/profile/credential-ref 校验必须在联网前完成;
   `containerManaged: false`、Worker 绑定和房间归属由联网后的 RepoMesh preflight 校验,
   但必须发生在 Matrix sync 和 CLI spawn 之前。
3. 增 `external-worker-binding.schema.json`,作为 RepoMesh preflight 的版本化返回;
   Bridge 不持有 AgentTeams 管理凭据,也不直接查询 Go Controller。
4. 统一 schema version:`repomesh.agent-bridge.enrollment.v1`、
   `repomesh.agent-bridge.binding.v1`、`repomesh.room-observation.v1`。
5. 平台 heartbeat/online 展示移到档位 C;本期只提供本机 health probe,
   不在 contract 中承诺一个没有接收端的远程心跳。
6. 记录信任模型:Matrix 房间消息只是唤醒与展示;Task、assignee、权限和终态只认 RepoMesh。
7. 明确本期隔离级别:受限 OS 身份/ACL + 环境变量 allowlist + 专用 workspace;
   协议 permission callback 只是第二道防线。

**改动范围**:

```text
docs/adr/0004-room-native-agent-bridge.md
contracts/agent-bridge/v1/README.md
contracts/agent-bridge/v1/external-worker-enrollment.schema.json
contracts/agent-bridge/v1/external-worker-binding.schema.json       # 新增
contracts/agent-bridge/v1/room-observation.schema.json
tests/contracts/test_agent_bridge_v1_contract.py                    # 新增
```

**验收**:三个 schema 有实现侧 round-trip contract test;字段命名无冲突;每条 startup
不变量都能映射到后续某个自动化验收;ADR 与 schema 不再声称未计划实现的 heartbeat。

**红线**:PR 0 不实现 Matrix、CLI 或任务执行;未通过不得开始 PR 1。

---

## PR 1 — 外部 Worker 投影与绑定查询(估 3–4 人日)

**目标**:RepoMesh 通过一个显式 application command 创建、读回并校验
`containerManaged: false` 的 AgentTeams Worker,并向 Bridge 提供只读 preflight;
默认 Project Runtime 投影仍是 managed,Controller 零改动。

**改动范围**:

| 文件 | 改动 |
|---|---|
| `src/repomesh/modules/agent_runtime/ports/agent_team.py` | `WorkerProjection`(:60)增 `container_managed: bool = True`;`WorkerRuntimeRef` 增只读观测字段 |
| `src/repomesh/integrations/agentteams/control_plane.py` | `ensure_worker`(:113)、`get_worker`(:189)、`_create_or_reconcile`(:219)携带并检查 `containerManaged`;不匹配 = `AgentTeamsConflict` |
| `src/repomesh/modules/agent_runtime/contracts.py` | 新增显式 external Worker command/query 与非秘密 binding view |
| `src/repomesh/modules/agent_runtime/application/external_worker.py` | 新增 application use case;拒绝非 Worker 身份与 managed↔external 静默转换 |
| `src/repomesh/modules/agent_runtime/api/router.py` | 新增受认证 preflight;只返回 v1 binding,不返回 secret |
| `src/repomesh/integrations/agentteams/runtime_projection.py` | adapter 供 use case 显式投影 external Worker;不动默认 project 路径 |
| `tests/contracts/test_agentteams_integration.py` | 契约测试:默认仍 managed;显式 external 为 false;managed↔external 冲突被拒 |
| `tests/integrations/agentteams/test_runtime_projection.py` | external provisioning 用例 |

**验收**:契约测试全绿 + 真机 smoke——创建一个 external Worker,验证有 Matrix 身份、
无容器、能进 Team(即走通 `member_reconcile.go` 的跳过路径);preflight 能把 RepoMesh
`workerAgentId`、AgentTeams Worker、Matrix 用户与 allowed rooms 绑定起来;错误绑定 fail-closed。

**红线**:不建 Bridge、不启 CLI、不把“哪些 Worker external”藏进环境变量或隐式名单。

---

## PR 2 — 可启动 Bridge v1 骨架(估 2–3 人日;依赖 PR 0/1)

**目标**:立起 `RoomNativeAgent` 外部 interface 与三个真实 seam,产出可安装、可检查、
可启动的单 Worker 进程,但尚不连接 Matrix 或启动 CLI。

**主要改动范围**:

```text
src/repomesh_agent_bridge/
├── __init__.py
├── application.py        # RoomNativeAgent.run(enrollment) 主循环骨架
├── contracts.py          # ExternalWorkerEnrollment 等,与 schema 对齐
├── ports.py              # WorkerBindingPort、RoomPort、CodingSessionPort
├── cli.py                # run/check 子命令;不输出 secret
├── adapters/repomesh_binding.py  # WorkerBindingPort 的 RepoMesh HTTP adapter
└── __main__.py

components/repomesh-agent-bridge/
├── README.md             # 构建、运行与部署说明
└── component.toml

tests/agent_bridge/
├── test_application.py   # 只穿 RoomNativeAgent interface
└── test_cli.py
```

同步修改 `pyproject.toml` 增加 `repomesh-agent-bridge` entrypoint,
修改 `docs/architecture/module-map.md` 登记责任归属。

**验收**:本地 schema/profile/credential-ref 错误在零网络调用下 fail-fast;
联网 preflight 拒绝 Worker 非 external/身份不匹配/房间越权;`--check` 不启动 Matrix/CLI;
空运行生命周期启动 → 取消 → 干净收尾;同一 Worker 的第二个 Bridge 实例 fail-fast;
wheel 安装后的 entrypoint smoke 通过。

---

## PR 3 — Matrix 与可靠性核心(估 5–8 人日,全案最重)

**目标**:fake coding session 下,真实语义的收提及、回消息、去重、重启恢复。

**提取来源**:`feat/agentteams-external-cli-runtimes` **只取设计与测试场景**——
cursor 首次同步基线、受信邀请处理、bounded seen-set、`(thread, trigger-event)` turn ledger、
session store、outbox、确定性 Matrix transaction id、supervisor 取消语义。
**禁止**提取该分支的 Claude/Codex driver 与 projector(与 main 的 runner driver 重复)。

**改动范围(全部为新增)**:

```text
src/repomesh_agent_bridge/
├── adapters/matrix.py    # RoomPort 生产 adapter(/sync、提及检测、txn id 发送)
├── state.py              # sqlite 状态:cursor/inbox/session refs(internal seam,不是 port)
├── inbox.py              # 入站去重与 turn ledger
├── outbox.py             # 发送意图先落盘;txn id 来自 trigger event + response ordinal
└── supervisor.py         # Matrix 循环、重连、取消;CLI 子进程收尾仍归 ProcessFactory/Driver

tests/agent_bridge/
├── test_inbox.py         # 重复/乱序/重连
├── test_recovery.py      # crash-before-persist / persist-before-send / send-before-ack
└── test_room_scope.py    # allowlist、非提及忽略、受信邀请
```

**验收**:首轮不执行历史消息;同一 event 重放只产生一个回合;send 后 crash 再启动
不产生重复房间消息;只响应 allowlist 房间内的明确提及。
状态存储测试直接用临时目录真 sqlite(它是自己的测试替身,不写 in-memory state adapter)。
Matrix 文本只是 `room-observation` 的显示投影;`observationId`/txn id 必须可重放确定,
不能在重试时重新生成随机身份。

---

## PR 4 — 受限本机进程 + Codex 对话(估 5–8 人日)

**目标**:真 Codex 进房对话并恢复同一 thread;不把 RepoMesh worktree、SCM 凭据或
控制面 token 交给对话会话,同时用宿主机可验证的受限进程阻止越界访问。

**改动范围**:

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/repomesh_agent_bridge/adapters/coding_session.py` | 新增 | 消费 `ProtocolDriver.execute`;含一次性只读 workspace 与 deny-all `PermissionPolicy` |
| `src/repomesh_agent_bridge/adapters/restricted_process.py` | 新增 | 实现 Runner `ProcessFactory`;Windows-first 受限身份/ACL/进程树,环境变量 allowlist |
| `tests/agent_bridge/test_coding_session.py` | 新增 | scripted adapter 与真 driver 共用同一组行为契约 |
| `tests/agent_bridge/test_process_isolation.py` | 新增 | sentinel 越权读取/写入、环境泄漏与整棵进程树终止 |
| `src/repomesh_runner/**` | **零改动** | 方案 (a) 红线;缺口→升级方案 (b) 另议 |

只投影 `TEXT` / `SESSION_STARTED`;`THINKING`、`LOG`、原始协议帧不进房间。

**验收**:同一 Room 第二次提及恢复同一 Codex thread(`thread/resume`);另一 Room 不能
串入该会话;任何工具调用被 deny-all 拒绝;CLI 看不到仓库外 sentinel、SCM 凭据和
RepoMesh 控制 token;运行前后真实仓库状态不变;取消后无子进程残留;真机 smoke 一轮。

**降级规则**:当前 OS 没有通过 isolation probe 时,真实 Codex 模式拒绝启动,只允许 fake。
这比把空目录 + deny-all 误称为“完全无写权限”更诚实。

---

## 平行轨 P — 执行面修复(估 3–5 人日;与 PR 2–4 并行,PR 5 硬前置)

已核实的执行面缺口,不修则 PR 5 验收跑不通:

| 项 | 问题 | 改动落点 |
|---|---|---|
| P1 | `handoff_docs` 表无迁移 → materialize 必 500 | `migrations/versions/20260826_0036_*.py`(链尾现为 `20260816_0035_widen_idempotency_key`) |
| P2 | 默认值/默认路径错配 → 已建团仓库 materialize 硬 409 | 修复点待侦察:`src/repomesh/integrations/runner/context_materializer.py` 或拓扑默认值(见 08-26 四人团队 T1 裁定) |
| P3 | Runner Dockerfile 已存在,但 compose 无消费者且镜像未做活体验证 | 构建现有 mock Runner 镜像做执行面诊断;compose 明确不增加第二消费者;Bridge 打包归 PR 2 |

**验收**:已建团仓库 materialize 不再 409/500;`start_assigned_task` 全链路在 compose 环境走通。

---

## PR 5 — 复用完整 Runner 链的受治理执行(估 6–10 人日)

**目标**:"会说话的 Codex Worker" 升级为 "受治理、能编码的 Codex Worker"。

**改动范围**:

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/repomesh_agent_bridge/adapters/governed_task.py` | 新增 | `GovernedTaskPort`:调用 `start_assigned_task`;房间消息只是唤醒,RepoMesh 负责 Task/assignee 校验 |
| `src/repomesh_agent_bridge/runner_consumer.py` | 新增 | 组合现有 `HttpLongPollTaskSource`、`serve/ExecuteRunnerTask`、`DriverExecutor`、`HttpEventSink`、`TaskLedger` |
| `src/repomesh_agent_bridge/ports.py` | 修改 | 只补 `GovernedTaskPort`;不得复制 Runner 已有 `TaskSource`/`RunnerEventSink` seam |
| `tests/agent_bridge/test_governed_execution.py` | 新增 | 六条验收全覆盖 |
| `src/repomesh/integrations/runner/{worker_execution,dispatch,task_projection,gateway}.py` | 复用 | 预期零改动或小改 |
| `src/repomesh/integrations/workspace/git_worktree.py` | 复用 | 零改动 |
| `src/repomesh_runner/executor.py` | 小改(允许) | composition root 可注入 `ProcessFactory`/observer;默认 Runner 行为不变 |
| `src/repomesh/modules/agent_runtime/api/router.py` | 小改 | Worker-scoped lease/event auth;请求不能自报另一个 Worker 身份 |

房间进度不伪造 Runtime event:现有 engine 仍只发 `runner.accepted` + 终态;
Bridge 把 Driver observer 映射成 `room-observation` 的 `run_started`/`phase_changed`/
`tool_action`/`test_completed` 等显示投影,结构化真相仍走现有 Runner event sink。

Runner 控制凭据必须 Worker-scoped:task lease 的认证主体绑定 `workerAgentId`,event sink
校验 run 确属该 Worker。现有 managed Runner 的全局 token 路径保持兼容,但 Bridge 不获得它。

**验收(八条,全部自动化)**:

1. 普通聊天要求改代码 → 拒绝并提示走 Task;
2. 非 assignee 不能启动;
3. allowed path 之外的改动失败;
4. 测试失败不创建 commit;
5. 相同 Task 重投复用 in-flight Run;
6. 房间文本"完成"不推进 Task,只认 Runner 终态事件。
7. Bridge token 改写 `workerAgentId` 或投递其他 Worker 的 run event → 401/403;
8. 同一进程并发运行 Matrix loop 与 Runner consumer,任一失败不会留下 CLI 子进程或丢失 cursor。

---

## 里程碑与排期(单人,总计约 25–40 人日 ≈ 5–8 周)

| 里程碑 | 内容 | 周 | 出口判据 |
|---|---|---|---|
| M0 | PR 0 | 第 1 周前半 | v1 contract 可实现、可测试,无悬空 heartbeat/安全承诺 |
| M1 | PR 1 + PR 2 | 第 1–2 周 | external Worker + preflight + 可安装空 Bridge |
| M2 | PR 3 | 第 3–4 周 | 可靠房间成员(fake session):dedup + outbox + 重启恢复全绿 |
| M3 | PR 4 | 第 4–6 周 | 真 Codex 对话,thread 可恢复,宿主隔离 probe 通过 |
| M4 | 平行轨 P + PR 5 | 第 6–8 周 | 八条治理验收全过,活体 E2E 一轮 |

平行轨 P 可由第二名工程师在 M1 后并行;单人计划中仍计入总人日,唯一硬约束是先于 PR 5 完成。

## 改动面汇总

| 区域 | 级别 | 数量 |
|---|---|---|
| 新增:`src/repomesh_agent_bridge/` + `tests/agent_bridge/` + `components/` | 大 | ~20–24 文件 |
| 修改/新增:`contracts/agent-bridge/v1/` + ADR 0004 + contract test | 中 | 6 文件 |
| 修改:`agent_runtime` contracts/application/API + `integrations/agentteams` | 中 | ~6–8 文件 |
| 修改:Runner composition root + 测试 | 小 | 2–3 文件 |
| 新增:迁移 + compose 文档 | 小 | 2–3 文件 |
| Go Controller、Runtime v1 wire contract、前端 | **零改动** | 0 |

## 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| R1 | Enrollment 身份/连接不足 | PR 0 补 workerAgentId、homeserver URL 与 binding schema;PR 1 preflight 校验 |
| R2 | 凭据交付 | MVP 用 OS keyring/env 引用,人工放置 token;不做在线秘密交换;日志禁止展开引用值 |
| R3 | Matrix 重放/崩溃一致性 | PR 3 用 inbox + outbox + deterministic txn;覆盖三个 crash window |
| R4 | 协作式权限被误当隔离 | PR 4 的受限 ProcessFactory 与 sentinel 测试是硬门禁;probe 失败拒绝真 CLI |
| R5 | Worker 越权领取/回写 | PR 5 使用 Worker-scoped token,服务端从认证主体派生 Worker,不信任 query/body 自报身份 |
| R6 | Windows 差异(CRLF/MSYS/ACL/进程树) | Windows-first 活体 isolation test;Linux adapter 后续;估算取上限 |
| R7 | 实验分支漂移 | 只取场景与设计;禁 cherry-pick driver/projector |
| R8 | 双 Runner 消费者竞争 | Bridge 兼任 consumer;compose 不增加第二 Runner;同 Worker 启动锁 fail-fast |

## 每个 PR 的统一合并门禁

1. 目标模块的 interface 行为测试先于实现细节测试;不得用目录结构断言代替行为验收。
2. 所有外部副作用具备幂等键或明确 retry policy;新增 HTTP adapter 有 production + in-memory/test adapter。
3. `ruff check .`、`pytest` 全绿;涉及真实 Matrix/Codex 的 smoke 独立标记,无凭据时明确 skip。
4. 扫描日志/fixture/Enrollment,确认无 token、系统提示、THINKING、原始协议帧或绝对私有路径。
5. 跨模块只依赖 `repomesh.modules.<producer>.contracts`;composition root 才连接具体 adapter。
6. PR 描述列出验证证据、回滚方式和未解决风险;禁止混入当前工作区无关改动。

## 本期明确不做(档位 C,另行立项)

`input_required` 房间问答闭环、claude-code/kimi adapter、多 Worker supervisor、
Linux 宿主隔离 adapter、Windows Service/systemd 安装器、在线凭据交换/轮换、
平台 heartbeat/OTLP 观测、前端 external/online 展示。
