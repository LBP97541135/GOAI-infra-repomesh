# RepoMesh 数据库基线报告

- 生成时间：2026-09-14 14:09:56
- 数据源：`postgresql+asyncpg://***@localhost:5432/repomesh`（只读巡检，未写任何业务表）
- 已装扩展：plpgsql 1.0, vector 0.8.6
- Alembic head：20260914_0056
- 概览：17 个 schema，71 张表，合计 7409 行，空表 25 张

## agent_directory

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| agent_principals | 26 | 208.0kB | — | `{"id": "471904c1-b93e-4f48-a54e-617ec43e297f", "role": "organization_leader", "status": "active", "repository_id": null, "singleton_key": "organization:c095f4b7-f679-5c1e-86b8-834a63a1405a:leader", "idempotency_key": "workspace-leader:e1-main-workspace", "leader_agent_id": null, "organization_id": "…` |

## agent_runtime

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| runner_dispatches | 30 | 296.0kB | — | `{"run_id": "00f6ed62-f4a0-42ab-b965-10bb4cca8250", "status": "failed", "attempt": 1, "task_id": "a076b8f0-b0a1-442a-ad21-057bc254688f", "created_at": "2026-09-03T04:41:33.316637+00:00", "project_id": "63a66b71-1923-5751-bfd0-ac5aace68bbf", "lease_until": null, "completed_at": "2026-09-03T04:45:01.09…` |
| runner_events | 60 | 208.0kB | 2026-09-08 06:57:33 (occurred_at) | `{"run_id": "00f6ed62-f4a0-42ab-b965-10bb4cca8250", "payload": {"runId": "00f6ed62-f4a0-42ab-b965-10bb4cca8250", "taskId": "a076b8f0-b0a1-442a-ad21-057bc254688f", "attempt": 1, "eventId": "8aba6ad6-6df9-528c-99e1-5b46aac6de35", "payload": {"adapterId": "claude-code"}, "sequence": 1, "eventType": "run…` |
| worker_execution_reservations | 64 | 352.0kB | 2026-09-08 07:14:06 (lease_expires_at) | `{"id": "cccbde9e-d1b4-4daa-b2fb-3f8febb0afba", "run_id": "56478d66-fb96-4beb-b51a-cdb71ef0b577", "status": "expired", "attempt": 6, "task_id": "cf286f8d-063a-4e82-941c-6d53338b7892", "version": 2, "created_at": "2026-09-07T08:07:07.959281+00:00", "project_id": "626ef15b-a1b6-5596-bbe7-aa3dbc56db30",…` |
| worker_recovery_operations | 30 | 64.0kB | — | `{"id": "cee88b56-f2bb-4b0f-9539-d0ed3b659576", "state": "completed", "reason": "failed", "task_id": "a076b8f0-b0a1-442a-ad21-057bc254688f", "attempts": 1, "decision": "no_action", "created_at": "2026-09-03T04:45:01.220487+00:00", "error_code": null, "updated_at": "2026-09-07T02:30:15.060012+00:00", …` |

## capability_management

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| mcp_server_policies | 4 | 32.0kB | — | `{"id": "github", "max_retries": 2, "timeout_seconds": 30, "retryable_only_reads": true, "degraded_block_writes": true, "required_task_features": []}` |
| skill_evaluations | 0 | 24.0kB | — | — |
| skill_snapshots | 0 | 32.0kB | — | — |
| skill_versions | 15 | 80.0kB | 2026-09-02 12:58:46 (created_at) | `{"id": "a7d74377-69f7-4822-a1bb-1f1e73a655ee", "status": "promoted", "version": "1.0.0", "skill_id": "project-intake", "created_at": "2026-09-01T05:47:20.855571+00:00", "created_by": "bootstrap", "local_path": "capabilities/skills/project-intake/SKILL.md", "updated_at": "2026-09-01T05:47:20.855571+0…` |

