# 交付读模型契约 v0.1（Delivery Read Model）

- 状态：草案，待前后端 Owner 评审
- 版本：0.1
- 更新日期：2026-08-11
- 生产方：`api`（聚合视图，无独立事实源）
- 消费方：`frontend/`（交付控制台，见 `frontend-prototype/DESIGN-DECISION.md`）
- 关联：`docs/contracts/public-contracts-v0.1.md`（标识符与版本引用规则沿用）

本契约只定义**只读聚合**。它不引入新事实源：每个字段都注明来源模块；来源模块的
`contracts.py` 变化时本契约同步修订。后端未实现的字段显式标记 `nullable`，
返回 `null`，前端降级展示——禁止编造。

## 0. 聚合根定义

**一次「交付」（delivery）= 一个 Project 驱动的完整闭环**，对外以 `project_id` 为主键。
ExecutionPlan、ChangeSet、Task、ValidationSnapshot 都是它的子对象。理由：

- 后端没有单一 delivery 实体；Project 是唯一贯穿需求→计划→执行→交付的稳定 ID；
- 一个 Project 当前至多一个活跃 ExecutionPlan / ChangeSet（历史版本经 plan snapshots 保留）。

## 1. 端点

| 端点 | 用途 | 前端消费位置 |
| --- | --- | --- |
| `GET /api/v1/deliveries` | 交付列表（分页） | 左栏项目树 |
| `GET /api/v1/deliveries/{project_id}` | 交付全貌聚合 | 中栏 artifact 卡、计划纸面、环境窗 |
| `GET /api/v1/deliveries/{project_id}/events` | 合并事件流（游标分页） | 环境窗/未来房间流 |
| `GET /api/v1/deliveries/{project_id}/messages` | 协作消息流 | 对话主线程 |
| `GET /api/v1/deliveries/{project_id}/decisions` | 待决策项 | 决策夹 |
| `POST /api/v1/deliveries/{project_id}/governance-decisions` | 记录 head-bound 治理决策（唯一写端点） | 审批弹窗 |

鉴权沿用现有 `Authorization: Bearer`（读端点可用会话票据替代，另行定）。

## 2. `GET /deliveries` — 列表

```json
{
  "items": [
    {
      "project_id": "uuid",
      "project_key": "PRJ-2026-0042",
      "title": "string",                      // project 标题
      "phase": "contract|plan|execute|validate|release|delivered|failed",
      "phase_note": "string",                 // 人类可读补充，如 "2 完成 · 1 修复中"
      "pending_decision_count": 0,
      "updated_at": "UTC ISO 8601"
    }
  ],
  "next_cursor": "string|null"
}
```

`phase` 推导规则（读模型内实现，单一函数，配套单测）：

| 条件（按序判定） | phase |
| --- | --- |
| ChangeSet.status = DELIVERED | `delivered` |
| ChangeSet 存在且未终态 | `release` |
| ValidationSnapshot 存在且 ChangeSet 不存在 | `validate` |
| ExecutionPlan.status = IN_PROGRESS | `execute` |
| Plan snapshot 存在但 ExecutionPlan 未建 | `plan` |
| 仅有 Specification | `contract` |
| ExecutionPlan/ChangeSet 任一 FAILED / MANUAL_INTERVENTION | `failed` |

## 3. `GET /deliveries/{project_id}` — 全貌聚合

