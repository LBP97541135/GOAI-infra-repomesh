# 交付读模型契约 v0.2 增量（issue / 网格 / 房间）

- 状态：**草案，待主脑裁决**（起草者：后端_施工1，2026-08-11）
- 版本：0.2（**增量**：v0.1 全文继续有效，本文件只定义新增端点与新增派生规则）
- 基线：`docs/contracts/delivery-read-model-v0.1.md`（截至追认注记 1df9ebf）
- 生产方：`api`（聚合视图，无独立事实源）
- 消费方：`frontend/`（控制台 v2，见 `frontend-prototype/DESIGN-DECISION-V2.md`）
- 覆盖工作项：CONS-31（issue 读模型）、CONS-32（网格/团队/花名册）、CONS-33（房间读模型）

沿用 v0.1 的两条红线，本文件不再重复论证：**（一）诚实数据**——无持久化事实源的字段
标 `nullable` 返回 `null`，前端显「未接入」，禁止编造；**（二）状态映射唯一实现在读模型**，
前端禁止另行映射。所有新增派生规则的输入均注明来源模块，来源 `contracts.py` 变化时本文同步修订。

## 0. 语义等式与聚合层级

v2 信息架构引入 issue 作为顶级对象。经代码核实**零新实体**：

| v2 概念 | 既有实体 | 标识符 |
| --- | --- | --- |
| 工作区 | Organization | `organization_id`（全实体原生隔离字段） |
| issue | Project | `issue_id = project_id` |
| 轮次（round） | ExecutionPlan | `round_id = execution_plan_id = v0.1 的 delivery_id` |
| 团队 | RepositoryTeam（project 模块拓扑） | `team_id`，`agentteams_team_name = rm-team-*` |
| 房间 | AgentTeams 真实 Matrix 房间 | `room_id` / `leader_room_id`（拓扑持久化） |

层级：`工作区 > issue > 轮次 > 仓库候选`。v0.1 的 `/deliveries` 是**轮次粒度**且按
project 分组，v0.2 的 `/issues` 是**issue 粒度**——两者并存不互相取代：issue 列表页用
`/issues`，issue 详情页的轮次区继续用 `/deliveries/{round_id}`。

**已知边界（继承 v0.1 §6.9）**：系统**没有 Project 实体或注册表**。issue 的标题、需求
文本、创建时间均来自最早 `PlanSnapshot`；`issue_key` 恒 `null`（无人类可读编号，前端显
`issue_id` 短版）。这一项是 v0.2 最大的诚实缺口，补齐路径是 project 模块落地注册表。

## 1. 新增端点

| 端点 | 用途 | 前端消费位置 | 工作项 |
| --- | --- | --- | --- |
| `GET /api/v1/issues` | issue 列表（Open/Closed + 徽标） | 主屏 GitHub 式列表 | CONS-31 |
| `GET /api/v1/issues/{issue_id}` | issue 概览（元数据 + 轮次索引 + 关联芯片） | issue 详情页头部 | CONS-31 |
| `GET /api/v1/repositories` | 仓库网格（驻扎团队数 + 业务活动） | 仓库页 | CONS-32 |
| `GET /api/v1/teams` | 团队清单（归属仓库/issue + 成员 + 状态） | 团队页 | CONS-32 |
| `GET /api/v1/agents` | 智能体花名册（状态/归属/运行时/时长） | 智能体页 | CONS-32 |
| `GET /api/v1/issues/{issue_id}/rooms` | 房间清单（每仓 teamRoom + leaderDM） | issue 详情页房间区 | CONS-33 |
| `GET /api/v1/rooms/{room_id}/stream` | 单房间合并流（消息 + 投影事实） | 活体房间视图 | CONS-33 |
| `GET /api/v1/issues/{issue_id}/repositories/{repository_id}/plan` | 单仓 DAG·PLAN·SPEC 纸面 | 房间视图右侧双视图 | CONS-33 |

鉴权沿用 v0.1（`Authorization: Bearer` 动作 token）。**开放问题 Q1**：main 带来了本地
账户/会话体系（`/api/v1/auth/*`，401 `{"detail":"local authentication is required"}`），
读端点是否改走会话票据、两套鉴权如何并存，需主脑裁决（见 §7）。

## 2. `GET /issues`