## collaboration

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| messages | 319 | 760.0kB | 2026-09-08 07:08:46 (created_at) | `{"id": "b45b869a-a738-49ce-b9ea-e762abbb3d8d", "body": "检查订单创建和取消编排中是否存在通知服务直接调用或共享通知集成层。若存在，统一改用已确认的通知接口请求字段，并携带稳定的业务幂等标识；订票成功通知应传递订单号、…", "kind": "task_assignment", "status": "delivered", "room_id": "!gaTUzpnxVwJYIK3Qwu:matrix-local.agentteams.io:18080", "subject": "Implement changes for ts-order-…` |
| processed_matrix_events | 92 | 112.0kB | — | `{"task_id": "755bf0ee-a81d-4649-bf4e-9da957578f08", "event_id": "$ExvgNfL1hMYXmqCmRhSJZ9DyZ5ee3gRYIZjz4DAU9wg", "project_id": "6252c608-eba3-50f1-a48e-b58eecd2487b", "sender_agent_id": "e8145624-ba9c-4938-94da-a50e4fa41168"}` |
| room_timeline_messages | 759 | 1.2MB | 2026-09-08 07:10:26 (occurred_at) | `{"body": "@agt-leader-5c696951b895:matrix-local.agentteams.io:18080 {\"body\":\"检查订单创建和取消编排中是…", "room_id": "!gaTUzpnxVwJYIK3Qwu:matrix-local.agentteams.io:18080", "event_id": "$-1NHTppBXbiO4AZVNk1ROycEBuA4Sz1Qol9A35Z4yPk", "project_id": "6252c608-eba3-50f1-a48e-b58eecd2487b", "occurred_at": "2026-0…` |

## context

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| context_access_events | 0 | 72.0kB | — | — |
| context_bundle_items | 0 | 32.0kB | — | — |
| context_bundles | 42 | 216.0kB | 2026-09-08 11:09:06 (expires_at) | `{"id": "65c9a98c-b628-408f-8ced-59612705d652", "role": "worker", "run_id": "00f6ed62-f4a0-42ab-b965-10bb4cca8250", "agent_id": "56b59179-a921-4243-b7b6-05df6dcbc70f", "base_sha": "930ea9f878e2614692c5f95d7303d79a7bd38b7d", "created_at": "2026-09-03T04:41:30.381061+00:00", "expires_at": "2026-09-03T0…` |
| context_delta_items | 0 | 32.0kB | — | — |
| context_deltas | 0 | 40.0kB | — | — |
| context_object_versions | 35 | 112.0kB | 2026-09-08 07:08:46 (created_at) | `{"id": "c4e75f20-56cc-4457-8cfe-e93175f5d655", "version": 1, "mime_type": "application/vnd.repomesh.specification+json", "created_at": "2026-09-02T13:33:09.970644+00:00", "created_by": "6ede16c1-2ffd-4357-b713-a502129eebd1", "size_bytes": 2952, "content_uri": "specification://4694700c-86cf-4f91-ac06…` |
| context_objects | 35 | 128.0kB | 2026-09-08 07:08:46 (created_at) | `{"id": "bf8cfd14-f60b-4298-bc81-f3196a7869ec", "scope": "task_private", "title": "Task spec: Implement changes for ts-order-service", "status": "approved", "created_at": "2026-09-02T13:33:09.970595+00:00", "project_id": "6252c608-eba3-50f1-a48e-b58eecd2487b", "object_type": "task_spec", "owner_subje…` |
| context_relations | 0 | 32.0kB | — | — |

## decision_chain

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| decision_chain_nodes | 88 | 184.0kB | 2026-09-08 07:08:40 (business_time) | `{"step": "classification", "actor": {"type": "llm", "agent_id": null}, "source": "event", "status": "merged", "version": 1, "event_id": "a968bffc-31d0-5047-9946-5f5528a61f10", "event_type": "ClassificationDecided", "project_id": "9d1e4c56-1b39-4f72-a4e5-88fd30de0037", "decision_id": "330f5063-fc71-4…` |
| decision_embeddings | 29 | 440.0kB | 2026-09-04 10:20:09 (recorded_at) | `{"embedding": "[-0.03299713,-0.03438486,-0.024670754,0.043482203,-0.01719243,-0.0117186075,0.01…", "decision_id": "330f5063-fc71-413a-81a2-d40f73c90eae", "recorded_at": "2026-09-01T13:01:26.013633+00:00"}` |

