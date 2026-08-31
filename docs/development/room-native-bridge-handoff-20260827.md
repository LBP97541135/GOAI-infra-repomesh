# Room-Native Bridge 交接文档(至 PR 3 收口 + 活体验收)

> 日期:2026-08-27
> 分支:`feat/room-native-agent-bridge`(HEAD `441e52dc`,**main 之上 29 提交,未推送**)
> 状态:**PR 0–3 代码全部收口;阶段 1 活体验收与真 preflight 全链路均已通过**
> 上一份交接:`docs/development/room-native-bridge-handoff-20260826.md`(至 PR 1,已过期但历史仍有效)
> 读者:零上下文接手本线的工程师或 agent 会话

---

## 1. 这条线是什么(30 秒版)

把本地 Coding CLI(Codex 先行)以 AgentTeams **外部 Worker**(`containerManaged: false`)身份接进 Matrix
房间:可被 @、可连续对话、可重启恢复;正式改码仍走 RepoMesh 的 Task / worktree / 测试 / commit 治理。
Bridge 是**装在操作者机器上的独立进程**,复用 `repomesh_runner` 驱动栈,Runtime v1 wire contract、
Go Controller、前端**零改动**。

**阶段 1 验收(「能稳定进 Matrix 聊天」)已达成**,详见 §7。

---

## 2. 关键文档(按此顺序读)

| # | 文档 | 作用 |
|---|---|---|
| 1 | `docs/adr/0004-room-native-agent-bridge.md` | **冻结的裁决**:独立进程、四个 seam、两段式启动验证、协作式 deny-all、Bridge 兼任 Runner consumer |
| 2 | `contracts/agent-bridge/v1/README.md` + 三个 `*.schema.json` | **已冻结**,改字段=升 v2(`additionalProperties:false` 使加字段也算破坏);README 的 Interface semantics 是行为规格,含幂等三键与不变量→验收映射 |
| 3 | `docs/development/room-native-bridge-execution-plan-20260826.md` | 执行计划(档位 B+,PR 0–5 + 平行轨 P),文件级改动范围与合并门禁 |
| 4 | `output/bridge-team/012-mainsession-pr3-design.md` | **PR 3 设计提取与施工拆分**(gitignored):八项机制逐条裁定、Matrix 客户端方案、sqlite 表设计、13 条待裁决及其裁定 |
| 5 | `.superpowers/sdd/progress.md` | **全程台账**(gitignored):每批工单、裁决、门禁数字、活体验收证据链 |
| 6 | `docs/development/room-native-coding-agent-bridge-proposal-20260826.md` | 原始提案(背景与工作量依据) |

> `output/bridge-team/` 与 `.superpowers/` 都是 gitignored,`git clean -fdx` 会清掉;
> 恢复只能靠 `git log` 与本文档。

---

## 3. 代码地图与关键调用链(精确到行号)

### 3.1 Bridge 包(`src/repomesh_agent_bridge/`,全部为本线新增)

| 文件 | 行数 | 角色 |
|---|---|---|
| `contracts.py` | 707 | 三份 schema 的 wire 模型 + `from_wire`/`to_wire` + 异常族 |
| `ports.py` | 200 | 三个 seam 的 Protocol 与其词汇类型 |
| `state.py` | 684 | sqlite 状态(**internal seam,不是 port**) |
| `inbox.py` | 258 | 入站决策:基线、去重、turn ledger |
| `outbox.py` | 294 | 出站意图:落盘、派生身份、**唯一的 `RoomBody` 构造点** |
| `supervisor.py` | 376 | **唯一知道顺序的模块** |
| `application.py` | 264 | `RoomNativeAgent.run` —— 模块唯一外部 interface |
| `cli.py` | 156 | `run` / `check` 子命令与组装根 |
| `instance_lock.py` | 122 | 一 Worker 一实例的 OS 级锁 |
| `adapters/matrix.py` | 466 | `RoomPort` 的生产实现(httpx 手写四端点) |
| `adapters/memory.py` | 220+ | 生产 stand-in(`InertCodingSession`)与测试替身 |
| `adapters/repomesh_binding.py` | 141 | `WorkerBindingPort` 的 HTTP 实现 |

### 3.2 启动链(冷启动到进入房间)

