# 交接：PR-A 第二刀已落地（§8.17 + M1 + M2），下一刀 T5 装配（2026-09-05）

写于 2026-09-05 本机时间（UTC−7）。本会话按 `2026-09-05-handoff-wave1-pr-a-cut2.md` §7 的 prompt 开工。
用户指定「先自己读完文档，再用 agents 施工，我来验收」：接缝契约（`contracts.py`）与内存 store 由我亲手写，
store+迁移 / round / observer+approval 三路 agent 并行施工，测试由另两路 agent 补齐；本文所有数字均经我复跑。

术语按 `CONTEXT.md`。上一份交接：`2026-09-05-handoff-wave1-pr-a-cut2.md`。

---

## 0. 一句话

托管原生第二刀四笔提交在 `feat/hosted-native-wave1`，**未推送**：§8.17 裁决 `63e5052e` → M1 `5f35e11c` → M2 `7205b35c` → 本次 docs。
`integrations/hosted_native/` 从零到七个模块 + 迁移 `20260904_0056` + 三条 settings；`HostedNativeRound` 三个动词、观察器、自动审批全部有测试。
**一行都没接进 bootstrap**：投递分叉、观察器后台服务、审批扇入、恢复分支、M5/M6 真适配器都属 T5。

## 1. 本会话做了什么

| # | 产出 | 位置 | 状态 |
|---|---|---|---|
| §8.17 | D-23 归一化规则定案：只剥恰好一个 `cd <该尝试自己的 shared/tasks/<attempt_id>> && ` 前缀，其余逐字比对；串接/分号/管道/重定向/别的目录/多余参数/大小写一律留给人 | spec D-23、§8.17（条内「09-05 裁决」①–⑤）、`contracts/agentteams-task/v2/helper-cli.md`「Auto-approval normalisation」、`package.schema.json` | `63e5052e` |
| 接缝 | `integrations/hosted_native/contracts.py`：`AttemptPhase`（开放 = notified/acknowledged/review_pending/verifying，终态 = verified/failed/blocked/fenced）、`HostedNativeAttempt`、`HostedNativeEvent`、`HostedNativeAttemptStore`、`SharedTaskDirectoryReader`、`SharedTaskEvent`、`RoundTransition(next_attempt_id)`、`BaseBundleSource`（M6 位）、`CandidateVerificationLauncher`（M5+调度位）、`ConstructionPolicySource`、`MatrixSenderResolver`、`ApprovalSender` | 我写 | `5f35e11c` |
| M1 store | `store.py`：两张表的记录类 + `PostgresHostedNativeAttemptStore` + `InMemoryHostedNativeAttemptStore`；迁移 `20260904_0056_hosted_native_attempts.py`（部分唯一索引 `uq_hosted_native_attempts_open_task`、事件 `UNIQUE(attempt_id, kind, marker)`）；`migrations/env.py` 注册；settings 三条 + `.env.example` 第一段 | agent | `5f35e11c` |
| M1 round | `round.py`（913 行）+ `messages.py`：`open` / `observe` / `expire` 全按 spec §4.2 M1；见 §2 偏离 | agent，我修两处（§2 ⑦⑧） | `5f35e11c` |
| M2 | `observer.py`、`approval.py`、`storage.py`（Disk/MinIO/InMemory 读取器）、`matrix.py` 加 `send_approval` | agent | `7205b35c` |
| 测试 | `tests/hosted_native/test_store.py` 31、`test_round.py` 29、`test_observer.py` 47、`test_approval.py` 78；`tests/integrations/agentteams/test_matrix_approval.py` 17；`tests/integration/test_hosted_native_attempts_postgres.py` 2（Postgres 回环） | 两路补测试 agent + 我加 2 例 | 随 M1/M2 |
| 验证 | 受影响回归 556 过（`tests/hosted_native` 除 round、`tests/integrations/agentteams`、`tests/contracts`、`tests/architecture`、`test_worker_failure_recovery`、`test_repository_team_onboarding`、`test_task_publication_translation`）+ round 29 过；Postgres 回环 4 过（0056 新 2 + 0055 旧 2，一次性库）；`alembic heads` 单头 `20260904_0056`；ruff 全绿；新文件全 LF | — | 通过 |
| 文档 | spec §4.2 M1/M2「09-05 落地」注、§5.3.1 0056 行、§5.3.2「设置」「尝试」行、§5.3.3 测试清单、§8 引言 | 本次 docs 提交 | — |

