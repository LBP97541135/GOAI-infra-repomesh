# 交付读模型契约 v0.1（Delivery Read Model）

- 状态：草案（已按 2026-08-11 评审结论修订），待后端 Owner 认领
- 版本：0.1
- 更新日期：2026-08-11
- 生产方：`api`（聚合视图，无独立事实源）
- 消费方：`frontend/`（交付控制台，见 `frontend-prototype/DESIGN-DECISION.md`）
- 关联：`docs/contracts/public-contracts-v0.1.md`（标识符与版本引用规则沿用）

本契约只定义**只读聚合**（外加两个治理写端点）。它不引入新事实源：每个字段都注明
来源模块；来源模块的 `contracts.py` 变化时本契约同步修订。后端未实现的字段显式标记
`nullable`，返回 `null`，前端降级展示——禁止编造。

## 0. 聚合根定义

**一次「交付」（delivery）= 一个 ExecutionPlan 的完整生命周期**，对外
`delivery_id = execution_plan_id`。Project 是组织容器，一个 Project 可挂多次交付
（失败重来、后续需求各成一轮）。理由：

- 产品定义的交付单元是「一次需求 → 一个 Release Candidate」，即一个 plan 的生命周期；
- 已有事实：一个 Project 会积累多个 plan（live-github-delivery-e2e §8.3 的遗留计划问题）；
- ChangeSet、PlanSnapshot 均已携带 `execution_plan_id` / `plan_id`，聚合天然成立。

ExecutionPlan 之前的阶段（需求澄清、契约起草、范围确认）尚无 plan id，由「Project 下
最新未物化的 Specification/PlanSnapshot」构成一个**虚拟草稿交付**（`delivery_id: null`，
`phase: contract|plan`），materialize 后获得正式 id。

## 1. 端点

| 端点 | 用途 | 前端消费位置 |
| --- | --- | --- |
| `GET /api/v1/deliveries` | 交付列表，按 project 分组（分页） | 左栏项目树 |
| `GET /api/v1/deliveries/{delivery_id}` | 交付全貌聚合 | 中栏 artifact 卡、计划纸面、环境窗 |
| `GET /api/v1/deliveries/{delivery_id}/events` | 合并事件流（游标分页） | 环境窗/未来房间流 |
| `GET /api/v1/deliveries/{delivery_id}/messages` | 协作消息流 | 对话主线程 |
| `GET /api/v1/deliveries/{delivery_id}/decisions` | 待决策项 | 决策夹 |
| `POST /api/v1/deliveries/{delivery_id}/governance-decisions` | 记录 head-bound 治理决策 | 审批弹窗 |
| `POST /api/v1/deliveries/{delivery_id}/archive` | 归档旧交付（运维缺口补齐） | 侧栏管理 |

鉴权沿用现有 `Authorization: Bearer`（读端点可用会话票据替代，另行定）。

## 2. `GET /deliveries` — 列表

```json
{
  "projects": [
    {
      "project_id": "uuid",
      "project_key": "string|null",         // 同 §3：Project 注册表未落地前为 null（§6.9）
      "title": "string",
      "deliveries": [
        {
          "delivery_id": "uuid|null",          // null = §0 虚拟草稿交付
          "title": "string",                    // plan 需求摘要或 project 标题
          "phase": "contract|plan|execute|validate|release|delivered|failed|archived",
          "phase_note": "string",               // 人类可读补充，如 "2 完成 · 1 修复中"
          "pending_decision_count": 0,
          "updated_at": "UTC ISO 8601|null"    // nullable：该轮既无 ChangeSet 也无快照时无时间源
        }
      ]
    }
  ],
  "next_cursor": "string|null"
}
```

`phase` 推导规则（读模型内实现，单一函数，配套单测）：

| 条件（按序判定） | phase |
| --- | --- |
| 已归档 | `archived` |
| Plan FAILED，或 ChangeSet MANUAL_INTERVENTION / COMPENSATED，且无活跃恢复 | `failed` |
| ChangeSet.status = DELIVERED | `delivered` |
| ChangeSet 存在且未终态 | `release` |
| ValidationSnapshot 存在且 ChangeSet 不存在 | `validate` |
| ExecutionPlan.status = IN_PROGRESS | `execute` |
| ExecutionPlan 已终态但尚无验证/交付证据 | `validate` |
| PlanSnapshot 存在但未 materialize | `plan` |
| 仅有 Specification | `contract` |