```json
{ "issues": [ {
  "issue_id": "uuid",
  "issue_key": null,                    // nullable：无 Project 注册表（§0）
  "organization_id": "uuid",
  "title": "string",                    // 最早 PlanSnapshot.requirement_text 截断
  "requirement_text": "string|null",
  "state": "open|closed",               // §2.1 派生
  "phase": "contract|plan|execute|validate|release|delivered|failed|archived",
  "phase_note": "string",               // 复用 v0.1 §2 的措辞
  "round_count": 3,                     // 该 issue 下 ExecutionPlan 总数
  "active_round_id": "uuid|null",       // 活跃轮次；无活跃轮次为 null
  "latest_round_id": "uuid|null",       // 最近一轮（含终态）；纯草稿 issue 为 null
  "pending_decision_count": 1,          // v0.1 §4.3 派生跨轮次求和
  "repository_count": 2,
  "team_count": 2,
  "operational_status": "active|paused|cancelled",   // project 拓扑，main 引入
  "execution_mode": "auto|supervised|manual_controlled",
  "opened_by_agent_id": "uuid|null",    // 最早 PlanSnapshot.created_by_agent_id
  "opened_at": "...",                   // 最早 PlanSnapshot.created_at
  "updated_at": "..."                   // §2.3
} ], "next_cursor": null }
```

### 2.1 `state`（Open/Closed）派生规则

按序判定，**首个命中即返回**：

1. `operational_status == cancelled` → **closed**（人工取消是终局）；
2. 存在活跃轮次（`ExecutionPlan.status == in_progress`）→ **open**；
3. 存在非终态 ChangeSet（`status ∉ {delivered, compensated}`）→ **open**；
4. 存在虚拟草稿（该 project 最新 PlanSnapshot 的 `execution_plan_id is null`）→ **open**；
5. 存在轮次且全部终态 → **closed**；
6. 无任何轮次且无草稿 → **open**（空 issue 视为待办，不是已完成）。

`operational_status == paused` **不影响** state：暂停不等于关闭，前端以独立徽标呈现。
理由与 v0.1 phase 推导一致——state 描述「工作是否还需要人或 agent 继续」，
paused 的工作仍需继续。

### 2.2 `phase`（issue 粒度）派生规则

issue 的 phase 不是新映射，而是 v0.1 §2 八相在 issue 粒度上的**选择规则**：

1. 有活跃轮次 → 取该轮次的 v0.1 phase；
2. 无活跃轮次但有轮次 → 取**最近一轮**（按 `updated_at`）的 v0.1 phase；
3. 无轮次但有草稿 → 取草稿的 phase（`contract` 或 `plan`，规则同 v0.1 虚拟草稿）；
4. 无轮次无草稿 → `plan`（需求已存在但未规划）。

**禁止在 issue 层新增第 9 相**：v2 徽标只允许呈现这八相 + `state` + `operational_status`。

### 2.3 `updated_at` 与排序

`updated_at = max(所有轮次 ChangeSet.updated_at, 所有 PlanSnapshot.created_at)`；
无任何时间源时回退 `opened_at`。列表默认按 `updated_at` 降序（GitHub 式）。
沿用 v0.1 修复过的原则：**取不到时间戳时回退最近的持久化事实，不编造**。

### 2.4 归档与筛选

`GET /issues?state=open|closed|all`（默认 `open`）、`?organization_id=`（默认全部；
Q2 见 §7）。v0.1 的交付归档（`delivery_archives`）是**轮次粒度**，不是 issue 粒度：
issue 的所有轮次都归档时 phase 取 `archived`，但 `state` 仍按 §2.1 判定。**v0.2 不新增
issue 级归档实体**。

## 3. `GET /issues/{issue_id}`

在 §2 单条的全部字段之上追加：

```json
{ "rounds": [ { "round_id": "uuid", "phase": "...", "status": "...",
                "plan_version": 1, "created_at": "...", "updated_at": "..." } ],
  "repositories": [ { "repository_id": "uuid", "name": "string",
                      "team_id": "uuid|null", "role_in_issue": "string|null" } ],
  "teams": [ { "team_id": "uuid", "agentteams_team_name": "rm-team-...",
               "repository_id": "uuid", "runtime_status": "pending|ready|failed" } ],
  "contract": { ... },                  // 复用 v0.1 §3 contract 整块（可 null）
  "human_grants": [ { "human_principal_id": "uuid", "role": "...",
                      "code_access": "none|read|write" } ],
  "required_checkpoints": ["specification", "delivery"] }
```