## delivery

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| change_set_repositories | 4 | 56.0kB | — | `{"status": "merged", "head_sha": "8deb466aff32990f7acf3858c61c045ebeaff335", "change_set_id": "c2a2efdb-ae4f-4d87-999a-c89669724ad2", "repository_id": "48ff85ee-83e1-5d9f-8120-154ef5fb5908", "pull_request_number": 1}` |
| change_sets | 3 | 136.0kB | 2026-09-04 07:15:39 (created_at) | `{"id": "c2a2efdb-ae4f-4d87-999a-c89669724ad2", "status": "delivered", "payload": {"title": "RepoMesh delivery 066833fc", "merge_cursor": 2, "repositories": [{"run_id": null, "status": "merged", "plan_id": null, "reviews": [], "task_id": "f39e255f-f423-58ff-b574-0e19d271e3ea", "base_sha": "9999999999…` |
| conflict_cases | 0 | 80.0kB | — | — |
| delivery_archives | 0 | 8.0kB | — | — |
| delivery_policies | 0 | 24.0kB | — | — |
| scm_commands | 0 | 64.0kB | — | — |
| scm_observations | 6 | 112.0kB | 2026-09-04 06:29:39 (observed_at) | `{"id": "02b7bc5d-0759-5d69-9005-18b52a5980d4", "source": "webhook", "status": "processed", "payload": {"check": "test", "head_sha": "8deb466aff32990f7acf3858c61c045ebeaff335", "conclusion": "success"}, "version": 1, "attempts": 1, "provider": "github", "claimed_at": null, "event_type": "check_run.co…` |
| scm_poll_cursors | 0 | 24.0kB | — | — |

## identity_access

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| local_human_accounts | 2 | 72.0kB | — | `{"id": "22ce904e-5239-46dd-a9b2-a77e0a75108d", "active": true, "is_admin": true, "username": "admin", "display_name": "E1 Admin", "password_hash": "<redacted>", "password_salt": "<redacted>"}` |
| local_human_sessions | 12 | 88.0kB | 2026-09-14 12:16:11 (expires_at) | `{"id": "2bd376f9-a5f0-4879-a5e6-da194d47a1d2", "account_id": "22ce904e-5239-46dd-a9b2-a77e0a75108d", "expires_at": "2026-09-01T14:58:55.05769+00:00", "token_hash": "<redacted>"}` |
| organizations | 2 | 40.0kB | 2026-09-04 07:15:40 (created_at) | `{"id": "c095f4b7-f679-5c1e-86b8-834a63a1405a", "name": "E1", "created_at": "2026-09-01T06:59:12.174222+00:00"}` |

