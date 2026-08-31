# Room-Native Bridge 终局验收 Spec 与执行计划

> 日期:2026-08-28
> 状态:**Spec 已按评审校正;Plan 待开工**
> 验收口径:[room-native-bridge-final-acceptance-standard-20260827.md](../room-native-bridge-final-acceptance-standard-20260827.md)(冻结,范围冲突以它为准)
> 当前基线:[room-native-bridge-handoff-20260827-pr5.md](../room-native-bridge-handoff-20260827-pr5.md)(PR 0–5 + 平行轨 P 全部收口)
> 并行编排:[room-native-bridge-parallel-orchestration-plan-20260828.md](room-native-bridge-parallel-orchestration-plan-20260828.md)(本计划的日历压缩配套,依赖真伪判定与波次编排以它为准)
> 现状证据:2026-08-28 三路全仓核查(Leader 链 / Room 读模型与前端 / 交付链),本文所有 file:line 均为当日核实
> 分支:`feat/room-native-agent-bridge` 续行;同名 `.html` 是本文可视化版
> 术语:本文用 module / interface / seam / adapter / 深度(depth)的固定语义,见 codebase-design 词汇表

## 0. 一句话

终局验收要求「六个本地 Codex(3 Leader + 3 Worker)走通前端 Issue → 三仓分析计划 → Leader 生成本仓 Spec/DAG 并派活 → Worker 受治理执行 → Leader 基于证据审查汇总 → 三个真实 GitHub Draft PR,且前端全程可见」;对照代码实态,缺口 = **一个开工前契约校正阶段(External Leader 接入与 leader-mode 激活)+ 三个归属既有 module 的能力切片(Leader 决策面、Matrix 时间线摄取、External 运行形态读模型)+ 两条 FAIL 红线接线(自动接单、Q5 收窄)+ 一批从未活体触发的配置开关**,合计约 30–45 人日,组织为 **PR 5.5A/B + PR 6–10 + 平行轨 Q + 环境轨 E + 活体轨 V**。

---

## 1. Spec — 现状实态(已核实,四类)

### 1.1 A 类:已达成,验收时只需取证

| 能力 | 证据 |
|---|---|
| 发现链前半段全链(前端建 Issue → 需求分析 → 扫描评分 → 三档 → 范围确认 → 计划 → materialize → 自动建团建房) | `frontend/src/components/NewIssueModal.tsx` / `DiscoveryPanel.tsx` / `DiscoveryApproval.tsx`;后端 `repository_intelligence/api/discovery_chain.py:299-577`;范围确认硬门 `:399-408`(未审批 409) |
| 角色边界(只有 WORKER 进 Runner 执行路径) | 四道独立硬校验:`integrations/runner/worker_execution.py:118-124`、`modules/specification/materialization.py:106-108`、`modules/identity_access/policy.py:90-128`、`modules/agent_runtime/application/external_worker.py:186` |
| Bridge Worker 形态(在场/对话/thread 恢复/受限进程/deny-all) | PR 4 活体验收(PR 4 交接 §6) |
| 受治理执行本体(八条治理验收) | PR 5 全自动化通过(PR 5 交接 §2) |
| 前端「无数据不显故障」「不伪造 uptime/ready」「投影不冒充发言」「Room 只读」 | `frontend/src/display.ts:330-352`、`AgentsPage.tsx:82`、`RoomView.tsx:60-83,439-441` |
| 一仓一 worker 粒度恰好满足最低编制(3 仓 × 1 = 3 Worker) | `modules/project/application.py:188` |
| AC-07 底线机制(BLOCKED 落态、redispatch、in-flight 复用) | `integrations/runner/worker_execution.py:59-84`、`api/round_dispatch.py:101-127` |

### 1.2 B 类:代码齐全,但从未活体触发

| 环节 | 断点 |
|---|---|
| 治理执行链(Bridge 自带 consumer) | 八条验收只有自动化证据;§6.5 执行面活体用的是 mock Runner 容器,不是 Bridge 的组合方式 |
| ChangeSet → 推分支 → Draft PR | 唯一触发点被 `delivery_auto_enabled` 门控(`bootstrap/container.py:1597-1602`),默认 false 且 `.env` 未配;翻开关还需 `delivery_required_checks` 非空 + `required_approvals ≥ 1`(`container.py:1428-1431`),GitHub 凭据未配则 `container.py:1346-1347` 直接 RuntimeError |
| 需求分析 / 三档分类 / 计划集成 | 硬依赖 LLM,本机 `.env` 无 `REPOMESH_MODEL_API_KEY` → 必 503(`repository_intelligence/api/router.py:500-505,546-549,614-620`) |

### 1.3 C 类:缺失(验收的真实工程量)