列表分页说明：v0.1 数据量下 `next_cursor` 恒为 `null`，游标语义保留待后续实现。

## 3. `GET /deliveries/{delivery_id}` — 全貌聚合

```json
{
  "delivery_id": "uuid",
  "project": {
    "project_id": "uuid",
    "project_key": "string|null",             // Project 实体/注册表未落地前为 null（§6.9）
    "title": "string",                        // 暂以 plan snapshot requirement_text 截断，Project 落地后切换
    "requirement_text": "string|null",        // plan snapshot.requirement_text
    "created_at": "...|null"                  // nullable：该 project 从无 PlanSnapshot（只有 ExecutionPlan）时
  },
  "contract": {                                // specification (kind=ENGINEERING, FROZEN 优先)；整体可为 null（该交付未建 ENGINEERING spec）
    "specification_id": "uuid", "version": 3, "status": "frozen",
    "goal": "string",
    "acceptance": ["string"],
    "constraints": ["string"],
    "allowed_paths": ["string"],
    "forbidden_paths": ["string"],             // specification 新增可选字段，随本读模型同批实现（§6.2）
    "tests": ["string"],
    "non_goals": null,                         // nullable：暂缓（§6.2）
    "release_rules": null                      // nullable：暂缓（§6.2）
  },
  "repositories": [                            // repository_intelligence
    { "repository_id": "uuid", "name": "string", "evidence": "string|null" }
  ],
  "plan": {                                    // plan snapshot + execution plan status
    "plan_version": 2,                         // nullable：该轮无 PlanSnapshot 时为 null
    "status": "in_progress",
    "current_batch_index": 1,
    "execution_batches": [["repo-name"]],
    "merge_order": ["repository_id"]           // 由 ChangeSet depends_on 拓扑排序导出
  },
  "tasks": [                                   // task_orchestration TaskView + DAG 边
    {
      "task_id": "uuid", "task_key": "string|null",
      "repository_id": "uuid",
      "title": "string",
      "backend_status": "assigned|in_progress|blocked|succeeded|failed|cancelled|superseded",
      "display_status": "pending|running|repairing|blocked|succeeded|failed",   // §5.1 映射（6 态）
      "agent": "string|null",                  // 由 assignee agent_directory 解析
      "attempt": 1,                            // 1 + 同仓 rework 链长度（§5.2）
      "depends_on": ["task_id"],               // plan snapshot task_dag
      "result_summary": "string|null",
      "evidence": {                            // 【提案 · 待主脑裁决】A-18，见 §5.4；无结构化证据时整块为 null
        "verified": false,                     // 派生自下面两个结构化字段，不读散文（§5.4）
        "blockers": ["string"],                // agent 结构化声明的 blocker，逐字；未声明即 []
        "summary_text": "string|null",         // Runner summary 原文，逐字，不摘要不截断
        "test_command": "string|null",
        "test_results": [{ "command": "string", "exit_code": 0, "summary": "string" }],
        "artifact_count": 0                    // 只报有无/几件：产物本身尚无可取端点
      },
      "repair_timeline": [                     // rework task + recovery action 合成，可为空
        { "at": "...", "what": "string" }
      ],
      "escalated_to_human": false              // §5.2：仅转述 recovery plan 的 MANUAL_INTERVENTION
    }
  ],
  "change_set": {                              // delivery ChangeSetView 直投影，可为 null
    "change_set_id": "uuid", "status": "delivering", "merge_cursor": 1,
    "repositories": [
      {
        "repository_id": "uuid", "task_id": "uuid",
        "status": "pr_open|ci_pending|...|merged",          // 12 态原样透出
        "gate_display": "open|blocked|running|waiting",     // §5.3 映射
        "pull_request_url": "string|null", "pull_request_number": "number|null",  // PR 创建前为 null
        "head_sha": "string",              // 候选 commit SHA（Runner 产出），非 base；ChangeSet 创建即存在，恒非空
        "base_sha": "string", "branch_name": "string",
        "depends_on": ["repository_id"], "merge_order": 1,
        "ci_checks": [{ "check_name": "string", "passed": true, "summary": "string" }],
        "required_checks": ["string"], "required_approvals": 1,
        "reviews": [{ "reviewer": "string", "state": "approved", "summary": "string" }],
        "merge_gate": { "allowed": false, "reasons": ["string"] },
                                           // 仅 pre-merge 状态有意义；status ∈ merge_requested/merged/
                                           // compensation_pending/compensated 时为 null（合并已发起或已过阶段）
        "merge_sha": "string|null"
      }
    ],
    "governance_decisions": [                  // GovernanceDecisionView 直投影
      { "id": "uuid", "repository_id": "uuid", "head_sha": "string",
        "decision": "ready|blocked|rollback_required",
        "decided_by_agent_id": "uuid", "reason": "string", "decided_at": "..." }
    ],
    "recovery_plans": [ { "trigger": "string", "reason": "string",
        "actions": [ { "kind": "string", "status": "string",
                       "repository_id": "uuid|null",     // 非仓库相关的动作为 null
                       "detail": "string" } ] } ]
                                               // RecoveryPlanView 的 id / created_at 有意不投影（非漏投影）
  },
  "validation_snapshot": {                     // review_validation，可为 null
    "id": "uuid", "status": "passed", "candidate_heads": { "repository_id": "sha" },
    "environment_hash": "string", "expires_at": "..."
  },
  "diffs": [                                   // runner.completed 证据
    {
      "repository_id": "uuid",
      "run_id": "uuid|null",                   // nullable：Runner 证据未带 runId 时（准入只校验 commitSha）
      "commit_sha": "string",
      "changed_files": ["path"],
      "diffstat": null                         // nullable：Runner 暂未采集 ±行数（§6.3）
    }
  ],
  "cost": null,                                // nullable：无 token/成本采集（§6.4）
  "matrix_room_id": "string|null",             // agentteams 投影
  "trace_id": "string|null"                    // 贯穿 trace 未实现前为 null
}
```

