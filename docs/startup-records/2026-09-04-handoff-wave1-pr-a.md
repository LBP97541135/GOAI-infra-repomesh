# 交接：下一会话「PR-A 第一刀：M7 construction_mode + M3 任务包 v2」（2026-09-04）

写于 2026-09-05 00:13 本机时间（UTC−7；本会话的产物全部标 09-04）。本会话从「五角度审计双模式进度」开始，
依次做了裁决、提交推送、共享队列隔离，**没有写一行托管原生的施工代码**；结束时 Docker 引擎没在跑。

术语按 `CONTEXT.md`。上一份交接：`docs/startup-records/2026-09-03-handoff-wave1-kickoff.md`（其 §3 八条待裁决本会话已全部处理）。

---

## 0. 一句话

本地 CLI（Bridge）模式**可演示、不可交付**；托管原生（hosted_native）模式**设计与波次 0 实证完毕、§9 六条已裁决写回 §3、
共享派发队列的抢活隐患已修**，施工代码仍为零行——下一刀是 PR-A 第一刀：M7 列与推导（迁移 **0055**）+ M3 任务包 v2。

## 1. 本会话做了什么

| 产出 | 位置 | 状态 |
|---|---|---|
| 五角度只读审计（设计文档 / 托管原生代码 / 本地 CLI / 版本史 / 共享地基）与一页报告 | 报告页 <https://claude.ai/code/artifact/ab44300b-bd51-4bed-849a-8d7d011c0c2e>；记忆 `dual-mode-progress-audit-20260904` | 已发布（私有） |
| spec §9 六条裁决写回 §3：D-3 / D-6 / D-12 / D-21 标「09-03 实证修订」，**新增 D-23**（Tool Guard 归平台，第一阶段 = M2 观察器兼自动审批）；§8 去「不阻断波次 1」、9/11/12/13/14 标已裁决、新增 **§8.16**（启动时间载体待定）；§9 加「裁决」列；目的文档 §7 措辞；剧本 §4 AC-02 补「无需人工审批」 | `docs/development/agentteams-native-execution-mode-spec-20260902.md`、`…-purpose-20260902.md`、`hosted-native-e2e-acceptance-script-20260902.md` | 提交 `2015f8d2` |
| 新分支 `feat/hosted-native-wave1`（从 main `6974698b` 切）四笔：`09731bdc` style 修 main 两处 E501（CI ruff 命令本地全绿）→ `2015f8d2` docs(hosted-native) → `9e68b5c9` docs(startup-records) 95 文件（09-02/09-03 全部记录与截图证据）→ `ad37fec2` chore(scripts) 24 文件（ruff format + 手工消 E501/E741；`pyproject.toml` 把 `docs/startup-records` 加进 ruff exclude） | GitHub `LBP97541135/GOAI-infra-repomesh` | **已推送** |
| 共享队列隔离：`GET /runtime/runner-tasks/next` 加可重复 `adapter` 查询参数（按冻结 payload 的 `adapterId` 过滤，无新列）；全局 control token 无 `adapter` → 400，且永远领不到 `REPOMESH_RUNNER_WORKER_TOKENS` 里有 id 的成员队列（点名 403 / 不点名跳过）；runner 侧 `REPOMESH_RUNNER_ADAPTERS`（compose 默认 `mock`），未设则 `launchable_profiles(resolve_binary)`，为空拒绝启动；规则写进 `contracts/runtime/README.md` | `bdd04406` feat(runtime)；spec §1.1/§5.2/§5.3.1（迁移改 0055/0056/0057）/§5.3.2/§6/§7/§9 同步 = `32ab4ee9` | **未推送（ahead 2）** |
| 验证 | `uv run ruff check .` 全绿；针对性测试 136 过 3 跳过（跳过 = 需真 Postgres 的 `test_runner_gateway_postgres.py`）：`tests/api/test_runner_scoped_auth.py`、`tests/integrations/runner/test_gateway.py`、`tests/runner/{test_task_source,test_runtime_env,test_profiles,test_main_loop}.py`、`tests/test_api.py::test_runner_control_requires_configured_token`、`tests/test_plan_loop_e2e.py`、`tests/agent_bridge/test_governed_execution.py` | 通过 |
| 记忆 | `dual-mode-progress-audit-20260904.md`（含步骤 1–3 结果与 How to apply） | 已写 |