| # | 缺口 | 核实结论 |
|---|---|---|
| C-A | **Leader 工作流**(AC-02 前半) | 今天的 Repository Leader 是「一个身份 + 一个收件箱」:派活消息发进 leader DM 后服务端**不等回**就在同一函数里同步拆解直派(`task_orchestration/application.py:1039-1045`);Spec/DAG/allowed paths/测试命令/指派全部由服务端代办;**不存在任何 leader 可调写入面**(唯一 MCP 工具 `start_assigned_task` 是 worker 专用,`api/worker_mcp.py:103-119`);审查被证据门顶掉(`integrations/scm/plan_delivery.py:242-340`)、汇总被自动 roll-up 顶掉(`application.py:1047-1072`) |
| C-B | **Matrix 真实消息 → Room 读模型**(AC-06) | Room 页气泡只来自 RepoMesh 自己发的消息(`collaboration.messages` 唯一写入者 = 出站派工 `collaboration/application.py:123`);已有 sync 轮询只认 `agent-report.v1` JSON,其余**一律丢弃不落库**(`collaboration/application.py:309-311`)。现有 `InboundMatrixMessage` 还缺 Matrix 原始时间戳(`collaboration/contracts.py:65-70`),既有 verifier 只能验证给定 principal,不能做 matrix user → principal 反向解析(`integrations/agentteams/identity.py:5-16`) |
| C-C | **External 运行形态展示**(AC-06) | `containerManaged` 只活在写侧,读模型层被丢弃(`bootstrap/container.py:1008-1018`,`api/read_models/sources.py:180-193` 无此字段),前端零感知;external 成员现状显示容器词汇 `运行时 · Pending`(controller `member_reconcile.go:1793-1801` 默认分支)——**正撞 AC-06 FAIL 条款** |
| C-D | **Worker 自动接单**(AC-03,FAIL 红线) | 平台派活正文自带 `{"task_id":...,"worker_agent_id":...}`(`application.py:545-555`),Bridge 只认 `start task <uuid>`(`supervisor.py:147`),不匹配 → 必须人工手敲 |
| C-E | **六实例编排** | 隔离机制已备(enrollment/state/codex-home/ledger 按 `worker_agent_id` 分目录,`cli.py:310-311`;instance lock),缺六份 enrollment、六个 token、六个身份开通与启动编排;「共享 Codex 登录态」无机制(每 worker id 各一个 codex-home) |
| C-F | **PR 正文追溯偏薄** | 主路径 PR body 只带 plan id(`plan_delivery.py:437-461`);协调补建路径反而写全 change_set/repository/branch/commit(`delivery.py:646-675`),可照抄 |
| C-G | **External Leader 接入与激活**(AC-01/02) | `ProvisionExternalWorker` / binding preflight 只接受 `AgentRole.WORKER`(`agent_runtime/application/external_worker.py:171-191`),room allowlist 也只按 team room + worker DM 建模(`:149-168`);Repository Leader 即使拿到 enrollment v2 也会被服务端 409。且 D-2 所说「显式 leader 模式」目前没有产品激活路径 |

### 1.4 D 类:未决裁决(不裁则有验收风险)

| # | 事项 | 风险 |
|---|---|---|
| D-Q5 | 房间 `agent-report.v1` JSON 可把自己的 task 写成 SUCCEEDED(`collaboration/application.py:291-353`) | AC-04 口径下,一条伪汇报即可让 coding task 假成功 → 交付门拒发 PR → 终局 FAIL |
| D-mock | 容器 coding agent 工厂默认注入 mock(`bootstrap/app.py:406`) | 验收 §3.1 禁止 fake/mock;须确认六成员执行全部走 Bridge consumer,该工厂在验收路径上不被触达 |

---

## 2. Spec — 目标架构:三个能力切片的 interface 设计

设计总纲:这里是三个**归属既有 module 的能力切片**,不新建三个浅 package。它们分别归 `task_orchestration`、`collaboration`、read-model/runtime integration 所有;外部 interface 各自保持 1–3 个操作,全部复杂度(校验、时序、幂等、身份映射)藏在 implementation 里;每个外部 seam 至少两个 adapter(生产 + 测试)才算真 seam。**既有红线不动**:`src/repomesh_runner/**` 零改动;`contracts/agent-bridge/v1` 三 schema 不改字段(需要新字段的走 v2 新文件,v1 原文保留供旧 Worker 形态使用);房间只收 `room-observation.v1` 投影。

### 2.1 能力切片一:`LeaderDecision`(归 `task_orchestration`)—— 全案最重

**问题的本质**:不是「给 leader 一个 CLI」,而是服务端没有 leader 侧的写入面,且派活时序是同步直拆。

**Seam 放置**:application 层新 use case 一组,HTTP 暴露在 `/api/v1/agent-actions/leader/*`(与 Bridge 已用的 `start-worker-task` 同一 agent-actions 面,同一鉴权机制)。**不做 leader MCP server**——六成员都是 external Bridge,无容器无 mcporter,MCP 通道今天只有零个消费者;一个 adapter 的 seam 是假 seam,等容器 leader 真需要时再包一层。

**Interface(仍然只有三个操作,但产物归属必须真实)**:

```text
GET  /agent-actions/leader/assignments/{taskId}
     → RepositoryAssignmentPackage:
       phase=planning 时返回仓库级任务、发现/依赖证据、worker 名册、
       allowed-paths/测试安全包络与非权威提示;
       phase=review_due 时返回所有 worker Task/Run/commit/diff/测试证据

POST /agent-actions/leader/assignments/{taskId}/plan
     body: RepositoryPlanDecision {
       engineering_spec, task_dag, worker_tasks[assignee, instruction,
       allowed_paths, tests]
     }
     → 服务端校验/clamp 后持久化 leader 产物,创建并派发 worker task
     幂等键 = leader task id(重复提交返回既有结果)

POST /agent-actions/leader/assignments/{taskId}/review
     body: {verdict: approve|request_rework|escalate, summary, findings[]}
     → approve 使 leader task SUCCEEDED(汇总正文来自 summary)
       request_rework 通过正式 AssignTask + execution permit 创建新修订任务
       escalate 使其 BLOCKED 并触发既有 EXCEPTION_ESCALATION checkpoint
```

**Implementation 藏起来的复杂度**(caller 一概不用知道):

1. **时序改造**:`_assign_batch`(`application.py:1014-1045`)按 team 的拆解模式分叉——`server` 模式保持今天的同步直拆(默认,存量测试全绿);`leader` 模式在派出 leader task 后**停**,worker task 由 decomposition 提交时创建。批次推进天然衔接:worker 终态照旧触发 advance。
2. **产物归属与夹具(clamp)**:Engineering Spec、DAG 与 WorkerTask 必须来自 leader Codex 会话并带 leader task/thread provenance;服务端只给事实输入、安全包络与非权威提示,不能代写最终产物。提交后校验 assignee ∈ 该 team workers、DAG 无环且节点覆盖 worker tasks、allowed paths ⊆ (worker responsibility ∪ repo test paths)(复用 `application.py:700-736` 的推导)、测试命令不可删除。今天的 `DecomposeRepositoryTask` 降级为包络/提示生成与 clamp 内部实现,不再冒充 leader 决策。
3. **审查门**:workers 全终态后 leader task 不再自动 roll-up(`application.py:1047-1072` 在 leader 模式下改为进入 review-due,并向 leader DM 发审查通知);同一个 GET 在 review_due 阶段返回不可变证据包(Task/Run/commit/diff/tests)。leader task 只能经 review 提交转终态;`request_rework` 不篡改已终态 Worker Task,而是按 review revision 幂等创建新子任务。交付门(`plan_delivery.py:242-349` 要求 leader task SUCCEEDED)因此自动等待审查——**零改动继承**。
4. **鉴权**:复用 PR 5 的 worker token 机制——`REPOMESH_RUNNER_WORKER_TOKENS` 的 map 语义推广为「external 成员 token」(环境变量名不动,历史名),leader 端点从 token 派生主体、按 role=REPOSITORY_LEADER 校验;伪造他人 taskId → 403,与验收 2/7 同一套论证。
5. **AC-02 反向封锁不动**:leader token 调 `start-worker-task` 依旧被 `worker_execution.py:121` 拒。

**测试面 = interface**:`tests/api/test_leader_actions.py` 全部穿 HTTP 契约;必须覆盖 leader 产物 provenance、DAG 无环/覆盖、review evidence 完整性、request_rework 幂等;时序改造走 `AdvanceExecutionPlan` 既有测试面加 leader 模式分支用例。

### 2.2 能力切片二:`RoomTimelineIngest`(归 `collaboration`)

**Seam 放置**:不开新连接——挂在**既有** inbound seam 上。`AgentTeamsMatrixInboundPoller`(`integrations/agentteams/inbound.py:15-77`)已经在 long-poll `/sync`;poller 把带 `origin_server_ts` 的消息分别交给 `RecordRoomTimeline` 与既有 `ProcessMatrixTaskReport`。写入和读取 interface 都定义在 `repomesh.modules.collaboration.contracts`,API read model 不直接查询 collaboration schema。

**Interface(两个操作,分别服务写入与读模型)**:

```text
record(command: RecordRoomTimelineCommand, idempotency_key=event_id)
    → RoomTimelineEntryView
list_room(room_id, after, limit)
    → tuple[RoomTimelineEntryView, ...]
```

生产使用 PostgreSQL adapter,测试使用 memory adapter。`AgentTeamsMatrixInboundPoller` 是写 caller,API read model 是读 caller;跨模块只依赖 collaboration contracts。唯一前端变化仍是 `room_stream` 多出 `source:"matrix"` 条目(`api/read_models/service.py:1056-1158` 合并)。

**Implementation 藏起来的复杂度**:

