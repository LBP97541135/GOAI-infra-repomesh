# 交付读模型契约 v0.2 增量（issue / 网格 / 房间）

- 状态：**已裁决 · 生效**（起草：后端_施工1；八项开放问题于 2026-08-11 全部裁决，见 §7）
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
| `GET /api/v1/console/repositories` | 仓库网格（驻扎团队数 + 业务活动） | 仓库页 | CONS-32 |
| `GET /api/v1/console/teams` | 团队清单（归属仓库/issue + 成员 + 状态） | 团队页 | CONS-32 |
| `GET /api/v1/console/agents` | 智能体花名册（状态/归属/运行时/时长） | 智能体页 | CONS-32 |
| `GET /api/v1/issues/{issue_id}/rooms` | 房间清单（每仓 teamRoom + leaderDM） | issue 详情页房间区 | CONS-33 |
| `GET /api/v1/rooms/{room_id}/stream` | 单房间合并流（消息 + 投影事实） | 活体房间视图 | CONS-33 |
| `GET /api/v1/issues/{issue_id}/repositories/{repository_id}/plan` | 单仓 DAG·PLAN·SPEC 纸面 | 房间视图右侧双视图 | CONS-33 |

鉴权沿用 v0.1（`Authorization: Bearer` 动作 token）——**Q1 裁决：维持动作 token**，
会话票据接入另立 backlog 项。main 的本地账户/会话体系（`/api/v1/auth/*`，未认证返回
401 `{"detail":"local authentication is required"}`）是 main 自有端点的鉴权，前端在其上
构建登录 UI 与本表八个读端点的鉴权互不影响，两者不混改。

## 2. `GET /issues`

```json
{ "issues": [ {
  "issue_id": "uuid",
  "issue_key": null,                    // nullable：无 Project 注册表（§0）
  "organization_id": "uuid|null",       // nullable：三级取值链全空时（见下）
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
  // 以下两项 nullable：未建团（无 project 拓扑行）时为 null，禁止填默认值
  "operational_status": "active|paused|cancelled|null",   // project 拓扑，main 引入
  "execution_mode": "auto|supervised|manual_controlled|null",
  "opened_by_agent_id": "uuid|null",    // 最早 PlanSnapshot.created_by_agent_id
  "opened_by_name": "string|null",      // **agent 资源名（rm-worker-01），非人名**；
                                        // 与 v0.1 §4.2 sender_name 同源同精度，解析不到为 null
  "opened_at": "...|null",              // 最早 PlanSnapshot.created_at；无任何快照时 null
  "updated_at": "...|null"              // §2.3；opened_at 为 null 且无其它时间源时同为 null
} ],
  "open_count": 3,                      // 见 §2.5（2026-08-11 主脑追认）
  "closed_count": 12,
  "next_cursor": "string|null" }
```

`organization_id` 的取值链（**2026-08-11 主脑追认**，理由见 §2.5）：轮次
`ExecutionPlan.organization_id` → 项目拓扑 `organization_id` → **最早 PlanSnapshot 的
`created_by_agent_id` 所属组织**（`agent_directory` 持久化事实）；三者皆无为 `null`。
第三级不可省：草稿态 issue 既无轮次也无拓扑，缺了它会被工作区筛选**静默丢弃**。

以下字段在 issue 从未建团时**无持久化事实源，返回 `null` 或空数组**，不得填默认值
（诚实数据红线，2026-08-11 主脑追认）：`operational_status`、`execution_mode`（→ `null`），
`team_count`（→ `0`）；§3 的 `teams`、`human_grants`、`required_checkpoints`（→ `[]`），
`repositories[].team_id`（→ `null`）。前端按「未接入」呈现，**禁止把缺拓扑显示成 `active`**。
联调锚点（CONS-33 扩种子后）：场景 A/B/C 有拓扑，**场景 D 故意不建团**，即降级路径的活体样本；
起草期 `project.agent_topologies` 曾是空表，那时四个场景全走降级。

### 2.1 `state`（Open/Closed）派生规则

按序判定，**首个命中即返回**：

1. `operational_status == cancelled` → **closed**（人工取消是终局）；
2. 存在活跃轮次（`ExecutionPlan.status == in_progress`）→ **open**；
3. 存在非终态 ChangeSet（`status ∉ {delivered, compensated}`）→ **open**；
4. 存在虚拟草稿（该 project 最新 PlanSnapshot 的 `execution_plan_id is null`）→ **open**；
4b. **最近一轮（§2.2 口径）phase 为 `failed` 且该轮未归档 → open**（见下方勘误）；
5. 存在轮次且全部终态 → **closed**；
6. 无任何轮次且无草稿 → **open**（空 issue 视为待办，不是已完成）。

`operational_status == paused` **不影响** state：暂停不等于关闭，前端以独立徽标呈现。
理由与 v0.1 phase 推导一致——state 描述「工作是否还需要人或 agent 继续」，
paused 的工作仍需继续。