`role_in_issue` nullable：拓扑不记录仓库在 issue 中的角色语义（生产者/消费者只存在于
CONTRACT spec 的 scope），取不到时为 `null`。

## 4. 网格 / 团队 / 花名册（CONS-32）

### 4.1 `GET /repositories`

```json
{ "repositories": [ {
  "repository_id": "uuid", "name": "string", "url": "string",
  "description": "string", "topics": ["string"], "languages": ["string"],
  "profiled_at": "...",
  "resident_team_count": 2,             // 拓扑派生：该仓库被多少 team 驻扎
  "open_issue_count": 1,                // 业务活动派生：state=open 且含本仓的 issue 数
  "active_task_count": 1,               // 非终态 Task 数（task_orchestration）
  "last_delivery_at": "...|null",       // 最近一次含本仓的 ChangeSet.updated_at
  "teams": [ { "team_id": "uuid", "issue_id": "uuid",
               "runtime_status": "..." } ]
} ] }
```

来源：`repository_intelligence` catalog（RepositoryProfile）+ project 拓扑 + task 派生。
**`auto_card` 不投影**（发现证据未按 project 存储，v0.1 §6.10），仓库卡片的「证据」
一栏继续显「未接入」。

### 4.2 `GET /teams`

```json
{ "teams": [ {
  "team_id": "uuid", "agentteams_team_name": "rm-team-...",
  "issue_id": "uuid", "repository_id": "uuid", "repository_name": "string",
  "runtime_status": "pending|ready|failed",          // 拓扑持久化字段
  "team_room_id": "string|null", "leader_room_id": "string|null",
  "leader": { "agent_id": "uuid", "name": "string|null", "role": "repository_leader" },
  "workers": [ { "agent_id": "uuid", "name": "string|null", "role": "worker" } ],
  "runtime": {                                        // §4.4 实时代理，整块 nullable
    "reachable": true, "phase": "string|null",
    "ready_workers": 2, "total_workers": 2
  }
} ] }
```

`runtime_status`（拓扑记录的**建团结果**）与 `runtime.phase`（Controller 的**当前观测
态**）是两个不同事实，**不得合并**：前者是历史，后者可能不可达。

### 4.3 `GET /agents`

```json
{ "agents": [ {
  "agent_id": "uuid", "organization_id": "uuid",
  "role": "organization_leader|repository_leader|worker",
  "status": "active|disabled",                       // agent_directory 持久化
  "agentteams_resource_name": "rm-worker-...",
  "leader_agent_id": "uuid|null", "repository_id": "uuid|null",
  "repository_name": "string|null",
  "responsibility_paths": ["src/**"],
  "team_id": "uuid|null", "issue_id": "uuid|null",    // 拓扑反查
  "active_task_count": 1,
  "runtime": {                                        // §4.4，整块 nullable
    "reachable": true,
    "phase": "string|null",                           // Controller 观测阶段
    "runtime_kind": "openclaw|copaw|hermes|openhuman|repomesh-runner|null",
    "matrix_user_id": "string|null",
    "room_id": "string|null",
    "message": "string|null",
    "awake": null,                                    // nullable：见 §4.4 诚实说明
    "uptime_seconds": null                            // nullable：见 §4.4 诚实说明
  }
} ] }
```

### 4.4 AgentTeams 实时代理与降级（**诚实说明，重要**）

`runtime` 整块经 `AgentTeamControlPlane.get_worker/get_manager/ensure_team` 实时代理，
**不落库**（避免读模型持有过期运行时事实）。可得字段以 Controller 返回为准：

- `WorkerRuntimeRef` 实际返回：`name / phase / runtime / room_id / matrix_user_id / message`；
- `TeamRuntimeRef` 实际返回：`name / phase / team_room_id / leader_room_id / leader_name /
  ready_workers / total_workers`；
- `ManagerRuntimeRef` 实际返回：`name / phase / room_id / matrix_user_id`。

由此两个 v2 设计稿点名的字段**无源，恒 null**：

