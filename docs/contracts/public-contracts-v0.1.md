# RepoMesh 公共契约 v0.1

- 状态：第一批开发基线
- 版本：0.1
- 更新日期：2026-08-02
- 适用范围：模块间调用、HTTP 命令、事件和 Adapter 边界

本文定义语义和最小字段。Python 中真正可导入的契约必须由生产模块放在
`repomesh.modules.<module>.contracts`，并配套契约测试。本文没有定义的字段不得由消费方猜测。

## 1. 契约规则

1. 一个契约只有一个生产模块；消费方不得复制同名 DTO。
2. 模块间只导入生产方 `contracts`，不得导入其 domain、application 或 infrastructure。
3. 数据库表、ORM Model、AgentTeams 消息、GitHub Payload 都不是公共业务契约。
4. 新增可选字段保持小版本兼容；删除、改名或改变语义必须增加 `schema_version`。
5. Event 描述已经在同一事务提交的事实，不得表达“准备做”。
6. Command 可被拒绝；Event 不可被消费者改写。
7. 时间统一 UTC ISO 8601，主键统一 UUID，面向人的 key 单独保存。

## 2. 公共标识符

| 名称 | 类型 | 生成方 | 说明 |
| --- | --- | --- | --- |
| `organization_id` | UUID | Identity Access | 长期组织边界 |
| `repository_id` | UUID | Repository Intelligence | 仓库稳定身份，不使用 URL 作主键 |
| `project_id` | UUID | Project | 一次 PRD 驱动的项目 |
| `project_key` | string | Project | `PRJ-YYYY-NNNN` |
| `workstream_id` | UUID | Project | Project 中一个仓库的工作流 |
| `workstream_key` | string | Project | `{project_key}-{repo_short_name}` |
| `task_id` | UUID | Task Orchestration | 可调度最小单元 |
| `task_key` | string | Task Orchestration | `{project_key}-{repo_short_name}-{NN}` |
| `run_id` | UUID | Agent Runtime | 一次执行尝试 |
| `event_id` | UUID | 事件生产方 | 全局唯一，消费者幂等键 |
| `correlation_id` | UUID | 最外层调用方 | 一条端到端操作链 |
| `causation_id` | UUID/null | 当前生产方 | 直接原因命令或事件 |

不同类型的 UUID 不可互换。代码中逐步使用强类型 ID；在 API/事件序列化时仍为 UUID 字符串。

## 3. 版本引用

所有不可变版本对象使用同一语义：

```json
{
  "object_id": "uuid",
  "version_id": "uuid",
  "version": 3,
  "content_hash": "sha256:...",
  "created_at": "2026-08-02T10:00:00Z",
  "created_by": "actor-id"
}
```

`version_id` 指向固定内容；`object_id` 指向逻辑对象。消费方必须保存实际使用的 `version_id`，
不得只保存“当前版本”。

## 4. Command 契约

写操作使用明确命令，禁止通用 PATCH 任意改状态。

### HTTP 元数据

| 字段 | 位置 | 必须 | 规则 |
| --- | --- | --- | --- |
| `Idempotency-Key` | Header | 是 | 同一次重试保持不变 |
| `If-Match-Version` | Header | 修改已有聚合时 | 当前 `state_version` |
| `X-Correlation-Id` | Header | 否 | 缺失时入口生成 |
| `actor_id/type` | 认证上下文 | 是 | 服务端注入，禁止正文伪造 |
| `organization_id` | 认证上下文/路径 | 是 | 必须经过授权校验 |

内部命令的最小信封：

```json
{
  "command_id": "uuid",
  "command_type": "ConfirmRepositoryScope",
  "schema_version": 1,
  "idempotency_key": "caller-generated-key",
  "expected_version": 4,
  "organization_id": "uuid",
  "project_id": "uuid-or-null",
  "actor": {"type": "human|agent|service", "id": "stable-id"},
  "correlation_id": "uuid",
  "causation_id": "uuid-or-null",
  "issued_at": "UTC timestamp",
  "payload": {}
}
```

### 通用失败响应

| HTTP | `code` | 含义 |
| --- | --- | --- |
| 403 | `permission_denied` | actor 没有该动作权限 |
| 409 | `version_conflict` | `expected_version` 不是当前版本 |
| 409 | `invalid_state_transition` | 当前状态不允许执行命令 |
| 409 | `idempotency_conflict` | 同 key 对应不同请求 hash |
| 409 | `scope_not_confirmed` | 仓库范围尚未最终确认 |
| 422 | `validation_error` | 请求字段或业务输入无效 |
| 503 | `dependency_unavailable` | PostgreSQL、SCM、AgentTeams 等暂不可用 |

错误体至少包含 `code`、`message`、`correlation_id`；冲突时增加 `current_version` 和
`allowed_actions`。

## 5. Event Envelope