1. 新表 `room_timeline_messages`(迁移 `20260828_0037_*`,链尾现为 0036):event_id 唯一键去重,持久化 Matrix `origin_server_ts` 并以 `(occurred_at,event_id)` 稳定排序(复用 `processed_matrix_events` 的思路但独立表——两种消费语义不共享游标)。
2. 身份映射:新增 `MatrixIdentityResolver` port 做发送者 matrix user → AgentPrincipal 反向解析;生产 adapter 组合 Agent Directory + AgentTeams control plane,测试用 memory adapter。现有 verifier 保持其「验证已知 principal」单一职责,不滥用为 resolver。映射不到的**如实存 raw matrix_user_id**,前端按未知发送者渲染——AC-06 要求「不显示成错误身份」,如实的未知优于猜测。
3. **出站去重**:RepoMesh 自己发的消息也会出现在 sync timeline,与 `collaboration.messages` 的气泡重复。现有 `SendCollaborationMessage` 已经落库 Matrix event_id(`collaboration/contracts.py:45-53`),PR 9 不重复改发送契约;`room_stream` 合并时按 event_id 去重,timeline 让位于出站记录(出站记录带业务语义,timeline 只是回声)。
4. 只摄取授权房间(团队 room / leader DM),白名单来自拓扑,不做全量镜像。

**前端零逻辑新增**:5 秒轮询已就位(`frontend/src/api/rooms.ts:48`),气泡渲染判据 `message !== null` 已就位(`RoomView.tsx:81-83`)——缺的从来只是数据。

### 2.3 能力切片三:`ExternalRuntimePresentation`(read model 数据流修复)

**这不是新 seam,是修一条被打断的数据流**:`containerManaged` 在 controller 应答里有(`resource_handler.go:723`),在 probe adapter 构造 `RuntimeSnapshot` 时被丢弃(`bootstrap/container.py:1008-1018`)。

改动链(每层最小增量):

1. `RuntimeSnapshot` 增 `container_managed: bool | None` + `coding_profile: str | None`(`api/read_models/sources.py:180-193`);probe adapter 透传。
2. `_agent_runtime_fields`(`service.py:2097-2113`):`container_managed is False` 时 runtime 块产出 `{kind:"external", profile:...}`,**不透传 controller phase**(`Pending` 是容器生命周期词汇,对 external 是谎言)。
3. 前端 `runtimeDisplay`(`display.ts:330-352`)加 external 分支 → 文案 `External · Codex`;`RuntimeBadge` 中性色;Teams 成员芯片加 external 标记。
4. 没有的事实继续显示「未接入 / —」——这部分现状已合格,不动。

### 2.4 Bridge 侧两处增量(不新增 module)

1. **自动接单(补 C-D)**:`supervisor` 分派路径(`supervisor.py:510`)在 `governed_task_id` 之前加 `assignment_directive(prompt)`——解析派活正文里的 JSON,`worker_agent_id` 等于自己才接(别人的派活是别人的),随后进**同一个** `_start_governed`:同一幂等键(trigger event)、同一 `GovernedTaskPort`、同一叙事。`start task <uuid>` 保留为人工触发口。安全性不变的论证写进代码注释:房间消息只是唤醒,Task/assignee/权限全由 RepoMesh 复核(冻结契约的信任模型,与 PR 5 验收 2 同一条)。
2. **Leader 轨(消费能力切片一)**:enrollment v2 增 `role: worker|repository_leader`(v1 `additionalProperties:false`,加字段必然 v2;v1 原文保留)。role=leader 时:CLI 拒绝 `--workspace-root`(AC-02 纵深防御第一道);supervisor 识别 leader 派活正文(正文 B)与审查通知,走新 `LeaderActionPort`(fetch_assignment / submit_plan / submit_review,HTTP adapter + memory fake 两个 adapter);协调会话是**纯文本事实包 → 结构化 RepositoryPlanDecision / RepositoryReviewDecision**,不给 repo workspace(leader 不碰代码,连只读都不给——最小知情面);计划/审查结果 POST 后在房间叙事一条 note(展示,非真相)。

### 2.5 通信与真相源(全案不变式,逐条对 AC)

```text
Matrix 房间 = 唤醒 + 展示          RepoMesh = 唯一真相源
─────────────────────────────────  ─────────────────────────────
派活通知(正文 A/B)→ 唤醒 Bridge    Task/assignee/权限/终态校验
leader 拆解/审查   → HTTP 决策面    夹具校验 + 状态机推进
worker 执行叙事    → observation    Runner events 推进终态
房间文本"完成"     → 永不推进 Task  (Q5 收窄后 coding task 全关)
```

---

## 3. 裁决(D-1 … D-12,评审通过即冻结)