1. **`uptime_seconds`（时长）**：Controller 响应无 `startedAt`/`uptime`/`lastTransitionTime`
   任何时间字段，无法计算。补齐路径：AgentTeams Controller 在 worker/manager status 中
   暴露启动时间戳（上游 CRD 变更，需与 AgentTeams 侧协调）。
2. **`awake`（醒睡观测态）**：`DesiredRuntimeState`（Running/Sleeping/Stopped）是我们
   **下发的期望态**，不是观测态，且 `get_worker` 不回读它。以期望态冒充观测态即为编造。
   前端只能显 `phase` 字面值 + 「醒睡未接入」。补齐路径同上。

**不可达降级**：`AgentTeamsUnavailable`（网络错误）或非 404 的 `AgentTeamsResponseError`
→ 该条 `runtime = {"reachable": false}` 其余字段省略，**HTTP 状态仍为 200**（花名册的
持久化部分可用，不因运行时不可达整体失败）。404（资源不存在）→ `runtime = null`。
读模型对 Controller 调用**必须设超时并逐条隔离**，禁止一条超时拖垮整页。

**开放问题 Q3**：花名册规模上限。当前实现按 `directory.list()` 全量列出后逐个代理
Controller，N 个 agent = N 次 HTTP。需主脑裁决是否加 `?with_runtime=false` 默认关闭
实时代理、或加分页。

## 5. 房间读模型（CONS-33）

### 5.1 `GET /issues/{issue_id}/rooms`

```json
{ "rooms": [ {
  "room_id": "string", "kind": "team_room|leader_dm",
  "issue_id": "uuid", "team_id": "uuid", "repository_id": "uuid",
  "repository_name": "string",
  "members": [ { "agent_id": "uuid", "name": "string|null", "role": "..." } ],
  "last_message": { "at": "...", "kind": "...", "subject": "string",
                    "sender_agent_id": "uuid" },   // nullable：空房间为 null
  "message_count": 12,
  "live": false                                     // §5.3 派生，禁止假 presence
} ] }
```

房间来源是拓扑持久化的 `RepositoryTeamView.room_id`（teamRoom）与 `leader_room_id`
（leaderDM），每仓两条。`kind` 由字段位置决定，不猜测。**空房间的 `last_message` 为
`null` 且 `message_count: 0`——不装填占位消息**（v2 设计原则「空房间不装满」）。

### 5.2 `GET /rooms/{room_id}/stream`

单房间合并流。**前置改动（CONS-33 必做）**：v0.1 §4.2 的 `/messages` 投影**当前未透出
`room_id`**（`CollaborationMessageView.room_id` 有数据，只是读模型未投影）。v0.2 补投影，
`/deliveries/{id}/messages` 与本端点共用同一投影函数。

```json
{ "items": [ {
  "at": "...", "source": "message|governance|gate|runner",
  "room_id": "string",
  "message": { ... },                  // source=message 时为 §4.2 投影（含 room_id）
  "text": "string",                    // 非 message 源的人类可读摘要
  "repository_id": "uuid|null", "task_id": "uuid|null",
  "payload_ref": "string|null"         // 稳定源引用，兼作排序决胜键（沿用 v0.1 §4.1）
} ], "next_cursor": "string|null" }
```

**治理决策投影进 leaderDM 流**（设计稿要求）：治理决策是 leader 层事实，投影规则为
——该 issue 各轮次的 `GovernanceDecisionView` 投进**对应仓库的 `leader_room_id` 流**，
`source: "governance"`，`text` 形如 `治理决策 ready: {reason}`，`payload_ref:
governance-decision:{id}`。teamRoom 流不含治理决策。

**诚实说明**：这是**投影**而非真实 Matrix 事件——治理决策由控制台写入 DB，从未发进
Matrix 房间。前端必须以视觉区分「投影事实」与「真实房间消息」（如系统条目样式），
不得让用户以为 leader 在房间里说过这句话。**开放问题 Q4**：是否接受这种混流，或改为
详情页独立时间线。

### 5.3 `live` 派生（禁止假 presence）

`live = 该房间所属仓库存在 status == in_progress 的 Task`。沿用 v2 设计原则：LIVE 由
**在途任务派生**，不是 Matrix presence（我们没有 presence 数据源，编造即违约）。
刷新机制：v0.2 仍为**前端轮询**；main 带来的 SSE 模式（`/review-requests/events`）是
升级位，**不在 v0.2 范围**（Q5）。