当前 Python 基础类型为 `repomesh.shared.events.EventEnvelope`。持久化时数据库补充
`recorded_at`。

```json
{
  "event_id": "uuid",
  "event_type": "RepositoryScopeConfirmed",
  "schema_version": 1,
  "occurred_at": "UTC timestamp",
  "recorded_at": "UTC timestamp",
  "actor_type": "human|agent|service",
  "actor_id": "stable-id",
  "organization_id": "uuid-or-null",
  "project_id": "uuid-or-null",
  "workstream_id": "uuid-or-null",
  "task_id": "uuid-or-null",
  "run_id": "uuid-or-null",
  "correlation_id": "uuid",
  "causation_id": "uuid-or-null",
  "aggregate_type": "Project",
  "aggregate_id": "uuid",
  "aggregate_version": 5,
  "payload": {}
}
```

消费者按 `event_id` 幂等；同一 aggregate 的顺序使用 `aggregate_version` 判断。

## 6. 第一批核心 DTO

### RepositoryProfileRef

生产方：Repository Intelligence。消费方：Project、Specification、Context。

```json
{
  "repository_id": "uuid",
  "profile_version_id": "uuid",
  "version": 2,
  "source_sha": "git-sha",
  "content_hash": "sha256:...",
  "freshness": "fresh|stale|scanning|failed",
  "evidence_refs": ["evidence-id"]
}
```

### EngineeringSpecRef

生产方：Specification。消费方：Repository Intelligence、Project、Task Orchestration、Validation。

```json
{
  "project_id": "uuid",
  "spec_id": "uuid",
  "spec_version_id": "uuid",
  "version": 1,
  "content_hash": "sha256:...",
  "acceptance_criteria_ids": ["uuid"],
  "status": "draft|published|superseded"
}
```

### RepositoryScopeCandidate

生产方：Repository Intelligence。消费方：Project。

```json
{
  "project_id": "uuid",
  "repository_id": "uuid",
  "profile_version_id": "uuid",
  "proposed_classification": "required|conditional|validation_only|excluded",
  "confidence": 0.86,
  "reason": "human-readable summary",
  "evidence_refs": ["evidence-id"]
}
```

分类只能取四个固定值；算法输出只是建议，不能自动成为最终范围。

### RepositoryScopeConfirmed

生产方：Project。消费方：Specification、Task Orchestration、Context、Validation。

```json
{
  "project_id": "uuid",
  "scope_version": 3,
  "repositories": [
    {
      "repository_id": "uuid",
      "profile_version_id": "uuid",
      "classification": "required",
      "scope_review_id": "uuid"
    }
  ],
  "confirmed_by": "actor-id",
  "confirmed_at": "UTC timestamp"
}
```

现有 `RepositorySelected` 是过渡契约；第一批开发在出现消费方前，应由 Project 模块的
`RepositoryScopeConfirmed` 替换，避免 Repository Intelligence 越权确认范围。

### ContractRef

生产方：Specification。消费方：Project、Task Orchestration、Context、Validation、Delivery。

```json
{
  "contract_id": "uuid",
  "contract_version_id": "uuid",
  "project_id": "uuid",
  "version": 2,
  "content_hash": "sha256:...",
  "owner_agent_id": "uuid",
  "producer_repository_id": "uuid",
  "consumer_repository_ids": ["uuid"],
  "status": "draft|in_review|frozen|superseded"
}
```

### TaskExecutionRequest

生产方：Task Orchestration。消费方：Agent Runtime。

```json
{
  "task_id": "uuid",
  "task_spec_version_id": "uuid",
  "run_number": 1,
  "repository_id": "uuid",
  "base_revision": "git-sha",
  "context_bundle_version_id": "uuid",
  "allowed_paths": ["src/**"],
  "denied_paths": [".github/**"],
  "acceptance_check_ids": ["uuid"],
  "timeout_seconds": 1800
}
```

每个 Task 只有一个主仓库。多仓库 Workspace 只能用于联合验证。

### CodingRunResult

生产方：Agent Runtime。消费方：Task Orchestration、Review And Validation、Observability。

```json
{
  "run_id": "uuid",
  "task_id": "uuid",
  "task_spec_version_id": "uuid",
  "status": "succeeded|failed|cancelled|timed_out|interrupted|waiting_for_input",
  "candidate_ref": "uuid-or-null",
  "checkpoint_ref": "uuid-or-null",
  "changed_files": ["path"],
  "test_evidence_refs": ["uuid"],
  "provider_session_ref": "opaque-reference-or-null",
  "summary": "observable result summary"
}
```

不得包含隐藏思维过程、远程 Git 凭据或完整密钥。

### ValidationSnapshotRef

生产方：Review And Validation。消费方：Delivery、Project、Observability。

