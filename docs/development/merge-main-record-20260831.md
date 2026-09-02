# 合并 main 改动记录 — 2026-08-31

## 1. 概述

| 项目 | 内容 |
|---|---|
| 合并方向 | `origin/main` → `feat/repo-scan-chain-merge-main` |
| 合并提交 | `8c816b5` Merge origin/main into feat/repo-scan-chain-merge-main |
| 合并前状态 | 本地分支 `c04fdd8`（上次合并记录 `a237a2f` 之后 1 个文档提交） |
| main 分支 HEAD | `d2e84c7` feat(capability-management): run skill and MCP governance for real |
| 新增提交 | `f3d343b..d2e84c7` 共 **119 个提交** |
| 合并结果 | 0 behind / 319 ahead origin/main，工作区干净 |
| 承载分支 | `feat/repo-scan-chain-merge-main`（基于合并提交 `8c816b5`） |

本次合并将 main 分支 119 个新提交合入本地 `feat/repo-scan-chain-merge-main`，引入
capability_management、recovery_management、delivery conflict cases、repository verification
PATCH、worker execution reservations、platform bootstrap / runtime config、agent bridge、
leader actions、room timeline 等新能力，同时完整保留本地分支的决策链历史、世界图缓存 M1、
图预补充与平台白名单语义等演进。

## 2. 冲突概览

合并共产生 **19 个冲突文件**，全部采用人工逐块裁决：

| 组 | 文件数 | 处理方式 |
|---|---|---|
| 后端 src | 9 | 逐块手动合并（可共存则共存） |
| 前端 frontend | 2 | contract.ts 逐块合并，vite.config.ts 取 theirs |
| 测试 tests | 3 | 按干净适用性选择单侧或混合 |
| 配置/文档 root | 4 | 逐文件决策 |
| 持久化 | 1 | 手动合并后重写 |

另有 386 个文件自动合并无冲突后直接 `git add`。

## 3. 决策原则

- **ours 优先保留**：本地分支独有的能力演进，包括：
  - 决策链历史（`decision_chain` 持久化、`decision_chain_nodes` 迁移）
  - 世界层依赖图缓存 M1（`container.py` 的 `repository_profiles()` / `world_graph()` /
    `invalidate_world_graph()`）
  - 图预补充（supplements / conflicts / observations）
  - `DeliveryAuditLog`、discovery 配置、平台白名单语义（已知平台免配置扫描 +
    `REPOMESH_REPOSITORY_PLATFORMS` + `REPOMESH_REPOSITORY_SCAN_ALLOWED_HOSTS`）
  - 双参 `require_single_repo_url(url, fetcher)`
- **theirs 采纳**：main 已发布的既有行为与新功能，包括：
  - `capability_management` / `recovery_management` 模块
  - delivery conflict cases（`delivery/conflicts.py`）与 repository verification PATCH
  - worker execution reservations 与 `worker_recovery_reconciler`
  - platform bootstrap / runtime config、setup/status dependencies
  - agent bridge（`repomesh_agent_bridge`）、leader actions、room timeline
  - 前端 `AgentRuntimeHosting`（"container" \| "external"）、`TeamDecompositionMode`
    （"server" \| "leader"）、`SetupDependency`、`RepositoryVerificationUpdate`
- **手动合并**：同时涉及两边演进、不能整体取一方的文件（如 `contract.ts`、
  `bootstrap/app.py`、`task_orchestration/application.py`）。

## 4. 分组决策明细

### 4.1 后端 src 组

| 文件 | 决策 | 说明 |
|---|---|---|
| `settings.py` | 合并 | 保留 ours 平台白名单字段，并入 theirs 新增配置 |
| `api/router.py` | 合并 | 两侧路由共存 |
| `application/__init__.py` | 合并 | 两侧应用注册共存 |
| `bootstrap/app.py` | 合并（4 块） | ours 初始化 + theirs 新模块装配 |
| `bootstrap/container.py` | 合并 | 两侧容器注册共存 |
| `delivery/application.py` | 合并 | 保留 `DeliveryAuditLog`，并入 conflict cases |
| `modules/repository_intelligence/api/router.py` | ours + PATCH 块 | 保留 ours 整体，追加 theirs 的 verification PATCH |
| `task_orchestration/application.py` | 合并（3 块） | 两侧编排逻辑共存 |
| `persistence/base.py` | 合并后重写 | 合并产物损坏（imports 位置错误、内容重复），重写为干净合并，`ALL_SCHEMAS` 含 `decision_chain` 与 `capability_management` |

### 4.2 frontend 组

| 文件 | 决策 | 说明 |
|---|---|---|
| `src/api/contract.ts` | ours 基础 + theirs 添加项 | 保留图预补充等 ours 演进；追加 theirs 的 `test_commands`/`test_paths`、`AgentRuntimeHosting`、`RepositoryVerificationUpdate`/`RepositoryVerificationView`、`TeamDecompositionMode`、`sender_agent_id` 可空（D-4）、`SetupDependencyState`/`SetupDependencyView` |
| `vite.config.ts` | theirs | 与 main 的前端构建配置保持一致 |