```
cli.main(argv)                                   cli.py:69
 └─ _load_enrollment(path)                       cli.py:121   # 文件 → ExternalWorkerEnrollment.from_wire
 └─ _default_binding_port(enrollment)            cli.py:133   # 生产装配:RepoMeshBindingAdapter
 └─ RoomNativeAgent.run(enrollment)              application.py:195
     ├─ InstanceLock(instance_lock_path(...))    application.py:219 → instance_lock.py:63
     ├─ _startup(...)                            application.py:231 → application.py:140
     │   ├─ _validate_locally(...)               application.py:73    # 阶段 1:零网络 fail-fast
     │   │    · codingProfile ∈ CODING_PROFILES  application.py:87
     │   │    · credentialRefs 非空 / 可解析       application.py:91-100
     │   ├─ after_local_validation=lock.acquire  application.py:161 → instance_lock.py:80
     │   │    # 锁在阶段 1 之后、preflight 之前:坏配置不占锁,合法第二实例不打网络
     │   ├─ binding_port.fetch_binding(...)      application.py:163 → adapters/repomesh_binding.py:71
     │   └─ _confirm(enrollment, binding)        application.py:167 → application.py:104
     │        · organizationId/workerAgentId/workerName/matrixUserId/teamName 逐项相等
     │        · allowedRoomIds 取交集,空 → BindingRefused    application.py:130-135
     ├─ open_state(state_path(...), worker_agent_id=...)     application.py:231 → state.py:226
     │    · WAL + synchronous=FULL                            state.py:243-244
     │    · bridge_meta 身份认领:worker/schema 不符即拒       state.py:253
     ├─ room_port.start(..., access_token=...)   application.py:241 → adapters/matrix.py:122
     │    · whoami 校验 token 属于 enrollment 的 user         adapters/matrix.py:122-169
     └─ RoomSupervisor(...).serve()              application.py:258-264 → supervisor.py:134
```

**取消收尾**:`AsyncExitStack` 反序 unwind —— coding session → room port → state.close → lock.release
(`application.py:238-240`;注册顺序即逆序执行顺序)。

### 3.3 稳态主循环(每一轮的写顺序 = 崩溃一致性的全部前提)

```
RoomSupervisor.serve()                supervisor.py:134
 └─ while True: _round()              supervisor.py:165
     ├─ _drain()                      supervisor.py:225   # ① 先把上次残留的意图发出去
     ├─ _sync()                       supervisor.py:188 → adapters/matrix.py:170
     ├─ _accept_invites(batch)        supervisor.py:196   # ② 房间在 confirmed 列表内才 join
     ├─ if inbox.is_baseline():       inbox.py:85
     │     record_baseline(batch)     inbox.py:103        # ③ 首轮只记不执行
     └─ else:
         for trigger in inbox.triggers(batch, ...)        inbox.py:126
             └─ _run_turn(trigger)    supervisor.py:261
                 ├─ inbox.claim(trigger)                  inbox.py:173
                 ├─ _decide(trigger)  supervisor.py:299   # asyncio.timeout 包 respond
                 ├─ outbox.enqueue(...)                   outbox.py:190
                 ├─ _drain()          supervisor.py:225   # send + mark_sent
                 └─ finally: inbox.settle(trigger, status) inbox.py:196
         inbox.commit(batch)          inbox.py:222        # ④ cursor 永远最后
```

取消时 `CancelledError` 在 commit 之前穿出,整批下次重放(`supervisor.py:134-164` docstring)。

### 3.4 幂等与确定性(为什么崩溃不产生重复)

| 事实 | 位置 |
|---|---|
| txn id `= "rmb-" + sha256(trigger_event_id \x1f ordinal)[:40]` | `outbox.py:75-91`(`TXN_PREFIX` :48) |
| observationId `= uuid5(NAMESPACE, worker\|room\|trigger\|ordinal)` | `outbox.py:94-108`(namespace :54) |
| **ordinal 在落盘时分配**,不在发送时现算;`UNIQUE(trigger_event_id, ordinal)` 守不变量 | `outbox.py:190-249`,表定义 `state.py:89` 内 `_SCHEMA` |
| `emitted_at` 落盘一次,重放读回不重取 now() | `outbox.py:220`、`_pending_from_row` :272 |
| 三态 ledger + 死实例重授权 + timeout 非终态 | `inbox.py:173-221`;状态常量 `state.py:79-80` |
| 有界 seen-set(4096,插入序淘汰) | `state.py:66` |
| `RoomBody` 只由 `render` 构造(+ 从库读回一处) | `outbox.py:111-133`、:272;类型 `ports.py` 内 `RoomBody`;**源码扫描测试**钉死 |

### 3.5 Matrix adapter(零决策、零 id 生成)

| 端点 | 常量 | 实现 |
|---|---|---|
| whoami | `adapters/matrix.py:57` | `start` :122 |
| sync | :58 | `sync` :170(`since=None` → timeout 0) |
| join | :59 | `join` :196 |
| send | :60 | `send` :205(txn 由调用方给) |

