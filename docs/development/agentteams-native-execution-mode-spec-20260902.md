# 托管原生施工模式：施工 spec（2026-09-02）

> 日期：2026-09-02
> 状态：设计定稿草案，待用户复核后开工；波次 0 实证已于 2026-09-03 完成（零代码）；§9 六条挑战已于 2026-09-04 裁决并写回 §3——被修订的决策标「09-03 实证修订」，D-23 为裁决新增
> 基线：`docs/development/agentteams-native-execution-mode-purpose-20260902.md`（目的与边界，已含五项执行边界裁决）、`CONTEXT.md`（术语）、
> `docs/startup-records/2026-09-02-defect-summary.md`（失败证据）
> 范围：把目的文档 §12 的前五项落成可施工的方案；§12 第 6 项（主体凭据）与第 7 项（切换状态机）只留接缝，不在本 spec 施工

术语按 `CONTEXT.md`：**Manager** / **仓库团队** / **开工（materialize）** / **判据** / **最小证据集**。
设计语言按 codebase-design：**模块（module）**、**接口（interface）**、**接缝（seam）**、**适配器（adapter）**、**深度（depth）**。
前端文案按仓库既有规则：UI 写「本地 CLI」「Leader」「Worker」「物化并开工」，不写「外部成员」「队长」（`frontend/src/display.ts`）。

---

## 0. 一句话

让 AgentTeams 自带的 copaw worker 在自己的容器里真的写代码、跑测试、交候选补丁；RepoMesh 只做四件事：**打任务包、听回执、让 Leader 先审、让独立复验器再验**，
验证通过后把候选提交塞回现有交付链，一行交付代码不改。

---

## 1. 为什么改，改成什么

### 1.1 现状与失败证据

2026-09-02 从零起平台并用四个夹具仓走 issue→开工→房间→提交链（`docs/startup-records/2026-09-02-issue-to-commit-chain.md`）。
编制造出来了（4 支仓库团队、8 个 copaw 容器、8 间房、4 个任务、任务包进了共享盘），但**没有任何进程真正施工**：

| 证据 | 位置 | 含义 |
|---|---|---|
| copaw worker 调 `start_assigned_task` 12 次全 401 | 记录 §5 V-1 | 控制器给 worker 的 Bearer 是 Higress consumer key，平台不认；CRD `MCPServer` 无自定义 header（`components/agentteams/agentteams-controller/api/v1beta1/types.go:86-101`） |
| dispatch 永远 `queued`，30 分钟 `runner-tasks/next` 零次 | 记录 §6 V-5 | 产品部署里没有 runner；活体控制器二进制含 `repomesh-runner` 字符串 0 次，即上游镜像根本不认这个运行时。**09-04 更新**：`b38549a0`（09-03）已把 runner 作为 sidecar 服务放进 compose platform profile（`compose.yaml:167`），用全局 control token 领活——控制器仍不认 `repomesh-runner` 运行时，D-2 不变；但「无主体消费者今天不存在」的前提已变，见 §6 `runner-tasks/next` 行 |
| worker 自己交了 `STATUS: BLOCKED` 的 `result.md` | 记录 §5 | **AgentTeams 原生任务协议是通的**：worker 领了包、读了 spec、按协议交了回执、@ 了 Leader；缺的只是代码和一份对得上的 spec |
| Leader 绕圈三分钟 | 记录 §5 V-3 | 服务端拆解下 Leader 无包无仓，平台没告诉它不必动 |

结论（目的文档 §3）：旧设计是「房间里的 agent 调平台 → 平台排 runner dispatch → 第二个 coding CLI 施工」的两段式转交，
两个运行实体、两套身份、两套就绪；重设计不是修转交，是**取消第二施工者**。

### 1.2 目的

目的文档 §1、§10 的口径不重复。本 spec 的可验证目标只有一条：

> 在现在活着的环境上，新建一条 issue，选择托管原生模式开工，pricing-core 的 copaw worker 在容器内改完 `quote()` 并跑绿测试，
> Leader 给出 `ACCEPT`，复验器在一次性容器里复验通过，issue 页看到候选提交，`delivery` 读模型看到候选分支就绪。不推 GitHub。

### 1.3 不做什么

- 不动 `src/repomesh_runner/**`、`src/repomesh_agent_bridge/**`（本地 CLI 模式原样保留，ADR 0004 红线）。
- 不做 worker 主体凭据与直连回调（目的文档 §12.6，第二阶段）。
- 不做模式切换状态机的持久实现（§12.7），只落模式字段与创建时选择。
- 不做按团队选工具链镜像（留列、留接缝）。
- 不修改任何冻结契约文件的既有字段；Runtime v1 只做可选字段加法。

---

## 2. 必读文件

按顺序。读前七行就能讨论方案；读完全部才能开工。

| # | 文件 | 读什么 |
|---|---|---|
| 1 | `docs/development/agentteams-native-execution-mode-purpose-20260902.md` | 目的、不变量（§5 共 11 条）、概念执行链（§6）、工作区边界（§7）、复验位置（§7.1） |
| 2 | `docs/startup-records/2026-09-02-defect-summary.md` 与 `-issue-to-commit-chain.md` §5–§7 | 失败证据；`-issue-to-commit-chain.rooms.md` 看 copaw worker 与 Leader 在房里怎么推理 |
| 3 | `components/agentteams/manager/agent/copaw-worker-agent/skills/task-management/SKILL.md`、`file-sharing/SKILL.md` | worker 侧原生协议：任务目录布局、`ack_task` / `submit_task`、四种状态、`base/` 与 `workspace/` 的归属 |
| 4 | `components/agentteams/copaw/src/copaw_worker/task.py:553-577`、`hooks/tools/taskflow.py:299-340`、`sync.py:1-20,258-266` | ack 幂等、submit 覆盖、身份校验只比 `assigned_to`、后台同步根是 `~/.copaw-worker/<worker>/` |
| 5 | `src/repomesh/integrations/agentteams/task_publishing.py` | 现有任务包发布器：`publish()` 接口、对象键、manifest v1、摘要与冲突检查（`:76-80`） |
| 6 | `src/repomesh/modules/task_orchestration/application.py:377-589,872-964,1172-1300,1348-1412,1483-1530` | 分配、投递、服务端拆解、批次推进、Leader 任务自动结算（`_roll_up`） |
| 7 | `src/repomesh/integrations/runner/gateway.py:96-149`、`src/repomesh/integrations/scm/plan_delivery.py:279-374`、`src/repomesh/integrations/scm/git_branch.py:55-58` | 终态事件→证据→批次推进→交付；交付要求「本地工作树 HEAD == commit_sha」 |
| 8 | `src/repomesh/modules/task_orchestration/assignment.py:39-127,148-195,233-260`、`src/repomesh/modules/agent_runtime/execution_reservation.py:34-73,200-240,308`、`src/repomesh/modules/agent_runtime/runner_store.py:32-73,251-291,336` | 分配尝试/代次、执行预留/租约、事件收件箱与 fencing |
| 9 | `src/repomesh/integrations/runner/worker_execution.py:93-290` | 旧路径的副作用顺序，新的「开一次尝试」以它为对照 |
| 10 | `src/repomesh/bootstrap/app.py:234-251,592-727`、`src/repomesh/bootstrap/container.py:548-549,627,683,2306-2325` | 发布器/MinIO 选择、恢复循环、运行时投影装配、就绪门禁装配 |
| 11 | `src/repomesh/integrations/agentteams/runtime_projection.py:135-205,279-293,355-444`、`src/repomesh/modules/project/domain.py:162-281`、`infrastructure.py:138-197,591-625` | 投影为什么是全局运行时；团队表与 `decomposition_mode` 的落列样板 |
| 12 | `src/repomesh/modules/agent_runtime/application/readiness.py:194-300,486-560`、`src/repomesh/modules/repository_intelligence/application/discovery_materialization.py:280-370`、`api/discovery_chain.py:530-538,677` | 就绪租约、门禁调用点、409 形状 |
| 13 | `contracts/runtime/v1/*.json`、`task-and-result-reference.md:168-211`、`contracts/runtime/README.md:31-35` | Runtime v1 的加法规则；终态 payload 里已有 `commitSha`（引擎 `src/repomesh_runner/engine.py:68-85`） |
| 14 | `compose.yaml`、`Dockerfile.bootstrap`、`src/repomesh/integrations/bootstrap/executor.py:100-190`、`src/repomesh/modules/platform_config/runtime_config.py` | 复验器服务的镜像样板、docker CLI 用法、运行时配置装载 |
| 15 | `frontend/src/components/MaterializeModal.tsx`、`DiscoveryPanel.tsx:988-1056`、`ProvisionTeamModal.tsx`、`PlanDagPanel.tsx`、`viewmodel.ts:175-210`、`api/contract.ts:1700-1760`、`api/discovery.ts:112-145` | 前端落点 |
| 16 | `docs/adr/0002-first-party-agentteams-runtime.md`、`docs/adr/0004-room-native-agent-bridge.md` | 跨进程契约必须进 `contracts/`；Bridge 红线 |

---

## 3. 决策记录

每条：决策 / 为什么 / 证据。编号 D-n 供后文引用。标「09-03 实证修订」的决策于 2026-09-04 按 §9 的裁决改过正文（修订前原文与挑战见 §9 表）；D-23 起为裁决新增。