| # | 裁决 | 理由 |
|---|---|---|
| D-1 | Leader 决策面走 HTTP agent-actions,**不建 leader MCP server** | 六成员皆 external,MCP 通道零消费者;一个 adapter 的 seam 是假 seam |
| D-2 | 拆解模式 per-team 二值 `server\|leader`,默认 `server`;Materialize 采用已绑定的 external Repository Leader 时由正式拓扑 use case 持久化为 `leader`,并在 Team 读模型显示 | 存量行为与测试零破坏;不靠脚本/数据库写业务状态;验收前可从前端确认模式 |
| D-3 | 复用现有出站 Matrix event_id,`room_stream` 按 event_id 去重,出站记录优先 | `CollaborationMessageView.event_id` 已存在,不重复改发送契约;业务语义优于回声 |
| D-4 | 身份映射不到的 timeline 消息如实存 raw matrix_user_id | AC-06 禁错误身份;如实未知不是错误 |
| D-5 | enrollment/binding 升 **v2 新文件**增 `role`;server provision/preflight 与 Bridge 同步理解 v2,v1 原文不动 | v1 冻结 + `additionalProperties:false`;只改 Bridge 会让 Repository Leader 在服务端 409 |
| D-6 | `REPOMESH_RUNNER_WORKER_TOKENS` 语义推广为 external 成员 token map,环境变量名不改 | 改名破坏兼容;历史名 + 文档注记,成本最低 |
| D-7 | **Q5 收窄**:assignee 为 WORKER 且任务带 published 任务包的,`ProcessMatrixTaskReport` 一律 IGNORED(落审计) | coding task 真相只走 Runner events;leader task 房间上报保留为 PR 7 落地前的既有路径,PR 7 后由 review 提交取代 |
| D-8 | Leader 协调会话不给 repo workspace(纯文本工作包) | AC-02 纵深防御;最小知情面 |
| D-9 | 交付前由 owning application 组装 `DeliveryTraceability(issue_id, change_set_id, plan_id, repository_id, task_id, run_id, worker_agent_id, commit_sha)`,两条 PR 路径共用一个正文生成器 | 验收证据清单第 9/10 条;SCM adapter 不跨模块查询 Task/Run/Issue |
| D-10 | 共享 Codex 登录 = 开通脚本把指定已登录 codex-home 的 `auth.json` 复制进六个成员 codex-home,**不改产品代码** | 验收允许脚本起进程/备环境;产品化的凭据分发是档位 C |
| D-11 | `ExternalWorker` capability 泛化为 `ExternalMember`;只允许 WORKER / REPOSITORY_LEADER,继续拒绝 ORGANIZATION_LEADER;room allowlist 按角色生成 | 先修 server-side provision/binding/preflight,再让 Bridge 消费 role;不让「v2 能解析但身份不能上线」 |
| D-12 | GitHub 自动交付只在隔离的 V2 环境、且只对白名单三仓开启;V1 不开 delivery,验收后关闭 | 避免复用数据库中的无关 active plan 被自动发布;最小化真实外部副作用窗口 |

---

## 4. Plan — PR 与轨道

依赖关系:**PR 5.5A/B → E0a → V1 → PR 6 → PR 7 → PR 8 → M7**;PR 9、PR 10、轨 Q 并行。终局汇合点必须满足 **V2 ← {M7,PR 9,PR 10,Q1–Q3,E1,E0b}**,任一缺失不得开始 V2。

### 环境轨 E(非代码,E0a/E0b 各约半天 + E1 约 2–3 人日)

- **E0a(V1 前置,不接 GitHub)**:配 `REPOMESH_MODEL_API_KEY`(LLM)、单 Worker external identity/Matrix token、对应 `REPOMESH_RUNNER_WORKER_TOKENS`;保持 `delivery_auto_enabled=false`。
- **E1(六实例编排)**:在 V2 目标 organization/repository topology 下,通过正式 provision/onboarding use case 预建三名 Repository Leader + 三名 Worker principal,再 provision 为六个 `containerManaged:false` AgentTeams 成员;生成六份 enrollment、复制 auth.json(D-10)、启动/停止脚本(PowerShell,按 PID 收尾)。先做一 Leader + 一 Worker 子集供 M7 smoke,再扩成六实例。Materialize 前必须只读预检六个 binding 与预期 principal/resource name 一致,确保采用 external 成员而不是新建 managed 成员。
- **E0b(仅 V2 前短时开启 GitHub 交付)**:使用隔离 organization/数据库(或证明无无关 active plan),GitHub App/PAT 仅授权三个 `catbobyman/repomesh-e2e-*` 仓库;再设置 `delivery_auto_enabled=true`、非空 `delivery_required_checks`、`required_approvals ≥ 1`。这里的 approvals 是 eventual merge 元数据,不增加 Draft PR 前的人工作业门。V2 取证完成后关闭 delivery。

### PR 5.5 — 开工前契约校正(估 3–5 人日;必须先于 PR 6–10)

#### PR 5.5A — ExternalMember provision / binding v2(owning module: `agent_runtime`)

| 文件 | 改动 |
|---|---|
| `contracts/agent-bridge/v2/` | enrollment/binding v2 增 `role`;v1 原文与 round-trip 测试不动 |
| `src/repomesh/modules/agent_runtime/application/external_worker.py` + contracts/ports | use case 泛化为 role-aware external member;允许 WORKER / REPOSITORY_LEADER;按角色解析 authoritative rooms |
| runtime provision/binding API + composition root | v2 server 契约、preflight、错误矩阵;继续拒绝 Organization Leader |
| tests | Repository Leader 200;Organization Leader 409;身份/role 不一致 409;leader/worker room allowlist 精确断言 |