解析:`_batch` :314 / `_event` :345 / `_inviter` :373 / `_mentions_me` :395 / `_unquoted` :430(mx-reply 剥离)。
错误二分:`RoomUnavailable` :80(可重试)vs `RoomRefused` :89(不可重试),基类 `RoomTransportError` :66。

### 3.6 服务端(RepoMesh 侧)

| 关注点 | 位置 |
|---|---|
| preflight 路由 `GET /runtime/external-workers/{id}/binding` | `src/repomesh/modules/agent_runtime/api/router.py:59`;认证 `_authorize_runner` :109 |
| **provisioning 路由 `PUT /runtime/external-workers/{id}`** | 同文件 :118;admin 守卫 `_authorize_administrator` :195(镜像 `modules/delivery/api/router.py:33-52`) |
| use case | `application/external_worker.py`:`ProvisionExternalWorker` :49、`ResolveExternalWorkerBinding` :92 |
| 窄读协议 `WorkerBindingReader`(仅 get_worker/get_team) | `ports/agent_team.py:164`;`AgentTeamControlPlane` :195 继承它;`ExternalWorkerProvisioner` :215 |
| 组装根工厂 | `bootstrap/container.py:457`(binding reader)、:492(provisioner,**含 adapter 冲突→模块异常的翻译**) |
| 409 修复:`_register` 先读后建 | `integrations/agentteams/runtime_projection.py:192`;`_assert_bound` :112;`AgentTeamsResourceMismatch` :82 |
| `containerManaged` 严格布尔 | `integrations/agentteams/control_plane.py:390` `_matches`;字段集 :54;调用点 `_assert_fields` :413 |
| `ExternalWorkerView.to_wire`(operator 回执) | `modules/agent_runtime/contracts.py:138/151` |
| `handoff_docs` 迁移 | `migrations/versions/20260827_0036_handoff_docs.py`;模型注册 `migrations/env.py` |

---

## 4. 测试与门禁 log

### 4.1 最终门禁(HEAD `441e52dc`)

```
$ .venv/Scripts/python.exe -m ruff check .
All checks passed!

$ .venv/Scripts/python.exe -m pytest -q
1612 passed, 21 skipped, 7177 warnings in 313.18s (0:05:13)
```

> **不要带 `-p no:warnings` 跑门禁** —— warning 数是证据的一部分(基线约 7000,全部来自
> pytest-asyncio 对 Python 3.14/3.16 asyncio-policy 的弃用告警,与本线无关)。
> 21 skipped 中有 2 个是 `test_handoff_docs_postgres.py`(无 `REPOMESH_TEST_POSTGRES_URL` 时明确 skip)。

### 4.2 门禁数字演进(可用于判断某次改动是否引入回归)

| 时点 | passed / skipped | 增量来源 |
|---|---|---|
| main 基线 | 1315 / 19 | — |
| PR 0+1 完成 | 1347 / 19 | +32 契约与 provisioning |
| 首批(B1+P2+S1) | 1451 / 19 | +104 |
| 二批(B2+P1+S2) | 1481 / 21 | +30,+2 skip(postgres 集成) |
| PR 3 wave 1(C1+C2) | 1592 / 21 | +111 |
| **PR 3 收口(C3)** | **1612 / 21** | +20 |

### 4.3 本线测试文件清单

```
tests/agent_bridge/test_matrix_adapter.py     759 行  42 例   # HTTP 细节,MockTransport
tests/agent_bridge/test_room_scope.py         896 行  20 例   # 端到端行为验收
tests/agent_bridge/test_inbox.py              634 行  29 例   # 状态/去重/ledger/outbox
tests/agent_bridge/test_recovery.py           423 行  12 例   # 三个 crash 窗口 + 持久性
tests/agent_bridge/test_packaging.py          346 行   4 例   # wheel smoke(marker: packaging)
tests/agent_bridge/test_application.py        321 行  11 例
tests/agent_bridge/test_cli.py                269 行  12 例
tests/agent_bridge/test_wire_contracts.py     207 行  13 例
tests/agent_bridge/test_repomesh_binding.py   194 行  10 例
tests/api/test_external_worker_provisioning.py  504 行  22 例
tests/api/test_external_worker_binding.py       332 行   9 例
tests/integrations/agentteams/test_runtime_projection.py 1474 行 34 例
tests/integration/test_handoff_docs_postgres.py 367 行   2 例（需 postgres）
tests/contracts/test_agent_bridge_v1_contract.py 272 行  14 例
tests/integrations/agentteams/test_control_plane_conflicts.py 130 行 5 例
```

**跑法**:
- 全量门禁:`.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q`
- 反选慢测:`pytest tests/agent_bridge -q -m "not packaging"`(92→203→223 例,秒级)
- postgres 集成:`REPOMESH_TEST_POSTGRES_URL=postgresql+asyncpg://... pytest tests/integration/test_handoff_docs_postgres.py`