| # | 决策 | 为什么 | 证据 |
|---|---|---|---|
| D-1 | 两种施工模式正式并列：`hosted_native`（默认）与 `local_cli` | 产品初衷即两种；本地 CLI 已验收到 PR（R6、W4） | 目的文档 §2、§4 |
| D-2 | 托管原生下 copaw worker 是唯一施工者，容器内不启动任何 coding CLI，不用 runner dispatch；成立的前提是 worker 的 shell 无需人工逐条审批，该前提由 D-23 保证 | 取消第二施工者；上游控制器不认 `repomesh-runner` 运行时，fork 镜像无发布链；实证 S-1 证明 copaw + DeepSeek 能独立做完任务，卡的只是 Tool Guard 审批 | 活体二进制 `grep -c repomesh-runner` = 0；`docs/startup-records/2026-09-02-defect-summary.md` §2.2；`2026-09-03-hosted-native-spike.md` §0、§4 S-1 |
| D-3（09-03 实证修订） | Leader 保留为候选结果第一道审阅者，只返回 `ACCEPT` / `REVISION` / `BLOCKED`；审阅通过**派给 Leader 的原生任务**完成，结论用 `submit_task` 的状态表达。**Leader 只在自己的 Leader 房收审阅包；worker 的完成通知不 @Leader**：派单 spec 要求 worker 完成后在团队房 `@admin`（平台发信身份）报告或不 @；平台以共享盘 `result.md` 为事件源（D-6），房间文字只给人看 | taskflow 的 `ack_task` / `submit_task` 不限角色，只有 `delegate_task` 限 Leader；四种原生状态一一映射三种结论；不发明新协议。实证 S-4：worker 在团队房 `@Leader TASK_COMPLETED` 两次把 Leader 拖进身份混淆（「the worker seems to be me」），Leader 在自己房收结构化审阅包则 70 秒 ACCEPT | `copaw_worker/hooks/tools/taskflow.py:258-263,299-340`；`task.py:553-577`；`2026-09-03-hosted-native-spike.md` §4 S-4 |
| D-4 | 新增独立 compose 服务 `verifier`，持 docker socket，按候选结果拉起一次性验证容器；api 与 bootstrap 不跑测试 | api 容器只有 git+python，跑不了 node 仓；bootstrap 只保障服务存在 | `Dockerfile:14-16`；目的文档 §7.1 |
| D-5 | 施工工作区固定在容器本地 `/work/<attempt_id>`；共享任务目录只放控制元数据、`candidate/`、`evidence.json`、BLOCKED 原因 | copaw 后台推送根是 `~/.copaw-worker/<worker>/`；`submit_task` 会把整个任务目录推上去，只排除 `spec.md` 与 `base/` | `sync.py:1-20,263-266`；`taskflow.py:310,329` |
| D-6（09-03 实证修订） | 第一阶段以原生 `ack_task` / `submit_task` 为事件源，由观察适配器幂等摄取；`acknowledged_at` 只是领取事实，不是租约。**观察器按目录名认领**：任务目录名 = `attempt_id`，观察器只对自己库里 `hosted_native_attempts` 有行的目录读 `meta.json` / `result.md`，**不读 `meta.json.repomesh`**；平台控制数据放 `base/package.json`（`base/` 不被 worker 重推，S-9），`meta.json.repomesh` 只在发布时刻有效，仅供人看与发布时的冲突检查 | 不把 Skill、主体凭据、mcporter 鉴权放上首条关键路径。实证 S-3：copaw `ack_task` / `submit_task` 用 `TaskMeta` 原生字段重写 `meta.json`，`repomesh` 块整个丢失，后台推送也不会带回 | 目的文档 §6 末三段；`copaw_worker/task.py:145-170`；`2026-09-03-hosted-native-spike.md` §4 S-3、S-9 |
| D-7 | 不变量：模型访问由 AgentTeams 网关治理，任务包、共享目录、工作区、结果包一律不得含模型密钥 | 现状即每 worker 一把网关 key；防止「换强模型」把密钥塞进包 | 活体 worker env `AGENTTEAMS_WORKER_GATEWAY_KEY`；目的文档 §5.11 |
| D-8 | **一次尝试 = 一个原生任务目录**，目录名即尝试 id，永不复用；同一 Task 的第二次尝试是新目录 | `submit_task` 覆盖式写 `result.md`，共用目录无法 fencing；`ack_task` 幂等所以重发通知无害但无意义 | `task.py:566-577` |
| D-9 | fencing 复用既有 `task_assignment_attempts.generation` 与 `worker_execution_reservations.{id,version,lease}`；只新增一张 `hosted_native_attempts` 存 worker 侧阶段 | 事件收件箱已按这四个 id 拒绝错代次事件；过期租约回收循环已存在，不造第二套 | `runner_store.py:336`；`bootstrap/app.py:698`；`assignment.py:64` 唯一活跃约束 |
| D-10 | **验证调度就是一条 `runner_dispatches` 行**，`adapterId = "repomesh-verifier"`；复验器用 control token 轮询 `runner-tasks/next?adapter=repomesh-verifier`，结果以 Runtime v1 事件回投 | 删除测试：另起一套验证流水线要重做收件箱、证据、终态迁移、批次推进；全部是 v1 可选加法 | `gateway.py:96-149`；`runner_store.py:251-291`；`contracts/runtime/README.md:31-35` |
| D-11 | 候选工作树由 api 在 Leader `ACCEPT` 时物化：把 `candidate.bundle` fetch 进镜像仓、`git worktree add --detach <sha>`，路径写进验证调度的 `workspace.path` | 交付终结器要「磁盘上 HEAD == commit_sha 的工作树」+ `base_sha` + `test_results`；api 已拥有镜像仓与工作树管理，复验器只管测试 | `plan_delivery.py:317-374`；`git_branch.py:58`；`integrations/workspace/git_worktree.py:32` |
| D-12（09-03 实证修订） | 尝试预算 = 执行预留的租约长度（默认 2700 s）；到期走既有 `WorkerRecoveryReconciler` → `WorkerRecoveryCoordinator.decide`，`max_execution_attempts`（默认 3）即尝试上限。**中断信号分三级**：① 预算到期是唯一保证生效的兜底；② **worker 进程重启即中断**——以 worker 进程/容器的启动时间晚于尝试 `notified_at` 为准（`reason="worker_restarted"`），旧尝试封存、开新代次；③ 控制器 `phase != Running` 或 `containerState` 非 running 作补充信号（`reason="worker_not_running"`）。房间里的「等待审批」文字与 `phase` 都不能单独当中断依据。信号②的启动时间载体见 §8.16（**09-05 对照后定为 (b) verifier 心跳附带容器 `State.StartedAt`**，PR-B 落地前只有①③生效） | 不造第二个计时器；重试/改派/升级三种决策已有。实证 S-6：`docker restart` 7 秒完成，控制器 `phase` 大概率一直 Running，工作区与未提交改动保留，只有会话内存（含待审批）丢失，worker 不自发续做——真正的信号是进程重启，不是 `phase`；S-5：DeepSeek 会输出仿冒的等待文字 | `bootstrap/app.py:698-727`；`integrations/runner/recovery.py:31-59`；`settings.py:89-95`；`agentteams-controller/api/v1beta1/types.go:371-383`（`WorkerStatus` 无 `startedAt`）；`2026-09-03-hosted-native-spike.md` §4 S-5、S-6 |
| D-13 | Leader 审阅预算 900 s；超时不跳过，任务 blocked 并开人工检查点；Leader 容器不在 Running 进开工门禁 | `ACCEPT` 是进入复验的前置（目的文档 §7.1）；Leader BLOCKED/FAILED 已有升级路径 | `task_orchestration/application.py:706` |
| D-14 | 越界修改由复验器判定：变更路径不在 `allowedPaths` 或命中 `deniedPaths` → `runner.failed`，`blockers=["changed_path_denied: <path>"]`，计一次尝试 | worker 在容器内无法被技术约束；路径策略沿用任务投影已有来源 | `integrations/runner/task_projection.py:100-120,185-187` |
| D-15 | 第一版一个默认工具链镜像（git + python3 + node）；`repositories.toolchain` 列与按团队 worker `image` 留第二波 | 三个夹具仓只需 python/node；Worker CRD 已有 `image` 字段可后接 | `types.go:178` |
| D-16 | 第一阶段不要求组织 Manager 容器运行，门禁不查它；V-2 端口冲突不修 | 09-02 整条链未用到它；Manager = RepoMesh 组织 leader 记录 + 人 | `2026-09-02-issue-to-commit-chain.md` §5 V-2 |
| D-17 | 团队表加 `construction_mode` 列；由它推导 `container_managed` / worker runtime / 拆解模式默认值；`RepositoryAgentTeamOnboard` 去掉 `leader_runtime` / `worker_runtime` | 两处默认值打架（请求模型默认 `repomesh-runner`，settings 默认 `copaw`）；前端本就不发运行时字段 | `human_control_models.py:124-125`；`settings.py:35-36`；`frontend/src/api/humanControl.ts:48` |
| D-18 | 第一阶段**保留** `repomesh-task-control` 的 MCP 投影，但任务包与房间消息不再让 worker 调它；投影的移除与直连回调一起放第二阶段 | 控制器逐字段比对 `mcpServers`，改投影会让既有 worker 的再注册 409 | `principal_registration.py:36-45` 文档串 |
| D-19 | 就绪三态 `execution_plane ∈ {missing, wired, ready}` 进 `setup/status` 与开工门禁；托管原生 ready = 团队 worker 与 Leader `phase == Running` + 复验器心跳有效；本地 CLI ready = 成员租约有效。`/health/ready` **保持进程存活语义，不改成 degraded 503** | 09-02 假绿：`/health/ready` 在 `REQUIRED=false` 下 200。但 compose 里 `web` 依赖 `api` healthy，而 api 的 healthcheck 就是 `/health/ready`，一改 setup-plane 模式 web 起不来；所以真相只放 `setup/status`，README 与脚本统一到这一个口径 | `api/health.py:18`；`container.py:742-747`；`compose.yaml:100-110,126-128` |
| D-20 | 启动修复随本 spec 一起收口：`external: true` 改显式 `name:`、写完运行时配置重启 api 与 verifier、`start.ps1` EAP、`start-platform.ps1` 管道 bug、`.env.example` 去重并改默认 | 一条命令到底是波次 3 的完成标志 | `2026-09-02-from-zero-windows.md` §10 |
| D-21（09-03 实证修订） | 任务包 v2、候选结果布局、帮手脚本命令行、审阅结论映射写成新契约 `contracts/agentteams-task/v2/`。**帮手脚本定名 `repomesh-work.sh`**（命令行 `bash base/tools/repomesh-work.sh init\|test\|bundle\|clean`）；脚本名与四条命令行都不得含 `rm`、`sudo`、`curl … \| sh` 之类会命中 copaw Tool Guard 的片段。契约测试把四条完整命令行跑一遍 Tool Guard 规则集，任何一条命中即失败——规则集在 copaw 运行时包 `copaw.security.tool_guard` 里，vendored 源码只有 `ToolGuardConfig` 结构，所以先从活体 worker 镜像导出规则夹具固定进 `tests/contracts/`。控制数据文件 `base/package.json` 一并进契约（`package.schema.json`） | 它是 RepoMesh 与 copaw worker 之间的跨进程数据模型，按 ADR 0002 归 `contracts/`。实证 S-1：`rm-work.sh` 名字里的 `rm` 命中 `TOOL_CMD_DANGEROUS_RM`，三次尝试 8 次 shell 全被拦；S-8：脚本随包分发、spec 里写清命令就够，不需要技能安装 | `docs/adr/0002-first-party-agentteams-runtime.md` Ownership 表；`components/agentteams/copaw/src/matrix/config.py:1083-1095`；`copaw_worker/hooks/credential_guard.py:140`（规则模型来自 `copaw.security.tool_guard`）；`2026-09-03-hosted-native-spike.md` §4 S-1、S-8 |
| D-22 | 波次 4：启动逻辑归容器。`.env` 已有模型密钥且控制器缺失时，api 启动即 `ensure_requested` 一个 bootstrap 操作，装 AgentTeams、取控制器与 Matrix token、取 MinIO 账密、写运行时配置、重启 api 全部走容器内既有逻辑；宿主脚本缩成「生成三枚 token → `compose up` → 等 `setup/status`」 | 同一套启动逻辑在 bash 与 PowerShell 各写一遍是 #1 #3 #4 #7 的共同根因；容器侧逻辑在「网页填密钥」路径上已完整存在且 09-02 证明可跑，缺的只是一条不经网页的触发 | `integrations/bootstrap/executor.py:100-190`；`modules/platform_config/bootstrap_store.py:63-88` |
| D-23（09-04 裁决新增） | **worker 的 Tool Guard 策略由平台负责；第一阶段的实现是「观察器兼自动审批」**。M2 `SharedTaskDirectoryObserver` 同时以平台发信身份（`@admin`）监听团队房；对**它自己开的尝试**（目录名 = `attempt_id`，D-6）里该 worker 发出的 Tool Guard 审批请求，仅当被拦的命令行**按 §8.17 归一化（只剥恰好一个「`cd <该尝试自己的目录> && `」前缀，目录须以 `shared/tasks/<attempt_id>` 结尾）后**与该尝试 `base/package.json.helper_commands[]` 中某一条**逐字相同**时，回复正文恰好 `/approve` 且 `m.mentions.user_ids` 带该 worker（唯一有效形状，§8.10）；其他任何命令（串接、分号、管道、重定向、别的目录、多余参数）一律不自动批，留给人。每次自动审批先写 `hosted_native_events(kind=auto_approved, marker=<审批请求 event_id>)` 再发，重复请求由唯一约束去重，进任务审计。**不改 worker 镜像、不改 `.copaw/config.json`**（§5.4.4 不变）。配置下发（`security.tool_guard.{disabled_rules,guarded_tools}`）放第二阶段，随主体凭据一起走控制器改动。AC-02 补「三条帮手命令无需人工审批跑完」 | 2026-09-04 代码审读：控制器 Go 侧 `WorkerSpec` 与 `agentconfig/generator.go` 没有任何 `security` / `tool_guard` 字段；copaw `bridge.py:226-306 _write_config_json` 生成 `.copaw/config.json` 时只写 `channels`；交接文档点名的 `credential_guard.py:21 apply_credential_guard` 在 vendored 源里无调用者，且只设 `enabled=True`——**配置下发要改控制器才成立**，而 fork 镜像无发布链（D-2）；MinIO 旁路直写 `agents/<worker>/.copaw/config.json` 要 worker 重启且未验证。自动审批只批平台自己下发的命令行、不放宽任何规则，worker 仍无权绕过判据与路径（目的文档 §5、D-14） | `agentteams-controller/api/v1beta1/types.go:174-256`；`agentteams-controller/internal/agentconfig/generator.go`；`copaw/src/copaw_worker/bridge.py:226-306`；`copaw/src/copaw_worker/hooks/credential_guard.py:21,81`；`copaw/src/matrix/channel.py:1146-1240,1920-2010`；原型 `scripts/hosted-native-e2e/spike/auto_approve.py:71-103`；`2026-09-03-hosted-native-spike.md` §4 S-1、S-2 |