**勘误 4b（2026-08-12，缺陷 A-22）【提案 · 待主脑裁决】**：起草 §2.1 时尚无「整轮失败」
这一形态，规则 5 的「全部终态」把它算作终局。活体反例：issue `74e9701e` 的第 1 轮
`5b1cbfd1` 两个任务全败、未产出 ChangeSet——没有 ChangeSet 就够不上规则 3，于是规则 5
把它判成 `Closed · 执行失败`。**这与 §2.1 自己的定义相抵触**：本节为 `paused` 不关闭
issue 给出的理由是「state 描述工作是否还需要人或 agent 继续」，而一轮失败的工作显然
还需要——它正是重新派发路径的入口。故插入规则 4b。

- **归档是例外的例外**：轮次卡的「归档本轮」（`POST /deliveries/{id}/archive`，v0.1 §4.5）
  是操作者在说「本轮失败已知悉、且是终局」。已归档的失败轮回落规则 5，照常 closed。
  语义分工：未归档的失败 = 还要干（重新派发）；归档 = 有意收尾（另起一轮或就此作罢）。
- **不改 §2.4**：轮次粒度归档的既有语义不动，v0.2 仍不引入 issue 级归档实体；本规则
  只是让 state 读取那条已存在的归档事实。
- **不重编号**：新规则取名 4b 而非顶掉 5/6，因为 §6.1 与 §7 的 Q8 都按序号引用规则 6。
- **实现无新增存储**：`derive_phase` 判 `archived` 在判 `failed` 之前，所以 phase 为
  `failed` 已经等价于「失败且未归档」，读模型不做第二次归档查询
  （`api/read_models/service.py` 的 `_issue_bundle`）。
- **对 §2.2 phase 无影响**：phase 仍取最近一轮的 `failed`；变的只有 state。因此该 issue
  呈现为 `Open · 执行失败`，而不再是自相矛盾的 `Closed · 执行失败`。

### 2.2 `phase`（issue 粒度）派生规则

issue 的 phase 不是新映射，而是 v0.1 §2 八相在 issue 粒度上的**选择规则**：

1. 有活跃轮次 → 取该轮次的 v0.1 phase；
2. 无活跃轮次但有轮次 → 取**最近一轮**（按 `updated_at`）的 v0.1 phase；
3. 无轮次但有草稿 → 取草稿的 phase（`contract` 或 `plan`，规则同 v0.1 虚拟草稿）；
4. 无轮次无草稿 → `plan`（需求已存在但未规划）。

**禁止在 issue 层新增第 9 相**：v2 徽标只允许呈现这八相 + `state` + `operational_status`。

### 2.3 `updated_at` 与排序

`updated_at = max(所有轮次 ChangeSet.updated_at, 所有 PlanSnapshot.created_at)`；
无任何时间源时回退 `opened_at`。**`opened_at` 本身亦可为 `null`**（issue 有轮次但无任何
快照），此时 `updated_at` 同为 `null`，该 issue 在默认排序里置末——实现的排序键已为此
兜底，契约起草时漏写了这一层。列表默认按 `updated_at` 降序（GitHub 式）。
沿用 v0.1 修复过的原则：**取不到时间戳时回退最近的持久化事实，不编造**。

### 2.4 归档与筛选

`GET /issues?state=open|closed|all`（默认 `open`）、`?organization_id=`（默认全部；
Q2 见 §7）、`?cursor=&limit=`（Q7 的 offset 不透明游标，语义与 §4.1 events 完全一致：
`cursor` 是不透明字符串，内部为 offset；`limit` 默认 100、上限 500；非法 cursor → 422）。
v0.1 的交付归档（`delivery_archives`）是**轮次粒度**，不是 issue 粒度：
issue 的所有轮次都归档时 phase 取 `archived`，但 `state` 仍按 §2.1 判定。**v0.2 不新增
issue 级归档实体**。

### 2.5 `open_count` / `closed_count`（**2026-08-11 主脑追认，契约增量**）

GitHub 式列表的两个标签页各带总数（`Open 3 | Closed 12`）。消费方无法从条目数推出总数：
翻页之下数出来的数是错的，等于编造；显「—」则丢掉关键信息量。故响应体增加两个总数字段。

语义（三条，实现与本文本同批交付）：

1. **不受 `state` 影响**——两个计数恒为全量，看 open 标签时 closed 总数依然为真；
2. **不受分页影响**——`limit` / `cursor` 只裁剪 `issues` 数组，不动计数；
3. **受 `organization_id` 影响**——计数是「当前工作区的总数」，否则切换工作区后
   标签数与列表内容不一致。工作区隔离优先于计数便利。

计数与条目同源于 §2.1 的 state 派生（同一次聚合内求和），不存在两套判定。

## 3. `GET /issues/{issue_id}`

在 §2 单条的全部字段之上追加：

