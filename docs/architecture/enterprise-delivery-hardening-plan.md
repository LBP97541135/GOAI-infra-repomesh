# RepoMesh 企业级多仓库交付强化方案

## 1. 目标与当前基线

目标是把 RepoMesh 从“能够由多 Agent 生成多个仓库 Commit”提升为“能够可信、可控、可恢复地交付多个仓库 PR”。

当前已经具备：

- PRD、仓库发现、仓库确认与 IntegratedPlan；
- Engineering Spec、Contract Spec、Repository Leader Task；
- Repository Leader 创建并冻结 Task Spec 后分派 Worker；
- Worker Preflight、Context Grant、隔离 Worktree、Runner 和 Coding Agent；
- 测试执行、Commit 生成、Runner Event 和任务结果回写；
- ChangeSet、仓库依赖排序、PR/CI/Merge 观测与 Recovery Plan 控制面；
- 未合并 PR 关闭、已合并仓库 Revert PR、Runner Resume/Retry 决策。

当前关键限制：

- PR、CI 和 Merge 仍是内部状态记录，尚未接真实 GitHub/GitLab；
- ChangeSet 暂未校验 ValidationSnapshot 的真实性与新鲜度；
- Delivery API 的调用者身份与外部 Webhook 事实尚未形成可信链路；
- Recovery Action 已持久化，但缺少执行器和自动恢复扫描器；
- 缺少主分支漂移检测、Merge Queue、CODEOWNERS 和人工审批；
- 缺少完整的 Transactional Outbox、Inbox、死信和重放控制面。

## 2. 目标流程

```text
PRD
  -> Repository Discovery / Dependency Graph
  -> Engineering Spec / Contract Specs / Task DAG
  -> Repository Leader Tasks
  -> Frozen Worker Task Specs
  -> Worker + Runner + Coding Agent
  -> Candidate Commits
  -> Four-level Validation
  -> Immutable ValidationSnapshot
  -> ChangeSet
  -> Draft PRs
  -> Review / CI / Policy Gates
  -> Drift Reconciliation
  -> Dependency-ordered Merge Queue
  -> Delivered
  -> Runtime Feedback or Compensating Revert PRs
```

## 3. 设计原则

1. Coding Agent 永远不能直接 Push、创建 PR 或 Merge。
2. 外部状态以 SCM/CI 主动查询与已验签 Webhook 为准，不信任请求体中的布尔值。
3. ChangeSet 固定 Candidate SHA、ValidationSnapshot、Contract 版本和合并顺序。
4. 所有外部副作用必须有幂等键、超时、重试、对账和补偿动作。
5. 已合并代码不得强推回滚，只能创建并审核 Revert PR。
6. 主分支、Contract、测试计划或 Candidate 变化后，旧验证立即失效。
7. Agent 不能批准自己的高风险交付动作。
8. 数据库保存业务事实，聊天和 Trace 只用于沟通与观察。

## 4. 修改清单

### ED-01 SCM 公共契约

新增 `src/repomesh/integrations/scm/contracts.py`：

- `SCMProvider`: `github | gitlab`；
- `RepositoryRef`；
- `BranchObservation`；
- `PullRequestObservation`；
- `MergeabilityObservation`；
- `CreateBranchCommand`；
- `PushCommitCommand`；
- `CreatePullRequestCommand`；
- `ClosePullRequestCommand`；
- `MergePullRequestCommand`；
- `CreateRevertPullRequestCommand`；
- `GetRepositoryStateQuery`；
- `SCMAdapter` Protocol。

约束：

- 每个写命令必须带 `idempotency_key`；
- PR Head SHA 必须等于 ChangeSet Candidate SHA；
- 不允许 Force Push；
- Credential 只传引用，不传明文；
- 创建 PR 重放时返回原 PR。

### ED-02 GitHub App Adapter

新增：

- `integrations/scm/github/client.py`；
- `integrations/scm/github/adapter.py`；
- `integrations/scm/github/webhooks.py`；
- `integrations/scm/github/models.py`。