## 2. 与 spec 的偏离（都已写回 spec §4.2 落地注，这里给结论）

1. **`hosted_native_attempts` 多三列**：`room_id`（通知发到的团队房，自动审批按房间 + 发信人认领请求）、`base_sha`（bundle 钉住的提交，候选校验与复验都要）、`review_budget_until`（D-13 审阅预算，与 worker 预算分开）。
2. **M5/M6 未施工，round 只依赖窄端口**：`BaseBundleSource.build(repository_id)`、`CandidateVerificationLauncher.launch(candidate, attempt=) -> run_id`、`ConstructionPolicySource.resolve(task_id, worker_agent_id=)`；真适配器 T5 装配。审阅包与验证调度的策略回读该尝试 `base/package.json`（worker 实际被告知的那份），文件缺失才重解析。
3. 人工检查点与恢复决策是两个可选回调 `escalate(task_view, reason)` / `recover(attempt, reason)`，T5 接 `HumanReviewRequestStore.ensure` 与 D-12 恢复决策。
4. `attempt_id = uuid4()`，不从 task+generation 派生（同代次封存后重开要新目录，D-8）；幂等 = 该 task 当前代次已有开放尝试即 `created=False`。
5. 预留 `task_payload` 绑 `{"schema": "repomesh.hosted-native.attempt/v1", …}`，恢复循环据此区分托管原生尝试与 runner 调度；Leader `REVISION` 重开同一代次**续租**同一条预留。
6. 审阅通知以**组织 Leader 为发信身份**发给仓库 Leader（`SendCollaborationMessage._route` 只在组织 Leader 为一端时落 `leader_room_id`；方向校验 TASK_ASSIGNMENT 须直接下属，恰好成立）。
7. **我修的第一处（agent 指出的盲点）**：`observe`/`open` 因代次前进封存旧尝试时原本不释放其预留，下一代次换 worker 的 `open()` 会被 `reserve()` 以「different task binding」拒掉、任务卡 block 直到过期扫描（最长一个预算）。现在封存 `generation_advanced` 同时 `fail_preparation` 该预留（租约已过期则留给恢复循环）。测试 `test_open_fences_a_stale_generation_attempt_and_releases_its_reservation`。
8. **我修的第二处（D-13）**：`expire` 对 `review_pending` 的尝试不再只交恢复决策，而是 block 任务 + `escalate(task, reason)`（审阅超时不跳过，开人工检查点）；其他阶段仍只封存 + `recover`，不 block。测试 `test_expire_while_the_leader_holds_the_review_blocks_and_escalates`。
9. 自动审批不是观察器自己轮询房间，而是实现既有 `MatrixInboundProcessor`，由既有 `/sync` 轮询器扇入（一条 sync 两个消费者，不新开接缝）；候选尝试只取 **worker 侧阶段**（`notified`/`acknowledged`），比 D-23 的「非终态」更窄——审阅中的 worker 已经没有 shell 要跑。
10. 观察器：`submitted` 要等 `result.md` 真可读（copaw 逐文件推送，`meta.json` 可能先一拍到）；已记录未 applied 的事件行下一轮重投；Leader 的 ack 不是事件。
11. **未做**：复验器终态（`runner.completed/failed`）把尝试推到 `verified/failed` 的钩子——需要网关写回路径上多一个订阅点，属 T5/PR-B；`package.py` 未单开（并入 round/contracts）。

## 3. 补测试 agent 留下的四条观察（我判定：都不改）

- `approval.py` 用 `occurred_at >= notified_at`，§8.17 ⑤ 写「晚于」；毫秒级等号无害。
- 一个 worker 在同一房间同时持两个开放尝试时，**裸**命令行（无 `cd`）会归到 `list_open` 里靠前的那个（审计归属而非审批正误）；实际上 `uq_worker_execution_reservations_active_worker` 让一个 worker 同时只有一条活跃预留，这种情况不会出现。
- 审批不查 `budget_until`：预算已过但 `expire` 还没封存的窄窗口里仍会批一条帮手命令；§8.17 没把预算列为闸门，批帮手命令也无害。
- `WORKER_SIDE_PHASES` 收窄见 §2 ⑨。