### 4.4 额外验证手段(两个子代理自发采用,建议延续)

- **变异测试**:C2 对 adapter 做 19 处定点变异,首轮 16/17 被杀;幸存的 `3xx-retryable`
  暴露出"过得不是原因"的弱测(301/302 塞在通用 4xx 参数化里,变异后靠缺 `next_batch` 报同一异常),
  据此改写成 `test_a_redirect_is_refused_and_never_followed`。C3 做 9 处,全部被捕获,
  其中"invite 只在基线轮处理"最初存活 → 补 `test_an_invitation_arriving_after_the_baseline_is_accepted_as_well`。
- **源码扫描测试**:`RoomBody(` 的拼写在全包内只允许出现在 ports 定义与 `outbox.py`。

---

## 5. 关键决策与其理由

### 5.1 架构级(ADR 0004 冻结,不可在本线推翻)

1. Bridge 独立进程,Runtime v1 零改动;要改 Runner 语义 = 升 v2 + 另开 ADR。
2. 外部 Worker 走 `containerManaged:false`,Go Controller 零改动。
3. 一 Worker : 一 Bridge 实例 : 一 CLI profile。
4. 外部 interface 只有 `RoomNativeAgent.run(enrollment)`;三个真实 seam;**sqlite 状态是 internal seam
   不是 port**(它自己就是测试替身),凭据解析是注入函数。
5. 房间只收 `repomesh.room-observation.v1` allowlist 投影;THINKING / 协议帧 / 未脱敏输出永不入房。
6. 信任模型:Matrix 房间消息只是唤醒与展示;Task、assignee、权限、终态**只认 RepoMesh**。

### 5.2 PR 3 设计裁决(共 13 条,全部记于 `012` 设计文档 §G)

| 编号 | 裁决 | 理由 |
|---|---|---|
| G-1 | sync timeout 30s;读超时 = `timeout_ms/1000+5` 下限 10s;退避 1→2→…→60s | 两侧都有活体验证过的参数 |
| G-2(a) | `m.mentions.user_ids` 在场即权威 | 客户端已明确说了它指谁 |
| **G-2(b)** | **`m.mentions.room=true` 不算提及** | 一条 `@room` 公告会让房间里每个 external worker 同时起一轮,并把雪崩全播回房间 |
| **G-2(c)** | body 回退**必须先剥 `<mx-reply>` 与 `> <@user>` 引用行** | 实验分支没做,导致"回复一条提及你的消息"被误判为新提及 —— 可复现的假触发 |
| **G-3** | 受信邀请判据 = **房间在 preflight confirmed 列表内**(不是邀请人白名单) | 本线有实验分支没有的权威源;白名单是本机可篡改的 |
| G-4 | seen-set 4096 有界;**ledger 不设界** | ledger 行数量级=回合数;seen-set 淘汰后的重放正靠 ledger 兜住 |
| **G-5** | `RoomBody` NewType + 单点构造 + 源码扫描测试 | 把"THINKING 不入房"从纪律变成结构;`TurnOutcome` 也不设 raw 字段 |
| **G-6** | **不做 backfill**;`limited:true` 只记 WARNING | 要引入 `/messages` 分页与 ack 水位线两块新表面。**唯一中置信度裁决,已知缺口见 §6** |
| G-7 | ledger 键 `(room_id, thread_id, trigger_event_id)` | 契约措辞是 `native session id`,但冷启时它还不存在;`thread_id` 是其稳定前身,映射由 `session_refs` 持有。**裁定为实现细节,不改冻结契约** |
| G-8 | `CodingSessionPort.respond` 在 PR 3 就出现 | ADR 那句"conversation surface 属 PR 4"指的是真 CLI adapter,不是 port 方法;否则 supervisor 只能对着 `close()` 编程 |
| G-9 | `RoomPort.start` 收 per-call `access_token` | 与 `fetch_binding(credential=...)` 同理:secret 的生命周期属于调用而非进程 |
| G-10 | 一次一回合,不并发 | 一台笔记本一个 workspace,并行是污染不是加速 |
| G-11 | 回合超时 PR 3 就做(`asyncio.timeout`,900s 可注入) | 否则"timeout 非终态可重试"这条语义要裸奔一个 PR |
| G-12 | `emittedAt` 落盘 | 同一 observationId 不得出现两个 emittedAt |
| G-13 | 新增第 4 个测试文件 + `ports.py` 有改动 | 对计划"全部为新增"的两处偏离,已显式声明 |

### 5.3 施工级决策(评审时确认过的)