## 4. 事件、消息与决策

### 4.1 `GET /deliveries/{delivery_id}/events`

合并三个来源为统一时间线（游标分页，`kind` 过滤）：

```json
{ "items": [ { "at": "...", "kind": "runner|matrix|gate|plan|deny",
               "text": "string", "task_id": "uuid|null", "repository_id": "uuid|null",
               "payload_ref": "string|null" } ],
  "next_cursor": "string|null" }
```

来源：`agent_runtime.runner_events`（runner）、`collaboration.messages` 投递记录（matrix）、
delivery CI/review/merge observations（gate）、plan snapshot 版本变化（plan）。
`deny`（权限拒绝）目前无审计存储，v0.1 不产出该 kind——`kind=deny` 过滤合法且恒返回
空集，条目出现即为契约违约。matrix 条目时间戳来源为 `collaboration.messages.created_at`
（该列原不存在，2026-08-11 主脑追认加列，迁移 `20260811_0017`——`at` 必须取持久化
时间戳、不得编造，与 §5.2 repair_timeline 同一裁决）。

### 4.2 `GET /deliveries/{delivery_id}/messages`

`CollaborationMessageView` 直投影（kind、subject、body、sender/recipient、status、
event_id、correlation_id）。已知限制：当前仅含 Leader→Worker 方向；Worker→Leader 回报
摄取是审计缺口（closed-loop-gap-analysis §4.2），补齐后本端点自然包含，契约不变。
响应另含附加字段 `direction`/`sender_name`/`recipient_name`/`created_at`
（2026-08-11 主脑追认）：`direction=leader_to_worker` 显式标记上述单向限制，
前端以该字段辨识，勿以列表恒单向为前提硬编码。

**本端点不分页**（2026-08-11 澄清，消费方问询后补写）：响应恒为 `{"items": [...]}`，
**没有 `next_cursor`**，也不接受 `?cursor=`——与 §4.3 decisions 同风格，与 §4.1 events
不同。一条交付的消息量以「一屏读完」为设计前提；真正需要翻页的是**房间流**，那是
v0.2 §5.2 `/rooms/{room_id}/stream` 的职责，不在此端点重复一套游标。
本段是澄清既有行为、不改变实现——写下来是因为消费方曾按 §4.1 的形状类推补了游标字段。
v0.2 另为本端点补投影 `room_id`（见 v0.2 §5.2），与房间流共用同一投影函数。