### 5.4 `GET /issues/{issue_id}/repositories/{repository_id}/plan`

房间视图第二视图（DAG·PLAN·SPEC 纸面）：

```json
{ "issue_id": "uuid", "repository_id": "uuid", "plan_version": 1,
  "dag": {
    "nodes": [ { "repository_id": "uuid", "name": "string",
                 "batch_index": 0, "is_focus": true } ],
    "edges": [ { "from_repository_id": "uuid", "to_repository_id": "uuid" } ],
    "granularity": "repository",       // 恒为 repository，见 §5.5
    "edge_source": "task_dag.depends_on"
  },
  "execution_batches": [["repo-a"], ["repo-b"]],
  "spec": {                            // 每仓 spec 投影，可 null
    "specification_id": "uuid", "kind": "repository|task",
    "status": "draft|submitted|approved|frozen", "revision": 2,
    "goal": "string", "acceptance": ["string"],
    "allowed_paths": ["src/**"], "forbidden_paths": ["legacy/**"],
    "tests": ["pytest"]
  },
  "engineering_contract": { ... }       // 复用 v0.1 §3 contract 整块，项目级，可 null
}
```

每仓 spec 选取规则：该 project 下 `kind ∈ {REPOSITORY, TASK}` 且 `repository_id` 匹配的
specification，优先 `FROZEN`，其次 `APPROVED`，同级取最新 `revision`；无匹配为 `null`
（前端显「本仓无独立 spec，适用项目工程契约」）。`ENGINEERING` kind 是项目级，走
`engineering_contract`，不混入 `spec`。

### 5.5 DAG 显式依赖边确认（**主脑点名问询项，结论：部分可得**）

实测结论比预期好，如实分两层报告：

- **`plan_snapshots.graph_edges` 列存在且被持久化，但恒为空**：唯一生产者
  `change_orchestration/application.py:318` 硬编码 `graph_edges=[]`；5533 联调库实测
  全部快照 `jsonb_array_length(graph_edges) = 0`。→ **v0.2 不投影 graph_edges**。
- **但仓库粒度依赖边真实存在**：`plan_snapshots.task_dag[].depends_on` 有真实数据
  （实测 `{"repository":"repomesh-e2e-client","depends_on":["repomesh-e2e-api"]}`），
  且 v0.1 已用同一来源派生 `tasks[].depends_on`。→ **`dag.edges` 用 task_dag 的
  depends_on 投影，仓库粒度**，`granularity: "repository"` 与 `edge_source` 字段显式
  自述来源，前端不必猜。

因此**不退化为泳道列表**：v0.2 能画出真实的仓库级 DAG（节点=仓库，边=depends_on，
分层=execution_batches）。更细粒度（任务级/接口级 `graph_edges`）另立项，补齐路径是
`change_orchestration` 在建快照时写入真实边。

## 6. 与 v0.1 的关系与复用清单

**v0.1 全文继续有效**，v0.2 只做增量。明确的复用（**禁止另写第二套实现**）：

| v0.2 用到的派生 | 唯一实现位置 |
| --- | --- |
| 八相 phase、任务 7→6 态、仓库 12→4 门禁态 | `api/read_models/mappings.py`（v0.1 §5） |
| merge_gate 终态置 null | v0.1 契约追认 889464e |
| approve / watch 决策项与计数 | v0.1 §4.3（`MERGE_GATE_GOVERNANCE_MISSING_REASON` 常量） |
| repair_timeline 时间戳回退 | v0.1 §3 + 追认（611ab88） |
| events 四源合并与游标语义 | v0.1 §4.1 |
| messages 投影与 `direction` | v0.1 §4.2 + 追认（1df9ebf），v0.2 补 `room_id` |

**v0.2 明确不做**（承接 v0.1 §6 与工作计划 §4「明确不在本批」）：deny 审计、
Worker→Leader 回报摄取、统一 trace_id、clarify 决策实体、cost 采集、diffstat、
issue 级归档实体、SSE 推送、ReviewRequest 与治理决策的统一决策夹。