- **P2 修的是 seam 不是默认值**:`_register` 对已绑定资源改为读+验,不再以全局 spec 重 `ensure_*`。
  只改默认值"只会把 409 挪到下一个字段"。identity/readiness 仍留在 reconcile 之后
  (`_assert_identities`),提前会把等待变成死锁。
- **S2 用 200/200 不做 201**:`ensure_worker` 对"创建"与"读到已存在"返回同一份文档,
  RepoMesh 不持有 201 要断言的事实。
- **S2 自报字段 422 不是忽略**:读到 200 却发现自己写的 `containerManaged:true` 被丢弃,比被拒更糟。
- **adapter 冲突翻译在 composition root**:module 代码不得 import `repomesh.integrations.*`,
  沿用 `project_runtime_projector` 的先例。
- **Bridge 不 import 服务端 Matrix 客户端**(`integrations/agentteams/matrix.py`):依赖方向 + 会把
  FastAPI/SQLAlchemy/asyncpg 拖进 wheel(`test_packaging.py` 正在盯)。**照抄形状,不复用代码**。
- **0036 迁移不加模型未声明的唯一约束**:重生成路径对同一 `(project, version, repository)` 写新 id
  再 supersede,加唯一约束会把受支持的行为变成 IntegrityError。索引/主键名按本分支 naming convention
  而非照抄底稿(照抄会让 autogenerate 永远看到漂移)。
- **wheel smoke 不允许"永远 skip"**:本机 venv 由 uv 管理,**没有 pip/build/setuptools**,
  故构建器是三档候选链 `build → pip wheel → uv build --offline`;从**源码树副本**构建以免
  setuptools 把 `build/`、`*.egg-info` 写进仓库;依赖用 `.pth` 借(PYTHONPATH 会让 wheel 的包被源码树顶掉)。

---

## 6. 当前待处理问题(按优先级)

### 6.1 归 PR 4 一并裁决

| # | 问题 | 现状与建议 |
|---|---|---|
| A | **稳态被吊销的 token 会无限退避** | serve 循环把所有 `RoomTransportError` 归入退避,一个被吊销的 token 会每 60s 一条 WARNING 而不退出。需要一条"重试改变不了的拒绝应当结束 run"的规则 |
| B | **超时回合的 ordinal 撞车** | 超时产出 1 条 note 占 ordinal 0;若在该批 commit 前崩溃、重放后成功产出 N 条,新 ordinal 0 被 `INSERT OR IGNORE` 挡掉,房间第一行仍是旧的超时 note。触发条件苛刻(超时 note 已发 + commit 前崩溃 + 重试成功),修它要动 `outbox.enqueue` 的 `enumerate` |
| C | **`await_runtime` 启动门禁被弃用** | 实验分支有(防"CLI 没装/没登录就开始吃消息")。PR 3 是 fake session 不需要,**真 CLI 一进来立刻变成硬需求**,且失败方式会一模一样:首轮基线把积压全 ack、事后登录不补跑、房间里毫无提示 |

### 6.2 已知缺口(记账,不阻塞)

| # | 缺口 |
|---|---|
| D | **无 backfill**(G-6):长时间离线后重连,超出 timeline limit(100)的历史提及被静默跳过。实验分支有整套实现 + 5 个测试可近乎照搬,约 0.5 体量,若立项即 C4 |
| E | `turn_count` 只统计产出了 outcome 的回合(失败与超时不计) |
| F | `join` 失败无独立测试(路径与 sync 失败同构,都走 `_RoomTrouble` → 退避 → cursor 不动) |
| G | `observation_id` 在 `supervisor._note` 与 `outbox` 各派生一次(同源同值;写行时以 outbox 为准),若哪天 outbox 改派生方式两处会静默分叉 |
| H | CLI 的第二实例测试若锁逻辑回归会**挂死而非失败**(application 层同名测试有 5s 超时兜底,CLI 层没有等价手段) |
| I | `MatrixRoomAdapter.start()` 不可重入(重复调用泄漏前一个 client);port 语义未要求 |
| J | `RoomInvite` 无房间名字段;`origin_server_ts` 非 int 时归 0(排序退化为到达序,不是拒绝) |
| K | `tests/api` 里的 `from integrations.agentteams.fakes import ...` 是**全套件第一个跨目录测试 import**,依赖 pytest prepend importmode(已实测可行,是裁决 006 §7 规定的布局) |
| L | `ExternalWorkerProvisioner` port docstring 仍点名 adapter 专有的 `AgentTeamsConflict`(翻译已在 composition root 落实;第二个 provisioner 出现时应改成模块拥有的冲突契约) |

### 6.3 活体环境发现的问题(非代码)