**再补三个附加字段 `id` / `repository_id` / `task_id`（2026-08-11 主脑追认，消费方
code-review 问询后补写，与 `direction` 先例同类）**：投影函数直出 view 的这三列，起草时
正文枚举没写全，实现从未变过。语义与可空性以 `collaboration/contracts.py` 的
`CollaborationMessageView` 为准：

- `id`：消息主键，**恒非空**（`UUID`）——消费方可安全用作列表 key；
- `repository_id`：`UUID | None`，**非仓库相关的消息为 `null`**；
- `task_id`：`UUID | None`，**非任务相关的消息为 `null`**。

投影函数 `_message_item()` 由 `/messages` 与 v0.2 §5.2 房间流**共用**，故 `items[].message`
里同样有这三个字段，两端形状恒等（这正是当初共用一个函数的目的）。

至此本端点响应的 16 个键**全部见于契约文本**：正文枚举 8（`kind` / `subject` / `body` /
`sender_agent_id` / `recipient_agent_id` / `status` / `event_id` / `correlation_id`）+
前段追认 4（`direction` / `sender_name` / `recipient_name` / `created_at`）+ `room_id` 1 +
本段 3。**记账教训（同 v0.2 §7.3）**：「直投影」这种省略式写法会系统性漏字段——
凡写「直投影」的段落，字段表都应从投影函数逐字段对读生成，而不是凭印象枚举。

### 4.3 `GET /deliveries/{delivery_id}/decisions`

```json
{ "items": [ {
  "id": "string", "kind": "approve|watch",
  "title": "string", "body": "string",
  "repository_id": "uuid|null",
  "head_sha": "string",                           // 恒非空：两类决策项都取 RepositoryDeliveryView.commit_sha

  "created_at": "...",
  "actions": ["approve_merge", "view_evidence"]   // 枚举，前端按 kind 渲染
} ] }
```

两类均为**纯派生只读**，不新建实体：

- `approve`：merge gate 评估中**除「缺 head-bound READY 治理决策」外无其他阻塞原因**的仓库
  （CI/审批/依赖/验证全过、仅差治理放行），每仓一项。原「allowed=true 且缺 READY」表述
  为自相矛盾（gate 本身把缺 READY 计入 allowed=false），2026-08-11 修正。治理缺失的
  reason 由 delivery contracts 导出常量，读模型据此判定，禁止散落魔法字符串。
- `watch`：存在未终态 recovery plan / rework task 的仓库，每仓一项。
- `clarify`：**v0.1 不提供**。它需要「Agent 提问 → 定向到人 → 回答结构化回写契约 →
  通知 Worker」的完整 ChangeRequest 回路（team-handoff §5.4），不做只读残缺版；
  Demo 演示走前端回放模式（mock 数据），不受影响。

### 4.4 `POST /deliveries/{delivery_id}/governance-decisions`

包装既有 `RecordGovernanceDecisionCommand`，补上 API 层缺口（live-github-delivery-e2e
§8.4 遗留项）：

```json
{ "change_set_id": "uuid", "repository_id": "uuid",
  "head_sha": "string",                        // 必填：head-bound，SHA 漂移即 409
  "decision": "ready|blocked|rollback_required",
  "decided_by_agent_id": "uuid",               // 必填：决策主体（bearer 为共享动作 token，无法承载身份）
  "reason": "string",
  "idempotency_key": "string" }
```

响应体（**2026-08-11 补记**：起草时只定义了请求体与错误码，实现一直返回
`GovernanceDecisionView` 直投影）：

```json
{ "id": "uuid", "repository_id": "uuid", "head_sha": "string",
  "decision": "ready|blocked|rollback_required",
  "decided_by_agent_id": "uuid", "reason": "string", "decided_at": "..." }
```