### 4.3 tests 组

| 文件 | 决策 | 说明 |
|---|---|---|
| `tests/observability/test_usage_recorder.py` | theirs | 纯健壮性修复（Counters 处理 UTC 午夜跨越） |
| `tests/api/test_issue_discovery.py` | theirs + ours 机制 | 采用 theirs 基础，恢复 ours 的 `gate_block_from` 并发门控机制 |
| `tests/test_api.py` | ours + theirs 添加项 | 保留 ours 平台白名单语义测试；追加 theirs 的 setup dependencies 断言与 PATCH verification 测试（成功、幂等重放、404、401） |

### 4.4 配置/文档组

| 文件 | 决策 | 说明 |
|---|---|---|
| `.env.example` | 合并 | 两侧环境变量共存 |
| `README.md` | theirs | main 对产品描述与功能清单的更新成立 |
| `compose.yaml` | theirs + ours scan 配置 | 采用 theirs 编排，保留 ours 的扫描配置项 |
| `docs/architecture/module-map.md` | 合并 | 保留两侧新增模块 |

### 4.5 migrations 组

| 文件 | 决策 | 说明 |
|---|---|---|
| `migrations/env.py` | 合并 | 修复 F401：将 `McpServerPolicyRecord`/`SkillEvaluationRecord`/`SkillSnapshotRecord`/`SkillVersionRecord` 加入 `_REGISTERED_MODELS`，同时保留 `DecisionNodeRecord` 与 `DeliveryConflictCaseRecord` |

## 5. 迁移链处理

本次合并未产生迁移链冲突：

- 本地保留上次合并后的迁移（含 ours 的 `20260815_0032_plan_snapshot_discovery_version`、
  `20260828_0033_decision_chain_nodes`）
- main 新增迁移 `20260826_0036` … `20260830_0049`（platform credentials、bootstrap
  operations、scm command leases、worker execution reservations、task assignment attempts、
  execution plan revisions、delivery conflict cases、unified recovery cases、capability
  governance、handoff docs、leader decision、team decomposition mode、leader decision state、
  room timeline）自动并入
- 完整 revision 校验：文件名即 revision，无重复（0032/0033 为 ours 与 main 同号不同日期文件）

## 6. 合并引入的回归与修复

验证阶段定位并修复了以下回归：

1. **`persistence/base.py` 合并产物损坏**：imports 出现在 class 之后且整个文件内容重复，
   用干净合并重写（含正确的 `ALL_SCHEMAS` / `BUSINESS_SCHEMAS`）。
2. **`migrations/env.py` F401 违规**：导入 capability_management 模型但未注册，已加入
   `_REGISTERED_MODELS`。
3. **前端 `contract.ts` 竞争丢失**：对同一文件的并行编辑导致 `AgentRuntimeHosting` 丢失，
   已按顺序重新应用并通过 `git grep` 验证。
4. **`test_decision_chain_events.py` 失败**：theirs 的 grounded-edge 规则（`plan_integration`
   现在要求新 LLM 契约边必须由 approved plan 的 `depends_on`/`impacts` 支持）使原本串行的
   ts-notify/ts-order 变为并行。确认该行为是预期的合入行为后，在测试脚本中为 MAYBE 确认
   添加 `_confirmation("MAYBE", depends_on=("ts-notify",))`，精确恢复串行依赖断言。

## 7. 验证结果

| 检查项 | 结果 |
|---|---|
| `ruff check .` | 通过（全绿） |
| 前端 `tsc -b` | 通过（exit=0） |
| 全量 `pytest` | **2664 passed, 31 skipped**（875s） |
| flaky 用例 | `test_worker_execution_reservations.py::test_concurrent_reservation_returns_one_run` 全量运行时偶发失败；单独重跑整个文件 4/4 通过、该用例连续 3 次通过——SQLite 并发时序敏感，非合并回归（该文件为 main 原生实现，合并无冲突） |

## 8. 遗留处理：GOAI 初赛 PPT 文件

与 2026-08-28 合并相同的现象：合并后工作区出现 20 个 D 状态的 GOAI 初赛 PPT 文件
（`GOAI初赛PPT-*.md` / `*.pptx`）及 1 个文档删除。这些删除发生在 stash 之后、与合并内容
无关，已通过 `git reset --hard HEAD` 从 HEAD 完整恢复，未进入合并提交。

## 9. 提交与分支

- 合并提交：`8c816b5` Merge origin/main into feat/repo-scan-chain-merge-main
  - parent1：`c04fdd8`（本地分支）
  - parent2：`d2e84c7`（origin/main）
- 合并内容：406 个文件变更（+87580 / -4343）
- 本地分支领先 `origin/feat/repo-scan-chain-merge-main` 120 个提交（含本次合并）
- 历史 stash `wip-pre-merge-main-20260831` 经验证仅含行尾差异（`--ignore-space-at-eol`
  后 diff 为空），无真实内容改动，已删除