| # | 问题 |
|---|---|
| M | **external worker 没有凭据交付点**:controller 把 `WORKER_MATRIX_TOKEN` 注入容器 env,而 `containerManaged:false` 没有容器;kine registry 里**根本没有 `/registry/secrets`**。解法见 §7.3(appservice login),不需要改 controller |
| N | **AgentTeams controller 的 `DELETE` 返回 204 但资源不消失**:team 删两次后 `phase` 仍是 `Active`,其成员随之 409 拒删。用户环境本就有多个同类遗留 |
| O | **external leader 同样卡在 `invite`**:需要手动 join 才能发消息。印证"一 Worker 一 Bridge"——每个 external 成员都需要自己的 Bridge |
| P | **RepoMesh provisioning 会用自己的投影覆盖 worker 的 runtime/skills**(用 controller 建的 `repomesh-runner`/`task-execution`,PUT 之后变成 `copaw`/`coding`)。RepoMesh 是权威,行为正确,但读 controller 状态时要知道 |
| Q | **Docker Desktop 的 socket 复发性损坏**:`%LOCALAPPDATA%\Docker\run\userAnalyticsOtlpHttp.sock` 无法 stat 也无法删除,导致引擎起不来。修法 = 停进程 + 整个 `run` 目录改名重建。该目录下已积累 8-12/14/15/26/27 五次残留 |

---

## 7. 活体验收(阶段 1 出口判据)

### 7.1 两轮验收

**第一轮(Bridge 全栈对真 Matrix,preflight 用替身)**、
**第二轮(真 preflight 全链路,全生产装配)** —— 两轮都通过。

### 7.2 四条计划验收的真机证据

| 计划验收原文 | 活体证据 |
|---|---|
| 首轮不执行历史消息 | `baseline established: cursor=set skipped_events=0`,零次 respond、零条发出 |
| 同一 event 重放只产生一个回合 | 手动把 cursor 回退到回合前,重启后日志无第二次 `answering`,房间无第二条回复 |
| send 后 crash 再启动不产生重复房间消息 | 把 outbox 行的 `sent_event_id` 清空模拟"发了没确认",重启后 `PUT .../send/.../rmb-17d4d2c784e2de32fdec6f29b70fc4eb05aa052c` —— **完全相同的 txn**;房间消息数 `BEFORE=2 AFTER=2` |
| 只响应 allowlist 房间内的明确提及 | filter 把 confirmed 房间下推服务端;`m.mentions` 触发回合;自己的消息不回声 |

**额外真机证据**:

- **同 txn 重发返回完全相同的 event_id**(`$uOzm4q-MJVrJBfHOGY6IkwoFE61Vt7JM2ZHXDYhZGyg`)—— 服务端去重
  从 MockTransport 断言变成 homeserver 实际行为。
- **`limited: true` 在真环境确实出现** —— G-6"只记不回填"的裁决在这个部署里会被触发,不是理论情况。
- **PR 1 顺延的真机 smoke 达成**:external worker 有 Matrix 身份、有房间、**无容器**。
- **「本地 agent 进不了 Matrix 房间」那条裁定被修复证明**:Bridge 看到 invite 后**自己 join 了房间**
  (此前控制器只 invite、没人 join)。

### 7.3 真 preflight 全链路(第二轮,全生产装配)

```
一次性 postgres:17 @15546 → alembic upgrade head(含 0036)
 → seed:admin 本地账户 + org leader → repository leader → worker 三级 principal
        (worker 的 agentteams_resource_name = repomesh-preflight-probe)
 → uvicorn 8077(REPOMESH_AGENTTEAMS_CONTROLLER_URL 指向 forwarder 18090)
 → PUT /api/v1/runtime/external-workers/{id}   → HTTP 200 {containerManaged:false}   ← WO-S2 首次真机
 → GET .../binding(worker 尚未入 team)         → HTTP 409 "belongs to no Team"       ← 正确 fail-closed
 → GET .../binding(无 token)                   → HTTP 401
 → controller 建 external leader + team,worker 入队
 → GET .../binding                              → HTTP 200 + 完整 binding.v1,两个 allowedRoomIds
 → repomesh-agent-bridge check                  → exit 0(凭据只打印槽位名)
 → repomesh-agent-bridge run                    → "bridge ready ... rooms=2",自己 join 了 @admin 邀请的 team room
 → team leader 在真 team room @ 它              → "answering <event> (new)" → 房间出现回复
```

房间里的最终画面:

```
@repomesh-preflight-leader | @repomesh-preflight-probe:... status check from the team leader
@repomesh-preflight-probe  | [note] I am in this room and I can hear you, but this build cannot run a coding session yet…
```

### 7.4 拿 external worker 的 Matrix token(§6.3 M 的解法)