`decided_by_agent_id` 必须是同组织活跃的 ORGANIZATION_LEADER 或该仓库的
REPOSITORY_LEADER，否则 403；每次落盘写 platform 审计事件。幂等语义为
**内容重放去重**（相同决策重放 no-op、版本不涨）；head-bound 下幂等键复用无害，
如需严格 key 存储语义后补 `platform.idempotency_records`。前端审批弹窗的
「任一 SHA 变化即失效」由 head-bound 语义 + merge gate fail-closed 保证，无需前端轮询锁。

### 4.5 `POST /deliveries/{delivery_id}/archive`

归档非活跃交付（幂等）。仅允许终态（delivered / failed / 无活跃 ChangeSet 且 plan 非
IN_PROGRESS）；活跃交付返回 409。归档不删数据，列表默认过滤 `archived`。

响应体（**2026-08-11 补记**，同 §4.4）：

```json
{ "delivery_id": "uuid", "archived_at": "..." }
```

**查询参数 `GET /deliveries?include_archived=`（2026-08-11 补记）**：默认 `false`，
即上面那句「列表默认过滤 archived」；置 `true` 时归档交付一并返回。起草时只写了默认
行为、没写这个开关，于是契约里读不到「如何看已归档交付」的路径，而实现一直有。

### 4.6 回滚：`GET .../rollback-scope` + `POST .../rollback`（**2026-08-12 新增，批次 E-1**）

GUI 设计定稿 ④ 的回滚对话框需要两件事：**回滚会撤销什么**（范围表）与**提交回滚决策**。
两者都不让前端自己算——「哪个仓走 revert PR、排第几步」的规则在 delivery 的
recovery planner 里，读模型只是把同一个 planner 跑在预览模式上（`preview_recovery`，
不落盘）后转述结果。

**读：`GET /deliveries/{delivery_id}/rollback-scope`**

```json
{ "delivery_id": "uuid",
  "change_set_id": "uuid|null",
  "available": true,                       // false = 没有可撤销的东西，入口不显示
  "unavailable_reason": "no_change_set|nothing_delivered|null",
  "recovery_in_progress": false,           // 已有未完成 recovery plan，再提交必 409
  "repositories": [
    { "repository_id": "uuid", "name": "string",
      "state": "merged|unmerged",                    // merge_sha 有无
      "action": "revert_pull_request|withhold|none", // 该仓在计划里的第一个动作
      "step": 1,                                     // 逆序动作序号；action=none 时 null
      "merge_sha": "string|null", "pull_request_number": 1 } ] }
```

- **无 ChangeSet 返 200**（`available:false` + `no_change_set`），不是 404——
  「本轮还没有发布过候选」是对话框要讲的一种状态，同 §5.1 空房间清单的口径；
  只有交付本身不存在才是 404。
- `state` / `action` 是**机器口径的枚举**，中文措辞（免费撤回 / revert PR 第 k 步）
  由前端 `display.ts` 渲染，与 §5.3 `gate_display` 同一分工；读模型不出中文。
- `action` 只有两种取值来自计划：`CLOSE_PULL_REQUEST → withhold`、
  `CREATE_REVERT_PULL_REQUEST → revert_pull_request`。`MERGE_REVERT_PULL_REQUEST`
  是同一仓 revert 的后半段、`REVALIDATE_CHANGESET` 属于整个 ChangeSet，都不开行。

**写：`POST /deliveries/{delivery_id}/rollback`**

```json
{ "change_set_id": "uuid", "reason": "string",
  "requested_by_agent_id": "uuid", "idempotency_key": "string" }
```

**无 `repository_id`、无 `head_sha`**：粒度＝GUI 裁决 4「只做整 change set」，
给出仓库字段等于承诺一个不存在的选择；各仓当前 head 由服务端自己读，不信浏览器枚举。

一次调用两个写，因为它们必须同生共死：

1. 每个候选一条 **head-bound `ROLLBACK_REQUIRED` 治理决策**——这才是真正堵死 merge
   gate 的东西（§4.4 同一实体，gate 消费见 `evaluate_merge_gate`）；
2. 一个 `OPERATOR_REQUESTED` **recovery plan**——恢复 saga 下一轮（默认 30s，
   `delivery_recovery_interval_seconds`）接管执行。

响应体：