#### PR 5.5B — external leader adoption 与 leader-mode 激活(owning module: `project`)

| 文件 | 改动 |
|---|---|
| project topology contracts/application/persistence | Materialize 采用已绑定 external Repository Leader 时持久化 `decomposition_mode=leader`;managed/未绑定保持 `server` |
| read models/frontend Teams | 只读展示 decomposition mode 与 external role,使操作者在前端可核对 |
| tests | 已预建 external leader 被 adoption、不会创建 managed 替身;模式激活幂等;无 external leader 的存量路径不变 |

**PR 5.5 总验收**:三名 Repository Leader 可以完成 provision + binding preflight;一组测试 topology Materialize 后前端显示 external leader 与 `leader` mode;所有 v1 Worker 契约和存量 server 模式测试不变。

### 活体轨 V

- **V1 — 治理路径活体 E2E**(PR 5 交接 §7.1 原案,估 2–4 人日):单 Worker 全链,验收对账三处(worktree 真改码真测试真提交 / 房间 run lane 四条且终态纯 evidence / runner events 与 rollout 一致)。**它是 §1.2 B 类转实证的唯一手段,先于一切新功能。** 环境坑沿用:`MSYS_NO_PATHCONV=1`;控制面与 Bridge 同跑 Windows 宿主(uvicorn)。
- **V2 — 终局三仓验收**(估 3–5 人日 + 排障余量;硬前置 = M7 + PR 9 + PR 10 + Q1–Q3 + E1 + E0b):按验收标准 §5 产品链 + §9 证据清单十条逐项收证;PASS 结论用标准 §8 的推荐原文。三个 Draft PR 产生后立即关闭 delivery 开关。

### PR 6 — Worker 自动接单(估 1–2 人日;C-D,FAIL 红线)

| 文件 | 改动 |
|---|---|
| `src/repomesh_agent_bridge/supervisor.py` | `assignment_directive()` 解析派活正文 JSON;分派顺序 = 派活指令 → `start task` 命令 → 会话轨;两入口共用幂等键与 `_start_governed` |
| `tests/agent_bridge/test_governed_wakeup.py` | 新用例:真实正文 A 触发 start;他人 worker_agent_id 忽略;同 trigger 双入口只 start 一次;畸形 JSON 落会话轨 |

**验收**:AC-03 四条全绿;用 `_assignment_body` 生成的**逐字正文**做 fixture(防措辞漂移假绿)。

### PR 7 — LeaderDecision 服务端(估 7–10 人日;依赖 PR 5.5B + PR 6 后合入,可与 PR 9/10 并行开发)

| 文件 | 改动 |
|---|---|
| `src/repomesh/modules/task_orchestration/application.py` | `_assign_batch` 按拆解模式分叉;leader 模式下 roll-up 改 review-due;`SubmitRepositoryPlan` / `SubmitRepositoryReview` use case;rework 创建新 revision child task |
| `src/repomesh/modules/task_orchestration/contracts.py` | `RepositoryAssignmentPackage`、`RepositoryPlanDecision`、Engineering Spec/DAG/WorkerTask drafts、`RepositoryReviewDecision`、完整 worker evidence view |
| task-orchestration persistence/migration | 持久化 leader plan provenance、DAG、review phase/revision/findings;不把这些状态塞进 project schema |
| `src/repomesh/api/agent_actions.py`(或既有路由文件) | 三个 leader 端点;token 派生主体 + role 校验 |
| `src/repomesh/bootstrap/container.py` | 组装;`REPOMESH_RUNNER_WORKER_TOKENS` 语义推广(D-6) |
| `tests/api/test_leader_actions.py` + `tests/test_*` | HTTP 契约矩阵(401/403/404/409/200)+ 时序用例 |

**验收**:server 模式存量测试零破坏;leader 模式下不提交 plan 则 worker task 不产生;最终 Spec/DAG 带 leader provenance;夹具外计划(有环/缺节点/越权 path / 非本团队 assignee / 删测试命令)409;review_due 包可追到每个 Worker Task/Run/commit/diff/tests;审查 approve 前交付门不取候选;request_rework 幂等产生新修订任务;leader token 调 `start-worker-task` 仍 403(AC-02)。

### PR 8 — Bridge Leader 轨(估 4–6 人日;依赖 PR 7)

| 文件 | 改动 |
|---|---|
| `contracts/agent-bridge/v2/` | 消费 PR 5.5A 已冻结的 role-aware enrollment/binding v2,不在 PR 8 临时发明第二份 schema |
| `src/repomesh_agent_bridge/ports.py` | `LeaderActionPort`(三方法) |
| `src/repomesh_agent_bridge/adapters/leader_actions.py` | HTTP adapter(+ tests 的 memory fake = 第二 adapter) |
| `src/repomesh_agent_bridge/supervisor.py` / `cli.py` / `contracts.py` | role 感知:leader 轨识别正文 B 与审查通知;role=leader 拒 `--workspace-root` |
| `src/repomesh_agent_bridge/adapters/coding_session.py` | 协调会话模式(纯文本事实包 → `RepositoryPlanDecision` / `RepositoryReviewDecision`,D-8) |
| `tests/agent_bridge/test_leader_lane.py` | 拆解/审查/越界拒绝/幂等全套 |