**ReviewRequest 边界**：main 引入的 `HumanReviewRequestView` /
`ProjectCheckpointDecisionView`（`checkpoint_decisions` 表）与 v0.1 的治理决策
（`delivery` 模块，head-bound）是**两套并行审批机制，不同表不同语义**。v0.2 的决策夹
只含治理决策；ReviewRequest 走 main 既有的 `/review-requests`。统一呈现是产品级整合
待议项（Q6）。

## 7. 开放问题与争议项（**待主脑裁决**）

| # | 问题 | 我的建议 |
| --- | --- | --- |
| Q1 | 读端点鉴权：继续用共享动作 token，还是改走 main 的本地会话票据？两套并存期如何过渡 | v0.2 先维持动作 token（不阻塞前端），会话票据接入单立一项；理由：混改鉴权会同时动 8 个端点，风险与 v2 页面开发并行不可控 |
| Q2 | 工作区（organization）过滤：`/issues` 是否必须带 `organization_id`？当前无「当前工作区」概念的服务端来源 | 参数**可选**，缺省返回全部并在响应回显每条的 `organization_id`；工作区切换器由前端持有选择，服务端不猜 |
| Q3 | 花名册实时代理规模：N agent = N 次 Controller HTTP | 加 `?with_runtime=` 开关（默认 `true` 便于 Demo，规模变大再默认关），逐条超时隔离 + 不可达降级 |
| Q4 | 治理决策投影进 leaderDM 混流是否可接受（它不是真实 Matrix 事件） | 接受但**必须视觉区分**；若主脑认为混流有误导风险，改为详情页独立「治理时间线」，我实现成本相同 |
| Q5 | 房间刷新：v0.2 保持轮询，SSE 另立项 | 保持轮询；SSE 需先定「哪些事实值得推」，否则会推出一个和轮询同构但更脆的通道 |
| Q6 | ReviewRequest 与治理决策统一决策夹 | v0.2 不做。两者审批对象不同（项目检查点 vs 仓库候选 head），强行统一会丢失 head-bound 语义 |
| Q7 | `/issues` 分页：`next_cursor` 形状 | 沿用 v0.1 events 的 offset 游标语义（不透明字符串），列表端点当前 `next_cursor` 恒 `null` 的现状一并收敛 |
| Q8 | issue 无轮次无草稿时 `state=open` 的裁决（§2.1 规则 6） | 维持 open；空 issue 是「待规划」不是「已完成」，显示为 Closed 会让人以为工作做完了 |

## 8. 字段来源速查（新增部分）

| 响应字段 | 来源模块 / 表 | 备注 |
| --- | --- | --- |
| `issue_id` / `organization_id` | 各表 `project_id` / `organization_id` | 无 Project 实体 |
| `title` / `requirement_text` / `opened_at` / `opened_by_agent_id` | `repository_intelligence.plan_snapshots` | 最早快照 |
| `round_count` / `active_round_id` | `task_orchestration.execution_plans` | |
| `state` / `phase` / `phase_note` | 读模型派生（§2.1/§2.2） | 唯一实现 |
| `pending_decision_count` | v0.1 §4.3 派生 | 跨轮次求和 |
| `operational_status` / `execution_mode` / `required_checkpoints` / `human_grants` | `project` 拓扑（main 引入） | |
| `team_id` / `agentteams_team_name` / `runtime_status` / `room_id` / `leader_room_id` | `project` 拓扑 `RepositoryTeamView` | 持久化 |
| 仓库 `name` / `url` / `topics` / `languages` / `profiled_at` | `repository_intelligence` catalog | `auto_card` 不投影 |
| agent `role` / `status` / `responsibility_paths` / `agentteams_resource_name` | `agent_directory` | |
| `runtime.*` | AgentTeams Controller 实时代理 | 不落库；`awake` / `uptime_seconds` 恒 null |
| 房间 `last_message` / `message_count` / 流中 `message` | `collaboration.messages` | `created_at` 由 0020 迁移持久化 |
| `live` | `task_orchestration.tasks` 派生 | 非 presence |
| `dag.nodes` / `dag.edges` / `execution_batches` | `plan_snapshots.task_dag` / `execution_batches` | `graph_edges` 恒空不投影（§5.5） |
| `spec` / `engineering_contract` | `specification` | 每仓 vs 项目级分开 |