```json
{
  "project": {
    "project_id": "uuid", "project_key": "string", "title": "string",
    "requirement_text": "string|null",        // plan snapshot.requirement_text
    "created_at": "..."
  },
  "contract": {                                // specification (kind=ENGINEERING, FROZEN 优先)
    "specification_id": "uuid", "version": 3, "status": "frozen",
    "goal": "string",
    "acceptance": ["string"],
    "constraints": ["string"],
    "allowed_paths": ["string"],
    "tests": ["string"],
    "non_goals": null,                         // nullable：specification 暂无此字段（§6.2）
    "forbidden_paths": null,                   // nullable：同上
    "release_rules": null                      // nullable：同上（human_approval / rollback_condition）
  },
  "repositories": [                            // repository_intelligence
    { "repository_id": "uuid", "name": "string", "evidence": "string|null" }
  ],
  "plan": {                                    // plan snapshot + execution plan status
    "plan_id": "uuid", "plan_version": 2, "status": "in_progress",
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
      "display_status": "pending|running|repairing|succeeded|failed",   // §5.1 映射
      "agent": "string|null",                  // 由 assignee agent_directory 解析
      "attempt": 1,                            // 1 + 同仓 rework 链长度（§5.2）
      "depends_on": ["task_id"],               // plan snapshot task_dag
      "result_summary": "string|null",
      "repair_timeline": [                     // rework task + recovery action 合成，可为空
        { "at": "...", "what": "string" }
      ]
    }
  ],
  "change_set": {                              // delivery ChangeSetView 直投影，可为 null
    "change_set_id": "uuid", "status": "delivering", "merge_cursor": 1,
    "repositories": [
      {
        "repository_id": "uuid", "task_id": "uuid",
        "status": "pr_open|ci_pending|...|merged",          // 12 态原样透出
        "gate_display": "open|blocked|running|waiting",     // §5.3 映射
        "pull_request_url": "string|null", "pull_request_number": 1,
        "head_sha": "string", "base_sha": "string", "branch_name": "string",
        "depends_on": ["repository_id"], "merge_order": 1,
        "ci_checks": [{ "check_name": "string", "passed": true, "summary": "string" }],
        "required_checks": ["string"], "required_approvals": 1,
        "reviews": [{ "reviewer": "string", "state": "approved", "summary": "string" }],
        "merge_gate": { "allowed": false, "reasons": ["string"] },
        "merge_sha": "string|null"
      }
    ],
    "governance_decisions": [                  // GovernanceDecisionView 直投影
      { "id": "uuid", "repository_id": "uuid", "head_sha": "string",
        "decision": "ready|blocked|rollback_required",
        "decided_by_agent_id": "uuid", "reason": "string", "decided_at": "..." }
    ],
    "recovery_plans": [ { "trigger": "string", "reason": "string", "actions": [] } ]
  },
  "validation_snapshot": {                     // review_validation，可为 null
    "id": "uuid", "status": "passed", "candidate_heads": { "repository_id": "sha" },
    "environment_hash": "string", "expires_at": "..."
  },
  "diffs": [                                   // runner.completed 证据
    {
      "repository_id": "uuid", "run_id": "uuid", "commit_sha": "string",
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

### 4.1 `GET /deliveries/{project_id}/events`

合并三个来源为统一时间线（游标分页，`kind` 过滤）：

```json
{ "items": [ { "at": "...", "kind": "runner|matrix|gate|plan|deny",
               "text": "string", "task_id": "uuid|null", "repository_id": "uuid|null",
               "payload_ref": "string|null" } ],
  "next_cursor": "string|null" }
```

来源：`agent_runtime.runner_events`（runner）、`collaboration.messages` 投递记录（matrix）、
delivery CI/review/merge observations（gate）、plan snapshot 版本变化（plan）。
`deny`（权限拒绝）目前无审计存储，v0.1 不产出该 kind——出现即为契约违约。

### 4.2 `GET /deliveries/{project_id}/messages`

`CollaborationMessageView` 直投影（kind、subject、body、sender/recipient、status、
event_id、correlation_id）。已知限制：当前仅含 Leader→Worker 方向；Worker→Leader 回报
摄取是审计缺口（closed-loop-gap-analysis §4.2），补齐后本端点自然包含，契约不变。

### 4.3 `GET /deliveries/{project_id}/decisions`

```json
{ "items": [ {
  "id": "string", "kind": "approve|watch",
  "title": "string", "body": "string",
  "repository_id": "uuid|null", "head_sha": "string|null",
  "created_at": "...",
  "actions": ["approve_merge", "view_evidence"]   // 枚举，前端按 kind 渲染
} ] }
```

- `approve`：ChangeSet 中 `merge_gate.allowed=true` 且缺 READY 治理决策的仓库，每仓一项。
- `watch`：存在未终态 recovery plan / rework task 的仓库，每仓一项（纯派生，只读）。
- `clarify`：**v0.1 不提供**（无后端实体，见 §6.5）。

### 4.4 `POST /deliveries/{project_id}/governance-decisions`（唯一写端点）

包装既有 `RecordGovernanceDecisionCommand`，补上 API 层缺口（live-github-delivery-e2e
§8.4 遗留项）：

```json
{ "change_set_id": "uuid", "repository_id": "uuid",
  "head_sha": "string",                        // 必填：head-bound，SHA 漂移即 409
  "decision": "ready|blocked|rollback_required",
  "reason": "string",
  "idempotency_key": "string" }