**验收**:leader Bridge 收到仓库级派活 → 由 Codex 产出带 provenance 的本仓 Spec/DAG/worker plan → worker task 出现并派发;审查通知 → 读取 worker evidence → verdict 提交 → leader task 终态;真机 smoke 一轮(一 leader 一 worker 两实例)。

### PR 9 — RoomTimelineIngest(估 4–6 人日;owning module: `collaboration`;与 PR 7/8 并行)

| 文件 | 改动 |
|---|---|
| `migrations/versions/20260828_0037_room_timeline.py` | `room_timeline_messages`(event_id 唯一、occurred_at、raw sender、resolved principal、授权 room) |
| `src/repomesh/modules/collaboration/contracts.py` / `ports.py` / `application.py` | `RecordRoomTimelineCommand`、`RoomTimelineEntryView`、record/list interface;PostgreSQL + memory adapters |
| `src/repomesh/integrations/agentteams/inbound.py` | 保留 `origin_server_ts`;poller 顺序喂 timeline recorder 与 task-report consumer |
| `src/repomesh/integrations/agentteams/identity.py` | 新 `MatrixIdentityResolver` production adapter;现有 verifier 职责不变 |
| `src/repomesh/api/read_models/sources.py` / `service.py` | 只依赖 collaboration contracts/source adapter;`room_stream` 合并 `source:"matrix"` + event_id 去重 |
| `tests/**` | interface 行为、稳定排序/重放、去重、身份反解/未知发送者、授权房间白名单、跨模块 import 约束 |

**验收**:人在 Matrix 发一条话 → 5 秒轮询下约 10 秒内按 Matrix 原始时间出现在 Room 页,身份正确;延迟/重放不乱序;RepoMesh 出站消息不出双气泡;非授权房间消息不落库;API 不直接查询 collaboration schema。

### PR 10 — ExternalRuntimePresentation(估 2–4 人日;与 PR 7/8/9 并行)

改动链见 §2.3(sources.py / container.py / service.py / display.ts / RuntimeBadge / TeamsPage)。
**验收**:external 成员显示 `External · Codex`,永不显示 `Pending`/容器词汇;managed 成员显示不变;`tsc -b` + oxlint 受影响文件 + 浏览器实走(本项目验证方法论:`tsc --noEmit` 是空转桩,不作数)。

### 平行轨 Q(估 2–3 人日;随时可做,V2 硬前置)

- **Q1**:在 owning application 组装 `DeliveryTraceability`(D-9),扩展交付 command/candidate contract 后让 `plan_delivery.py:437-461` 与协调补建路径共用一个正文生成器;SCM adapter 不反查其他 module。
- **Q2**:Q5 收窄(D-7),`ProcessMatrixTaskReport` 对带任务包的 WORKER 任务 IGNORED + 审计落账 + 测试钉死。
- **Q3**:确认 mock coding agent 工厂(`bootstrap/app.py:406`)在六成员验收路径零触达,写进 V2 取证清单。

---

## 5. 里程碑

| 里程碑 | 内容 | 出口判据 |
|---|---|---|
| M4.5 | PR 5.5A + PR 5.5B | Repository Leader external preflight 200;测试 topology adoption 后为 `leader` mode;前端可核对 |
| M5 | E0a + V1 | delivery 保持关闭;单 Worker 治理链活体三处对账全过 |
| M6 | PR 6 + 轨 Q | 自动接单活体验证(人不敲 UUID);Q5 收窄测试钉死 |
| M7 | PR 7 + PR 8 + E1 子集 | 一 leader 一 worker 真机 smoke:Leader 生成 Spec/DAG → 派活 → 执行 → 基于证据审查全链 |
| M8 | PR 9 + PR 10 | Room 页见真实消息;Agents/Teams 页 external 诚实展示 |
| M9 | M7 + PR 9 + PR 10 + Q + E1 + E0b + V2 | 六实例同跑;三个真实 Draft PR;验收标准 §8 PASS;证据清单十条归档;delivery 随后关闭 |

单人合计约 30–45 人日;PR 9/10/Q 可由第二人并行。功能关键路径是 PR 5.5 → V1 → PR 6 → PR 7 → PR 8 → M7;终局 V2 是显式 join,还必须等待 PR 9/10/Q/E1/E0b。

## 6. AC / FAIL 覆盖矩阵

