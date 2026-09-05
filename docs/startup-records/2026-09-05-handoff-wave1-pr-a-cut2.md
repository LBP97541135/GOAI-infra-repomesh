# 交接：PR-A 第一刀已落地（M7 + M3），下一刀 §8.16 载体 → M1/M2（2026-09-05）

写于 2026-09-05 本机时间（UTC−7）。本会话按 `2026-09-04-handoff-wave1-pr-a.md` §6 的 prompt 开工：推送、M7、M3、活体验证。
用户中途放行「用 agent 分头做、我来验收」，M3 与活体验证各由一个 agent 执行，本文所有结论均经我复跑/复核。

术语按 `CONTEXT.md`。上一份交接：`2026-09-04-handoff-wave1-pr-a.md`。

---

## 0. 一句话

托管原生施工代码从零行到两笔：**M7 `construction_mode`（`277959b4`）与 M3 任务包 v2（`fdc42f8d`）已提交到 `feat/hosted-native-wave1`，未推送**；
T3（§8.16 启动时间载体）已按活体对照定为 (b)（见 §3）；下一刀是 T4：M1 `HostedNativeRound` + M2 观察器兼自动审批（迁移 `20260904_0056`），M2 前先定 §8.17。

## 1. 本会话做了什么

| # | 产出 | 位置 | 状态 |
|---|---|---|---|
| T0 | 推送 `bdd04406`、`32ab4ee9`、`2ac657fd` | `origin/feat/hosted-native-wave1` | 已推。**分支 CI 不存在**：`ci.yml` 只在 `pull_request` 与 push `main` 触发；要看绿得开 PR |
| 环境 | Docker 引擎起不来，`%LOCALAPPDATA%\Docker\run\` 又是 `-?????????` 坏 socket（第 8 次），按记忆配方改名 `run.broken.0905…` 重建后 20 秒就绪；8 个 copaw worker 这次**自己起来了**（没手工 `docker start`） | — | 已恢复 |
| M7 | `ConstructionMode` / `DerivedRuntime` / `derive_runtime()` / `TeamConstructionModeReader` + `PersistedTeamConstructionModeReader`；`RepositoryTeam.construction_mode`（默认 hosted_native）；`with_adopted_leader` 闸只在 local_cli；迁移 `20260904_0055`；`settings.construction_mode_default`（`.env.example` 已写）；接团队 API 去 `leader_runtime/worker_runtime` 改 `construction_mode`（None → settings）；投影按团队推导 runtime 与 `container_managed`；读模型 `list_teams` 加 `construction_mode` | 提交 `277959b4` | **未推送** |
| M7 验证 | 受影响单测 94 过；`tests/api/test_repository_team_onboarding.py` 5 过（含旧字段被忽略 / 默认取 settings / 非法值 422）；`tests/integration/test_hosted_native_postgres.py` 2 过（本机 compose Postgres 一次性库：0054 处写拓扑必 `42703`、head 处 local_cli 往返、降级再升级旧行读回 hosted_native）；周边回归 365 过（materialize、test_api、plan_execution、architecture、leader-actions、external-member、read_models）；`ruff check` 全绿；`alembic heads` 单头 | — | 通过 |
| M3 | `PackageInputs`/`PathPolicy`/`ReviewInputs` 进 `task_orchestration/contracts.py`，`publish(..., package=None)` v1 字节不变（固定 UUID 哈希钉死）；v2 布局 `teams/<team>/shared/tasks/<attempt_id>/`：spec.md（construction/review 两模板，完成通知 `@admin` 或不 @、永不 @Leader）、meta.json（`repomesh` 块仅发布时有效）、`base/package.json`（含 `helper_commands[]` 四条逐字命令行）、`base/tools/repomesh-work.sh`、`base/base.bundle` 或 `review/*`、manifest v2 全文件摘要；一次装配成字节、磁盘与 MinIO 只存取；模板与脚本为包数据 `integrations/agentteams/task_package/`；契约 `contracts/agentteams-task/v2/` 七件；Tool Guard 规则夹具从活体镜像导出 | 提交 `fdc42f8d` | **未推送** |
| M3 验证 | `test_task_publishing.py` 9 过（v1 定值、v2 两种布局、replay 不重写、冲突拒绝、磁盘/MinIO 假实现同摘要同字节、非法输入）；`test_agentteams_task_v2_contract.py` 7 过（schema 驱动的结构校验、四条命令行三处一致、无 `rm` token、spec 文案、**四条命令行与 `cd … &&` 前缀形态过 13 条规则零命中，旧 `rm-work.sh` 恰命中 `TOOL_CMD_DANGEROUS_RM`**）；`test_task_publication_translation.py`、`test_plan_loop_e2e.py`、`test_agentteams_integration.py` 一起 56 过；agent 在本机 Git Bash 用临时仓实走了 `init/test/bundle/clean` 全部分支；`uv build --wheel` 确认包数据进 wheel | — | 通过 |
| 活体验证 | runner 镜像重建 + mock 链 + §8.16 `docker restart` 对照 | 见 §3 与 `2026-09-05-live-verify-runner-and-restart.md` | 见 §3 |
| 文档 | spec §4.2 M7/M3 各加「09-05 落地」注、§5.3.1/§5.3.2/§5.3.3 标已落地、§8 新增 17/18、§8.16 与 D-12 写入 (b) 裁决 | 本次 docs 提交 | — |
| 记忆 | `hosted-native-pr-a-cut1-20260905.md` | — | 已写 |

## 2. 与 spec 的偏离（都已写回 spec，这里给结论）

1. **接团队时的模式选择没有持久化载体到拓扑行。** 拓扑行（`project.repository_agent_teams`）在 materialize 时由 `EnsureProjectAgentTopology` 才建，spec 写的「从 catalog 团队记录带过去」——这种记录不存在。
   落地：`RepositoryAgentTeamOnboard.construction_mode` 只决定创建时刻的控制器投影（runtime + `container_managed`）；拓扑行由 `CreateProjectAgentTopology(construction_mode=settings.construction_mode_default)` 写。
   后果：**本地 CLI demo 环境必须设 `REPOMESH_CONSTRUCTION_MODE_DEFAULT=local_cli`**，否则新 materialize 出的行默认 hosted_native，外部 Leader 不再被采纳进 LEADER 拆解（闸只在 local_cli 生效）。
   按仓库持久化的候选位置 = catalog 仓库行（与 `capability_profile` 同型的供给侧开关），列第二波；要的话是 0057 之外再一列。
2. 投影 `ProjectRuntimeProjection` **不注入** `TeamConstructionModeReader`，直接按已加载拓扑的 `team.construction_mode` 推导（同一读、同一源）；读取器留给投递分叉/门禁/观察器，容器已注册 `team_construction_mode_reader()`。
3. `WorkerRuntime` 经 `agent_runtime.contracts` 再导出（架构测试只准跨模块 import `contracts`）。
4. M3：`PackageInputs` 多 `workspace_root`/`test_timeout_seconds`；`package.json` 多 `schema`/`test_timeout_seconds`/`helper`（schema `additionalProperties: false`）；审阅包也带 `base/package.json` 与脚本、不带 bundle；v2 spec 无数据库变更段（§8.18）。
5. **新开放项 §8.17（M2 前必定）**：波次 0 里 worker 实际敲的是 `cd <任务目录> && bash base/tools/… init`，与 `helper_commands[]` 裸命令行不逐字相等；D-23 的自动审批要么只批裸命令行（可能一条都批不出去），要么剥掉「`cd <该尝试目录> &&`」前缀再比。倾向后者、只认该尝试自己的目录。

## 3. 活体验证（runner 镜像 / mock 链 / §8.16）

原始记录：`docs/startup-records/2026-09-05-live-verify-runner-and-restart.md`（agent 写、我复核：无凭据值，时间线与结论对得上）。

**runner 半边通，api 半边没验到。** `docker compose --profile platform build runner` 后 runner 每次轮询都带 `adapter=mock`，204/200 正常；
但一键栈里跑着的 **api 是 09-02 镜像，没有 `bdd04406` 的 adapter 过滤**（不带 `adapter` 也 204 而非 400），于是 mock runner 一上线就把 09-02 遗留的
`claude-code` 调度领走并 `binary_not_found` 失败。要验 api 半边必须 `compose build api` 并重建 api 容器——api 容器的环境还与 `compose.yaml` 漂移
（缺 `REPOMESH_WORKER_DEFAULT_ADAPTER_ID` → 默认 `claude-code`、缺 `REPOMESH_AGENTTEAMS_MANAGER_IMAGE` 等），重建会同时把这些带上，**要你放行**。

**mock 链**（issue `7bbe605f…`）：建 issue → 分析 → 候选 → 分类 → 审批 → 计划 → materialize 全通（DeepSeek，1 任务 1 批，包进 MinIO、房间通知发出）；
copaw worker 自己调 MCP 仍 401（V-1，`.env` 关了 dev 直连），worker 在房间报 BLOCKED，队长还自作主张改派成 copaw 原生任务；
用 `POST /agent-actions/start-worker-task` 显式 `adapter_id=mock` → runner 14 秒内领活 → `runner.accepted` → mock 执行 → 冻结测试命令
`python scripts/run_tests.py` 退出 1 → `runner.failed`，任务/预留/调度/计划一致落 `failed`。**执行面闭环本身是通的**；没到 `succeeded` 是因为
夹具仓 `repomesh-e2e-pricing-core@882231dd` 基线单测本身是红的（tests 要 currency、src 没实现）。

**顺带发现的缺陷**（详见记录 §4）：① api 在宿主 worktree 里写的 `.git` 指针文件是 root 0600，runner uid 10001 读不到 → `changedFiles` 恒空
（`integrations/workspace/git_worktree.py:158`），真 CLI 接入前必修；② 裸 `docker compose up` 起 runner 会 401 死循环——控制令牌只在
`.secrets/platform.env`，launcher 之外要 `set -a; . .secrets/platform.env`（与 P-3/P-4 同源）；③ 任务 `failed` 后 `task_assignment_attempts`
仍 `active`、`finished_at` 空，且恢复开关为 false 时出现了 2 行 `worker_recovery_operations`；④ 控制器里 14 个 `repo-*` Pending 的遗留 CR 每轮刷日志。

**§8.16 对照（T3，已定）**：候选 (a) 出局。控制器 REST 投影根本不输出 `lastHeartbeat`/`lastActiveAt`（236 样本全空，
`resource_handler.go:718 workerToResponse` 没这两个字段）；`docker restart` 全程 `phase=Running`；`containerState` 只在停止→再起之间露出
≤ 4.3 s 的 `stopped`（Docker 在 SIGTERM 后 ~8 s 的停止阶段仍报 running），10 s 观察器命中概率 ≤ 43%，命中也给不出「启动晚于 `notified_at`」。
`docker inspect` 同一秒就给新 `State.StartedAt`。**口径已写回 spec D-12 / §8.16：信号② 的载体 = (b) verifier 心跳附带各 worker 容器
`State.StartedAt`（PR-B）；PR-B 未到前只有①③生效；`containerState` 非 running 只作 ③ 的补充。** 便宜替代（bootstrap 已挂 docker socket，
在既有对账循环里回报 `StartedAt`）越出 D-4 边界，要单独裁决。S-6 再证：`.copaw` 配置、`shared/tasks/`、`/work` 都在，只丢会话内存。

残留未清理（记录 §5）：新 issue/计划/任务（failed）、09-02 的 `b6e0bc59…` 链变 failed、MinIO 与 worker 本地任务目录、宿主 worktree `w/f13db2c7…`、
`goai-infra-repomesh-runner-1` 仍每 30 s 轮询、`agt-worker-dfb8a4cda6f7` 重启过一次。

## 4. 当前状态

- 分支 `feat/hosted-native-wave1`：`fdc42f8d`（M3）→ `277959b4`（M7）→ `2ac657fd`（已推）…；本地领先 origin 两笔 + 本次 docs 提交。`main = origin/main = 6974698b`。
- 迁移头 `20260904_0055`；下一个 `20260904_0056_hosted_native_attempts.py`。
- 环境：Docker 引擎活着；compose 四件 + controller/manager + 8 worker + 新建的 runner 全 Up；**api 是 09-02 镜像且环境漂移，未重建**；`repomesh-demo-*`、`multica-*` 无关；`coagenthub-smoke-pg` 长期 crash-loop 他线。
- 本机 Git Bash 的 `python3` 是 Windows Store 桩，跑帮手脚本要 shim 到真 python（agent 用的 Anaconda）；目标运行时是 Linux 容器。

## 5. 任务表（接 09-04 交接 §3）

| # | 任务 | 状态 |
|---|---|---|
| T0 | 推送三笔 | **完成** |
| T1 / T1-测 | M7 | **完成** `277959b4` |
| T2 / T2-契约 | M3 + 契约 + Tool Guard 夹具 | **完成** `fdc42f8d` |
| T3 | §8.16 载体决定 | **已定 (b)**（写回 D-12/§8.16）；PR-B 前只有①③ |
| T4 | M1 `HostedNativeRound` + M2 观察器（含 D-23 自动审批；先定 §8.17 归一化）+ 迁移 0056 | 未开工 |
| T5 | M5/M6/M8、`_deliver_assignment` 分叉（用 `team_construction_mode_reader()`）、恢复分支 | 未开工 |
| T6 | PR-B verifier | 未开工，可并行 |
| T7 | PR-C 前端（`RepositoryTeamOnboardRequest` 加可选 `construction_mode`；teams 表显示 `construction_mode`） | 未开工 |
| T8 | 活体 mock 链 | runner 半边通；**api 需重建才能验过滤**（等放行）；`.git` 0600 缺陷待修 |
| T9/T10 | 本地 CLI 顺手三修 / 工作树卫生 | 未动 |
| 新 | 推送 `277959b4`、`fdc42f8d` 与 docs 提交；是否开 PR 看 CI | **等用户放行** |

## 6. 坑（本会话）

- Docker 坏 socket第 8 次复发，配方不变（记忆 `docker-desktop-socket-corruption`）；这次 worker 容器自己复活了，别假定每次都会。
- `Edit` 工具改 spec 列表项时把新项插到了旧末项前面（编号乱序），插入后要 `grep -n "^1[0-9]\. "` 看一眼。
- `ruff` E501 按显示宽度算，中文一字两列；`.env.example` 有两段重复配置（D-20 的去重还没做），本次只在第一段加了 `REPOMESH_CONSTRUCTION_MODE_DEFAULT`。
- 容器里默认 `python` 没有 copaw，运行时在 `/opt/venv/standard`；导出规则用 `/opt/venv/standard/bin/python`。
- `git add` 的 LF→CRLF 警告无害；包数据文件（模板、脚本）必须 LF——`load_helper_script()` 读 bytes、模板用 `newline=""` 读，测试断言无 CR。

## 7. 给下一会话的 prompt（原样复制）

```text
上一个会话（2026-09-05）把 PR-A 第一刀落地了：M7 construction_mode（277959b4）与 M3 任务包 v2（fdc42f8d）在
feat/hosted-native-wave1 上，都经针对性测试与 alembic 回环验证；spec §4.2 M7/M3 有「09-05 落地」注写明与原文的偏离
（接团队时的模式没有载体到拓扑行→用 REPOMESH_CONSTRUCTION_MODE_DEFAULT；投影按拓扑推导不注入读取器）；新开放项
§8.17（D-23 逐字比对 vs `cd … &&` 前缀）、§8.18（v2 spec 无数据库变更段）。活体验证与 §8.16 对照的结论在
docs/startup-records/2026-09-05-live-verify-runner-and-restart.md 与交接 §3。

这个会话的任务（PR-A 第二刀）：
1. §8.16 已定 (b)（spec D-12/§8.16 已写）；先定 §8.17 归一化规则并写回 D-23。
2. M1 HostedNativeRound + 迁移 20260904_0056_hosted_native_attempts.py（两表）+ store 的 Postgres 与内存实现；
   tests/hosted_native/test_round.py；alembic 回环照 tests/integration/test_hosted_native_postgres.py。
3. M2 SharedTaskDirectoryObserver（按目录名认领、只读 meta.json/result.md、不读 meta.repomesh）+ approval.py
   （只批与 base/package.json.helper_commands[] 相同的命令行，按 §8.17 定的归一化；先写 hosted_native_events 再回恰好
   /approve + m.mentions）；tests/hosted_native/test_observer.py。
4. 每模块一提交，英文提交信息、不加 Co-Authored-By；推 GitHub 前问我。

先读：CONTEXT.md；本交接 §2、§3、§5、§6；spec §3 D-3/D-6/D-8/D-9/D-12/D-23、§4.2 M1/M2/M3（含落地注）、§5.3.1、
§5.3.2、§8.16-18；contracts/agentteams-task/v2/README.md 与 helper-cli.md；
src/repomesh/integrations/agentteams/task_publishing.py（assemble_v2_package / store_package）；
src/repomesh/integrations/agentteams/task_package/__init__.py（HELPER_COMMANDS）；
scripts/hosted-native-e2e/spike/{auto_approve.py,watch.py}（M2 原型）；
src/repomesh/modules/agent_runtime/runner_store.py 与 integrations/runner/recovery.py（fencing 与恢复复用点）。
约束同 09-04 交接（REPOMESH_GITHUB_APP_ID=0；不 ruff format 整文件；不动 repomesh_agent_bridge；runner 只加法；
Docker 起来前看坏 socket；不碰密码不填 API key；夹具仓不推）。
```