```json
{ "delivery_id": "uuid", "change_set_id": "uuid",
  "decisions": [ /* §4.4 的 GovernanceDecisionView，每仓一条 */ ],
  "recovery_plan": { "id": "uuid", "trigger": "operator_requested", "reason": "...",
                     "created_at": "...", "actions": [ /* RecoveryActionView */ ] },
  "replayed": false }
```

- 主体必须是同组织**活跃 ORGANIZATION_LEADER**——§4.4 允许的 repository leader
  分支在这里没有：这条命令替**所有**仓库说话，仓库 leader 不能。否则 403。
- 幂等：同 `reason`、同主体、且已存在同 reason 的 operator-requested plan → 200
  且 `replayed: true`，**零写入、版本不涨**（与 §4.4 的内容重放去重同一风格）。
- **已有未完成 recovery plan 且 reason 不同 → 409**，且**一条决策都不写**：
  半写的 409 会留下一个「gate 已堵死、计划却没建」的状态。
- 404 = 交付不存在或从未建过 ChangeSet；409 = `change_set_id` 不属于该交付；
  422 = `reason` 或 `idempotency_key` 为空。
- **不承诺干净还原**：revert PR 仍要过自己的 CI（`_require_revert_checks`），
  冲突由 saga 自动开冲突处理任务。界面文案与本条一致，实现不得反过来暗示别的。

## 5. 状态映射（读模型内唯一实现，禁止前端另行映射）

### 5.1 Task：后端 7 态 → 展示 6 态

| backend_status | display_status | 备注 |
| --- | --- | --- |
| assigned | pending | |
| in_progress | running | |
| in_progress 且存在未终态 rework 链 | repairing | §5.2 |
| blocked | blocked | 独立展示态，不并入 repairing |
| succeeded | succeeded | |
| failed / cancelled | failed | |
| superseded | —（列表默认过滤） | 计划改版被替代 |

### 5.2 attempt、修复时间线与人工升级

- `attempt = 1 + 指向同一 (repository, parent_task) 的 CI rework task 链长度`。
- `repair_timeline` 由 rework task 创建事件 + recovery action 状态变化按时间合成。
- `escalated_to_human`：**读模型不做任何升级判断**。「第 N 次失败升级人工」是业务策略，
  其结论已表达为 delivery recovery plan 中的 `MANUAL_INTERVENTION` action；读模型仅当
  该 action 存在且未终态时置 `true`。策略调整发生在 delivery/task_orchestration，
  与本契约无关。

### 5.3 RepositoryDelivery：12 态 → 门禁展示 4 态

| status | gate_display |
| --- | --- |
| ready_to_merge / merge_requested / merged | open |
| ci_failed / review_changes_requested / manual_intervention | blocked |
| pr_open / ci_pending / review_pending / compensation_pending / compensated | running |
| pending | waiting |

### 5.4 `tasks[].evidence`：agent 自述的验证状态（【提案 · 待主脑裁决】，A-18）

缺陷事实：live 任务 `6ba476ab`（run `d261dbb4`）以 `runner.completed` 收尾、`succeeded`、
GUI 画绿「已交付」；而同一份载荷里 `testResults: []`、`testCommand: null`、`artifacts: []`，
agent 自己的 `summary` 开头写着 "I could not execute anything to verify it"、
"Please re-run before merging."。这些字都在读模型的数据里（`result_summary` 那个 JSON
字符串），**没有一个字段装它们**，所以界面全页 grep 不到「blocker/未执行」——而这块不可见
正好压在整条链第一个不可逆动作（merge 审批）之前，delivery_auto 开启时 CI 绿即自动合。

本节是把已有事实**转述**出来，不新增判断：

| 字段 | 取自 | 规则 |
| --- | --- | --- |
| `verified` | `test_results` | **至少一条**已记录的测试命令，且每条 `exit_code == 0`。空列表 = 未验证。终态 `succeeded` 只说明进程跑完了，从不说明它检查过自己。 |
| `blockers` | 载荷的 `blockers` 列表 | 逐字透传；**仅当载荷把它声明为字符串列表时**。 |
| `summary_text` | 载荷的 `summary` | 逐字，不摘要、不截断、不重述。 |
| `test_command` / `test_results` / `artifact_count` | 同名载荷键 | 直投影；`artifact_count` 只报件数。 |