| 验收条款 | 落点 |
|---|---|
| AC-01 六个真实 external 身份 | PR 5.5A 修 External Leader binding + E1 开通/编排 + V2 取证 |
| AC-02 Leader 角色边界与真实决策 | PR 5.5B 激活模式 + PR 7/8 产出 Spec/DAG/审查;后半(WORKER 硬校验)已达成 |
| AC-03 自动接单 | **PR 6**(FAIL 红线) |
| AC-04 受治理执行 | 已实现;V1 转活体;Q2 关掉房间伪汇报口子 |
| AC-05 房间成员与层级 | Worker 已活体;Leader binding/role rooms = PR 5.5A,Bridge = PR 8;层级由 `_route`(`collaboration/application.py:180-195`)+ role-aware allowlist 保证,V2 取证 |
| AC-06 前端匹配 | **PR 9 + PR 10**;其余子项现状已合格 |
| AC-07 异常底线 | 机制已有;V2 若触发则按清单取证 |
| §7 三个 Draft PR | E0b 在隔离环境短时激活交付段 + Q1 追溯 + V2 实走并关闭开关 |
| FAIL「人工输 UUID」 | PR 6 |
| FAIL「fake/mock 代替」 | Q3 + V2 取证 |
| FAIL「脚本/curl 代业务」 | V2 操作纪律:脚本只起进程与收只读证据 |
| FAIL「Room 页看不到消息/身份错」 | PR 9 |
| FAIL「前端形态错」 | PR 10 |

## 7. 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| R0 | enrollment v2 能解析但 Repository Leader server preflight 仍 409 | PR 5.5A 先统一 provision/binding/preflight 的 role 契约;200/409 契约测试钉死 |
| R1 | Leader 拆解质量不可控(LLM 产出畸形/越界) | 夹具 clamp 全在服务端(PR 7),Bridge 侧先做 schema 校验再 POST;畸形 = 拒绝并叙事,不静默修正 |
| R2 | leader 模式卡死(leader 不提交拆解/审查) | AC-07 底线:超时不自动跳过(那是伪造 leader 角色),操作者用既有 redispatch;V2 排障预案写明 |
| R3 | timeline 摄取量、乱序与错误身份 | 只收授权房间;event_id 唯一键;保存 origin_server_ts;独立 reverse identity resolver;读模型分页既有机制 |
| R4 | 六 Codex 并发的本机资源与限流 | V2 前用 E1 脚本做六实例空转 soak;错峰派活(批次本身就是错峰) |
| R5 | 交付开关首开即炸或误发布无关 plan | E0b 隔离环境 + 三仓凭据白名单 + active plan 空集预检;`container.py:1428-1431` 的启动即错是 fail-fast;V2 后关闭 |
| R6 | v2 契约与 v1 并存的混淆 | v2 只加 `role`;README 写清「v1 = worker 形态继续有效」;契约测试双版本各自 round-trip |
| R7 | 并发写者/他线文件混入(本线历史教训) | 提交一律按路径 stage;工作区 M/?? 他线文件不读不动 |
| R8 | E1 预建成员与 Materialize 实际 principal/resource name 不一致,平台另建 managed 替身 | Materialize 前做六 binding 只读预检;PR 5.5B adoption 幂等测试;前端 Teams 同屏核对 role/runtime/mode |

## 8. 合并门禁(沿用 PR 0–5 的六条,外加)

1. 目标 module 的 interface 行为测试先于实现细节测试;不得用目录结构断言代替行为验收。
2. 外部副作用具备幂等键或明确 retry policy;新增 HTTP adapter 有 production + memory/test 两个 adapter。
3. `ruff check .` + 全量 `pytest -q` 全绿(基线 1777/21 起算);真机 smoke 独立标记,无凭据明确 skip。
4. 扫描日志/fixture:无 token、THINKING、协议帧、私有绝对路径。
5. 跨模块只依赖 `repomesh.modules.<producer>.contracts`;composition root 才接 adapter。
6. PR 描述列验证证据、回滚方式、未决风险;禁混入他线改动。
7. **(新增)** 每个 PR 的描述必须写明它关闭的 AC/FAIL 条款编号——覆盖矩阵(§6)是收口对账单。
8. **(新增)** 前端改动:浏览器实走 + `tsc -b` + oxlint 受影响文件(本项目定式)。
9. **(新增)** PR 5.5、PR 7、PR 9 先冻结 producer contracts 再接 API/adapter;PR 7 的 leader 产物必须可证明来自 leader Codex,PR 9 的 API read model 不得直接查询 collaboration schema。
10. **(新增)** V2 启动清单必须显式验证六类 join 前置(M7 smoke、PR 9、PR 10、Q、E1、E0b),不得因功能关键路径完成就提前验收。

## 9. 明确不做(与验收标准 §2.2 一致)

Organization Leader 本地化、claude-code/kimi adapter、POSIX 宿主、平台在线状态/heartbeat、Room 页聊天输入框、自动故障恢复/backfill/`input_required` 闭环、专项安全测试、CI 绿灯判定、自动 merge、leader MCP server(D-1)、产品化凭据分发(D-10)。