---

## 4. 架构：深模块与接缝

### 4.1 总图

```text
                    ┌───────────────────────── RepoMesh api ─────────────────────────┐
issue 开工 ──▶ AdvanceExecutionPlan ──▶ TaskOrchestrator._deliver_assignment ──▶ HostedNativeRound.open()
                                                                                    │  ① 分配尝试 / 执行预留(租约=预算)
                                                                                    │  ② 任务包 v2 (spec, meta, base/, tools/)
                                                                                    │  ③ 团队房 @worker
                                        SharedTaskDirectoryObserver ─ observe() ◀───┤
                                          │ 10s 轮询 meta.json / result.md          │
                                          │ 收件箱 hosted_native_events              │
                                          ▼                                          │
                                   HostedNativeRound.observe()                       │
                                     acknowledged → 记领取                            │
                                     submitted(SUCCESS) → 派审阅任务给 Leader          │
                                     submitted(BLOCKED/REVISION_NEEDED) → 任务 blocked │
                                     review(ACCEPT) → ④ CandidateWorktreeMaterializer │
                                                     ⑤ enqueue runner_dispatch(adapter=repomesh-verifier)
                                     review(REVISION) → 封存 + 新代次 open()           │
                                                                                    │
   AgentTeams 共享盘 ◀── task_publishing (v2) ──────────────────────────────────────┘
        teams/<team>/shared/tasks/<attempt_id>/{spec.md, meta.json, manifest.json, base/package.json, base/base.bundle, base/tools/repomesh-work.sh}
        ...................................................../candidate/{candidate.bundle, candidate.diff, changes.json, evidence.json}
        ...................................................../result.md              ◀── worker submit_task
        teams/<team>/shared/tasks/<review_id>/{spec.md(审阅包), meta.json, result.md} ◀── leader submit_task
                    ▲
   copaw worker 容器 │ ack_task → /work/<attempt_id> 施工 → repomesh-work.sh test|bundle → submit_task
                     └──────────────────────────────────────────────────────────────

   verifier 服务 ── GET runner-tasks/next?adapter=repomesh-verifier ──▶ api
       │ docker run <toolchain image> -v scratch:/verify  (base.bundle + candidate.bundle + verify.sh)
       │ 父提交 == base ? 路径策略 ? testCommands 退出码
       └─ POST runner-events: accepted, test_completed×n, completed|failed {commitSha, baseSha, changedFiles, testResults, artifacts[git-bundle]}
                                                     │
                                  RunnerControlGateway._write_back (不改) → 证据 → AdvanceExecutionPlan → PlanDeliveryFinalizer (不改)
```

### 4.2 模块清单

每个模块给：接口、藏在后面的东西、依赖类别（DEEPENING 的四类）、适配器。**只在至少两个适配器成立的地方开接缝。**

#### M1 `HostedNativeRound` — 一次托管原生尝试的全部业务

位置：`src/repomesh/integrations/hosted_native/round.py`（与 `integrations/runner/worker_execution.py` 同层：跨模块的应用服务）。

接口（三个入口）：

```python
class HostedNativeRound:
    async def open(self, task_id: UUID, *, idempotency_key: str) -> RoundOpened
    async def observe(self, event: SharedTaskEvent) -> RoundTransition
    async def expire(self, attempt_id: UUID, *, reason: str) -> RoundTransition
```

- `open`：幂等（`task_id + generation`）。依次：`assignments.ensure_initial|reassign` → `reservations.reserve(lease=预算)` → `bind_execution` → `TaskExecutionState.start` → 打包 v2 并发布 → 团队房 @worker → 落 `hosted_native_attempts(phase=notified)`。任一步失败：任务 `block`，不留半截尝试（对照 `worker_execution.py:248`）。
- `observe`：输入是观察适配器已去重的事件。按 `attempt_id` 取行；代次与当前活跃尝试不符 → 记 `fenced` 事件、返回 `Ignored`，**绝不推进任务**。
  - `acknowledged` → `phase=acknowledged`。
  - `submitted(SUCCESS|SUCCESS_WITH_NOTES)` → 校验 `candidate/` 四件齐全且 `evidence.json` 的 `attempt_id` 一致 → `phase=review_pending` → 发布审阅包（M3，`kind=review`）到 Leader，Leader 房 @leader，记 `review_task_dir` 与审阅预算。
  - `submitted(BLOCKED)` → `TaskExecutionState.block(reason)` → 交给恢复决策（D-12）。
  - `submitted(REVISION_NEEDED)` → 任务 blocked，`ProjectCheckpoint.EXCEPTION_ESCALATION`（需澄清，不自动重派）。
  - `review(ACCEPT)` → M5 物化候选工作树 → 构造验证调度（M4 的输入）→ `RunnerControlGateway.enqueue` → `phase=verifying`。
  - `review(REVISION)` → 封存本尝试（`fence_reason=leader_revision`）→ `open()` 下一代次，spec 附 Leader 理由。
  - `review(BLOCKED)` → 任务 blocked + 人工检查点。
- `expire`：预算到期或 worker `phase != Running`（由恢复循环判定后调用）→ 封存 + 交恢复决策；复验器终态由既有网关路径处理，不经此处。

藏在后面：包内容、房间文案、代次与租约的耦合、四件候选文件的校验、审阅包生成、验证调度投影。调用方（`TaskOrchestrator`、观察适配器、恢复处理器）只知道三个动词。

依赖与类别：

| 依赖 | 类别 | 适配器 |
|---|---|---|
| `TaskAssignmentStore`、`WorkerExecutionReservationStore`、`HostedNativeAttemptStore` | 本地可替代（Postgres / 内存） | 既有内存实现 + 新增内存实现 |
| `TaskAssignmentPublisher` | 远程自有 | 既有 MinIO 与磁盘两个适配器 |
| `CollaborationGateway`（房间消息） | 远程自有 | 既有 Matrix 适配器 + 测试内存适配器 |
| `WorkerBindingReader`（控制器 phase） | 真外部 | 既有 HTTP 适配器 + `tests/integrations/agentteams/fakes.py` |
| `CandidateWorktreeMaterializer`（M5） | 本地 | git 真实现 + 测试用临时仓 |
| `RunnerControlGateway.enqueue` | 进程内 | 既有 |

#### M2 `SharedTaskDirectoryObserver` — 把共享盘的文件变化变成幂等事件

位置：`src/repomesh/integrations/hosted_native/observer.py`，后台服务，形态同 `WorkerRecoveryReconciler`。

接口：`run_once() -> ObserverReport`（供测试与循环），构造入参 `interval_seconds`。

端口 `SharedTaskDirectoryReader`（**这是本 spec 唯一新开的外部接缝**，第二阶段的直连回调是它的第二个生产者）：

```python
class SharedTaskDirectoryReader(Protocol):
    async def read(self, team_name: str, task_dir: str, name: str) -> bytes | None
    async def stat(self, team_name: str, task_dir: str, name: str) -> ObjectStat | None   # size, etag, last_modified
```

适配器：`MinioSharedTaskDirectoryReader`（与发布器同一 `Minio` 客户端构造，`app.py:242-247` 同条件选择）、`DiskSharedTaskDirectoryReader`（`agentteams_storage_root`）、内存实现（测试）。

行为：对 `hosted_native_attempts` 中非终态尝试，读 `meta.json`、`result.md`；对审阅目录同样。生成事件 `SharedTaskEvent(attempt_id, kind, marker, payload)`，
`marker = meta.acknowledged_at | meta.submitted_at | result.md 的 etag`。写 `hosted_native_events`（`UNIQUE(attempt_id, kind, marker)`，样板 `delivery.scm_observations`），
只有插入成功才调 `round.observe`。房间里 worker 的 @ 不是事件源，只用于把下一次轮询提前（可选，第一版不做）。

认领按目录名（D-6）：目录名就是 `attempt_id`，观察器只处理自己库里有行的目录，不读 `meta.json.repomesh`。

自动审批（D-23）：观察器同时以平台发信身份订阅每个有非终态尝试的团队房；对该尝试 worker 发出的 Tool Guard 审批请求，仅当被拦命令行与该尝试
`base/package.json.helper_commands[]` 中某一条逐字相同时，先写 `hosted_native_events(kind=auto_approved, marker=<审批请求 event_id>)`，插入成功再回复
正文恰好 `/approve` 且 `m.mentions.user_ids` 带该 worker；命令行不同、尝试不属于自己、或尝试已终态的请求一律不动。房间订阅复用既有 `CollaborationGateway` 的 Matrix 适配器与内存适配器，不新开接缝。

#### M3 任务包 v2 — `task_publishing.py` 深化

不开新接缝，深化既有 `TaskAssignmentPublisher.publish()`：

```python
async def publish(self, task: TaskView, *, team_name, room_id, assignee_resource_name, idempotency_key,
                  package: PackageInputs | None = None) -> PublishedTaskPackage
```

`PackageInputs(kind: "construction"|"review", attempt_id, generation, budget_seconds, base_bundle: bytes | None,
helper_script: bytes, policy: PathPolicy, test_commands, review: ReviewInputs | None)`。
`package=None` 时行为与今天完全一致（v1 manifest），本地 CLI 模式不受影响。

写出的对象（`teams/<team>/shared/tasks/<attempt_id>/`）：

| 文件 | 内容 | 谁读 |
|---|---|---|
| `spec.md` | 任务描述 + 判据 + 三条命令（`bash base/tools/repomesh-work.sh init\|test\|bundle`）+ 交付物路径 + 完成通知规则（在团队房 `@admin` 或不 @，**不 @Leader**，D-3） | worker |
| `meta.json` | 原生字段（`task_id=attempt_id`, `assigned_to`, `room_id`, `status`）+ `repomesh.{kind, task_id, attempt_id, generation, budget_seconds, base_sha, repository_id, organization_id}`；`repomesh` 块只在发布时刻有效，worker `ack`/`submit` 后会被 copaw 重写丢失（S-3） | worker；平台只在发布时写，观察器不读它（D-6） |
| `base/package.json` | 平台控制数据：`kind, task_id, attempt_id, generation, budget_seconds, base_sha, repository_id, organization_id, test_commands[], allowed_paths[], denied_paths[], workspace_root, helper_commands[]`；`base/` 不被 worker 重推，全程不变 | 帮手脚本（`init`/`test` 读）、观察器（自动审批比对 `helper_commands`，D-23）、复验器（路径策略与测试命令） |
| `manifest.json` | `schema: repomesh.agentteams-task.v2`，`files[]` 列全部文件，`content_hash` 覆盖全部文件 | 冲突检查 |
| `base/base.bundle` | `git bundle` 钉 `base_sha`（由 M6 从镜像仓生成，带 `HEAD` 与分支两个 ref，S-10） | worker 的 `init` |
| `base/tools/repomesh-work.sh` | 帮手脚本（版本随包；名字与命令行不得命中 Tool Guard，D-21） | worker |