```json
{ "rounds": [ { "round_id": "uuid", "phase": "...", "status": "...",
                "plan_version": 1,          // nullable：该轮无 PlanSnapshot 时（同 created_at）
                "created_at": "...", "updated_at": "..." } ],
  "repositories": [ { "repository_id": "uuid", "name": "string",
                      "team_id": "uuid|null", "role_in_issue": "string|null" } ],
  // teams / human_grants / required_checkpoints：未建团时为 []（非 null，非占位）
  "teams": [ { "team_id": "uuid", "agentteams_team_name": "rm-team-...",
               "repository_id": "uuid", "runtime_status": "pending|ready|failed" } ],
  "contract": { ... },                  // 复用 v0.1 §3 contract 整块（可 null）
  "human_grants": [ { "human_principal_id": "uuid", "role": "...",
                      "code_access": "none|read|write" } ],
  "required_checkpoints": ["specification", "delivery"] }
```

`rounds` 按时间正序（第 1 轮在前），`created_at` 与 `plan_version` 均取自该轮次的
PlanSnapshot，**无快照时两者同为 `null`**（出自同一个判空分支，起草时只写对了
`created_at`）；`repositories` 是「该 issue 各轮次计划涉及的仓库 ∪ 拓扑驻扎仓库」的并集。

**勘误（2026-08-12，主脑裁决）**：`rounds[].updated_at` 在该轮**尚无 ChangeSet** 时
等于 `created_at`——实现里两者出自同一个表达式（`read_models/service.py`），从来不存在
「先 null、有活动后再填」的语义；它为 `null` 当且仅当快照本身缺失（与 `created_at`/
`plan_version` 同一个判空分支）。此前活体见到的 rounds 双 null 是快照没被链接（缺陷
A-5，v0.4 §8.2 具名 500 已封死），不是本字段的正常形态。

`role_in_issue` nullable：拓扑不记录仓库在 issue 中的角色语义（生产者/消费者只存在于
CONTRACT spec 的 scope），取不到时为 `null`。

`required_checkpoints` **保留投影**（Q6 裁决）：v0.2 的决策夹不含 ReviewRequest，但本
字段让前端能提示「本 issue 设有人工检查点」并链接到 main 既有的审核台，不必自己推断。

## 4. 网格 / 团队 / 花名册（CONS-32）

### 4.1 `GET /api/v1/console/repositories`