## observability

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| alert_events | 5 | 64.0kB | 2026-09-08 01:10:50 (triggered_at) | `{"id": "babb3834-3b61-4d34-a2d7-18f67abdf5ec", "value": 0, "status": "resolved", "message": "成功率过低：成功率 0.0 低于 阈值 0.8", "rule_id": "655aa9b0-8b3e-4028-8210-4c12dde16b80", "resolved_at": "2026-09-04T08:03:13.589671+00:00", "triggered_at": "2026-09-01T09:19:15.901541+00:00", "window_minutes": 1440}` |
| alert_rules | 3 | 24.0kB | 2026-09-01 05:48:20 (created_at) | `{"id": "655aa9b0-8b3e-4028-8210-4c12dde16b80", "name": "成功率过低", "metric": "success_rate", "enabled": true, "operator": "lt", "threshold": 0.8, "created_at": "2026-09-01T05:48:20.790861+00:00", "updated_at": "2026-09-01T05:48:20.790861+00:00", "window_minutes": 1440}` |
| llm_usage | 211 | 152.0kB | 2026-09-08 07:08:40 (created_at) | `{"id": "d7986126-3bc8-445b-8ba2-df6e8670e722", "model": "gpt-5.6-luna", "status": "error", "issue_id": "c57abca4-04b5-5dbd-bb8d-8aa227f70093", "provider": "deepseek", "operation": "chat", "created_at": "2026-09-01T09:18:53.103581+00:00", "latency_ms": 767, "total_tokens": "<redacted>", "finish_reaso…` |
| log_entries | 2989 | 2.7MB | 2026-09-14 04:33:32 (ts) | `{"id": "8019bdb4-1c11-4a22-869a-5f6d728dfdb3", "ts": "2026-09-01T09:18:53.115246+00:00", "level": "WARNING", "source": "repomesh.modules.repository_intelligence.api.discovery_chain", "message": "discovery step 1 failed for issue c57abca4-04b5-5dbd-bb8d-8aa227f70093", "exc_info": "Traceback (most rec…` |
| operational_responses | 4 | 56.0kB | 2026-09-08 01:10:50 (created_at) | `{"id": "fb5739fe-4c6d-4a6f-82ea-c3e2d7bdf5b3", "action": "none", "created_at": "2026-09-06T01:42:08.312051+00:00", "error_code": null, "updated_at": "2026-09-06T01:42:08.325405+00:00", "action_status": "skipped", "alert_event_id": "55f52d48-0e71-452c-8ff5-c5624d6c34cd", "notification_status": "sent"…` |
| trace_events | 1559 | 1.8MB | 2026-09-08 07:10:25 (ts) | `{"id": "412bdc3f-a92b-4aa9-bbc2-85b6350207e3", "ts": "2026-09-02T13:33:09.023+00:00", "seq": 1, "name": "task.assignment", "role": "user", "status": "ok", "payload": {"kind": "task_assignment", "schema": "repomesh.collaboration.v1", "subject": "Implement changes for ts-payment-service", "task_id": "…` |
| trace_sessions | 19 | 96.0kB | 2026-09-08 07:10:27 (object_mtime) | `{"id": "bbaf8f83-21c5-4b68-9dbb-944d37a6f28a", "runtime": "copaw", "parsed_at": "2026-09-08T06:01:42.813905+00:00", "agent_name": "agt-leader-5c696951b895", "session_id": "!gaTUzpnxVwJYIK3Qwu--matrix-local.agentteams.io--18080_matrix--!gaTUzpnxVwJYIK3Q…", "source_key": "agents/agt-leader-5c696951b89…` |

## platform

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| audit_events | 232 | 536.0kB | 2026-09-08 07:09:06 (occurred_at) | `{"run_id": null, "payload": {"role": "organization_leader", "repositoryId": null, "leaderAgentId": null, "agentteamsResourceName": "repomesh-org-leader-e1-c095f4b7"}, "task_id": null, "actor_id": "repomesh-api", "event_id": "6df749c3-8a73-44b4-a6bd-d696550b6f98", "actor_type": "service", "event_type…` |
| bootstrap_operations | 0 | 32.0kB | — | — |
| idempotency_records | 0 | 32.0kB | — | — |
| outbox_events | 98 | 240.0kB | 2026-09-08 07:09:06 (occurred_at) | `{"run_id": null, "payload": {"role": "organization_leader", "repositoryId": null, "leaderAgentId": null, "agentteamsResourceName": "repomesh-org-leader-e1-c095f4b7"}, "task_id": null, "actor_id": "repomesh-api", "attempts": 0, "event_id": "6df749c3-8a73-44b4-a6bd-d696550b6f98", "actor_type": "servic…` |
| platform_credentials | 0 | 16.0kB | — | — |
| state_events | 98 | 224.0kB | 2026-09-08 07:09:06 (occurred_at) | `{"run_id": null, "payload": {"role": "organization_leader", "repositoryId": null, "leaderAgentId": null, "agentteamsResourceName": "repomesh-org-leader-e1-c095f4b7"}, "task_id": null, "actor_id": "repomesh-api", "event_id": "6df749c3-8a73-44b4-a6bd-d696550b6f98", "actor_type": "service", "event_type…` |
| trace_links | 0 | 24.0kB | — | — |