审阅包（`kind=review`）：`spec.md` 内嵌 `candidate.diff`、`changes.json`、`evidence.json` 摘要、冻结判据与允许路径，
要求用 `submit_task` 回：`SUCCESS`/`SUCCESS_WITH_NOTES` = ACCEPT，`REVISION_NEEDED` = REVISION，`BLOCKED` = BLOCKED，`summary` 首行 `VERDICT: <...>`。
`meta.json.assigned_to` = Leader 的 Matrix localpart。

`_digest` 改为覆盖所有文件的有序拼接；`:76-80` 的冲突检查改比 v2 摘要。两个适配器（磁盘、MinIO）同步改。

**09-05 落地（提交 `fdc42f8d`）**，与上文的差异：
① `PackageInputs` / `PathPolicy` / `ReviewInputs` 定义在 `task_orchestration/contracts.py`（发布器端口所在模块），
`PackageInputs` 多带 `workspace_root="/work"`、`test_timeout_seconds=600`，`base/package.json` 相应多 `schema`、`test_timeout_seconds`、`helper` 三个键（`package.schema.json` 是 `additionalProperties: false` 的全集）；
② 包在 `task_publishing.py` 里**一次装配成有序字节**（`assemble_v1_package` / `assemble_v2_package`），磁盘与 MinIO 适配器只做 read/write，两通道字节与摘要逐文件相同；
v1 磁盘通道照旧做「已存在即比摘要」，v1 MinIO 通道照旧覆盖写（今天如此），只有 v2 尝试目录在两个通道都做 fencing；
③ 模板与帮手脚本作为包数据放 `integrations/agentteams/task_package/`（`pyproject` `package-data`），四条命令行的唯一定义处是该包的 `HELPER_COMMANDS`；
④ 审阅包也带 `base/package.json` 与帮手脚本（schema 统一），不带 bundle；`review_of` 只在 `meta.repomesh`；
⑤ v2 spec **没有** v1 的「Database change requirements」段（见 §8.18）。

#### M4 `repomesh_verifier` — 独立进程

位置：`src/repomesh_verifier/`（与 runner/bridge/launcher 平级），`pyproject.toml` 加 `repomesh-verifier = "repomesh_verifier.main:run"`。

接口对外只有配置（环境变量）与两个 HTTP 交互点：轮询 `GET /api/v1/runtime/runner-tasks/next?adapter=repomesh-verifier`、回投 `POST /api/v1/runtime/runner-events`、心跳 `POST /api/v1/runtime/v1/verifier/heartbeat`。

内部端口 `VerificationExecutor`（**第二个新接缝**，两个适配器都必要）：

```python
class VerificationExecutor(Protocol):
    async def run(self, spec: VerificationSpec) -> VerificationOutcome
```

`VerificationSpec(run_id, toolchain_image, base_bundle: Path, candidate_bundle: Path, base_sha, expected_head_sha, allowed_paths, denied_paths, test_commands, timeout_seconds)`。
`VerificationOutcome(parent_ok, path_violations, changed_files, test_results[{command, exit_code, tail}], head_sha, duration)`。

- `DockerCliVerificationExecutor`：`docker run --rm --name rm-verify-<run8> --mount type=volume,src=repomesh-verify-scratch,dst=/verify <image> bash /verify/<run>/verify.sh`；
  子进程用 `asyncio.create_subprocess_exec`（同 `integrations/bootstrap/command_runner.py:39` 的形态，不引入 docker SDK）。
- `ScriptedVerificationExecutor`：测试用，按 spec 返回预设结果。

`verify.sh`（随 verifier 镜像分发，复制进 scratch）：`git clone base.bundle` → `git bundle verify candidate.bundle` → `git fetch candidate.bundle` →
断言 `git rev-parse <head>^ == base_sha` → `git diff --name-only base..head` 对策略 → 逐条 `testCommands`（每条超时）→ 写 `outcome.json`。容器无模型、无 Matrix、无存储凭据。

事件序列：`runner.accepted` → 每条测试 `runner.test_completed` → `runner.completed`（全部通过）或 `runner.failed`（父提交错 / 越界 / 测试红）。
终态 payload：`status, summary, changedFiles, commitSha, testResults, artifacts=[{kind:"git-bundle", uri:"s3://<bucket>/<key>", contentHash}]`。
复用 `repomesh_runner.event_sink.HttpEventSink`（`Idempotency-Key`、瞬时/拒绝分流）与 `task_source.HttpLongPollTaskSource`（加 `adapter` 查询参数）。

心跳：每 15 s `POST /runtime/v1/verifier/heartbeat {instanceId, kind: startup|renew|shutdown, toolchains: [...]}`，control token。

#### M5 `CandidateWorktreeMaterializer`

位置：`src/repomesh/integrations/workspace/candidate_worktree.py`，与 `GitWorktreeManager` 同目录、同布局。

接口：`async def materialize(self, *, repository_id, bundle: bytes, expected_head_sha, base_sha) -> Path`。
藏：把 bundle 写临时文件 → `git -C <mirror> fetch <bundle> refs/heads/*:refs/repomesh/candidates/<attempt8>` → 校验 `<head>^ == base_sha` → `git worktree add --detach w/<hash>/<hash> <head>` → 返回容器内路径。
失败抛 `CandidateRejected(reason)`，M1 把它当 `review(ACCEPT)` 后的立即失败处理（任务 failed，计一次尝试）。

#### M6 `BaseBundleBuilder`

同目录 `base_bundle.py`：`async def bundle(self, *, repository_id, base_sha) -> bytes`，先 `GitWorktreeManager` 确保镜像仓存在并 fetch，再 `git bundle create <tmp> <base_sha>`。第一版全历史；大仓浅包留开放项。

#### M7 `ConstructionMode` — 项目模块

`modules/project/contracts.py`：

```python
class ConstructionMode(StrEnum):
    HOSTED_NATIVE = "hosted_native"
    LOCAL_CLI = "local_cli"

@dataclass(frozen=True)
class DerivedRuntime:
    container_managed: bool
    worker_runtime: WorkerRuntime
    decomposition_default: TeamDecompositionMode

def derive_runtime(mode: ConstructionMode) -> DerivedRuntime  # hosted_native → (True, COPAW, SERVER); local_cli → (False, COPAW, SERVER)
```

`RepositoryTeam.construction_mode`（默认 `HOSTED_NATIVE`）；`with_adopted_leader` 的 LEADER 闸只在 `LOCAL_CLI` 下生效。
`TeamConstructionModeReader` 协议 + `PersistedTeamConstructionModeReader`（照 `infrastructure.py:591-625` 抄）。

**09-05 落地（提交 `277959b4`，分支 `feat/hosted-native-wave1`）**，与上文的三处差异：
① `WorkerRuntime` 经 `agent_runtime.contracts` 再导出后才能进 `project.contracts`（架构测试只允许跨模块 import `contracts`）；
② 投影 `ProjectRuntimeProjection` 不再注入任何 worker runtime，也**没有**注入 `TeamConstructionModeReader`——它本来就加载整个拓扑，直接按 `team.construction_mode` 逐团队 `derive_runtime()`，
读取器留给投递分叉 / 门禁 / 观察器这些不持有拓扑的消费者（容器里已注册 `team_construction_mode_reader()`）；
③ 接团队请求的 `construction_mode` 只决定**创建时刻**的控制器投影（runtime + `container_managed`），
拓扑行的模式由 `CreateProjectAgentTopology(construction_mode=settings.construction_mode_default)` 写入——
接团队时的选择目前**没有持久化载体**能带到 materialize 时才建的拓扑行（没有「catalog 团队记录」这种东西），
所以一个部署要么统一用 `REPOMESH_CONSTRUCTION_MODE_DEFAULT`，要么按 §5.3.1 对个别行 `UPDATE`；按仓库持久化该选择的候选位置是 catalog 仓库行（与 `capability_profile` 同型的供给侧开关），列第二波。

#### M8 `ExecutionPlaneReadiness` — 就绪真相

位置：`modules/agent_runtime/application/execution_plane.py`。

```python
class VerifierLeaseStore:            # 与 ExternalMemberReadinessStore 同形，内存 + TTL 45s
    async def report(self, instance_id, kind, toolchains) -> Receipt
    async def snapshot(self) -> VerifierView         # ready | stale | offline

class RequireExecutionPlaneReady:    # 组合门禁
    async def check(self, project_id, repositories) -> ExecutionPlaneFacts
        # members: hosted_native 团队的 leader+workers 用 WorkerBindingReader.get_worker().phase == "Running"
        #          local_cli 团队沿用 RequireExternalMembersReady
        # services: verifier 租约；仅当计划里存在 hosted_native 团队才要求
```

`setup/status.checks.execution_plane`：`missing`（无控制器或无 verifier 配置）/ `wired`（配置齐但无心跳或无 Running 成员）/ `ready`。

预检多一种事实 `provisioning`：团队 `room_id` / `leader_room_id` 为空，或成员尚无 `matrixUserID`（今天开工首两次 503 的原因，`runtime_projection.py:298-352`）时，
门禁返回 `status=provisioning` 而不是报错；前端模态每 5 s 自动重查，直到全绿或超 90 s 才显示失败。服务端开工仍 fail-closed，只是用户不再需要重按三次。

### 4.3 一次尝试的时序（多币种例子）

1. 开工 → 批次 0 派 pricing-core 的 Leader 任务，服务端拆解出 worker 任务 → `_deliver_assignment` 发现团队 `hosted_native` → `HostedNativeRound.open(task)`。
2. 生成代次 1、预留（租约 45 分钟）、`base.bundle`（`882231dd`）、包 v2 → 团队房：「@agt-worker-… 任务包 `tasks/<attempt1>` 已就绪，按 spec 三条命令执行，完成后 `submit_task`；完成通知 `@admin` 或不 @，不要 @Leader」。
3. worker `ack_task`（观察器 10 s 内看到 `acknowledged_at`）→ `/work/<attempt1>` 施工 → `repomesh-work.sh test` 绿（若被 Tool Guard 拦下，观察器比对命令行后自动 `/approve`，D-23）→ `repomesh-work.sh bundle` 写 `candidate/` 四件 → `submit_task SUCCESS`。
4. 观察器看到 `submitted_at` + `result.md` → `observe(submitted)` → 审阅包发给 Leader，Leader 房 @leader。
5. Leader `ack_task` → 读 diff 与证据 → `submit_task SUCCESS, summary: "VERDICT: ACCEPT …"` → 观察器 → `observe(review ACCEPT)`。
6. M5 把 bundle 拉进镜像仓、建工作树 `/runner-workspaces/w/<h>/<h>`；M1 投影验证调度：`adapterId=repomesh-verifier`, `workspace={path, baseSha}`, `candidate={bundleUri, bundleHash, commitSha}`, `testCommands`, `permissions`。
7. verifier 领到 → 一次性容器复验 → `runner.completed{commitSha, baseSha, testResults, changedFiles}`。
8. 网关 `_write_back` → 证据含 `workspacePath` → `AdvanceExecutionPlan.on_task_terminal` → `_roll_up` 结算 Leader 任务 → `_deliver_batch` → `PlanDeliveryFinalizer` 在工作树上建候选分支（`REPOMESH_DELIVERY_AUTO_ENABLED=false` 时不外推）。