## 4. 当前状态

- 分支 `feat/hosted-native-wave1`：本次 docs → `7205b35c`（M2）→ `5f35e11c`（M1）→ `63e5052e`（§8.17）→ `d17fa054`（09-05 上一刀 docs）→ `fdc42f8d`（M3）→ `277959b4`（M7）→ `2ac657fd`（已推）。**本地领先 origin 七笔，未推送，推前要用户放行**；`main = origin/main = 6974698b`。
- 迁移头 `20260904_0056`；下一个 `20260904_0057_repository_toolchain.py`（第二波）。
- 环境未动：Docker 引擎活着（本会话没碰坏 socket）；compose Postgres 5432 在，一次性库测试自建自删；api 仍是 09-02 镜像未重建（上一刀 §3 的放行项还在）。
- 工作树里还有大量与本线无关的未跟踪文件（`docs/architecture/*.html/png`、`docs/development/*-proposal-*`、录屏 mp4、`defs.json`、`.playwright-cli/`）——**不是本线产出，别顺手 add**。

## 5. 任务表（接 cut2 §5）

| # | 任务 | 状态 |
|---|---|---|
| T3 | §8.16 载体 | 已定 (b) |
| T3' | §8.17 归一化 | **完成** `63e5052e` |
| T4 | M1 `HostedNativeRound` + 迁移 0056 + store | **完成** `5f35e11c` |
| T4' | M2 观察器 + 自动审批 + 读取器 + `send_approval` | **完成** `7205b35c` |
| T5 | 装配：`_deliver_assignment` 按 `team_construction_mode_reader()` 分叉到 `round.open()`；`container.py`/`app.py` 注册 `SharedTaskDirectoryObserver` 后台服务（MinIO/磁盘读取器与发布器同条件选择）与审批扇入（`AgentTeamsMatrixInboundPoller` 加一个复合 processor）；M6 `BaseBundleBuilder`（bundle 带 `HEAD` + 分支 ref，S-10）、M5 `CandidateWorktreeMaterializer` + 验证调度投影 + `RunnerControlGateway.enqueue` 合成 `CandidateVerificationLauncher`；`ConstructionPolicySource` 真适配器（`BuildCodingAgentPackage` ∪ catalog `test_paths`）；恢复分支（过期预留 `task_payload.schema == repomesh.hosted-native.attempt/v1` → `round.expire(budget_expired)` → decide；`_start_replacement` 分叉到 `round.open()`）；`escalate`/`recover` 回调接线；复验器终态 → 尝试 `verified/failed` 钩子 | 未开工 |
| T6 | PR-B verifier（含 §8.16 (b) 心跳带 `State.StartedAt`） | 未开工，可并行 |
| T7 | PR-C 前端 | 未开工 |
| T8 | 活体 mock 链：api 需 `compose build api` 重建才能验 adapter 过滤（等放行）；`.git` 0600 缺陷 | 未动 |
| 新 | 推送四笔 + 是否开 PR 看 CI | **等用户放行** |

## 6. 坑（本会话）

- **agent 写完源码后在写大测试文件阶段被看门狗判停滞（600 s 无进展）**，两个 agent 同时中招；源码已完整落盘且 ruff 干净，重起两个只写测试的 agent（窄 brief + 逐场景清单）40 分钟内补齐。经验：源码与测试分两批 agent；brief 里要求「每条 shell 命令短且有超时」。
- 用 `Edit` 之外的 python 脚本改 spec/契约（多处中英混排的长行，精确替换比 Edit 稳）；改完 `grep -n "^1[0-9]\. "` 看编号仍对。
- `python3` 在 Git Bash 是 Windows Store 桩；一律 `uv run`。`git add` 的 LF→CRLF 警告无害；新文件我用 `grep -q $'\r'` 全查过是 LF。
- `ruff check tests/hosted_native` 在别的 agent 还在写文件时会看到半成品的 E501——先看 `-->` 指向哪个文件再判。
- 消息路由：给仓库 Leader 的审阅通知必须以组织 Leader 为 `sender_agent_id`，否则 `_route` 落团队房（Leader 在团队房被 @ 会身份混淆，S-4）。