## project

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| agent_topologies | 19 | 112.0kB | — | `{"id": "ed49f596-46ee-4e4b-9975-4690f0948649", "project_id": "6252c608-eba3-50f1-a48e-b58eecd2487b", "human_grants": [], "execution_mode": "auto", "idempotency_key": "disc-console-discovery-materialize-0481ed7e-e8cd-4bd9-bf4b-348faf76a367:topology", "organization_id": "c095f4b7-f679-5c1e-86b8-834a63…` |
| checkpoint_decisions | 0 | 72.0kB | — | — |
| human_review_requests | 26 | 160.0kB | 2026-09-08 07:15:15 (created_at) | `{"id": "b37ee713-30fc-4e10-81be-ccbd7cd8819a", "title": "Worker 自动恢复需要人工处理", "status": "pending", "summary": "恢复无法安全继续，错误代码：no_healthy_replacement_worker", "checkpoint": "exception_escalation", "created_at": "2026-09-07T02:31:15.711513+00:00", "project_id": "626ef15b-a1b6-5596-bbe7-aa3dbc56db30", "u…` |
| repository_agent_teams | 72 | 232.0kB | — | `{"id": "e5395b57-ccdb-4f9c-9fb4-b8c1ca7e12ae", "room_id": "!uer3oWpKT2IzDEa8TK:matrix-local.agentteams.io:18080", "project_id": "6252c608-eba3-50f1-a48e-b58eecd2487b", "topology_id": "ed49f596-46ee-4e4b-9975-4690f0948649", "repository_id": "27adcc7e-d735-4fb4-acff-13c28f47a749", "leader_room_id": "!…` |
| topology_policy_drafts | 0 | 16.0kB | — | — |

## public

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| alembic_version | 1 | 56.0kB | — | `{"version_num": "20260914_0056"}` |

## recovery_management

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| recovery_cases | 0 | 96.0kB | — | — |
| recovery_decisions | 0 | 40.0kB | — | — |
| recovery_operations | 0 | 32.0kB | — | — |

## repository_intelligence

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| handoff_docs | 60 | 472.0kB | 2026-09-08 07:08:47 (created_at) | `{"id": "6eb4ecb3-789a-4e88-8cbc-9f5d183c71ec", "status": "PENDING", "content": {"adjustment": {"depends_on": [], "instruction": "检查订单创建和取消编排中是否存在通知服务直接调用或共享通知集成层。若存在，统一改用已确认的通知接口请求字段，并携带稳定的业务幂等标识；订票成功通知应传递订单号、车次、座位信息和成功状态，取消通知应传递订单标识、取消状态及可用的退款信息。配置统一的连接超时和有限重试策略，确保通知调用失败、超时或重试耗尽不会回滚或阻断已成功的订单操作；补充包含…` |
| issue_archives | 10 | 24.0kB | 2026-09-08 04:21:04 (archived_at) | `{"issue_id": "0393e582-34e2-53e0-9cfa-ed0bca6157d3", "archived_at": "2026-09-04T08:22:48.335878+00:00"}` |
| plan_snapshots | 48 | 752.0kB | 2026-09-08 07:05:47 (created_at) | `{"id": "1858a871-108c-4eec-808c-0d3c367a16f7", "task_dag": [], "contracts": [], "discovery": {"analysis": {"error": {"at": "2026-09-01T09:37:49.015520+00:00", "message": "Server error '502 Bad Gateway' for url 'https://www.vibeapi.cn/v1/chat/completions'\nFor more information check: https://develope…` |
| repositories | 8 | 64.0kB | 2026-09-04 07:15:39 (profiled_at) | `{"id": "3b43a8c5-117e-4f92-8c0e-557922fe122d", "url": "https://github.com/repomesh-train-ticket/ts-travel-service", "name": "ts-travel-service", "topics": [], "metadata": {}, "languages": [], "created_at": "2026-09-01T07:26:31.799548+00:00", "test_paths": [], "updated_at": "2026-09-01T07:26:31.79954…` |

## review_validation

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| database_branch_validations | 0 | 64.0kB | — | — |
| validation_snapshots | 2 | 112.0kB | 2026-09-04 07:15:39 (created_at) | `{"id": "8b04c628-4ff1-4840-a7fb-1e42e7849ec1", "status": "passed", "payload": {"tests": [{"command": "pytest", "summary": "", "exit_code": 0, "repository_id": "48ff85ee-83e1-5d9f-8120-154ef5fb5908"}, {"command": "pytest", "summary": "", "exit_code": 0, "repository_id": "1ea852aa-775c-5889-8f23-7da28…` |