---

## 5. 改动方案

### 5.1 前端改动

原则：类型加法、页面加块、不改现有交互路径；所有新文案进 `display.ts`。

| 文件 | 改动 |
|---|---|
| `frontend/src/api/contract.ts` | `ConsoleTeamView` 加 `construction_mode: "hosted_native" \| "local_cli"`（`:450-461`）；`SetupStatusView.checks` 加 `execution_plane: "missing" \| "wired" \| "ready"`（`:1644`）；新增 `ExecutionPlaneNotReadyDetail { code: "execution_plane_not_ready"; message; members: MemberReadinessFact[]; services: ServiceReadinessFact[] }`（放 `:1755` 旁）；`DeliveryTaskView` 加可选 `native_attempt?: { generation; phase; review_verdict?; verification_run_id?; budget_until? }`（`:670-708`）；`DiscoveryReadinessView` 加 `services` |
| `frontend/src/api/humanControl.ts:48` | `RepositoryTeamOnboardRequest` 加 `construction_mode` |
| `frontend/src/api/discovery.ts:127` | 409 判别同时接受 `external_members_not_ready` 与 `execution_plane_not_ready` |
| `frontend/src/components/ProvisionTeamModal.tsx:20-38` | 加「施工模式」单选（默认托管原生），随 `worker_count` 提交 |
| `frontend/src/components/MaterializeModal.tsx:12,162,264-326` | `Precheck` 三态扩为含 `services`；在「本地 CLI 就绪」块旁加「执行面」块：verifier 心跳、每团队 Worker/Leader 容器状态；`blocked` 同时看两块。托管原生团队不显示「启动并重新检查」按钮（无 Launcher 可拉） |
| `frontend/src/pages/IssueDetailPage.tsx:95-133,162` | 头部徽章加 `execution_plane`；仓库/团队 chip 显示模式 |
| `frontend/src/components/PlanDagPanel.tsx:111,129-155` 与 `viewmodel.ts:175-210` | `dagExecutionFromAggregate` 加按任务的 `nativeAttemptByTask` 映射；`NodeBox` 叠加「第 n 次尝试 · 阶段」小字，样板是 `unverifiedCountByRepository` |
| `frontend/src/pages/TeamsPage.tsx:88` | 模式徽章与 `decomposition_mode` 并排；文案表 `display.ts:65-70` 旁加 `CONSTRUCTION_MODE_LABEL/HINT` |
| `frontend/src/pages/SettingsPage.tsx:109` | 「执行面」段：`execution_plane` 三态与 verifier 最近心跳（只读，第一波不做组织默认值编辑） |

不做：模式切换按钮（§12.7 第二波）、Leader 审阅结论的房间回放定制（沿用 RoomView 现有时间线）。

验证：`npm run build`（`tsc -b`）、`npm run lint`（oxlint）、浏览器实走开工模态与 issue 页。

### 5.2 契约改动

**新增 `contracts/agentteams-task/v2/`（D-21）**

| 文件 | 内容 |
|---|---|
| `README.md` | 状态、目录布局、生命周期（notified → acknowledged → submitted → review → verifying → verified/failed/fenced）、fencing 规则（尝试目录不复用、代次不符即拒）、兼容规则（同 Runtime v1：可选加法向后兼容） |
| `manifest.schema.json` | `schema: "repomesh.agentteams-task.v2"`，`files[]` 必含 `meta.json, spec.md`，可含 `base/**`，`content_hash` 覆盖全部 |
| `meta.schema.json` | 原生字段 + `repomesh` 对象：`kind, task_id, attempt_id, generation, budget_seconds, base_sha, repository_id, organization_id, review_of?`；**写明 `repomesh` 对象只在发布时刻有效**，worker `ack`/`submit` 后会被重写丢失，消费者不得依赖它（D-6） |
| `package.schema.json` | `base/package.json`：`kind, task_id, attempt_id, generation, budget_seconds, base_sha, repository_id, organization_id, test_commands[], allowed_paths[], denied_paths[], workspace_root, helper_commands[]`；`base/` 目录全程只读不重推，是平台控制数据的唯一可靠载体 |
| `candidate.schema.json` | `changes.json { attempt_id, base_sha, head_sha, changed_files[] }`、`evidence.json { attempt_id, tests: [{command, exit_code, excerpt}], produced_at }`（最小证据集四元组把 request-id 换成 attempt_id） |
| `helper-cli.md` | `repomesh-work.sh init\|test\|bundle\|clean` 的输入、输出、退出码；工作区路径 `/work/<attempt_id>`；`bundle` 产出四件；脚本本身不含任何凭据；四条完整命令行逐字列出（与 `package.json.helper_commands[]` 一致，供 D-23 比对），并附 Tool Guard 规则集测试的夹具来源（D-21） |
| `review.md` | 审阅包 spec 的固定段落；`submit_task` 状态到 `ACCEPT/REVISION/BLOCKED` 的映射；`VERDICT:` 首行约定 |

**Runtime v1 可选加法（不升版本）**

| 位置 | 加法 |
|---|---|
| `contracts/runtime/v1/runner-task.schema.json` | 可选 `candidate: { bundleUri, bundleHash, commitSha, diffUri? }`；文档化 `adapterId` 取值 `repomesh-verifier` |
| `contracts/runtime/v1/task-and-result-reference.md:168-211` | 补记终态 payload 已有的 `commitSha`（引擎已发、文档漏写）；`artifacts[].kind` 增列 `git-bundle` |
| `contracts/runtime/README.md` | `GET /runtime/runner-tasks/next` 新增可选查询参数 `adapter`；**规则**：无主体的 control token 领任务必须带 `adapter`，且永远领不到持有自己凭据的成员（即 `containerManaged:false` 的 Bridge 成员）的调度——**09-04 已写入**；新增 `POST /runtime/v1/verifier/heartbeat`（PR-B） |
| `tests/contracts/test_runtime_v1_contract.py` | 覆盖 `candidate` 字段与 `repomesh-verifier` 任务样本 |

**`docs/contracts/`（控制台读模型）**

- 开工 409：新增 `code: execution_plane_not_ready`，`members[]` 行形状与 `external_members_not_ready` 相同，加 `services[]`；旧 code 保留一个版本周期。
- `setup/status`：`checks.execution_plane`。
- `ConsoleTeamView.construction_mode`、`DeliveryTaskView.native_attempt`、预检 `services`。

不改：`contracts/agent-bridge/*`、`contracts/leader-actions/*`、`worker-runtime.md`。

### 5.3 后端改动与数据库改动

#### 5.3.1 数据库（三个迁移，头 `20260902_0054`；09-04 改编号——`0053`/`0054` 已被数据库测试团队移交与合并迁移占用）

| 迁移 | 内容 | 样板 |
|---|---|---|
| `20260904_0055_team_construction_mode.py`（**09-05 已落地**，索引名按命名约定实为 `ix_repository_agent_teams_construction_mode`） | `project.repository_agent_teams` 加 `construction_mode String(20) NOT NULL server_default 'hosted_native'` + 索引；`downgrade` 删列 | `20260830_0047_team_decomposition_mode.py:63-111` |
| `20260904_0056_hosted_native_attempts.py` | 表 `agent_runtime.hosted_native_attempts(id PK, task_id, worker_agent_id, leader_agent_id, team_name, assignment_attempt_id, generation, execution_id, phase String(30), package_dir, review_dir NULL, budget_until, notified_at, acknowledged_at, submitted_at, submit_status, review_verdict, verification_run_id, fenced_at, fence_reason, created_at, updated_at)`，部分唯一索引「每 task 一个非终态尝试」；表 `agent_runtime.hosted_native_events(id PK, attempt_id, kind String(30), marker String(200), payload JSONB, observed_at, applied_at NULL, UNIQUE(attempt_id, kind, marker))` | `delivery/infrastructure.py:188-217`；`assignment.py:58-88` |
| `20260904_0057_repository_toolchain.py`（第二波） | `repository_intelligence.repositories.toolchain String(64) NULL` | `models.py:15-58` |

存量数据：迁移后所有团队默认 `hosted_native`；曾以外部成员建的团队由管理员一次性 `UPDATE ... SET construction_mode='local_cli'`（活体里目前没有这类团队）。

集成测试：`tests/integration/test_hosted_native_postgres.py`，照 `test_leader_assignments_postgres.py:106-238` 的子进程 alembic 红→绿→降级回环（09-05 已落地：0054 处写拓扑必 `42703`、head 处 `local_cli` 往返、降级再升级后旧行读回 `hosted_native`）。

#### 5.3.2 后端文件级改动