```

鉴权主体必须解析为有治理权的 agent/人类身份并写审计事件。前端审批弹窗的
「任一 SHA 变化即失效」由 head-bound 语义 + merge gate fail-closed 保证，无需前端轮询锁。

## 5. 状态映射（读模型内唯一实现，禁止前端另行映射）

### 5.1 Task：后端 7 态 → 展示 5 态

| backend_status | display_status | 备注 |
| --- | --- | --- |
| assigned | pending | |
| in_progress | running | |
| in_progress 且存在未终态 rework 链 | repairing | §5.2 |
| blocked | repairing | 展示层归入修复中，note 说明 |
| succeeded | succeeded | |
| failed / cancelled | failed | |
| superseded | —（列表默认过滤） | 计划改版被替代 |

### 5.2 attempt 与修复时间线

`attempt = 1 + 指向同一 (repository, parent_task) 的 CI rework task 链长度`。
`repair_timeline` 由 rework task 创建事件 + recovery action 状态变化按时间合成。

### 5.3 RepositoryDelivery：12 态 → 门禁展示 4 态

| status | gate_display |
| --- | --- |
| ready_to_merge / merge_requested / merged | open |
| ci_failed / review_changes_requested / manual_intervention | blocked |
| pr_open / ci_pending / review_pending / compensation_pending / compensated | running |
| pending | waiting |

## 6. 已知缺口与降级约定

| # | 缺口 | v0.1 行为 | 补齐路径 |
| --- | --- | --- | --- |
| 6.1 | 交付列表/聚合此前不存在 | 本契约补齐 | — |
| 6.2 | Specification 无 non_goals / forbidden_paths / release_rules | 返回 `null`，前端隐藏区块 | specification 增可选字段（小版本兼容） |
| 6.3 | Runner 无 diffstat（±行数） | `diffstat: null`，前端只列文件名 | Runner 变更采集时补 `git diff --numstat` |
| 6.4 | 无 token/成本采集 | `cost: null`，前端隐藏成本行 | 独立观测任务 |
| 6.5 | clarify 决策无实体 | 决策夹不出现 clarify 类 | ChangeRequest / 澄清问答机制（另行设计） |
| 6.6 | deny 治理拦截未入审计 | events 不产出 `deny` | 审计缺口任务（gap-analysis §4.2） |
| 6.7 | Worker→Leader 回报未摄取 | messages 单向 | 同上 |
| 6.8 | 无统一 trace_id | `trace_id: null` | 观测线任务 |

## 7. 字段来源速查

| 聚合字段 | 生产模块 |
| --- | --- |
| project / phase | project + 读模型推导 |
| contract | specification |
| repositories / plan | repository_intelligence（plan snapshots） |
| tasks / attempt | task_orchestration（+ agent_directory 解析 agent 名） |
| change_set / governance / merge_gate | delivery |
| validation_snapshot | review_validation |
| diffs | agent_runtime（runner_events 终态载荷） |
| messages | collaboration |
| matrix_room_id | integrations/agentteams 投影记录 |