## specification

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| specification_versions | 58 | 264.0kB | 2026-09-08 07:08:46 (created_at) | `{"id": "59e0ff12-ac5f-4c92-af6d-8bc718953232", "content": {"goal": "修复订票成功与订单取消成功后的通知邮件链路，同时保证邮件故障不阻断订单主流程。ts-order-service负责核查订单创建、取消编排及通知客户端是否存在直接调用，统一使用通知接口约定的请求字段、业务幂等标识、超时和有限重试策略；通知失败仅记录可关联的业务日志，不回滚已成功的订单操作。ts-payment-service负责核查退款处理及取消支付流程，确保取消结果和适用的退款信息能够以稳定字段提供给取消流程，并且支付或通知异常不会破坏已完成的取消操作。ts-…` |
| specifications | 58 | 248.0kB | 2026-09-08 07:08:46 (created_at) | `{"id": "3f598890-e28a-44d2-a1dd-ffba28087af9", "kind": "engineering", "title": "# TT-001：订票与取消流程通知邮件修复需求\n\n## 1. 需求概述\n\n修复订票成功和取消订单后的通知邮件发送异常。当前所有用户都无法收到相关邮件，初步排查…", "status": "draft", "task_id": null, "revision": 1, "created_at": "2026-09-02T13:33:08.292979+00:00", "project_id": "6252c608-eba3-50…` |

## task_orchestration

| 表 | 行数 | 大小 | 最近写入(按列) | 样本(脱敏) |
| --- | ---: | ---: | --- | --- |
| database_test_team_handoffs | 0 | 24.0kB | — | — |
| execution_plan_revisions | 0 | 56.0kB | — | — |
| execution_plan_tasks | 39 | 40.0kB | — | `{"plan_id": "36f526a2-fccd-49b7-a015-ef98139cd80e", "batch_index": 0, "leader_task_id": "eeafaac2-9fd4-4e4e-a861-869786cec8f5"}` |
| execution_plans | 18 | 240.0kB | 2026-09-08 07:08:46 (created_at) | `{"id": "36f526a2-fccd-49b7-a015-ef98139cd80e", "status": "in_progress", "batches": [[{"tests": [], "title": "Implement changes for ts-order-service", "acceptance": ["Code compiles without errors.", "Existing tests pass.", "检查订单创建和取消编排中是否存在通知服务直接调用或共享通知集成层。若存在，统一改用已确认的通知接口请求字段，并携带稳定的业务幂等标识；订票成功通知应传递订…` |
| leader_assignments | 0 | 56.0kB | — | — |
| task_assignment_attempts | 36 | 152.0kB | 2026-09-08 07:09:00 (created_at) | `{"id": "bbf8b65b-807a-40a4-8f24-0e6b3274735f", "state": "completed", "reason": "initial", "task_id": "cf286f8d-063a-4e82-941c-6d53338b7892", "created_at": "2026-09-07T01:53:57.696493+00:00", "generation": 1, "project_id": "626ef15b-a1b6-5596-bbe7-aa3dbc56db30", "assigned_by": "agent", "finished_at":…` |
| tasks | 79 | 416.0kB | — | `{"id": "eeafaac2-9fd4-4e4e-a861-869786cec8f5", "title": "Implement changes for ts-order-service", "origin": "planned", "status": "assigned", "version": 1, "acceptance": ["Code compiles without errors.", "Existing tests pass.", "检查订单创建和取消编排中是否存在通知服务直接调用或共享通知集成层。若存在，统一改用已确认的通知接口请求字段，并携带稳定的业务幂等标识；订票成功通…` |

## 空表清单

- capability_management.skill_evaluations
- capability_management.skill_snapshots
- context.context_access_events
- context.context_bundle_items
- context.context_delta_items
- context.context_deltas
- context.context_relations
- delivery.conflict_cases
- delivery.delivery_archives
- delivery.delivery_policies
- delivery.scm_commands
- delivery.scm_poll_cursors
- platform.bootstrap_operations
- platform.idempotency_records
- platform.platform_credentials
- platform.trace_links
- project.checkpoint_decisions
- project.topology_policy_drafts
- recovery_management.recovery_cases
- recovery_management.recovery_decisions
- recovery_management.recovery_operations
- review_validation.database_branch_validations
- task_orchestration.database_test_team_handoffs
- task_orchestration.execution_plan_revisions
- task_orchestration.leader_assignments