| 区域 | 文件 | 改动 |
|---|---|---|
| 项目模块（M7）**09-05 已落地** | `modules/project/contracts.py`、`domain.py`、`infrastructure.py`、`application.py`（`CreateProjectAgentTopology(construction_mode=...)`） | 枚举、字段、列映射、`PersistedTeamConstructionModeReader`；`with_adopted_leader` 加模式闸；拓扑创建器按注入的模式写行 |
| 设置 | `settings.py` | **已落地** `construction_mode_default=HOSTED_NATIVE`（`.env.example` 已写）；待落地 `hosted_native_attempt_budget_seconds=2700`、`hosted_native_review_budget_seconds=900`、`hosted_native_observer_interval_seconds=10`、`verifier_heartbeat_ttl_seconds=45`、`verifier_default_toolchain_image="repomesh-toolchain:default"`；`worker_recovery_enabled` 默认改 `True`（随恢复分支一起） |
| 接团队 **09-05 已落地** | `api/human_control_models.py`、`api/human_control.py`、`api/platform_setup.py:76-83`（走默认，未改） | 去 `leader_runtime/worker_runtime`，加 `construction_mode: ConstructionMode \| None`（`None` = `settings.construction_mode_default`）；用 `derive_runtime()` 决定 `RegisterNativeAgent` 的 runtime 与 `container_managed`；响应回报 `construction_mode`。**拓扑行的模式不从这里带过去**（见 §4.2 M7 落地注 ③） |
| 投影 **09-05 已落地** | `integrations/agentteams/runtime_projection.py` | 构造入参去掉全局 `worker_runtime`（未注入读取器，直接读已加载拓扑的 `team.construction_mode`）；`_register` 按团队 `derive_runtime()` 设 runtime 与 `container_managed`；MCP 投影**保留**（D-18）；`ExternalWorkerProjection`（Bridge 线）不动 |
| 装配 | `bootstrap/container.py:548-549,627,683,2306-2325`、`bootstrap/app.py:234-251,592-727` | 注入模式读取器；MinIO 读适配器与发布器同条件选择；注册 `SharedTaskDirectoryObserver` 后台服务；装配 M1/M5/M6/M8 |
| 投递分叉 | `modules/task_orchestration/application.py:534-565` | `_deliver_assignment`：团队 `hosted_native` 且 `assignee.role == WORKER` → `HostedNativeRound.open()`；否则走现有 publish+send。`_assignment_body:792-816` 加托管原生文案（不提 MCP） |
| 尝试（M1） | 新 `integrations/hosted_native/{round,observer,approval,package,storage,store,messages}.py` | 见 4.2；`store.py` 含 Postgres 与内存两实现；`approval.py` 是观察器的自动审批分支（D-23：逐字比对 `helper_commands`，先写事件再回 `/approve`） |
| 发布器（M3）**09-05 已落地** | `integrations/agentteams/task_publishing.py`、`task_package/`、`task_orchestration/contracts.py`（`PackageInputs`） | `PackageInputs`、v2 manifest、全文件摘要、`base/` 写入；construction/review 两模板；一次装配、两适配器只存字节（见 §4.2 M3 落地注） |
| 工作树（M5/M6） | 新 `integrations/workspace/candidate_worktree.py`、`base_bundle.py` | 见 4.2；复用 `git_worktree.py` 的镜像仓布局 |
| 验证调度投影 | `integrations/runner/task_projection.py` | `TaskProjection` 加可选 `candidate`；`adapterId` 由调用方给 `repomesh-verifier`；`workspace.path` 为 M5 返回的容器内路径 |
| 领任务过滤 | `modules/agent_runtime/api/router.py`（`next_runner_task`）、`runner_store.py`（`lease_next`）、`integrations/runner/gateway.py`；runner 侧 `repomesh_runner/{runtime_env,task_source,profiles,main}.py` | **09-04 已落地（分支 `feat/hosted-native-wave1`，先于 M1）**：可重复的 `adapter` 查询参数（也接受逗号分隔），按冻结 payload 的 `adapterId` 过滤，不加列；control token 无 `adapter` → 400；无主体调用永远领不到**持有自己 worker 令牌**的成员队列（`REPOMESH_RUNNER_WORKER_TOKENS` 的 id 集合：点名即 403，不点名即跳过）——按部署凭据表判定而不是每次领活读控制器绑定，控制器宕机时领活代价不变。runner 进程用 `REPOMESH_RUNNER_ADAPTERS` 广播可跑的 profile，未设则取本机能启动的 profile，一个都没有就拒绝启动；compose 默认 `mock` |
| 心跳与门禁（M8） | 新 `modules/agent_runtime/application/execution_plane.py`；`router.py` 加 `POST /runtime/v1/verifier/heartbeat`（control token）；`repository_intelligence/ports/member_readiness.py:62` 加 `ExecutionPlaneGate`；`discovery_materialization.py:358-366` 改调组合门禁；`api/discovery_chain.py:530-538,677` 新 409 与预检 | 见 4.2 |
| 设置状态 | `api/platform_setup.py:152-190` | `execution_plane` 检查 |
| 恢复 | `bootstrap/app.py:698`（`_discover`）、`integrations/runner/recovery.py:31-59`、`integrations/recovery/actions.py` | 过期预留若无调度且尝试表 phase 属 worker 侧阶段 → `reason="budget_expired"`；决策沿用 `decide()`（retry 同 worker = `open()` 新代次；reassign = 团队内其他 worker；escalate = 人工检查点）；新增两种探测（D-12）：worker 进程/容器启动时间晚于尝试 `notified_at` → `reason="worker_restarted"`（载体见 §8.16）；`WorkerBindingReader.get_worker(...).phase != "Running"` 或 `containerState` 非 running → `reason="worker_not_running"` |
| 读模型 | `api/read_models/service.py`（`list_teams`，**`construction_mode` 09-05 已落地**）；交付读模型任务视图 | `construction_mode`；`native_attempt` 块从 `hosted_native_attempts` 取（待 M1） |
| 复验器（M4） | 新 `src/repomesh_verifier/{__main__,main,config,executor,docker_executor,verify.sh,heartbeat}.py`；`pyproject.toml:39-41` | 见 4.2 |
| MCP | `api/worker_mcp.py` | 不改；`.env.example` 不再写 `REPOMESH_DIRECT_WORKER_MCP_ENABLED` |

#### 5.3.3 测试

新增：`tests/hosted_native/test_round.py`（三个动词、fencing、每种回执分支，全内存适配器）、`test_observer.py`（去重、marker、按目录名认领不读 `meta.repomesh`、自动审批只批与 `helper_commands` 逐字相同的命令行且先写事件再发）、`test_package_v2.py`（磁盘与 MinIO 假实现产同样摘要）、
`tests/workspace/test_candidate_worktree.py`（临时裸仓 + bundle）、`tests/verifier/test_executor.py`（Scripted）、`tests/verifier/test_verify_sh.py`（本机有 git 时跑真脚本，标 `integration`）、
`tests/contracts/test_agentteams_task_v2_contract.py`（**09-05 已落地**，含 Tool Guard 规则夹具 `tests/contracts/fixtures/copaw_tool_guard_rules.json`）、`tests/api/test_execution_plane_gate.py`；
09-05 另落地 `tests/contracts/test_project_construction_mode_contract.py`、`tests/api/test_repository_team_onboarding.py`、`tests/integration/test_hosted_native_postgres.py`（v2 包的磁盘/MinIO 同摘要测试并入 `tests/integrations/agentteams/test_task_publishing.py`，未另开 `test_package_v2.py`）。

更新：`tests/integrations/agentteams/test_task_publishing.py`、`test_runtime_projection.py`、`tests/api/test_issue_materialize.py`、`tests/task_orchestration/test_task_publication_translation.py`、
`tests/api/test_runner_scoped_auth.py`（`adapter` 规则）、`tests/test_worker_failure_recovery.py`、`tests/contracts/test_runtime_v1_contract.py`。

只做针对性验证：受影响模块测试 + 开工回归；不跑全量。

### 5.4 环境改动

#### 5.4.1 compose 与镜像

| 项 | 改动 | 理由 |
|---|---|---|
| `compose.yaml` 新服务 `verifier` | `profiles: [platform]`；`build: {context: ., dockerfile: Dockerfile.verifier}`；`networks: [default, agentteams]`（要到 `agentteams-controller:9000` 下载 bundle、到 `api:8000` 领任务）；`volumes: /var/run/docker.sock:/var/run/docker.sock, repomesh-verify-scratch:/verify-scratch, ${REPOMESH_SECRETS_DIR:-./.secrets}:/app/.secrets:ro`；`environment: REPOMESH_VERIFIER_API_URL=http://api:8000, REPOMESH_RUNNER_CONTROL_TOKEN, REPOMESH_VERIFIER_SCRATCH_VOLUME=repomesh-verify-scratch, REPOMESH_VERIFIER_DEFAULT_TOOLCHAIN_IMAGE`；`depends_on: api: condition: service_healthy`；`healthcheck: test -f /tmp/repomesh-verifier-ready` | 一次性容器用**命名卷**而不是宿主路径挂 scratch，避免 `REPOMESH_RUNNER_WORKSPACE_ROOT` 那种「同名变量宿主/容器双义」的坑（`compose.yaml:78,92`） |
| `Dockerfile.verifier` | 以 `Dockerfile.bootstrap` 为底：`python:3.12-slim` + `bash ca-certificates curl docker-cli git`；`pip install .`；复制 `src/repomesh_verifier/verify.sh` 与 `components/repomesh-verifier/toolchains/`；`CMD ["python","-m","repomesh_verifier"]` | 唯一同时有 docker CLI 与 `repomesh` 包的现成镜像 |
| `components/repomesh-verifier/toolchains/default/Dockerfile` | `python:3.12-slim` + apt `git nodejs npm`（或 `node:22-bookworm-slim` + `python3`）；verifier 启动时若本地无 `repomesh-toolchain:default` 则 `docker build` 一次 | 不为工具链再加 compose 服务；从零机器上多一次基础镜像拉取，README 要提示 |
| 基础镜像换源（#7） | `Dockerfile`、`Dockerfile.bootstrap`、`frontend/Dockerfile`、`Dockerfile.verifier`、工具链 Dockerfile 统一加 `ARG BASE_IMAGE=...`（node、nginx 同样），compose `build.args` 透传 `.env` 的 `REPOMESH_BASE_IMAGE_*`；README 加「国内网络」一节，写明死加速器的现象与配法 | 新方案多两张基础镜像；死加速器每张白等 1–5 分钟（`2026-09-02-from-zero-windows.md` §2） |
| 工作区变量拆名 | `REPOMESH_RUNNER_WORKSPACE_ROOT` 只在容器内使用且固定 `/runner-workspaces`；宿主 bind 源改名 `REPOMESH_WORKSPACE_HOST_ROOT`（`compose.yaml:92`），`.env.example` 同步 | 同名双义：宿主 `export` 后 `compose up` 挂错目录（`compose.yaml:78,92`） |
| 外部对象（D-20） | `compose.yaml:256-263` 去 `external: true`，保留 `name: agentteams-data` / `name: agentteams-net` | 两个安装器建网络都是 `inspect || create`（`agentteams-install.sh:3880`，`.ps1:2723-2724`），会复用 compose 建的对象；文档写明重装前先 `compose stop api verifier` |
| 运行时配置 | `verifier` 也用 `load_runtime_environment()` 读 `/app/.secrets/platform-runtime.env`（存储三元组在允许列表内） | 与 api 同一来源，不在 compose 里复制密钥 |
| 一次性容器 | 默认 bridge 网络（装依赖要外网）、`--rm`、`--memory 2g --cpus 2`、`--mount` 只挂 scratch 卷的本轮子目录；不挂 socket、不传任何凭据 | 目的文档 §7.1 |

#### 5.4.2 bootstrap

`integrations/bootstrap/executor.py`：production 模式在 api 重启并就绪之后加一步 `_ensure_verifier`：用 `DockerComposeApiTargetSelector` 泛化成按 `com.docker.compose.service` 选容器，
`verifier` 存在但非 running → `docker start`；不存在 → 操作进入 `verifier_missing`（可重试，提示运行 `docker compose --profile platform up -d verifier`）。`platform_bootstrap.py:44-67` 的视图加 `verifier` 阶段。bootstrap 不消费验证调度。

#### 5.4.3 启动脚本与示例配置（D-20）

| 文件 | 改动 |
|---|---|
| `scripts/start.ps1:6,45-46,66` | `$ErrorActionPreference = "Continue"`；`docker info` 用 `cmd /c "docker info >nul 2>&1"` 探测并查 `$LASTEXITCODE`；子脚本失败用 `try/catch` 而非 `$LASTEXITCODE` |
| `scripts/start-platform.ps1:80-84,285-296` | `Set-Utf8NoBom` 改位置调用；行尾 LF（`runtime_config.py:114` 拒绝 `\r`）；`Move-Item` 前断言文件非空 |
| `scripts/start-platform.sh:108-124` | `uname -s` 含 `MINGW` / `MSYS` 时改调 `agentteams-install.ps1 -NonInteractive`（#3）；两条安装路径都把 docker socket 探测提前到任何镜像拉取之前，探不到即退出，不再拉完 12 GB 才炸 |
| `scripts/start-platform.sh:226`、`.ps1:300` | `compose up` 列表加 `verifier`；写完 `platform-runtime.env` 后若 api 容器已存在 → `docker compose --profile platform restart api verifier` |
| `README.md`、`docs/clean-startup-guide-20260831.md` | PS 7+ 改 5.1+；每个 OS 只留一条启动命令；「cold path 未跑过」改为指向 `docs/startup-records/`；所有把 `docker inspect` 健康态或 `/health/ready` 当「平台就绪」的段落改成 `setup/status` 的 `agentteams` / `matrix` / `execution_plane`；写明 PS 安装器不装 dashboard、13000 不通是预期 |
| 整拆清单 | `clean-startup-guide` §10.1 补 `.repomesh-workspaces/`、`repomesh-verify-scratch` 卷、`~/agentteams-manager*`；新增 `scripts/reset.sh` / `reset.ps1` 一次清完，只在用户确认后运行 |
| 两脚本末尾 | 就绪断言改为 `GET /api/v1/setup/status` 的 `checks.agentteams && checks.matrix`，并打印 `execution_plane` |
| `.env.example` | 删除 `:86-156` 重复块；去掉 `REPOMESH_DIRECT_WORKER_MCP_ENABLED` 两行；`REPOMESH_WORKER_RECOVERY_ENABLED=true`；钉 `AGENTTEAMS_REGISTRY=higress-registry.cn-hangzhou.cr.aliyuncs.com`；加 `REPOMESH_CONSTRUCTION_MODE_DEFAULT=hosted_native`、`REPOMESH_VERIFIER_DEFAULT_TOOLCHAIN_IMAGE=repomesh-toolchain:default` |
| `.github/workflows/ci.yml:41-62` | 照 `runner-worker-image` 加 `verifier-image`：build `Dockerfile.verifier`，断言镜像内有 `docker`、`git`、`python -c "import repomesh_verifier"`，且**没有** `claude`/`codex` |