```bash
# as_token 来自 controller 容器 env AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN
curl -X POST -H "Authorization: Bearer $AS_TOKEN" -H "Content-Type: application/json" \
  -d '{"type":"m.login.application_service","identifier":{"type":"m.id.user","user":"<worker-name>"}}' \
  http://127.0.0.1:18080/_matrix/client/v3/login
```

不改 controller、不改 Bridge,符合 ADR R2「操作者人工放置 token」。
同一 as_token 还能注册辅助用户:`POST /_matrix/client/v3/register?kind=user`,
body `{"type":"m.login.application_service","username":"..."}`。
(普通注册会被 `M_EXCLUSIVE` 拒 —— appservice 占了用户名 namespace。)

### 7.5 验收留下的痕迹

**已清理**:一次性 postgres、两个 socat forwarder、后端与 bridge 进程、所有本地 token/enrollment/state 文件。
**留在环境里**(external = 零容器开销,且因 §6.3 N 删不掉):

- controller 资源 `repomesh-preflight-{probe,leader,team}`
- Matrix 房间 `!93OVgSvRdcMfTL3Mjk`(第一轮)、`!yGAJkjTmysYlBB1NFL`(第二轮 team room)
- Matrix 用户 `@repomesh-bridge-tester`、`@repomesh-bridge-probe`、`@repomesh-preflight-*`(Matrix 用户删不掉)

**工作区**:无任何代码改动(验收全程只读 + 环境操作)。

---

## 8. 环境信息

### 8.1 当前端口全表(2026-08-27 实测)

| 端口 | 服务 | 说明 |
|---|---|---|
| **18080** | **Matrix client-server API** | conduit,**内置在 agentteams-controller 容器里**,controller 已发布到宿主。**这就是 Bridge 的 `matrixHomeserverUrl`** |
| 18001 | agentteams 某 Web UI | HTML |
| 18088 | agentteams 某 Web UI | HTML |
| 13000 | agentteams-dashboard | |
| 18888 | agentteams-manager | |
| 9000 | `repomesh-minio-forwarder` | 既有 socat forwarder(→ controller:9000) |
| 55432 | `coagenthub-smoke-pg` | **他线,长期 crash-loop,勿动** |
| 8080 / 3000 / 5432 | multica 栈 | 他线 |
| 8000 | RepoMesh 后端(README 正规起法) | 本次验收未用 |
| 5280 / 8100 | `-live` worktree 的前后端 | **不是主工作树** |
| 5432 | 本机活体 postgres | **谱系与本分支不符,绝不对它跑本分支迁移** |

**容器内端口(未发布)**:conduit `6167`、controller API `8090`、`8443`、`9001`。

### 8.2 端口/环境的历史决策

| 决策 | 内容 |
|---|---|
| **Matrix 入口用 18080,不建 forwarder** | 起初误以为要给 6167 建 socat forwarder;实测发现 `server_name` 就是 `matrix-local.agentteams.io:18080` 且 controller 已发布 18080,forwarder 是多余的,已删除 |
| **controller 8090 必须建 forwarder** | 后端跑在宿主机时需要它;8090 未发布。本次用 `socat` 转到 `127.0.0.1:18090`,验收后已删除。**下次要重建**:见 §8.4 |
| **迁移只对一次性 postgres 跑** | 本机 5432 谱系不符(记忆 `live-postgres-lineage-mismatch`);P1 与本次验收都用 `--rm` 的一次性容器 |
| **socat forwarder 必须覆盖 entrypoint** | `alpine/socat` 默认 entrypoint 是 socat 本身,要 `--entrypoint sh` 才能用 `-c "socat ..."`(照既有 minio forwarder 的形状) |

### 8.3 凭据的真实位置(**`.env` 里的多个已失效**)

| 凭据 | 真实位置 | 状态 |
|---|---|---|
| controller API token | 容器内 `/var/run/agentteams/cli-token`(env `AGENTTEAMS_AUTH_TOKEN_FILE`,641 字节) | **有效** |
| `/data/agentteams-controller/admin-token` | 65 字节 | **不是 API token,认不了** |
| `.env` 的 `REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN` | | **已失效**(栈重建过没同步) |
| `.env` 的 `REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN` | | **已失效**(`M_UNKNOWN_TOKEN`) |
| appservice as_token | controller 容器 env `AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN`(64 字节) | **有效**,是拿 worker token 的钥匙 |
| 容器化 worker 的 matrix token | 该 worker 容器 env `AGENTTEAMS_WORKER_MATRIX_TOKEN` | 有效(仅容器化 worker 有) |

### 8.4 复现验收环境的命令