能力：

- Installation Token 获取与缓存；
- 分支创建、Push、Draft PR 创建；
- Branch Protection、CODEOWNERS、Merge Queue 查询；
- PR、Review、Check Run、Merge、Branch 更新事件接收；
- `X-Hub-Signature-256` 验证；
- API 限流与 `Retry-After`；
- 主动对账远端事实。

MVP 暂不自动 Merge，只允许创建 Draft PR 和读取状态。

### ED-03 GitLab Adapter

接口与 GitHub Adapter 保持一致：

- Project Access Token / OAuth；
- Branch、Merge Request、Approval Rule、Pipeline；
- GitLab Secret Token Webhook 验证；
- Merge Train 对接；
- Revert MR。

第一阶段只完成接口和 Mock Contract Test，第二阶段接真实 GitLab。

### ED-04 CI Adapter

新增 `integrations/ci`：

- `CIProvider`；
- `CheckSuiteObservation`；
- `TriggerCICommand`；
- `RetryCICommand`；
- `CancelCICommand`；
- `GetCIStatusQuery`；
- `CIAdapter` Protocol。

必须保存：

- Provider Check ID；
- Head SHA；
- Workflow/Pipeline 名称；
- Required/Optional；
- 状态、结论、开始与结束时间；
- 日志和 Artifact URI；
- 重试次数。

只有 Required Checks 全部对应当前 Head SHA 且成功，仓库才能进入 `READY_TO_MERGE`。

### ED-05 ValidationSnapshot

完善 `review_validation` 模块：

```text
ValidationSnapshot
  id
  project_id
  candidate_refs(repository_id, task_id, commit_sha)
  engineering_spec_version_id
  contract_version_ids
  test_plan_version_id
  environment_hash
  task_test_results
  repository_integration_results
  joint_test_result
  regression_test_result
  security_results
  content_hash
  status
```

状态：

```text
DRAFT -> RUNNING -> PASSED / FAILED -> STALE
```

ChangeSet 创建前必须校验：

- Snapshot 为 `PASSED`；
- Candidate SHA 完全一致；
- Contract/TestPlan 未产生新版本；
- Environment Hash 未变化；
- Snapshot 未被标记为 `STALE`。

### ED-06 四级验证门禁

实现：

1. `TaskValidation`: 每个 Worker Task 的测试；
2. `RepositoryIntegrationValidation`: 每个仓库整合后的测试；
3. `ProjectJointValidation`: 跨仓库联调测试；
4. `ProjectRegressionValidation`: 项目回归测试。

任一级失败：

- 创建结构化 Validation Failure；
- 定位责任仓库和 Task；
- 生成 Rework Task；
- 旧 Snapshot 失效；
- 不允许降低测试标准后直接重试。

### ED-07 ChangeSet 强化

在现有 ChangeSet 增加：

- `engineering_spec_version_id`；
- `contract_version_ids`；
- `policy_snapshot_id`；
- `base_observations`；
- `delivery_strategy`；
- `approval_requirements`；
- `release_window`；
- `risk_level`；
- `last_reconciled_at`；
- `stale_reason`。

状态扩展：

```text
DRAFT
-> VALIDATING
-> READY
-> CREATING_PRS
-> WAITING_REVIEW
-> WAITING_CI
-> READY_TO_MERGE
-> MERGING
-> DELIVERED

任意阶段 -> BLOCKED / REVALIDATION_REQUIRED
已产生副作用 -> COMPENSATING -> COMPENSATED / MANUAL_INTERVENTION
```

### ED-08 漂移检测与对账

新增 `DeliveryReconciler` 后台服务：

- 查询目标分支最新 SHA；
- 查询 PR Head SHA；
- 查询 CI Check SHA；
- 查询 Review、CODEOWNERS 和 Mergeability；
- 对比 Contract、Spec 和 ValidationSnapshot；
- 对比内部状态与远端事实。

以下情况进入 `REVALIDATION_REQUIRED`：