#### 5.4.4 AgentTeams 侧

不改 vendored 源码、不改 worker 镜像、不改 worker 的 `.copaw/config.json`：copaw v1.2.0 已有 git、python 3.11、node、npm、mc，且容器能直连 GitHub、PyPI、npm（活体核实）。
Tool Guard 出厂开启不动，靠 D-23 的自动审批过关；配置下发留第二阶段。
帮手脚本随任务包分发，不依赖技能安装时序（copaw 的 `skills/` 热同步留给第二阶段投 Skill）。

---

## 6. 影响范围分析

| 区域 | 变化 | 受影响的现有行为 | 风险与对策 |
|---|---|---|---|
| 托管原生团队的任务投递 | `_deliver_assignment` 分叉到 M1 | 房间文案与包内容变了；copaw 不再被指示调 MCP | 波次 0 先用手工包验证 copaw 照不照做三条命令 |
| 本地 CLI 模式 | 仅新增 `construction_mode` 列与组合门禁 | Bridge、`start_assigned_task`、`runner_dispatches` 语义不变 | `adapter` 规则只约束 control token 消费者；成员 token 路径零改动；`tests/agent_bridge/*` 必须全绿 |
| 投影 | 运行时按团队推导 | 既有 worker 再注册时 `runtime` 与 `mcpServers` 逐字段比对 | 推导结果对现有团队仍是 `copaw` + 同一 MCP 投影 → 无 409；D-18 |
| 开工门禁 | 新 409 code、新 `services[]` | 前端判别、`tests/api/test_issue_materialize*.py` | 旧 code 保留一个周期；前端同一波次更新 |
| 执行预留 | 托管原生尝试整段预算内持有预留 | 每 worker 一个活跃预留的唯一索引 → 同一 worker 不能并行接第二个任务 | 这是期望行为（不变量 5.6） |
| 恢复循环 | 新 `reason` 与 phase 探测 | `_discover` 原逻辑「有 queued 调度就续租」对无调度的尝试不再适用 | 只对 `hosted_native_attempts` 有行的预留走新分支；`tests/test_worker_failure_recovery.py` 扩例 |
| `runner-tasks/next` | 可选 `adapter` + 无主体规则（09-04 已落地） | 用 control token 领任务的既有调用者：一键栈的 runner sidecar（`b38549a0`）就是一个，09-04 起它带 `adapter=mock`（compose 默认）；W4 只用它重放事件，不受影响 | 文档写明；缺 `adapter` 返回 400 而非静默空；一键栈实走一次 mock 链确认 runner 仍能领活 |
| 交付链 | 零改动 | 依赖 `workspacePath` 上有工作树 | M5 在 ACCEPT 时就建好；验证失败或封存时清理工作树（`git worktree remove`） |
| 数据库 | 三个迁移 | 头从 `0054` 前进 | 旧谱系库不兼容（记忆：5433 已死谱系），只对全新库或 `0054` 头的库升级 |
| 复验器 | 新服务、docker socket | 宿主安全面多一个持 socket 的容器 | 一次性容器不挂 socket、无凭据、限资源；bootstrap 与 verifier 是仅有的两个持 socket 者 |
| 启动 | 外部对象改自建、脚本修正 | 安装器重装分支会 `network rm agentteams-net` | 文档写明重装前先停 api/verifier；`|| true` 不会让重装失败 |
| 前端 | 类型加法与新块 | 无路径删除 | `tsc -b` + oxlint + 实走 |
| 测试团队线 | 不变 | 测试资产仓仍按普通仓进计划；联调轮的一次性隔离环境不在本 spec | 复验器的 Docker 适配器将来可被联调轮复用，业务状态不共享（目的文档 §7.1 末段） |

---

## 7. 交付波次与验证

| 波次 | 内容 | 完成标志 | 验证 |
|---|---|---|---|
| 0 实证（零代码，半天） | 手工按 v2 布局打一个尝试包给活体 pricing-core worker（`base.bundle` 从公开夹具仓打、`rm-work.sh`、spec）；再手工给 Leader 一个审阅包；中途 `docker restart` 一次 worker | 三个答案：模型能否做完、三条命令照不照做、重启后会不会往旧目录交 | 共享盘产物 + 房间记录，写入 `docs/startup-records/` |
| 1 最薄闭环 | PR-A 后端：M7 列与推导、M3 包 v2（含 `base/package.json`、`repomesh-work.sh`）、M1/M2（含 D-23 自动审批）/M5/M6、M8 门禁、投递分叉、恢复分支；PR-B 复验器：M4 + `Dockerfile.verifier` + compose + 心跳（`adapter` 过滤已于 09-04 先行落地）；PR-C 前端：5.1 全部 | §1.2 的目标在活体达成 | 每 PR：受影响模块测试；合并后活体实走一条新 issue |
| 2 产品化 | `toolchain` 列与按团队 worker `image`；组织默认模式；审阅/预算落配置面；SettingsPage 执行面详情 | 控制台能选、能看 | 同上 |
| 3 从零复验 | 5.4.3 全部；整拆重跑 README 一条命令 | 白机一条命令到「候选提交」；推 GitHub 前用户放行 | 新记录文件 |
| 4 启动逻辑归容器（D-22） | api 启动时 `.env` 有模型密钥且控制器缺失 → 自动 `ensure_requested` bootstrap 操作；`start-platform.*` 缩成「生成 token → `compose up` → 等 `setup/status`」；Windows 不再走 `.ps1` 安装器 | Windows 白机一条 `start.ps1` 到候选提交，两个启动脚本行数减半以上 | 新记录文件 |

PR-A 与 PR-B 之间只有两个契约相连：`runner_dispatches` 行（`adapterId=repomesh-verifier` + `candidate`）与 Runtime v1 事件，可并行施工。

波次 1 的活体验收按 `hosted-native-e2e-acceptance-script-20260902.md` 的 30 幕执行：一页一图配一条探针，幕 25–27 是反证；
波次 0 之前先跑它的基线版（标 `B` 的幕），在活体上留「改造前」对照组。

---

## 8. 开放项

波次 0 实证（2026-09-03，`docs/startup-records/2026-09-03-hosted-native-spike.md`）的答案已并入；带「实证」标记的条目以该记录为准。
第 9–14 项已于 2026-09-04 裁决并写回 §3（D-23、D-6、D-12、D-21、D-3），原文保留作实证依据，**不再是开放项**；第 16 项是裁决后新留的实现开放项，PR-A 施工前定（09-05 已定 (b)）；第 17 项于 09-05 M2 施工前裁决（见条内），不再是开放项。

1. 大仓库浅 bundle（`git bundle` 不支持浅仓）；先全历史。实证：bundle 必须同时带 `HEAD` 与分支 ref，否则 `git clone` 报「remote HEAD refers to nonexistent ref」；`git bundle verify` 只能在仓库内跑（M6 照此打包）。
2. Leader 用 DeepSeek 审 diff 的可靠性。**实证（一次样本）：可用。** 收到结构化审阅包后 70 秒 `ack_task` → 读 diff/evidence → `submit_task SUCCESS`，首行 `VERDICT: ACCEPT`，理由抓到了 base 已有币种测试与 "mandatory" 对默认 USD 的张力；找不到本地仓库时自己想通「按 diff 审」。`REVISION`/`BLOCKED` 两个分支还没有样本。
3. 第二阶段：worker 主体凭据经控制器 worker-deps 通道投递（`deployer.go:56-66,819`），直连回调作为 `SharedTaskDirectoryReader` 接缝的第二个生产者；届时移除 MCP 投影。
4. 模式切换状态机（目的文档 §8）：generation 存哪、旧实例如何撤销；本 spec 只留列。
5. 候选工作树的清理策略：交付完成后多久删；与既有 runner 工作树共用策略。
6. 组织 Manager 容器：每次开工都留下一个因 18888 冲突停在 Created 的容器；短期至少不发布宿主端口，长期是否彻底不投影（要改 `principal_registration.py:27` 的断言）。
7. 一次性容器的网络策略：无依赖安装的仓可用 `--network none`。
8. 安装器无条件拉三种 worker 镜像共 8 GB（`agentteams-install.sh:3700-3702`，无跳过开关）：接受；或 fork 加 `AGENTTEAMS_INSTALL_WORKER_RUNTIMES` 过滤；把三个镜像变量指向同一镜像的 hack 只写进 README「国内网络」小节，不做默认。
9. **实证新增：copaw Tool Guard 的策略归属。已裁决 → D-23（选 b「观察器兼自动审批」；a 需改控制器，留第二阶段）。** 出厂 `security.tool_guard.enabled=true`，`execute_shell_command` 命中规则就要房间里人工 `/approve`（600 s 不批即拒）。三种解法待选：a) 平台在注册/投影 worker 时下发 `security.tool_guard.{disabled_rules,guarded_tools}`（要查控制器怎么写 worker 的 `.copaw/config.json`）；b) M2 观察器兼做自动审批者，协议见第 10 条；c) 帮手脚本改名 + 证明 `git`/`python` 不触发别的规则。波次 1 必须选一种，否则托管原生 = 每任务三次人工点头。
10. **实证新增：`/approve` 的唯一有效形状。** 群房默认 `requireMention`：裸 `/approve` 被当历史吞掉；正文带 `@worker` 前缀的 `/approve` 因 `approve` 不在 channel `_SLASH_COMMANDS` 里会被加 `sender:` 前缀送进 runner，`_is_approval` 只认恰好 `/approve`，等于拒绝。有效形状 = 正文恰好 `/approve` + `m.mentions.user_ids` 带 worker（`components/agentteams/copaw/src/matrix/channel.py:1146-1240,1920-2010`；copaw `runner.py:50-67,251-345`）。自动审批适配器原型：实证目录 `auto_approve.py`。
11. **实证新增：`meta.json` 非原生字段的存活期。已裁决 → D-6 修订（按目录名认领，控制数据放 `base/package.json`）。** copaw `ack_task`/`submit_task` 用 `TaskMeta` 原生字段重写 `meta.json`，`repomesh` 块整个丢失（`copaw_worker/task.py:145-170`）。M3 的控制数据改放 `base/package.json`（`base/` 不被重推），帮手脚本只读它；M2 观察器按目录名 = attempt_id 关联自己库里的行，`meta.json.repomesh` 只在发布时有效。契约 `meta.schema.json` 要写明这一点。
12. **实证新增：重启探测信号。已裁决 → D-12 修订（三级信号，进程启动时间为准）；目的文档 §7 措辞已改。** `docker restart` 7 秒完成，控制器 `phase` 很可能一直是 Running，而工作区与未提交改动全部保留，只有会话内存（含待审批）丢失，worker 不会自发续做。D-12 的「`phase != Running` 立即中断」要补一条：copaw 进程启动时间（或容器 `StartedAt`）晚于尝试 `notified_at` 即视为中断。目的文档 §7「容器重启导致工作区消失」应改为「重启或重建都视为中断；只有重建才丢工作区」。
13. **实证新增：帮手脚本命名与命令行。已裁决 → D-21 修订（定名 `repomesh-work.sh`，四条命令行过规则集测试）。** `rm-work.sh` 名字里的 `rm` 命中 `TOOL_CMD_DANGEROUS_RM`。D-21 的 `helper-cli.md` 改名（`repomesh-work.sh` 或 `work.sh`），契约测试里对整条命令行跑一遍 copaw tool guard 规则集。
14. **实证新增：worker 完成通知的对象。已裁决 → D-3 修订（`@admin` 或不 @，Leader 只在自己房收审阅包）。** worker 按 AgentTeams 技能在团队房 `@Leader TASK_COMPLETED`，Leader 两次因此陷入身份混淆（无破坏，但浪费一轮推理）；Leader 在自己房收结构化审阅包则正常。派单 spec 应让 worker 通知 `@admin`（平台身份）或不 @；Leader 只在 Leader 房收审阅包。
15. **实证新增：房间文字不是事件源（再证）。** DeepSeek 一次只输出「⏳ Waiting for approval」文字而没有真调工具，停了 9 分钟。M2 只看 `meta.json`/`result.md` 的做法是对的；预算到期（D-12）是唯一兜底，必须真的落地。
16. **09-04 裁决遗留：worker 启动时间的载体（PR-A 施工前定）。** D-12 修订后的信号②需要「worker 进程/容器启动时间」。控制器 `WorkerStatus` 今天只有 `phase / containerState / lastHeartbeat / lastActiveAt`，没有 `startedAt`（`agentteams-controller/api/v1beta1/types.go:371-383`）；api 不持 docker socket（D-4）。候选按代价排序：a) 观察器读控制器 `lastHeartbeat` / `containerState` 的序列，重启表现为心跳断档后 `containerState` 回到 running——要在活体上用 `docker restart` 对照一次才能采信；b) 持 socket 的 verifier 在心跳里附带各 worker 容器的 `State.StartedAt`（M8 `VerifierView` 多一个字段）——但 verifier 属 PR-B；c) fork 控制器给 `WorkerStatus` 加 `startedAt`——fork 镜像无发布链（D-2），第一阶段不选。载体落定前只有信号①③生效，M1 的 `expire` 必须先于信号②落地。
    **09-05 活体对照结论（`docs/startup-records/2026-09-05-live-verify-runner-and-restart.md` §3）：(a) 出局。**控制器 REST 投影（`internal/server/resource_handler.go:718 workerToResponse`）根本不输出 `lastHeartbeat`/`lastActiveAt`（236 个样本全空）；`docker restart` 全程 `phase=Running`；`containerState` 只在停止→再起之间露出 ≤ 4.3 s 的 `stopped` 窗口（Docker 在 SIGTERM 后的 ~8 s 停止阶段仍报 running），10 s 观察器命中概率 ≤ 43%，且命中也给不出「启动时间晚于 `notified_at`」。`docker inspect` 在同一秒给出新 `State.StartedAt`（08:04:15.65Z vs 旧 07:40:16.04Z），可直接比较。**口径：D-12 信号②的载体 = (b) verifier 心跳附带各 worker 容器 `State.StartedAt`（PR-B）；PR-B 未到前只有①③生效**；若 PR-B 滞后，第一阶段的便宜落点是 bootstrap 容器（已挂 `/var/run/docker.sock`）在其既有对账循环里回报 `StartedAt`——但这让 bootstrap 越出 D-4「只保障服务存在」的边界，要单独裁决。`containerState` 非 running 只当 D-12 ③ 的补充信号（`worker_not_running`），不当重启证据。