```json
{ "repositories": [ {
  "repository_id": "uuid", "name": "string", "url": "string",
  "description": "string", "topics": ["string"], "languages": ["string"],
  "profiled_at": "...",
  "test_commands": ["string"],          // 【已裁决 · 2026-08-12，A-19】该仓的验证命令；物化时注入
                                        // TaskNode.tests（节点自带者优先）；空数组=诚实无命令,
                                        // 交付将拒收该仓候选直至补齐

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

### 4.2 `GET /api/v1/console/teams`

```json
{ "teams": [ {
  "team_id": "uuid", "agentteams_team_name": "rm-team-...",
  "issue_id": "uuid", "repository_id": "uuid",
  "repository_name": "string|null",                  // catalog 查不到该仓库时为 null
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

### 4.3 `GET /api/v1/console/agents`

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

`runtime` 整块经 `AgentTeamControlPlane.get_worker/get_manager/**get_team**` 实时代理，
**不落库**（避免读模型持有过期运行时事实）。

**勘正（2026-08-11 主脑追认）**：起草时本节写团队运行时走 `ensure_team`，那是错的——
`ensure_team` 在资源不存在时会**创建**团队，读端点调用它等于让一个 GET 具备建团副作用。
已为 `AgentTeamControlPlane` 与 AgentTeams 客户端补只读的 **`get_team`**（镜像既有
`get_worker`/`get_manager`），读路径一律走它；`ensure_team` 仅供拓扑 reconcile 等写路径使用。

可得字段以 Controller 返回为准：

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

**不可达降级（硬性要求）**：`AgentTeamsUnavailable`（网络错误）或非 404 的
`AgentTeamsResponseError` → 该条 `runtime = {"reachable": false}` 其余字段省略，
**HTTP 状态仍为 200**（花名册的持久化部分可用，不因运行时不可达整体失败）。
404（资源不存在）→ `runtime = null`。

**逐条超时隔离（硬性要求，Q3 裁决）**：读模型对 Controller 的每次调用**必须设独立超时
且失败逐条隔离**——一条超时或报错只降级该条 `runtime`，禁止拖垮整页。这不是实现建议
而是契约约束：违反它会让「智能体页整页 500」成为可能，而持久化花名册本来是可用的。

**并发探测（硬性要求，2026-08-11 主脑实测后追加）**：隔离只防住失败传播，防不住**串行等待**。
首版逐条 await，controller 离线时花名册 9 条 × 2s 超时 = **实测 18.1s**（团队页 8.2s），
体验上与故障无异，而每一行其实都正确降级了。故要求：**一页内的所有探测并发发起**
（`asyncio.gather`），整页墙钟时间收敛到 ≈ 单条超时量级，隔离语义不变。定向测试须断言
N 条不可达的总耗时 < 单条超时的小倍数——**只测「降级正确」会漏掉这个缺陷**。

**`?with_runtime=`（Q3 裁决）**：默认 `true`（Demo 需要看到运行时）。置 `false` 时**不发任何
Controller 请求**，`runtime` 字段仍在响应里、**恒为 `null`**（起草原文写「整块省略」，与实现
不符，已按实现勘正——字段常在、值为 null，消费方不必对字段缺失做兼容）。当前实现按
`directory.list()` 全量列出后逐个代理，N 个 agent = N 次 HTTP；规模变大时改默认值或加分页，
届时修订本节。

**`runtime: null` 是同形的三义，消费方必须靠请求参数消歧（勘误补充，2026-08-11）**：
`null` 同时是「404 资源不存在」「未配置」与「`with_runtime=false` 未探测」三种情况的取值，
**响应体自身无法区分**。故本契约明文规定消费方指引：

- **发起过探测**（`with_runtime` 省略或 `true`）时，`null` 表示**无运行时事实**，呈现「未接入」；
  `{"reachable": false}` 表示**探测发生了但不可达**，呈现「不可达」——两者不可合并成一个态。
- **未发起探测**（`with_runtime=false`）时，`null` **只表示未探测，不表示无事实**。此时
  **禁止呈现为「未接入」**：那是把「没问」说成「问了没有」，属编造。正确呈现是运行时区域
  留白 + 「未探测」或独立加载态。
- 推荐做法（前端 CONS-44 已按此实现，作为消费方参照）：**按探测阶段门控渲染**——首屏若为
  求快走 `with_runtime=false`，运行时区域一律不下结论；待发起真实探测的请求返回后，再按上一条
  区分 `null` 与 `{"reachable": false}`。

**三义里有两义消费方永远分不开（2026-08-11 补记，诚实说明）**：上面的指引解决的是
「未探测」那一义（靠请求参数）。但 **404「controller 说没有这个资源」与「AgentTeams 未配置」
都产出 `null`，响应体与请求参数都无法区分**。二者的运维含义完全不同——前者是注册表与
controller 漂移（有人得去查），后者是本环境根本没接。当前前端只能合并成一句「未接入」。
补齐路径（本版不做）：给 404 一个可辨识的取值（如 `{"reachable": false, "present": false}`），
成本低于本节这段消费方指引；顶层的 `runtime_probed: bool` **只能解第三义、解不了这两义**，
不要误以为它是全解。

### 4.5 路径冲突与命名空间（**2026-08-11 主脑裁决，已生效**）

§1 与 §4 起草时把三个端点定在 `GET /api/v1/repositories|teams|agents`。**实测 `/api/v1/repositories`
已被 `repository_intelligence/api/router.py:102` 占用**（它的 catalog 视图，返回裸数组带
`auto_card`），且该路由**注册在前、运行时胜出**——把网格挂在裸路径上会得到一个永远不可达的
端点，而 OpenAPI 反而显示网格的定义（字典按方法覆盖），即「看起来在工作、实际返回别人的形状」。

**裁决结果**：三条统一收进 `console` 命名空间——
`GET /api/v1/console/repositories`、`/console/teams`、`/console/agents`（§1 表格已同步）。
理由：（一）不遮蔽也不改动他人已有端点（main 的审核台可能在消费它）；（二）三条同批同前缀，
消费方不必记「哪条有前缀哪条没有」；（三）main 的 API 面还在扩张，`teams` / `agents`
这类通名日后同样可能撞车，前缀是一次性的隔离。
备选方案「只给 `repositories` 加前缀、另两条保持裸路径」**已否决**：路径风格不一致的代价
会长期由消费方承担。

**由此得到的通用做法**：新增端点前先查路径占用。本次的失效模式是——同 path+method
先注册者在运行时胜出，而 OpenAPI 字典里后写者覆盖前者，于是**文档与实际行为相反，
且冒烟测返回 200 完全看不出来**。

## 5. 房间读模型（CONS-33）

### 5.1 `GET /issues/{issue_id}/rooms`

```json
{ "rooms": [ {
  "room_id": "string", "kind": "team_room|leader_dm",
  "issue_id": "uuid", "team_id": "uuid", "repository_id": "uuid",
  "repository_name": "string|null",                 // 同 §4.2：catalog 查不到时为 null
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

**查询参数 `?cursor=&limit=`（2026-08-11 补记）**：语义与 §4.1 events 完全一致
（`cursor` 不透明、内部为 offset；`limit` 默认 100、上限 500；非法 cursor → 422）。
起草时只定义了响应里的 `next_cursor`，没写调用方怎么把它传回来。**注意与 v0.1 §4.2
`/messages` 相反**——那个端点有意不分页，房间流才是需要翻页的那个。

#### `source` 语义（**契约明文，Q4 裁决落地**）

| `source` | 含义 | 是否房间内真实发生 | 前端渲染约束 |
| --- | --- | --- | --- |
| `message` | 真实房间消息（`collaboration.messages` 已投递到 Matrix 的记录） | **是** | 常规聊天气泡（头像 + 发送者） |
| `governance` | 控制台写入的治理决策**投影** | **否** | **必须系统条目样式，无头像气泡** |
| `gate` | SCM 门禁观测（CI / PR / merge）**投影** | **否** | 同上 |
| `runner` | Runner 执行事件**投影** | **否** | 同上 |

**`source != "message"` 的条目一律是控制台投影事实，并非房间内真实发生**；前端必须以
系统条目样式渲染（无头像气泡），不得让用户以为某个 agent 在房间里说过这句话。此约束
为契约文本明文要求，不是渲染建议。

**治理决策投影规则**（Q4 采纳方案 A：投影进 leaderDM 流）：治理决策是 leader 层事实，
该 issue 各轮次的 `GovernanceDecisionView` 投进**对应仓库的 `leader_room_id` 流**，
`source: "governance"`，`text` 形如 `治理决策 ready: {reason}`，
`payload_ref: governance-decision:{id}`。teamRoom 流不含治理决策。

### 5.3 `live` 派生（禁止假 presence）

`live = 该房间所属仓库存在 status == in_progress 的 Task`。沿用 v2 设计原则：LIVE 由
**在途任务派生**，不是 Matrix presence（我们没有 presence 数据源，编造即违约）。
刷新机制：v0.2 仍为**前端轮询**（Q5 裁决）；main 带来的 SSE 模式
（`/review-requests/events`）是升级位，另立项——SSE 需先定「哪些事实值得推」，否则只是
一个与轮询同构但更脆的通道。

### 5.4 `GET /issues/{issue_id}/repositories/{repository_id}/plan`

房间视图第二视图（DAG·PLAN·SPEC 纸面）：

```json
{ "issue_id": "uuid", "repository_id": "uuid", "plan_version": 1,
  "dag": {
    "nodes": [ { "repository_id": "uuid|null",   // catalog 解析不到该批次名时为 null
                 "name": "string",
                 "batch_index": 0, "is_focus": true } ],
    "edges": [ { "from_repository_id": "uuid", "to_repository_id": "uuid" } ],
    "granularity": "repository",       // 恒为 repository，见 §5.5
    "edge_source": "task_dag.depends_on"
  },
  "execution_batches": [["repo-a"], ["repo-b"]],
  "spec": {                            // 每仓 spec 投影，可 null
    "specification_id": "uuid", "kind": "repository|task",
    "status": "approved|frozen", "revision": 2,        // 见下：本端点只可能出这两个
    "goal": "string", "acceptance": ["string"],
    "allowed_paths": ["src/**"], "forbidden_paths": ["legacy/**"],
    "tests": ["pytest"]
  },
  "engineering_contract": { ... }       // 复用 v0.1 §3 contract 整块，项目级，可 null
}
```

**`status` 枚举（2026-08-11 勘正，本节正文）**：起草写的 `draft|submitted|approved|frozen`
错了两层——`submitted` **在枚举里根本不存在**（真实全集见 `specification/contracts.py`：
`draft|in_review|approved|frozen|superseded`），而本端点的选取规则又只在 `FROZEN`/`APPROVED`
里挑，所以 `draft` / `in_review` / `superseded` 在这里**恒不可达**。故本节正文取值为
`approved|frozen`。§7.2 当时只勘正了勘误表、没动正文，是「同一事实出现在两节、只改一节」
的**第三次复发**（前两次：`repository_name` nullable、v0.1 §4.2 直投影漏字段）。

每仓 spec 选取规则：该 project 下 `kind ∈ {REPOSITORY, TASK}` 且 `repository_id` 匹配的
specification，优先 `FROZEN`，其次 `APPROVED`，同级取最新 `revision`；无匹配为 `null`
（前端显「本仓无独立 spec，适用项目工程契约」）。`ENGINEERING` kind 是项目级，走
`engineering_contract`，不混入 `spec`。

**节点与边的降级不对称（2026-08-11 补记；2026-08-12 勘正）**：§7.2 只裁决了「无法解析的
依赖名丢弃该边」。节点侧规则不同——`execution_batches` 存的是仓库**名**，catalog 解析不到
时**节点保留、`repository_id` 为 `null`**（丢掉节点会让批次缺项，布局就错了）。消费方据此
不得拿 `nodes[].repository_id` 当列表 key 或跳转参数。

**2026-08-12 勘正（v0.4 附录 A）**：本段原写「**已知缺口**：节点侧目前不像边那样记
warning」——**该描述当时即已不实**，节点侧的 warning 早已落地（`service.py:1288`，含条数与
全部名字，与边侧同口径）。§6.1 对应条目同批标为已闭环，两处同批改，不再重演「同一事实两节
只改一节」。

**边被丢弃有两个原因，不是一个**（本次核实补写，契约此前从未记载第二条）：
1. 端点名 catalog 解析不到 → 丢弃（§7.2 记录的那条）；
2. **端点不在任何批次里** → 丢弃。节点来自 `execution_batches`、边来自 `task_dag`，两者
   本无约束使其一致，画一条通向空白处的线比少一条线更糟。

两条的处置路径不同：(1) 要补 catalog 行，(2) 指向规划产物自身不自洽。

**自述计数（v0.4 附录 A.1，加法向后兼容）**：`dag` 块增三个计数，供消费方如实告知用户
「这张图可能不完整」——日志是给运维看的，而看图的人看不到日志：

```json
{ "dag": { "...": "...",
  "unresolved_node_count": 1,          // 名字在 catalog 里查不到的节点数（节点仍在图上）
  "dropped_edge_unresolved_count": 2,  // 因端点名解析不到而丢弃的边数（原因 1）
  "dropped_edge_off_batch_count": 1 }} // 因端点不在任何批次里而丢弃的边数（原因 2）
```

分两个边计数而非合一：合成一个数，用户仍然答不出「为什么少了边」。
`unresolved_node_count` **不可**由 `nodes` 长度推出——无法解析的节点仍留在图上。

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

**ReviewRequest 边界（Q6 裁决：不统一）**：main 引入的 `HumanReviewRequestView` /
`ProjectCheckpointDecisionView`（`checkpoint_decisions` 表）与 v0.1 的治理决策
（`delivery` 模块，head-bound）是**两套并行审批机制，不同表不同语义**（项目检查点 vs
仓库候选 head）。v0.2 的决策夹只含治理决策；ReviewRequest 走 main 既有的
`/review-requests`。强行统一会丢失 head-bound 语义，故不做；issue 概览保留
`required_checkpoints` 供前端提示与跳转（§3）。

### 6.1 已备案 backlog（本契约不实现，记录补齐路径）

| 项 | 缺口 | 补齐路径 |
| --- | --- | --- |
| project 注册表 | `issue_key` 恒 null——**v0.2 最大的诚实缺口**（无 Project 实体） | project 模块落地注册表，届时 `issue_key` 与 v0.1 `project_key` 同时生效 |
| DAG 真实边 | `graph_edges` 列已持久化但恒空（§5.5） | `change_orchestration` 建快照时写入真实边，届时 `dag.granularity` 可升级 |
| 会话票据鉴权 | 读端点仍用共享动作 token（Q1） | 与 main 的 `/auth` 会话体系对齐，单独立项 |
| SSE 推送 | 房间刷新仍为轮询（Q5） | 先定「哪些事实值得推」，再复用 main 的 SSE 模式 |
| 统一决策夹 | 治理决策与 ReviewRequest 并存两面（Q6） | 产品级整合，需先统一审批对象语义 |
| 列表服务端筛选 | `/issues` 无 `?repository_id=` / `?phase=`（§7.1 裁决撤按钮） | issue 规模变大后再议：`repository_id` 需定义「issue 含该仓」的跨轮次包含语义 |
| 运行时时长与醒睡 | `uptime_seconds` / `awake` 恒 null（§4.4） | **2026-08-11 复审勘正**：起草把补齐路径写成「上游 CRD 变更、需与 AgentTeams 侧协调」，成本被高估——AgentTeams 是**本仓 fork**（`integrations/agentteams/upstream.toml`），CRD 已有 `lastHeartbeat`/`lastActiveAt`，`WorkerStatus` 亦有 `containerState`（观测态，非我们下发的期望态），缺的只是 fork 的 `WorkerResponse` DTO 与客户端解析。**⚠ 待验证**：本仓 vendored 镜像与部署中的 controller 存在已知漂移，落实前须对真实 controller 验证响应确实带这些字段，不得据 vendored 源码直接开工 |
| 花名册规模 | Q3 只加了 `?with_runtime=` 开关且**默认 true**，默认路径的 N 次 HTTP 一次没减（§4.4） | 分页，或改默认值。**本行 2026-08-11 补记**：该缺口起草时被记成「已裁决·生效」而没进本表，等于没有任何条目会让它再被拿出来看 |
| 探测并发上限 | `asyncio.gather` 扇出无上限，httpx 默认 `max_connections=100`，超出后连接池排队时间**计入每条自己的超时预算** → 规模上去后健康 controller 也可能被判 `reachable: false`（§4.4） | 加 `Semaphore` 限流或显式配 `httpx.Limits`；超时值提到 settings |
| 运行时降级可观测性 | 探测失败一律 `{"reachable": false}` + 无细节 warning，**token 配错 / controller 宕机 / 适配器自身 bug 三者同形**，HTTP 200 之下无告警信号（§4.4） | 日志带 `exc_info` 与 status_code，按 kind 聚合 degraded 计数 |
| 404 与未配置不可分辨 | 两者都返 `runtime: null`，运维含义不同却渲染成同一个「未接入」（§4.4） | 给 404 一个可辨识取值，如 `{"reachable": false, "present": false}` |
| ~~DAG 节点丢失不留痕~~ **已闭环（2026-08-12 勘误）** | 本行起草时即已过期：节点侧 warning 早已落地（`api/read_models/service.py:1288`，含条数与全部名字，与边侧同口径）。v0.4 附录 A.1 之后更进一步——`unresolved_node_count` 等三个自述计数进了响应体，看图的人不必读日志（§5.4） | 无需补齐；§5.4 正文同批勘正 |

## 7. 裁决记录（2026-08-11，八项全部裁决 · 生效）

八项原为起草期开放问题，均已裁决，全部采纳起草建议，Q3/Q4 附加了硬约束。

| # | 问题 | 裁决 | 落点 |
| --- | --- | --- | --- |
| Q1 | 读端点鉴权：共享动作 token 还是 main 的本地会话票据 | **维持动作 token**；会话票据接入另立 backlog。main 的 `/auth/*` 是其自有端点鉴权，前端登录 UI 与本契约八端点互不影响，两者不混改 | §1、§6.1 |
| Q2 | `/issues` 是否必须带 `organization_id` | **可选参数 + 响应逐条回显 `organization_id`**；工作区选择由前端持有，服务端不猜 | §2、§2.4 |
| Q3 | 花名册实时代理规模（N agent = N 次 HTTP） | **加 `?with_runtime=`，默认 `true`**；**逐条超时隔离升格为契约硬性要求**（非实现建议）。**2026-08-11 复审加注：本裁决是缓解、不是解决**——问的是规模，答的是开关，而默认路径的 N 一次没减；规模缺口已补进 §6.1 | §4.4 |
| Q4 | 治理决策投影进 leaderDM 混流是否可接受 | **接受方案 A（投影进 leaderDM）**，附加硬约束：`source != "message"` 必须以系统条目样式渲染（无头像气泡）；`source` 语义表升为契约明文 | §5.2 |
| Q5 | 房间刷新机制 | **v0.2 轮询**；SSE 另立项 | §5.3、§6.1 |
| Q6 | ReviewRequest 与治理决策统一决策夹 | **不统一**，v0.2 只含治理决策；ReviewRequest 走 main 既有面；issue 概览**保留 `required_checkpoints`** 供提示与跳转 | §3、§6 |
| Q7 | `/issues` 游标形状 | **沿用 offset 不透明游标**；v0.1 列表端点 `next_cursor` 恒 `null` 的现状一并收敛 | §2、§2.4 |
| Q8 | 空 issue（无轮次无草稿）的 `state` | **open**（理由采纳原文：空 issue 是「待规划」不是「已完成」，显 Closed 会让人以为工作做完了） | §2.1 规则 6 |

### 7.1 CONS-31 实现期追认（2026-08-11，随实现同批入文本）

| 追认项 | 内容 | 落点 |
| --- | --- | --- |
| 标签计数 | `/issues` 响应增加 `open_count` / `closed_count`（前端问询升级主脑后裁决） | §2、§2.5 |
| 列表分页 | `/issues` 落实 Q7 的 offer 游标（`?cursor=&limit=`），`next_cursor` 不再恒 null | §2.4 |
| 工作区归属 | `organization_id` 三级取值链，第三级为开票 agent 所属组织 | §2 |
| 发起者名 | `/issues` 与 `/issues/{id}` 增加 `opened_by_name`（nullable）。**值为 agent 资源名，不是人名**——与 v0.1 §4.2 `sender_name` 同源同精度；前端文案须写「AGENT xxx 发起」，不得呈现为同事姓名 | §2 |
| 列表筛选 | `?repository_id=` / `?phase=` **v0.2 不做**：列表响应无仓库字段、分页下的本地过滤是部分结果冒充全量。前端两个筛选按钮撤掉，另立 backlog | §2.4、§6.1 |
| 拓扑降级 | 未建团时拓扑派生字段返 null/空数组，实测种子拓扑表为空 | §2 |
| issue 全集 | 全集 = 有 ExecutionPlan 或 PlanSnapshot 的 project 并集；**§2.1 规则 6 当前不可达**（无注册表也无拓扑列举接口），issue 写端点落地后自动生效 | §2、§6.1 |

### 7.2 CONS-33 实现期追认（2026-08-11，随实现同批入文本）

| 追认项 | 内容 | 落点 |
| --- | --- | --- |
| 非 message 投影落点 | §5.2 只规定治理决策进 leaderDM。**runner 与 gate 投影进对应仓库的 teamRoom**（工作发生地），leaderDM 只含治理条目 | §5.2 |
| 硬约束的结构化保证 | `source != "message"` 的条目 `message` 字段**恒为 `null`**（由无法附加 message 的构造函数生成）。前端判据建议用 `message === null` 而非比对 `source` 字符串——同样的语义，更难写错 | §5.2 |
| 房间成员按类型 | teamRoom 成员 = 仓库 leader + workers；**leaderDM 成员 = 仓库 leader + 组织 leader**。leaderDM 列 workers 会误述「谁能读这个房间」 | §5.1 |
| 未建团的 issue | `/issues/{id}/rooms` 返回 `{"rooms": []}` 且 **HTTP 200**（不是 404）；issue 本身不存在才 404 | §5.1 |
| 无法解析的依赖名 | `task_dag[].depends_on` 中 catalog 查不到的仓库名**丢弃该边**，不产出带 null 端点的边 | §5.4 |
| spec 状态枚举校正 | §5.4 原文写 `draft\|submitted\|approved\|frozen`，**实际枚举无 `submitted`**：`draft\|in_review\|approved\|frozen\|superseded`（`specification/contracts.py`）。读模型透传真实值。**2026-08-11 再修**：本行当时只改了勘误表、没动 §5.4 正文（第三次「只勘正一节」），正文已同步；且本端点选取规则决定取值只可能是 `approved\|frozen` | §5.4 |
| 种子扩展 | 拓扑 + 双房间 + 4 仓库 leader/worker 注册 + A 两仓单仓 spec（frozen rev3 / approved rev2）；消息由占位房间迁入所属 teamRoom。**幂等，只动 5533** | 见 `scripts/seed-console-demo.py` |
| 名称解析恢复 | 补注册 principals 后 `members[].name`、v0.1 `messages[].sender_name`、`tasks[].agent` 不再恒 null（值仍是 agent 资源名） | §5.1、v0.1 §4.2 |
| live 锚点 | 派生自 in_progress 任务。**场景 C 的返工任务处于 in_progress，故 `!rm-team-c-billing` 与 `!rm-leader-c-billing` 实测 `live: true`**；A/B 任务全终态故 false。两个房间同属一仓，所以一仓在途时该仓两个房间都 live——这是 §5.3 原文「该房间所属仓库存在 in_progress 任务」的直接结果，不是 bug | §5.3 |
| 丢弃边留痕 | §5.4 丢弃无法解析的依赖名时**记 warning 日志**（含丢弃条数与 issue/仓库），不静默截断。选日志而非响应字段是因为 plan 端点形状已对前端冻结；如需自述字段再加。**2026-08-12：这个口子已经用了**——C-2 的 DAG 面板落地后，消费方自己声明「可能在画一张不完整的图」却无字段告知用户，故 §5.4 增三个自述计数（v0.4 附录 A.1），并同批补记边被丢弃的**第二个原因**（端点不在任何批次里） | §5.4 |

### 7.3 CONS-44 消费期勘误（2026-08-11，主脑允诺后同批入文本）

缘由：前端施工 CONS-44（仓库/团队/智能体/设置四页）消费 `console/*` 三端点时，发现两处
**契约文本与实现不符**。两项均为**纯文本勘正，实现零改动**——文本原就该这么写。

| 勘误项 | 原文 | 勘正后 | 落点 |
| --- | --- | --- | --- |
| `with_runtime=false` 的形状 | 「置 `false` 时整块 `runtime` 省略」 | 实现是**字段常在、值恒 `null`**（`service.py` 建行时即置 `"runtime": None`，`with_runtime` 只决定是否覆写）。按实现勘正，消费方不必兼容字段缺失 | §4.4 |
| `null` 同形歧义 | 未述 | `null` 是「404 / 未配置 / 未探测」三义同形，响应体不可自辨。**补消费方指引**：探测过的 `null` = 未接入；`with_runtime=false` 的 `null` **只表示未探测**，禁止呈现为「未接入」。前端按探测阶段门控的做法写入契约作参照 | §4.4 |
| `§4.2 repository_name` | `"string"` | `"string|null"`——`service.py:1189` 在 catalog 查不到 `repository_id` 时返 `None`（拓扑驻扎的仓库未必在 catalog 里） | §4.2 |
| `§5.1 repository_name`（**同源第三例，后端自行增补，请追认**） | `"string"` | 同上，`service.py:873` 同一失效模式。主脑只点名 §4.2；若只改一处，契约仍在另一处撒同样的谎，故一并勘正 | §5.1 |

`§4.3` 的 `repository_name` 起草时即标 `"string|null"`，无需改动——三处同一派生、原本只有一处写对。

**教训（承 §4.5 的「新增端点先查路径占用」）**：字段表的 nullable 标注若不是从实现逐字段对读
生成的，就会**只在作者当时想到的那一处写对**。同一派生出现在多节时，勘正必须全量搜索
（本次 `grep -n repository_name` 一次命中三处），否则等于把谎言从一处搬到另一处。

## 8. 实现顺序

裁决同时定下实现次序（每项独立任务分支，完成即报，验收锚点对 5533 四形态种子）：

1. **CONS-31** `/issues` 两端点（S）——前端 CONS-41 的 live 接线在等；
2. **CONS-33** 房间三端点 + `room_id` 补投影（M）；
3. **CONS-32** 网格 / 团队 / 花名册（M）。

## 9. 字段来源速查（新增部分）

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