- Base Branch 前进且影响 Candidate；
- PR Head 被人工修改；
- Required CI 对应旧 SHA；
- Contract 或 Spec 出现新版本；
- ValidationSnapshot 过期；
- Branch Protection 或审批规则改变。

Reconciler 必须支持服务重启后恢复扫描。

### ED-09 Merge Planner 与 Merge Queue

在仓库 DAG 上计算：

- Merge Order；
- 可并行 Merge Group；
- Required Compatibility Window；
- Revalidation Point；
- Rollback Order。

合并前检查：

- 所有上游 Repository Delivery 为 `MERGED`；
- 当前仓库 PR 可合并；
- Required CI 和审批通过；
- 当前 Head/Base SHA 与观察一致；
- ChangeSet 未暂停；
- 当前时间处于 Release Window。

实际合并通过 SCM Adapter 或 Provider Merge Queue，不在数据库中假装成功。

### ED-10 Recovery Executor

在已有 Recovery Plan 基础上增加：

- `RecoveryActionExecutor`；
- `RecoveryActionLease`；
- `RecoveryActionAttempt`；
- `next_attempt_at`；
- `max_attempts`；
- `last_error`；
- `external_operation_id`。

动作执行规则：

| 场景 | 恢复动作 |
| --- | --- |
| Runner 未创建 Session | 新 Run、新 Attempt、新幂等键 |
| Runner 有已确认 Session | Resume Session |
| 本地 Commit 未 Push | 保留 Workspace，重新验证或废弃 Candidate |
| PR 已创建未合并 | 关闭 PR，保留审计记录 |
| CI 失败 | 创建 Rework Task，不直接反复重跑相同代码 |
| 部分仓库已合并 | 按依赖逆序创建 Revert PR |
| Revert PR CI 失败 | `MANUAL_INTERVENTION` |
| Contract 变化 | 暂停下游，重新规划和验证 |

### ED-11 Transactional Outbox 与 Inbox

扩展平台事件基础设施：

- 业务状态更新与 Outbox Event 同事务提交；
- Publisher Lease；
- 指数退避和最大重试；
- Dead Letter Queue；
- 人工重放；
- Webhook Inbox 去重；
- `provider + delivery_id` 唯一键；
- 消费处理结果；
- Trace/Correlation ID。

必须覆盖的中断点：

- DB 成功、GitHub 超时；
- GitHub 成功、DB 更新失败；
- Webhook 重复或乱序；
- Worker 结果写入后通知失败；
- PR 已创建但返回响应丢失。

### ED-12 身份、凭证与策略

修改 Delivery API：

- 不再信任 Body 中的 `created_by_agent_id`；
- 从已验证 Principal 获取组织、项目和 Agent；
- Webhook 使用 Provider 身份；
- 敏感命令检查 Repository Scope；
- Merge、Revert、Bypass 必须单独授权。

新增 Policy Engine：

- 自动 Merge 是否允许；
- 必需审批人数；
- CODEOWNERS；
- 受保护路径；
- 最大修改规模；
- 安全扫描级别；
- Release Window；
- 高风险仓库人工审批；
- Database Migration DBA 审批。

### ED-13 审计和供应链证据

每个 ChangeSet 保存不可变链路：

```text
PRD -> Spec -> Contract -> Task -> Context Bundle
-> Model/Agent/Session -> Commit -> ValidationSnapshot
-> Review -> PR -> CI -> Merge/Revert
```

增加：

- Commit 签名；
- Build Provenance；
- SBOM；
- Dependency/Vulnerability Scan；
- License Scan；
- Secret Scan；
- Artifact Hash；
- Agent、Model、Prompt Template 和工具版本；
- 人工审批记录。

### ED-14 可观测性和运维

指标：

- ChangeSet Lead Time；
- PR/CI/Review 等待时间；
- 自动交付成功率；
- 补偿率和人工介入率；
- Runner Resume 成功率；
- Webhook/Outbox 延迟；
- SCM API 错误与限流；
- 每项目 Agent 成本。