17. **09-05 M3 落地遗留：D-23 逐字比对的形态。** 波次 0 里 worker 实际敲的是 `cd <任务目录> && bash base/tools/rm-work.sh init`（S-1 记录的 7 次执行全是这个形态），与 `helper_commands[]` 的裸命令行**不逐字相等**。契约测试已证明 `cd shared/tasks/<id> && …` 前缀形态同样不命中任何规则，但 M2 的自动审批要么只批裸命令行（波次 1 实走可能一条都批不出去），要么做「剥掉 `cd <该尝试目录> &&` 前缀后再逐字比」的归一化——M2 施工前定，倾向后者且只认该尝试自己的目录。
    **09-05 裁决（M2 施工前，已写回 D-23 与 `contracts/agentteams-task/v2/helper-cli.md`）：取后者。** 归一化只做一件事——剥掉**恰好一个**「`cd <目录> && `」前缀，其余逐字比对：
    ① 被拦命令行取自 copaw Tool Guard 审批请求正文 JSON 参数块的 `command`（工具必须是 `execute_shell_command`；S-5 那种仿冒的等待文字没有该块，根本不进入比对）；只去首尾空白，不折叠内部空白、不改大小写、不解引号。
    ② 前缀形态必须恰好是 `cd <目录> && `（`cd` 后一个空格，`&&` 两侧各一个空格）。`<目录>` 可裸写或用成对的单/双引号包住，不得含 shell 元字符（空白、`;|&<>$` 反引号 `(){}*?[]!~#` 与换行）。去掉末尾 `/` 后，`<目录>` 必须以 `shared/tasks/<attempt_id>` 结尾——相对写法 `shared/tasks/<id>` 与任意绝对前缀 `…/shared/tasks/<id>` 都认，`<attempt_id>` 必须是**该尝试自己的目录名**；同团队其他尝试的目录、`shared/tasks` 根、工作区 `/work/<id>` 都不算。
    ③ 剥前缀后的剩余部分必须与该尝试 `base/package.json.helper_commands[]` 中某一条逐字相同（读包内文件，不读 `HELPER_COMMANDS` 常量；两者由契约测试钉成一致）。
    ④ 其他一律不批：串了别的命令（`ls -la && cd … && …`、`… && echo ok`）、分号、`||`、管道、重定向、环境变量赋值、第二个 `cd`、`bash -c`、`sh`、多余参数、大小写差异。
    ⑤ 比对之外的闸门不变（D-23）：请求来自该尝试的 worker（发信 Matrix id 经既有 `AgentTeamsMatrixIdentityResolver` 反解为 `worker_agent_id`）、发在该尝试的团队房、尝试非终态、请求时间晚于 `notified_at`；先写 `hosted_native_events(kind=auto_approved, marker=<请求 event_id>)` 再发恰好 `/approve` + `m.mentions`，发送用以事件 id 派生的 Matrix 事务 id，重试不重发。
    证据：波次 0 七次真实执行全是绝对路径形态 `cd /root/.copaw-worker/<worker>/.copaw/workspaces/default/shared/tasks/<attempt_id> && bash base/tools/rm-work.sh init`（`output/hosted-native-e2e/2026-09-03/spike/rooms.jsonl` 20:01:05 / 20:12:48 / 20:21:45），尝试 3 的 `init` 前串了 `ls -la` 与 `tail -n 20 spec.md`（S-1）——那两条按 ④ 不批，正是「其他任何命令留给人」。
18. **09-05 M3 落地遗留：v2 spec 没有数据库变更段。** v1 spec 的「Database change requirements」（`task.database_change`）没进 construction 模板；托管原生第一阶段的三个夹具仓都没有数据库变更，先不加；需要时加进模板并让复验器检查 `.repomesh/database-change-report.json`。

## 9. 波次 0 实证对 §3 决策的挑战（2026-09-04 已裁决，写回 §3）

裁决依据：2026-09-03 实证（S-n）+ 2026-09-04 五角度代码审读（控制器 / copaw / 平台代码逐处核实）。首列保留修订前的决策原文，末列是裁决结果；§3 正文已按末列改。

| 决策（修订前原文） | 挑战 | 实证 | 建议口径 | 裁决（09-04） |
|---|---|---|---|---|
| D-2 copaw 是唯一施工者、容器内不起 coding CLI | 成立，但前提是 shell 无需人工审批；出厂 Tool Guard 让每条命令都要人批 | S-1、S-2 | 加 D-23：worker 的 Tool Guard 策略由平台负责（配置下发或自动审批），AC-02 补「三条命令无需人工审批跑完」 | **采纳，D-2 保留并加前提，新增 D-23**。三选一定 b「观察器兼自动审批」：代码审读证明控制器与 copaw 桥接层都没有 `tool_guard` 配置入口，a 要改控制器（fork 无发布链），留第二阶段；c 改名照做但只是必要条件。自动审批只批与任务包 `helper_commands` 逐字相同的命令行。AC-02 已补（剧本 §4） |
| D-6 观察适配器幂等摄取原生 `ack`/`submit` | 成立，但 ack 后 `meta.json` 只剩原生字段，观察器不能靠 `meta.repomesh.*` 认领 | S-3 | 按目录名认领；`repomesh` 块只在发布时有效 | **采纳，D-6 修订**。目录名 = `attempt_id` 是认领键；控制数据放 `base/package.json`（进契约 `package.schema.json`）；`meta.json.repomesh` 降级为发布时快照 |
| D-12 `phase != Running` 立即中断 | `docker restart` 期间 `phase` 看不出来；真正信号是进程重启 | S-6 | 以进程/容器启动时间为准，`phase` 作补充 | **采纳，D-12 修订为三级信号**：预算到期兜底 → 进程启动时间晚于 `notified_at` 即中断（`worker_restarted`）→ `phase` / `containerState` 补充。启动时间的载体控制器今天不给（`WorkerStatus` 无 `startedAt`），列为 §8.16，PR-A 前定 |
| D-3 Leader 通过派给它的原生任务审阅 | 成立；但 worker 的 `TASK_COMPLETED` @Leader 会把 Leader 拖进团队房的身份混淆 | S-4 | worker 通知 `@admin`；Leader 只在自己房收审阅包 | **采纳，D-3 修订**。审阅机制不变；worker 完成通知改 `@admin` 或不 @，spec 文案与房间派单都写明不 @Leader；Leader 只在 Leader 房收审阅包 |
| D-21 帮手命令行 `rm-work.sh init\|test\|bundle` | 名字触发 `TOOL_CMD_DANGEROUS_RM` | S-1 | 契约里改名并做规则集测试 | **采纳，D-21 修订**。定名 `repomesh-work.sh`（名字与四条命令行无 `rm` 等片段）；契约测试跑四条完整命令行过规则集，规则夹具从活体 worker 镜像导出（vendored 源无规则表） |
| 目的文档 §7 「容器重启导致工作区消失」 | 事实相反：重启保留工作区，重建才丢 | S-6 | 措辞改「重启或重建都视为中断；重建才丢」 | **采纳，目的文档 §7 已改**：重启或重建都视为中断；只有重建才丢工作区，重启保留工作区但丢会话内存、worker 不自发续做 |

裁决之外、同日审计发现的三件事（09-04 已处理）：① `b38549a0` 已把 runner 服务放进 compose platform profile，用全局 control token 领所有队列且 `runner-tasks/next` 无 `adapter` 过滤——§1.1 与 §6 已加更新注记，§5.3.2「领任务过滤」已先于 M1 落地（分支 `feat/hosted-native-wave1`，契约规则写进 `contracts/runtime/README.md`）；② 迁移头已是 `20260902_0054`，§5.3.1 编号改为 `0055/0056/0057`；③ 记忆与部分记录写 D-21 为末条，spec 实到 D-22（09-04 起到 D-23）。

S-n 编号见 `docs/startup-records/2026-09-03-hosted-native-spike.md` §4。