```bash
# 1) controller forwarder(后端跑宿主时必需)
docker run -d --name repomesh-controller-forwarder --network agentteams-net \
  -p 127.0.0.1:18090:8090 --restart unless-stopped --entrypoint sh \
  alpine/socat:latest -c "socat TCP-LISTEN:8090,fork,reuseaddr TCP:agentteams-controller:8090"

# 2) 一次性 postgres + 迁移
docker run --rm -d --name repomesh-preflight-pg -e POSTGRES_PASSWORD=preflight \
  -p 127.0.0.1:15546:5432 postgres:17-alpine
REPOMESH_DATABASE_URL="postgresql+asyncpg://postgres:preflight@127.0.0.1:15546/postgres" \
  .venv/Scripts/python.exe -m alembic upgrade head

# 3) 后端(env 见下)
REPOMESH_DATABASE_URL=... REPOMESH_AGENTTEAMS_CONTROLLER_URL=http://127.0.0.1:18090 \
REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN="$(docker exec agentteams-controller sh -c 'cat "$AGENTTEAMS_AUTH_TOKEN_FILE"')" \
REPOMESH_RUNNER_CONTROL_TOKEN=live-runner-token \
  .venv/Scripts/python.exe -m uvicorn repomesh.bootstrap.app:create_app --factory --host 127.0.0.1 --port 8077
```

seed 脚本(admin 账户 + 三级 principal)见台账;要点:用 `repomesh.bootstrap.app.build_default_container()`
(不是 `ApplicationContainer()`,后者要 10 个位置参数),关闭用 `container.close()`(不是 `stop()`)。

### 8.5 Python 环境

- `.venv/Scripts/python.exe`(Git Bash,Windows;CPython 3.14.0,**uv 管理**)。
- **主 venv 里没有 pip / build / setuptools / wheel** —— 影响任何"就地构建"的假设(见 §5.3 wheel smoke)。
- stdlib `sqlite3` 是 Bridge 的唯一状态依赖;`aiosqlite` 只在 dev extra 里,**不得**成为 Bridge 运行依赖。

---

## 9. 下一步

**PR 4 — 受限本机进程 + Codex 对话**(执行计划估 5–8 人日):

1. `adapters/coding_session.py`:消费 `ProtocolDriver.execute`(`src/repomesh_runner/drivers/base.py:114`),
   一次性只读 workspace + deny-all `PermissionPolicy`。
2. `adapters/restricted_process.py`:实现 Runner 的 `ProcessFactory`,Windows-first 受限身份/ACL/进程树,
   环境变量 allowlist。
3. 只投影 `TEXT` / `SESSION_STARTED`;`THINKING`、`LOG`、原始协议帧不进房间。
4. **降级规则**:当前 OS 没通过 isolation probe 时,真实 Codex 模式拒绝启动,只允许 fake ——
   这比把空目录 + deny-all 误称为"完全无写权限"更诚实。
5. 一并裁决 §6.1 的 A/B/C 三笔账。

**红线**:`src/repomesh_runner/**` 零改动(方案 (a));发现能力缺口才升级方案 (b) 并补 ADR 附录。

平行轨 P 的剩余项:**WO-P3**(mock Runner 镜像构建 + 活体诊断)、**WO-S3**(真机 smoke 服务端准备)——
Docker 已可用,但均需逐条批准环境操作。materialize 的活体验收(`handoff_doc_ids` 非空 + 无降级 warning)
仍未走过,需要全栈 + LLM。

---

## 10. 接手须知(踩过的坑)

1. **判定一批工作区改动的归属,先全仓扫描再下结论**:前缀线曾被误判为 4 个文件,险些半丢(实际 21 个)。
2. **`tests/conftest.py` import `repomesh.bootstrap`**,任何 module 层符号缺失会让全套件挂起;
   契约测试可用 `--noconftest` 独立跑,但那不能替代门禁。
3. **工作区常年有他线未提交文件**(`.github/workflows/ci.yml`、`docs/architecture/*.html`、
   `docs/development/` 若干分析文档、`tests/integration/test_runner_gateway_postgres.py`)——
   不读作依据、不改、不删、**不 stage**。提交一律按路径 stage。
4. **子代理施工的纪律**:禁一切 git 写操作,文件归属互不相交,报告里贴 `git status --short` 原文与
   验证输出尾 3 行。本线两次遇到子代理在交报告前被杀(看门狗 / 会话重启),
   **代码其实已完整落盘** —— 先查工作区再决定重开,别浪费已完成的工作。
5. **Windows 细节**:CRLF 警告是常态;`msvcrt.locking` 与 `fcntl.flock` 都是 per-handle(所以互斥测试能
   在同进程开两个句柄);bash 里 `\r` 之类的转义会被吃掉(写记忆/文档时注意)。