告警：

- ChangeSet 长时间无进展；
- Recovery Action 超过重试上限；
- 内外状态持续不一致；
- Revert PR 失败；
- Required CI 被绕过；
- Credential 或 Webhook 验证失败。

## 5. 数据库迁移清单

建议拆分迁移，避免一次引入所有表：

1. `delivery.repository_deliveries`：从 JSON Aggregate 拆出高频查询字段；
2. `delivery.pull_request_observations`；
3. `delivery.ci_observations`；
4. `delivery.recovery_plans`；
5. `delivery.recovery_actions`；
6. `review_validation.validation_snapshots`；
7. `review_validation.validation_results`；
8. `platform.inbox_events`；
9. `platform.outbox_attempts`；
10. `platform.dead_letter_events`；
11. `observability.audit_events` 或现有 Audit 表扩展。

每张业务表必须包含：

- `organization_id`；
- `project_id`；
- `version`；
- `created_at/updated_at`；
- 必要的唯一幂等约束；
- 查询索引；
- 升级和回滚说明。

## 6. API 与事件清单

### Commands

- `PrepareChangeSet`；
- `CreateRepositoryPullRequests`；
- `ReconcileChangeSet`；
- `RecordSCMObservation`；
- `RecordCIObservation`；
- `RequestMerge`；
- `PauseChangeSet`；
- `ResumeChangeSet`；
- `PlanRecovery`；
- `ExecuteRecoveryAction`；
- `RecordRecoveryActionResult`；
- `RequestRevalidation`。

### Events

- `ChangeSetPrepared`；
- `PullRequestCreated`；
- `PullRequestHeadChanged`；
- `CIStatusChanged`；
- `ReviewStatusChanged`；
- `ChangeSetBecameStale`；
- `RepositoryMerged`；
- `ChangeSetDelivered`；
- `RecoveryPlanned`；
- `RecoveryActionStarted`；
- `RecoveryActionFailed`；
- `RecoveryCompleted`；
- `ManualInterventionRequired`。

### Queries

- ChangeSet Detail；
- Repository Delivery Detail；
- Merge Plan；
- ValidationSnapshot；
- Recovery Timeline；
- SCM Reconciliation Differences；
- Delivery Audit Timeline。

## 7. 测试方案

### 单元测试

- DAG 拓扑排序与环检测；
- 合并前置条件；
- SHA/ValidationSnapshot 一致性；
- Recovery Action 生成顺序；
- 状态机非法转换；
- 幂等重放；
- 乐观锁冲突。

### Contract Tests

- GitHub/GitLab Adapter 同一套行为测试；
- SCM/CI 请求和响应序列化；
- Webhook 签名和事件解析；
- Provider 错误映射；
- API 向后兼容。

### 集成测试

- 数据库事务和 Outbox；
- Webhook Inbox 去重和乱序；
- GitHub/GitLab Mock Server；
- CI Check 对账；
- 服务重启恢复；
- Recovery Executor Lease。

### 真实端到端

准备 backend、frontend、deployment 三仓库：

1. backend 产生 API 变更；
2. frontend 依赖 backend；
3. deployment 依赖两者；
4. 创建三个 Draft PR；
5. backend 合并后释放 frontend；
6. 模拟 frontend CI 失败并创建 Rework Task；
7. 再次通过后继续；
8. 模拟 deployment 合并失败；
9. 验证补偿按逆序产生 Revert PR；
10. 验证 Trace、Audit、Recovery Timeline 完整。

## 8. 实施顺序

### P0：比赛与 MVP 必做

1. ED-02 GitHub Draft PR Adapter；
2. ED-04 CI Observation；
3. ED-05 ValidationSnapshot 最小模型；
4. ED-07 ChangeSet 新鲜度和状态扩展；
5. ED-08 Drift Reconciler；
6. ED-10 Recovery Executor；
7. 双仓库真实端到端测试。

### P1：企业可试用