## 7. 给下一会话的 prompt（原样复制）

```text
上一个会话（2026-09-05）把 PR-A 第二刀落地了：§8.17 归一化裁决（63e5052e）、M1 HostedNativeRound + store + 迁移 0056
（5f35e11c）、M2 观察器 + 自动审批 + 读取器 + Matrix send_approval（7205b35c），都在 feat/hosted-native-wave1，未推送。
spec §4.2 M1/M2 各有「09-05 落地」注写明偏离（attempts 多 room_id/base_sha/review_budget_until 三列；M5/M6 以
BaseBundleSource / CandidateVerificationLauncher / ConstructionPolicySource 三个窄端口占位；escalate/recover 两个可选回调；
审批是 MatrixInboundProcessor 由既有 /sync 轮询器扇入；expire 对 review_pending 走 D-13 block+人工检查点；代次前进封存旧尝试
同时释放其预留）。一行都没接进 bootstrap。交接在 docs/startup-records/2026-09-05-handoff-wave1-pr-a-cut3.md。

这个会话的任务（PR-A 第三刀 = T5 装配）：
1. `_deliver_assignment`（task_orchestration/application.py:534-565）按 `team_construction_mode_reader()` 分叉：hosted_native 且
   assignee 是 WORKER → `HostedNativeRound.open()`；否则原路。`_assignment_body` 不动（托管原生的文案在 hosted_native/messages.py）。
2. container.py/app.py 装配：SharedTaskDirectoryReader（MinIO/磁盘与发布器同条件，app.py:234-251）、HostedNativeRound、
   SharedTaskDirectoryObserver 注册为后台服务（interval = settings.hosted_native_observer_interval_seconds）、
   ToolGuardAutoApprover 扇入 AgentTeamsMatrixInboundPoller（加一个复合 processor，记录器仍先跑）；MatrixSenderResolver =
   AgentTeamsMatrixIdentityResolver，ApprovalSender = matrix_client.send_approval。
3. M6 BaseBundleBuilder（integrations/workspace/base_bundle.py：镜像仓 git bundle 带 HEAD + 分支 ref，S-10）；
   M5 CandidateWorktreeMaterializer（candidate_worktree.py：fetch candidate.bundle 进镜像仓、worktree add --detach）+
   验证调度投影（task_projection.py 加 candidate、adapterId=repomesh-verifier）+ RunnerControlGateway.enqueue 合成
   CandidateVerificationLauncher；ConstructionPolicySource 真适配器 = BuildCodingAgentPackage ∪ catalog test_paths（同 runner 投影）。
4. 恢复分支：过期预留 task_payload.schema == "repomesh.hosted-native.attempt/v1" → round.expire(budget_expired) → decide；
   _start_replacement 对 hosted_native 团队分叉到 round.open()；escalate → HumanReviewRequestStore.ensure（EXCEPTION_ESCALATION），
   recover → 恢复决策；复验器终态（runner.completed/failed）→ 尝试 verified/failed 的钩子。
5. 每模块一提交，英文提交信息、不加 Co-Authored-By；推 GitHub 前问我。

先读：CONTEXT.md；本交接 §2、§3、§5、§6；spec §4.2 M1/M2 落地注、§5.3.2「装配」「投递分叉」「恢复」三行、D-9/D-10/D-11/D-12；
src/repomesh/integrations/hosted_native/{contracts,round,observer,approval,storage}.py；
src/repomesh/bootstrap/app.py:234-251,440-470,592-727 与 container.py:2150-2275；
src/repomesh/modules/task_orchestration/application.py:534-600；integrations/runner/{recovery,gateway,task_projection}.py；
integrations/workspace/git_worktree.py。
约束同前（REPOMESH_GITHUB_APP_ID=0；不 ruff format 整文件；不动 repomesh_agent_bridge；runner 只加法；Docker 起来前看坏 socket；
不碰密码不填 API key；夹具仓不推）。
```