三条边界，都是有意的：

1. **不从散文里挖 blocker。** live 那条把 blocker 写成了 markdown 小节，标题是 agent 自拟的
   「## Blockers and gaps — read before accepting」。按标题匹配就会对下一个写「## Caveats」
   的 agent 报「0 条 blocker」——一个凭空造出来的区别，正是 A-18 本身的缺陷形态。故今天
   `blockers` 对所有存量行恒 `[]`，agent 的原话由 `summary_text` 逐字呈现。补齐路径见 6.12。
2. **`evidence: null` ≠ `verified: false`。** 前者是「没有任何结构化证据」（superseded、纯散文
   回报、Runner 之前的行），后者是「它自己说没验证」。压成一个，等于给从没做过声明的行扣帽子。
3. **`skipped_tests` 未纳入本提案。** Runner 的 `TestCommandResult` 只有 `command` + `exit_code`，
   没有 skip 语义；live 那条的「跳过的跨仓一致性测试」写在散文里。可派生的结构化来源不存在，
   加一个恒空字段只是摆一个永不亮的灯。补齐路径见 6.13。

**本提案不动合并门禁**（`merge_gate` / delivery_auto 行为一字未改）：界面在审批点把话摆出来，
不替人做决定。门禁语义是另一轮裁决。

## 6. 已知缺口与降级约定

| # | 缺口 | v0.1 行为 | 补齐路径 |
| --- | --- | --- | --- |
| 6.1 | 交付列表/聚合此前不存在 | 本契约补齐 | — |
| 6.2 | Specification 缺三字段 | `forbidden_paths` **随读模型同批实现**（治理语义，Runner 限权另一半）；`non_goals` / `release_rules` 返回 `null`，前端隐藏 | specification 增可选字段（小版本兼容） |
| 6.3 | Runner 无 diffstat（±行数） | `diffstat: null`，前端只列文件名 | Runner 变更采集时补 `git diff --numstat` |
| 6.4 | 无 token/成本采集 | `cost: null`，前端隐藏成本行 | 独立观测任务 |
| 6.5 | clarify 决策无实体 | 决策夹不出现 clarify 类 | ChangeRequest / 澄清问答机制（另行设计） |
| 6.6 | deny 治理拦截未入审计 | events 不产出 `deny` | 审计缺口任务（gap-analysis §4.2） |
| 6.7 | Worker→Leader 回报未摄取 | messages 单向 | 同上 |
| 6.8 | 无统一 trace_id | `trace_id: null` | 观测线任务 |
| 6.9 | 无 Project 实体/注册表 | `project_key: null`，title 用 requirement 截断 | project 模块落地后切换 |
| 6.10 | 发现证据未按 project 存储 | `repositories[].evidence: null` | repository_intelligence 证据关联 |
| 6.11 | 多团队时 Matrix 房间歧义 | 仅单仓/单团队时给 `matrix_room_id`，否则 null | 团队↔交付关联建模 |
| 6.12 | Runner 不声明 blocker（【提案】A-18） | `tasks[].evidence.blockers` 对存量行恒 `[]`；agent 原话由 `summary_text` 逐字呈现，界面从那里显示 | `RunnerExecutionResult` 增 `blockers: tuple[str,...]`，网关已预留透传（§5.4） |
| 6.13 | Runner 不声明 skip（【提案】A-18） | 不投影 `skipped_tests`——`TestCommandResult` 只有 `command`/`exit_code`，无 skip 语义 | `TestCommandResult` 增 skip 语义后另提 |

## 7. 字段来源速查

| 聚合字段 | 生产模块 |
| --- | --- |
| delivery_id / plan / phase | repository_intelligence（execution plan、plan snapshots）+ 读模型推导 |
| project | project |
| contract | specification |
| repositories | repository_intelligence |
| tasks / attempt | task_orchestration（+ agent_directory 解析 agent 名） |
| escalated_to_human / change_set / governance / merge_gate | delivery |
| validation_snapshot | review_validation |
| diffs | agent_runtime（runner_events 终态载荷） |
| messages | collaboration |
| matrix_room_id | integrations/agentteams 投影记录 |