**没做的**：① 活体一键栈实走一次 mock 链，确认重建后的 runner 镜像带 `adapter=mock` 仍能领活；② PR-A 一行未写；③ §8.16 启动时间载体未定；④ 21 篇不属本线的未跟踪文档（8 月设计稿、`复赛优化建议落实情况-20260903.md`）与工作树垃圾未处理。

## 2. 当前状态与上下文

### 2.1 双模式进度

| 线 | 设计 | 实证 | 裁决 | 施工 | 交付 | 一句话 |
|---|---|---|---|---|---|---|
| 托管原生 `hosted_native` | ✓ spec D-1…D-23、M1…M8、30 幕剧本 | ✓ 基线 13 PASS / 1 BLOCKED；波次 0 三个答案 | ✓ 09-04 六条写回 §3 | **零行**（M1…M8、`src/repomesh_verifier/`、`contracts/agentteams-task/v2/` 均不存在；任务包仍 v1） | — | 下一刀 PR-A |
| 本地 CLI `local_cli` / Bridge | ✓ | ✓ R6 八 AC | ✓ | ✓ 合并 main `2706483f`；09-03 demo 补丁 `bae2c5d5` | **可演示不可交付**：三次录 demo 跑通；未修 P-2（redispatch 不重发 Leader 通知）、P-3/P-4（.env 空串与运行时回填）、P-6（Low 标签无预检）、F-1/F-2/I-1、告警页 204 当 JSON、pause_intake 永久 503、NewIssueModal 503 不渲染、compose 不透传 worker 令牌 | 本线不动它 |

共享地基里最大的雷已拆：09-03 `b38549a0` 进栈的 runner sidecar 用全局 token 领所有队列且无 adapter 过滤——现在无主体领活必带 `adapter`，且领不到持令牌成员的队列（按凭据表判，不读控制器）。Bridge 代码零改动（它用 worker 令牌）。

### 2.2 三个决定 PR-A 形状的硬事实（09-04 代码审读）

1. **Tool Guard 不能经控制器下发**：控制器 Go 侧 `WorkerSpec`（`agentteams-controller/api/v1beta1/types.go:174-256`）与 `internal/agentconfig/generator.go` 没有 `security`/`tool_guard` 字段；copaw `copaw_worker/bridge.py:226-306 _write_config_json` 只写 `channels`；`credential_guard.py:21 apply_credential_guard` 在 vendored 源里无调用者。规则集在运行时包 `copaw.security.tool_guard`，vendored 只有 `ToolGuardConfig` 结构（`matrix/config.py:1083-1095`）。→ D-23 选 b，且 D-21 的规则集测试夹具要从活体 worker 镜像导出。
2. **迁移头 `20260902_0054`**，`0053` 是数据库测试团队移交 → 本线迁移从 `20260904_0055` 起（spec §5.3.1 已改）。
3. **控制器 `WorkerStatus` 没有 `startedAt`**（`types.go:371-383` 只有 `phase/containerState/lastHeartbeat/lastActiveAt`）→ D-12 信号②的载体待定（§8.16 三候选），落定前只有预算到期与 phase 两个信号生效。

### 2.3 分支与 CI

- 工作树在 `feat/hosted-native-wave1`，`HEAD = 32ab4ee9`，领先 `origin/feat/hosted-native-wave1` 两笔（`bdd04406`、`32ab4ee9`）；`main = origin/main = 6974698b`。
- GitHub Actions：main 自 `fd26f09f` 起六连红（ruff E501 ×2 + 一次 PG 并发抖动）；`09731bdc` 修了 E501，本地 `uv run ruff check .` 全绿，**分支上的 CI 结果还没看**。
- 注意 `ruff format` 不在 CI 里；`runner_store.py`、`router.py`、`gateway.py`、`test_gateway.py`、`test_runner_scoped_auth.py` 在 main 上本来就不是 format 干净的，**别对整文件 format**。

### 2.4 环境实况