```json
{
  "validation_snapshot_id": "uuid",
  "project_id": "uuid",
  "candidate_refs": [{"repository_id": "uuid", "candidate_id": "uuid", "sha": "git-sha"}],
  "contract_version_ids": ["uuid"],
  "test_plan_version_id": "uuid",
  "environment_hash": "sha256:...",
  "content_hash": "sha256:..."
}
```

任何 Candidate、Contract、TestPlan 或关键环境变化都必须创建新 Snapshot。

## 7. Command 所有权

| 生产模块 | Commands |
| --- | --- |
| Repository Intelligence | `StartRepositoryBaselineScan`、`PublishRepositoryProfile`、`MarkRepositoryProfileStale`、`StartRepositoryDiscovery` |
| Project | `CreateProject`、`ProposeRepositoryScope`、`ReviewRepositoryScope`、`ConfirmRepositoryScope`、`AssignRepositoryManager`、`ArchiveProject` |
| Specification | `PublishEngineeringSpec`、`AssignContractOwner`、`PublishContractVersion`、`FreezeContract` |
| Task Orchestration | `PublishTaskSpec`、`AssignTask`、`StartTaskRun`、`SubmitTaskResult` |
| Review And Validation | `PublishTestPlan`、`ReviewCandidate`、`RecordTaskValidation`、`RecordRepositoryIntegrationResult`、`CreateValidationSnapshot`、`RecordProjectJointTestResult`、`RecordProjectRegressionTestResult` |
| Change Control | `RaiseChangeRequest`、`ApproveChangeRequest`、`RejectChangeRequest` |
| Delivery | `ReconcileSCMObservation`、`CreateCIReworkTask`、`PrepareChangeSet`、`CreateRepositoryPullRequest` |

Command 名称属于生产模块；其他模块只能请求执行，不能直接更新生产方状态。

## 8. 必须发布的事件

| 生产模块 | Events | 主要消费方 |
| --- | --- | --- |
| Repository Intelligence | `RepositoryScanStarted`、`RepositoryProfilePublished`、`RepositoryProfileMarkedStale`、`RepositoryDiscoveryCompleted` | Project、Observability |
| Project | `ProjectCreated`、`RepositoryScopeProposed`、`RepositoryScopeReviewed`、`RepositoryScopeConfirmed` | Specification、Task、Context |
| Specification | `EngineeringSpecPublished`、`ContractOwnerAssigned`、`ContractFrozen` | Project、Task、Validation |
| Task Orchestration | `TaskSpecPublished`、`TaskReady`、`TaskRunRequested`、`TaskBecameStale` | Runtime、Context、Observability |
| Context | `ContextBundlePublished`、`ContextAccessRecorded` | Runtime、Observability |
| Agent Runtime | `AdapterProbeCompleted`、`AgentSessionStarted`、`AgentSessionRestored`、`CodingRunFinished`、`WorkspacePreserved`、`WorkspaceRestoreConflict` | Task、Validation、Observability |
| Review And Validation | `ReviewChangesRequested`、`TaskValidationRecorded`、`RepositoryIntegrationCompleted`、`ValidationSnapshotCreated`、`ProjectJointTestCompleted`、`ProjectRegressionTestCompleted` | Task、Delivery、Project |
| Delivery | `SCMObservationRecorded`、`CIFailureDetected`、`ChangeSetPrepared`、`PullRequestCreated` | Project、Validation、Observability |
| Change Control | `ChangeRequestRaised`、`ChangeRequestApproved`、`ChangeRequestRejected` | Project、Spec、Task、Validation |

## 9. 查询契约

查询由拥有最终读模型的模块提供，不允许前端跨 Schema 拼接：

| Query | Owner | 输出重点 |
| --- | --- | --- |
| Repository Catalog | Repository Intelligence | 当前 Profile、SHA、新鲜度、责任人、证据 |
| Project Overview | Project | 状态、范围、Workstream、门禁、风险、ChangeSet |
| Task Detail | Task Orchestration | TaskSpec、依赖、Run、Context、Candidate、Review |
| Agent Activity | Agent Runtime | 活动 Run、Session、资源、状态 |
| Collaboration | Collaboration | Issue、ChangeRequest、Decision、Contract 审批 |
| ChangeSet Detail | Delivery | 候选、验证、PR、依赖、合并和回滚 |

初次加载使用 Query API，增量更新使用 SSE/WebSocket；禁止固定高频轮询作为主要机制。

## 10. 契约变更流程

1. 生产方先修改本文和自己的 `contracts.py`。
2. 增加生产方序列化测试、旧版本读取测试和消费方契约测试。
3. 在 PR 中列出受影响消费者、上线顺序和回滚方式。
4. 生产方与至少一个受影响消费方共同 Review。
5. 非兼容变化增加 schema version，先发布兼容读取，再迁移消费者，最后停止旧版本。