1. ED-06 四级验证；
2. ED-09 Merge Queue；
3. ED-11 Outbox/Inbox/DLQ；
4. ED-12 身份与策略；
5. CODEOWNERS 与人工审批；
6. 三仓库部分失败补偿测试。

### P2：生产强化

1. GitLab Adapter；
2. ED-13 供应链证据；
3. Deployment/Canary/Feature Flag 接口；
4. 高可用、限流、配额、备份和灾难恢复；
5. 完整 SLO、告警和成本治理。

## 9. 验收标准

达到企业级 MVP 至少满足：

- 用户只提交需求，AgentTeams 能完成双仓库 Commit；
- 系统从可信 ValidationSnapshot 创建 ChangeSet；
- 系统自动创建两个 Draft PR，重复调用不重复创建；
- CI、Review 和 Head SHA 均从远端可信来源获取；
- 下游仓库不能越过上游合并；
- 主分支漂移会阻止 Merge 并要求重新验证；
- 服务重启不会丢失 ChangeSet 或 Recovery Action；
- 部分合并失败后自动生成逆序补偿计划；
- 已合并仓库只能通过 Revert PR 回滚；
- 所有 Agent、Spec、Context、Commit、测试、PR、CI 和审批可审计；
- Coding Agent 没有 Push、PR 或 Merge 权限。

## 10. 推荐近期任务拆分

| Issue | 负责人方向 | 预计产出 |
| --- | --- | --- |
| ED-MVP-01 | SCM | GitHub App、Draft PR、Webhook 验签 |
| ED-MVP-02 | Validation | ValidationSnapshot 最小模型和 Candidate 校验 |
| ED-MVP-03 | Delivery | ChangeSet 新鲜度、漂移与状态机 |
| ED-MVP-04 | Reliability | Recovery Executor、Lease、Retry、DLQ |
| ED-MVP-05 | CI | Check Run/Pipeline Observation 与 Required Gate |
| ED-MVP-06 | Security | Principal、Credential Ref、Merge/Revert 授权 |
| ED-MVP-07 | E2E | 双仓库 PR、CI、漂移和补偿真实验证 |

## 11. 当前 P0 落地方案（2026-08-07）

### 11.1 Required Checks 聚合

- 每个 `RepositoryCandidate` 在 ChangeSet 创建时冻结 `required_checks`，后续不得由 Worker 修改；
- Webhook 按 `repository + head_sha + check_name` 写入检查证据；
- 非 required check 仅归档，不参与放行；
- required check 缺失时状态为 `ci_pending`，任一失败为 `ci_failed`；
- 全部 required checks 成功后才进入 `ready_to_merge`；
- 同名检查重跑覆盖旧观察结果，保留最新可信结果；
- 未配置 required checks 的旧调用保留单检查兼容行为，迁移期结束后应禁止空清单。

### 11.2 Webhook 自动路由

统一入口根据 GitHub Repository ID/URL、PR number 和完整 head SHA 查找唯一的 ChangeSet
候选，不再信任 URL 中的 ChangeSet ID。若匹配零个或多个候选，事件进入待处理队列并告警，
禁止更新交付状态。该能力依赖规范化的 `change_set_repositories` 索引表，应在下一迁移完成。

### 11.3 GitHub App 鉴权

SCM Adapter 只消费短期 Installation Token。独立 Credential Provider 负责 App JWT 签发、
Installation 查询、Token 缓存和提前轮换；数据库只保存 credential reference，禁止保存私钥和
Token 明文。Push、PR、Merge 分别使用最小权限，Coding Agent 永远不接触这些凭据。

### 11.4 实施顺序

1. Required Checks 领域模型、持久化、Webhook 聚合和门禁测试；
2. 规范化 ChangeSet 仓库索引及 Webhook 自动路由；
3. GitHub App Credential Provider；
4. 真实仓库 Push、PR、CI、Merge 端到端测试；
5. Review/CODEOWNERS/人工批准聚合门禁。