- 00:13 探测：`docker ps` 报 named pipe `dockerDesktopLinuxEngine` 不存在 = **Docker 引擎没在跑**。上次已知（09-03 交接 §2）：compose 四件 `goai-infra-repomesh-{postgres,api,web,bootstrap}`、`agentteams-controller`、`agentteams-manager`、8 个 `agentteams-worker-agt-*`（**无 restart policy，引擎恢复后要 `docker start $(docker ps -aq --filter name=agentteams-worker-agt-)`**）、demo 的 `repomesh-demo-pg`(15549) 与 `repomesh-demo-controller-fwd`(18090)。
- 起 Docker Desktop 前先看 `%LOCALAPPDATA%\Docker\run\` 有无坏 socket（记忆 `docker-desktop-socket-corruption`，已复发 7 次）。
- **一键栈的 runner 镜像要重建**：`bdd04406` 改了 `repomesh_runner/task_source.py`，旧镜像不带 `adapter` 会被 api 400 后无限退避；`docker compose --profile platform build runner && docker compose --profile platform up -d runner`。
- 数据面 id（基线 issue `69ae763c…`、演练 issue `283c4640…`、波次 0 三个尝试目录、审阅目录）沿用 09-03 交接 §2；实证残留未清理，清理命令在 spike 记录 §7。
- 凭据位置不变（09-02 交接 §2.3）；任何起 app 的测试要带 `REPOMESH_GITHUB_APP_ID=0`（仓库根 `.env` 空串，P-3）。

### 2.5 未跟踪且不属本线的东西

`docs/development/` 下 21 篇 8 月设计稿与 `复赛优化建议落实情况-20260903.md`、`docs/architecture/*`（archify 生成物 4.1 MB）、`docs/defense/`（2.8 MB）、`.claude/worktrees/`（49 个 worktree 4.1 GB，未被 gitignore）、`Screen Recording 2026-09-03 01.23.mp4`（457 MB）、`defs.json`。要不要入库/忽略是用户的决定。

## 3. 任务表

| # | 任务 | 落点 | 依赖 | 状态 |
|---|---|---|---|---|
| T0 | 推送 `bdd04406`、`32ab4ee9`；看分支 CI | `git push origin feat/hosted-native-wave1` | 用户放行 | 待推 |
| T1 | **PR-A-1 M7 `ConstructionMode`**：枚举 + `DerivedRuntime` + `derive_runtime()`（hosted_native → `(True, COPAW, SERVER)`，local_cli → `(False, COPAW, SERVER)`）；`RepositoryTeam.construction_mode`（默认 HOSTED_NATIVE）；`with_adopted_leader` 的 LEADER 闸只在 LOCAL_CLI 生效；`TeamConstructionModeReader` + `PersistedTeamConstructionModeReader`；迁移 `20260904_0055_team_construction_mode.py`（列 + 索引 + downgrade，样板 `20260830_0047_team_decomposition_mode.py:63-111`）；接团队 API 去 `leader_runtime/worker_runtime` 加 `construction_mode`（`human_control_models.py:120-127`、`human_control.py:224-357`、`platform_setup.py:76-83`）；投影 `runtime_projection.py:159-205,279-293` 从全局 `worker_runtime` 改 `TeamConstructionModeReader` + 按团队 `derive_runtime()`，MCP 投影保留（D-18）；读模型 `read_models/service.py:1594-1642,2202-2207` 加 `construction_mode` | spec §4.2 M7、§5.3.1、§5.3.2 前四行 | T0 无关 | 未开工 |
| T1-测 | `tests/integration/test_hosted_native_postgres.py`（照 `test_leader_assignments_postgres.py:106-238` 子进程 alembic 红→绿→降级）；更新 `test_runtime_projection.py`、`tests/api/test_issue_materialize.py`；架构测试若锁定 project 模块字段要同步 | 只做针对性验证 | T1 | 未开工 |
| T2 | **PR-A-2 M3 任务包 v2**：`publish(..., package: PackageInputs \| None)`，`package=None` 行为不变；`manifest.schema=repomesh.agentteams-task.v2` 全文件摘要；`base/package.json`（`kind, task_id, attempt_id, generation, budget_seconds, base_sha, repository_id, organization_id, test_commands[], allowed_paths[], denied_paths[], workspace_root, helper_commands[]`）；`base/tools/repomesh-work.sh`（从 `scripts/hosted-native-e2e/spike/rm-work.sh` 改名，去掉命令行里任何 `rm` 片段，读 `base/package.json`）；`_render_spec` 分 construction/review 两模板（原型 `spike/spec_construction.md.tpl`、`spec_review.md.tpl`，文案改「完成通知 @admin 或不 @，不 @Leader」）；磁盘与 MinIO 两适配器同步改；`_digest` 与 `:76-80` 冲突检查改比 v2 | spec §4.2 M3、§5.3.2 发布器行、D-6/D-21/D-23 | 无 | 未开工 |
| T2-契约 | `contracts/agentteams-task/v2/{README.md, manifest.schema.json, meta.schema.json（写明 repomesh 块只在发布时刻有效）, package.schema.json, candidate.schema.json, helper-cli.md（四条完整命令行）, review.md}` + `tests/contracts/test_agentteams_task_v2_contract.py`；D-21 的 Tool Guard 规则集测试：夹具从活体 worker 镜像导出（要 Docker），四条命令行逐条过规则集 | spec §5.2 | Docker 活着 | 未开工 |
| T3 | **§8.16 载体决定**（PR-A 施工前）：a) 观察器读控制器 `lastHeartbeat`/`containerState` 序列（要在活体 `docker restart` 对照一次）；b) verifier 心跳附带 worker 容器 `State.StartedAt`（PR-B）；c) fork 控制器加 `startedAt`（第一阶段不选） | spec D-12、§8.16 | Docker 活着 | 未定 |
| T4 | PR-A-3 M1 `HostedNativeRound` + M2 观察器（含 D-23 自动审批 `approval.py`：只批与 `helper_commands[]` 逐字相同的命令行，先写 `hosted_native_events(kind=auto_approved)` 再回恰好 `/approve` + `m.mentions`）+ 迁移 `20260904_0056_hosted_native_attempts.py`（两表）| spec §4.2 M1/M2、§5.3.1 | T1、T2、T3 | 未开工 |
| T5 | PR-A-4 M5/M6 候选工作树与 base bundle（bundle 带 `HEAD` + 分支两个 ref，S-10）、M8 组合门禁与 `setup/status.execution_plane`、`_deliver_assignment` 分叉、恢复分支（`worker_restarted` / `worker_not_running`） | spec §4.2、§5.3.2 | T4 | 未开工 |
| T6 | PR-B verifier：M4 + `Dockerfile.verifier` + compose `verifier` 服务 + 心跳（`adapter` 过滤已落地） | spec §4.2 M4、§5.4 | 可与 PR-A 并行 | 未开工 |
| T7 | PR-C 前端 §5.1 | spec §5.1 | T1 契约 | 未开工 |
| T8 | 活体验证：一键栈 mock 链（重建 runner 镜像后确认 `adapter=mock` 领活）；波次 1 三十幕按剧本 | `docs/one-shot-e2e-guide.md`、剧本 §3 | Docker 活着 | 未做 |
| T9 | 本地 CLI 顺手三修：settings 空串校验（P-3，同时消掉 `test_leader_lane` 5 红与 `test_api` 33 红）；`redispatch` 补发 Leader 规划通知（P-2）；`InMemoryOperationalGate` 手动清除或删规则联动（pause_intake 永久 503） | `settings.py`、`task_orchestration/application.py:598,1436-1500`、`observability/.../operations.py:182-310` | 无 | 未做 |
| T10 | 工作树卫生：`.claude/worktrees`、mp4、`defs.json` 进 `.gitignore` 或删；21 篇未跟踪文档入库与否 | `.gitignore` | 用户决定 | 未做 |

## 4. 要读的文件（按顺序）

1. `CONTEXT.md`
2. 本交接（§2.2 三个硬事实、§2.4 环境、§3 任务表、§5 坑）
3. `docs/development/agentteams-native-execution-mode-spec-20260902.md`：§3 D-1…D-23（重点 D-3 / D-6 / D-12 / D-17 / D-18 / D-21 / D-23）、§4.2 M3 与 M7、§5.2、§5.3.1、§5.3.2、§8.16、§9
4. `docs/development/agentteams-native-execution-mode-purpose-20260902.md` §5（11 条不变量）、§7
5. `docs/startup-records/2026-09-03-hosted-native-spike.md` §0、§4（S-1…S-10）
6. PR-A-1 落点：`src/repomesh/modules/project/{contracts,domain,infrastructure}.py`（`TeamDecompositionMode`、`RepositoryTeam:163-179`、`with_adopted_leader:242-267`、`PersistedTeamDecompositionModeReader:591-625`）；`src/repomesh/api/human_control_models.py:120-127`、`api/human_control.py:224-357`；`src/repomesh/integrations/agentteams/runtime_projection.py:159-205,279-293`；`migrations/versions/20260830_0047_team_decomposition_mode.py`（样板）与 `20260902_0054_merge_decision_embeddings_and_test_team.py`（头）
7. PR-A-2 落点：`src/repomesh/integrations/agentteams/task_publishing.py`（全文，v1 在 `:44-92,188-238`）；`scripts/hosted-native-e2e/spike/{build_package.py,rm-work.sh,spec_construction.md.tpl,spec_review.md.tpl,config.json}`；`contracts/runtime/README.md`（加法规则的写法样板）
8. 刚落地的过滤（对照，不改）：`src/repomesh/modules/agent_runtime/api/router.py`（`next_runner_task`、`_adapter_filter`）、`runner_store.py`（`lease_next`）、`src/repomesh_runner/{runtime_env,task_source,profiles,main}.py`
9. 记忆 `dual-mode-progress-audit-20260904`、`hosted-native-wave0-spike-findings-20260903`

## 5. 坑（本会话新踩或再踩）

- 仓库根 `.env` 的 `REPOMESH_GITHUB_APP_ID=` 空串让 `Settings` 校验炸：`test_leader_lane` 5 红、`test_api` 33 红都是它；跑测试前 `REPOMESH_GITHUB_APP_ID=0`。
- CI 只跑 `uv run ruff check .`，不跑 `ruff format`；多处文件在 main 上就不 format 干净，对整文件 format 会带出无关 diff。
- `ruff` 按显示宽度算 E501，中文一字算两列。
- Markdown 表格里反引号内的 `|` 会切列，要写 `\|`（spec 里我改过的四行已转义，其他行原样）。
- Bash 工具的 heredoc 会吃反斜杠（`'\'` 直接 SyntaxError）；含转义的脚本用 Write 落盘再跑。
- Edit 工具要求先 Read；`git stash` / `stash pop` 之后已读文件全部失效要重读。
- `git add` 时的 LF→CRLF 警告无害（`core.autocrlf`），文件本身写 LF。
- Tool Guard 规则集不在 vendored 源里；`/approve` 唯一有效形状 = 正文恰好 `/approve` + `m.mentions.user_ids`；Git Bash 会把 `/approve` 改写成 `D:/Git/approve`（要 `MSYS_NO_PATHCONV=1`）。
- 一键栈 runner 镜像改了 `task_source.py` 后必须 `compose build runner`，否则旧镜像不带 `adapter` 会 400 循环。
- 交接文档 09-03 §5 prompt 里写的分支 `feat/module-test-team-v1` 已落后 main 17 提交，本会话改用 `feat/hosted-native-wave1`；别再往旧分支提。
- 记忆与 09-03 前的记录写「D-21 是末条」「fd26f09f = GitHub main」都已过期。

## 6. 给下一会话的 prompt（原样复制）

```text
上一个会话（2026-09-04）做了四件事：① 五角度只读审计双模式进度，结论是本地 CLI 模式可演示不可交付、托管原生模式
设计与波次 0 实证完毕但代码零行；② 裁决了 spec §9 六条并写回 §3（D-3/D-6/D-12/D-21 标「09-03 实证修订」，新增 D-23：
Tool Guard 归平台、第一阶段用 M2 观察器兼自动审批；帮手脚本定名 repomesh-work.sh；D-12 改三级中断信号，启动时间载体
待定列 §8.16）；③ 把 spec、目的文档、剧本、09-02/09-03 全部记录与 scripts/hosted-native-e2e 提交到新分支
feat/hosted-native-wave1 并推送（4 笔，CI ruff 本地全绿）；④ 修了共享派发队列的抢活隐患：runner-tasks/next 加 adapter
过滤，无主体 control token 不带 adapter 即 400、领不到持 worker 令牌的成员队列，runner 侧 REPOMESH_RUNNER_ADAPTERS
（compose 默认 mock），契约写在 contracts/runtime/README.md（这两笔 bdd04406、32ab4ee9 还没推）。托管原生施工代码仍零行。
结束时 Docker 引擎没在跑。

这个会话的任务（PR-A 第一刀）：
1. 推送 feat/hosted-native-wave1 上领先的两笔，看一眼分支 CI 是否绿。
2. M7 ConstructionMode：枚举 + derive_runtime()（hosted_native→(True, COPAW, SERVER)，local_cli→(False, COPAW, SERVER)）、
   RepositoryTeam.construction_mode（默认 hosted_native）、with_adopted_leader 的 LEADER 闸只在 local_cli 生效、
   TeamConstructionModeReader 与 Postgres 实现、迁移 20260904_0055_team_construction_mode.py（头是 20260902_0054，
   0053/0054 已被占用）、接团队 API 去 leader_runtime/worker_runtime 改 construction_mode、投影按团队 derive_runtime()
   （MCP 投影保留，D-18）、读模型加 construction_mode。测试：tests/integration/test_hosted_native_postgres.py 的
   alembic 红→绿→降级回环 + 受影响模块测试；只做针对性验证，不跑全量。
3. M3 任务包 v2：task_publishing.publish 加 package: PackageInputs | None（None 时行为与今天完全一致）；manifest v2 全文件
   摘要；base/package.json（含 helper_commands[]）；base/tools/repomesh-work.sh（从 scripts/hosted-native-e2e/spike/rm-work.sh
   改名，命令行里不能有 rm 片段，读 base/package.json）；construction/review 两套 spec 模板，文案要求 worker 完成后
   @admin 或不 @、不 @Leader；磁盘与 MinIO 两个适配器同步改。契约 contracts/agentteams-task/v2/ 七个文件 +
   tests/contracts/test_agentteams_task_v2_contract.py。D-21 的 Tool Guard 规则集测试要从活体 worker 镜像导出夹具，
   Docker 没起来就先留 TODO 并在交接里记。
4. 若 Docker 能起来：重建 runner 镜像（docker compose --profile platform build runner）并实走一次 mock 链，确认带
   adapter=mock 仍能领活；顺便做 §8.16 的 docker restart 对照（lastHeartbeat/containerState 序列能不能看出重启）。
5. 每完成一个模块提交一次，提交信息英文、不加 Co-Authored-By；推 GitHub 前问我。

先按顺序读这几个文件，读完再开口：
1. CONTEXT.md
2. docs/startup-records/2026-09-04-handoff-wave1-pr-a.md（§2.2 三个硬事实、§2.4 环境、§3 任务表、§5 坑）
3. docs/development/agentteams-native-execution-mode-spec-20260902.md §3（D-1…D-23，重点 D-3/D-6/D-12/D-17/D-18/D-21/D-23）、
   §4.2 M3 与 M7、§5.2、§5.3.1、§5.3.2、§8.16、§9
4. docs/development/agentteams-native-execution-mode-purpose-20260902.md §5、§7
5. docs/startup-records/2026-09-03-hosted-native-spike.md §0、§4
6. src/repomesh/modules/project/{contracts,domain,infrastructure}.py、src/repomesh/api/human_control_models.py:120-127、
   src/repomesh/api/human_control.py:224-357、src/repomesh/integrations/agentteams/runtime_projection.py:159-205,279-293、
   migrations/versions/20260830_0047_team_decomposition_mode.py 与 20260902_0054_merge_decision_embeddings_and_test_team.py
7. src/repomesh/integrations/agentteams/task_publishing.py 全文，scripts/hosted-native-e2e/spike/{build_package.py,rm-work.sh,
   spec_construction.md.tpl,spec_review.md.tpl,config.json}
8. 对照不改：src/repomesh/modules/agent_runtime/api/router.py 的 next_runner_task、runner_store.py 的 lease_next、
   contracts/runtime/README.md

约束：
- 跑任何会起 app 的测试要带 REPOMESH_GITHUB_APP_ID=0（仓库根 .env 空串），CI 只跑 ruff check 不跑 ruff format，别对整文件 format。
- 不动 src/repomesh_agent_bridge/**；src/repomesh_runner/** 只允许加法。
- Docker 起来前先看 %LOCALAPPDATA%\Docker\run\ 有无坏 socket；8 个 copaw 容器要手工 docker start。
- 不要碰我的密码、不在页面里填 API key；四个 GitHub 夹具仓不推任何东西。
- 回复用中文，代码和英文文档保持英文。
```
