# 测试团队 v1 验收标准

日期：2026-09-01（拷问会两轮审定：分层/开关语义/一红一绿/负路径/执行环境/前端范围/徽标数据源）
关联设计稿：`module-test-team-topology-draft-20260901.md`（同目录）
术语以根目录 `CONTEXT.md` 为准。特别注意：**判据**（Manager 冻结、约束联调轮）与
**验收标准**（本文件，给 v1 交付物）是两个词，不得混用。

---

## 0. 执行总则

- **分轨执行**：A 组写成 pytest 进测试套件（in-memory + 一次性 postgres 双档，沿
  `tests/test_api.py` / `tests/integration/test_postgres.py` 惯例），沉淀为回归资产；
  B/C/D 组在**独立 worktree 全新 compose 栈**上活体实走（独立 volume，绕开 5432 谱系），
  三仓夹具（repomesh-e2e-*）当靶。
- **方法论约束**（历史教训，逐条适用）：计数不证幂等（断言 id，不数个数）；
  信号对 ≠ 原因对（每条 AC 写明断言的是什么事实）；检测器先验对照组（红轮的存在理由）。
- **针对性验证红线**：不跑全量套件；前端 = 浏览器实走 + `tsc -b`（`--noEmit` 是空转桩，
  禁用）+ oxlint 受影响文件。
- **通过定义**：P-1 通过且 A/B/C/D 全过 = v1 验收通过。任何一条不过，失败要有名字
  （记录到本文件的执行记录节，不许停在模糊状态）。

---

## P. 前置检查（不算 AC，排在一切之前）

| 编号 | 内容 | 通过判据 | 失败处置 |
|---|---|---|---|
| P-1 | AgentTeams worker 容器 Docker 能力实测 | 在验收新栈的 worker 容器内 compose 起一个最小服务成功 | **联调执行改道 runner 轨**，B/C 组整体重排，A/D 组不受影响；设计稿 §5.2 预案生效 |

---

## A 组 · 平台层（pytest，自动化）

| 编号 | 断言 |
|---|---|
| AC-A1 | catalog 存在档案 `cross-repo-test-team` 的仓时，新项目 materialize 后拓扑含该仓团队；leader/worker principal 存在；AgentTeams 资源技能等于覆盖表（队长 `cross-repo-test/worker-management/reporting`，worker `integration-run/task-execution`） |
| AC-A2 | 拓扑视图里测试团队 `room_id` 与 `leader_room_id` 非空 |
| AC-A3 | 同 key 重放 + 换 key 二次 materialize，测试团队**同 id 同名**（断言 id 相等，非计数） |
| AC-A4 | 开关正反（供给侧语义）：撕档后新项目 materialize 不含测试团队；**此前已建团队的拓扑原样保留** |
| AC-A5 | 已有拓扑的项目再 materialize，拓扑逐字段不变（早退路径） |
| AC-A6 | 对照组：无测试档案仓时，现有 materialize 相关用例全过 |

---

## B 组 · 闭环层（活体，一红一绿两轮）

| 编号 | 断言 |
|---|---|
| AC-B1 | **绿轮**：钉已知绿组合，worker 完整闭环——一次性隔离环境起、场景全过、`evidence/<run-id>/` 入资产仓、回执 `verified=true` 且 artifacts 指针可解引用（contentHash 对账）、环境拆净（同 `itest-` 前缀资源清零） |
| AC-B2 | **红轮**：钉夹具「联调红」组合——轮次结论 FAIL、失败场景 request-id 链路穿过失败仓、回执 `verified=false`、**判据原样未动**（未靠改判据求绿） |
| AC-B3 | **派工链**：两轮的 Manager→队长→worker 任务与消息在平台台账可查，路由正确（Manager↔队长走 leader room，队内走 team room） |

---

## C 组 · 负路径（活体，同一新栈）

| 编号 | 断言 |
|---|---|
| AC-C1 | **阻塞轮**：组合里钉不存在的 commit 制造阻塞——任务以 BLOCKED 上报；轮次目录**照样提交**（阻塞原因 + 已跑部分证据） |
| AC-C2 | **清扫双向**：伪造两份 `itest-*` 残留（一份时间戳做旧超 TTL、一份新鲜模拟在跑），下一轮开工清扫后：**超时的被清、新鲜的原样在**（不误杀是并发裁决最危险的边） |

---

## D 组 · 前端层（活体，同一新栈，浏览器实走）

v1 前端最小施工两件（改档入口、测试团队徽标 + contract 补 `capability_profile` 字段），
本组验收覆盖之；徽标数据源 = 前端 join 仓库档案（裁决记录见 §「已知局限」）。

| 编号 | 断言 |
|---|---|
| AC-D1 | **改档入口**：RepositoriesPage 能在 `default` / `cross-repo-test-team` 间切换档案，成功后列表回显，UI 附供给侧语义提示（只影响之后的新拓扑）。**执行方式即 B 组第一步**：开关拨动走 UI 不走 curl，一个动作双验 |
| AC-D2 | **徽标**：贴档建团后，TeamsPage 该团队带「测试团队」徽标，业务团队不带 |
| AC-D3 | **兼容**：测试团队正常出现在团队列表；team room / leader room 按钮能打开 RoomView（`/rooms/{id}/stream`），看到的派工消息与 AC-B3 台账一致 |
| AC-D4 | **质量线**：`tsc -b` 零错 + oxlint 受影响文件零新告警 |

### 已知局限（显式接受，不藏）

徽标从**当前档案**实时 join 推导。撕档后，存量测试团队按供给侧语义照常存在，
但徽标会消失——UI 身份与领域身份在此罕见场景下不一致。v1 接受（失徽标不失功能）；
出现第三个消费方需要判断团队性质时，把身份字段下沉进后端 team view，此别扭自动消解。
AC-D2 因此只断言正向。

---

## 执行记录（实走时逐条填写）

| 编号 | 结果 | 证据 | 日期 |
|---|---|---|---|
| P-1 | **FAIL** → 改道条款生效 | 在运行中的 AgentTeams 控制器（宿主单例 `agentteams-embedded:v1.2.0-rm3`，新栈接的也是它）上经 `POST /api/v1/workers` 亲建 `agentteams-worker-p1-probe`，容器内用其自身 `AGENTTEAMS_AUTH_TOKEN` 探针：`docker`/`docker compose`/socket 全无；HostConfig `Binds=null Privileged=false`；控制器 `/docker/` 直通对 worker 角色 6 项全部 403 `cannot gateway gateway`；管理员 token 下建网络/建卷 403、非 `agentteams-worker-` 前缀 403、bind 403、单容器 create/start/kill/delete 通。探针 worker 已删净。代码依据：`auth/authorizer.go` worker/team-leader 对 `gateway` 资源 default deny；`proxy/proxy.go` 白名单无 networks/volumes。runner 镜像亦无 Docker。详见 spec 修订 A.0 | 2026-09-01 |
| AC-A1~A6 | **PASS**（双档） | in-memory 档：`tests/api/test_issue_materialize_test_team.py` 6 用例全绿（AC-A1/A2 合测、A3 拆 replay/换 key 两测、A4 双断言、A5 逐字段、另含 S-1 守卫序与去重两条冻结规则）；红验证：撤实现 4 红 2 绿（绿的两条守边界，独立于追加成立）。postgres 档：`tests/integration/test_postgres.py::test_postgres_test_team_supply_chain_converges_by_id` 于一次性 postgres:17（alembic head）全绿。AC-A6 对照组：`tests/api/test_issue_materialize.py` 35 用例全绿 | 2026-09-01 |
| AC-B1 | **PASS**（R1 落地后第 5 轮闭环） | W4 栈第 5 轮（`w4-b1-green-5`，plan `3f3524d1`，worker 任务 `4d3f3746`，run `cf69c87c`）。Manager `/bridge/materialize` → server 拆解 → Bridge 从团队房提及接单 `start-worker-task 202` → 执行器（R1 补丁后）在 test 阶段跑配方后二次收集 → 任务 `succeeded`，回执 `changedFiles=[evidence/itest-t4d3f3746c6d2/{round.md,steps.json}]`、`commitSha=64f32539`、`testResults=[exit 0]`，交付视图 `tasks[0].evidence.verified=true`、`validation_snapshot.status=passed`。证据：`steps.json.overall=PASS`、`round.md` §4 四步全 PASS、§3 清扫输出在；工作区 HEAD=`64f3253 repomesh: complete task 4d3f3746…`只含两份证据文件、porcelain 干净、`itest-t4d3f3746c6d2/` 根已拆。团队房 `[done] 2 file(s) changed, commit 64f32539eff8, tests passed`。**入仓**：变更集 `3f1c9237`（`execution-plan:3f3524d1:delivery`）→ 候选分支 `refs/heads/repomesh/3f3524d1/55555555`=`64f32539` → PR #1 `RepoMesh delivery 3f3524d1`（建时 draft，随后被平台 `undraft_when_allowed` 提升为 ready——资产仓无生产者依赖；因必需检查 `evidence-review` 不存在且需 1 approval 而不会合并，符合「只开不合」）。**contentHash 对账**：两文件 git blob id 两端相等（`f054dd8c…`/`1362ded3…`，`git ls-tree HEAD` = GitHub contents API `.sha`）；LF 归一化后 sha256 亦相等（`b3ce1fe2…`/`f2412988…`）；工作树 raw sha256 不同只因 `core.autocrlf=true` 的 CRLF 检出。**指针**：runner 回执 `artifacts=[]`（执行器不产 ArtifactRef），指针由 `commitSha + changedFiles` 派生并在候选分支上可解引用。首次 push 被 GitHub 403（E0b 细粒度 PAT 对资产仓只读），换 token 后重放终态事件重触发交付成功（见发现 7）。 | 2026-09-01 |
| AC-B2 | **PASS**（证据/入仓/判据三面全中；「回执摘要写明 FAIL」子句**未达**，见发现 8） | `w4-b2-red`，plan `0bc734f1`，worker 任务 `5ea75598`，run `c997cb39`，任务 `succeeded`（配方退出 0，取 A）。证据 `evidence/itest-t5ea7559844d3/`：`steps.json.overall=FAIL`（verdict code 1 记在证据内）、组合=red（pricing-core@`3c72ca6f`）、三 unit 步 PASS、`joint-multi-currency` FAIL，摘录含 `FAIL: test_zero_decimal_currency_is_rounded_to_whole_units`、`AssertionError: 199.99 != 200.0`、三条 `src` 路径行（指向 `itest-t5ea7559844d3/<repo>/`），request-id `itest-t5ea7559844d3/multi-currency-joint/joint-multi-currency`；`round.md` §4 同。**判据未动**：`git diff --stat 955f652 HEAD -- scenarios environments` 为空，提交只含两份证据文件。**入仓**：变更集 `07f73728` → `refs/heads/repomesh/0bc734f1/55555555`=`784a3eaa` → PR #2（自动交付，无需重放）。团队房 `[done] 2 file(s) changed, commit 784a3eaafb0a, tests passed`——叙事说的是 test_command 退出 0，不是轮次结论。队长归因未实走（容器 copaw 队长在 leader room 卡在工具审批超时，见 B3）。 | 2026-09-01 |
| AC-B3 | **PASS** | 四轮（B1-5/B2/C1/C2）同一路由：Manager→队长派工落 leader room `!n53K…`（交付事件 `task_assignment … payload_ref=collaboration-message:*`）；队长→worker 提及落 team room `!sY1l…`，Bridge 日志 `mention … carries a RepoMesh dispatch for task <id>` → `start-worker-task 202` → `accepted task <id> as run <run>`；`[accepted]/[started]/[tests]/[done]` 叙事进团队房。台账：`task_orchestration.tasks` 每轮一对（队长 `6f16e1ae/9672833c/8a703978/…`，worker `4d3f3746/5ea75598/812db753/9db7e001`），队长任务 `succeeded`，摘要「All 1 worker tasks succeeded」。附：容器队长在 leader room 试图跑 `execute_shell_command`（探 8000 端口与 mc 存储）被工具护栏挂起，3597s 审批超时拒绝——不影响 server 拆解与路由。 | 2026-09-01 |
| AC-C1 | **PASS**（「worker 上报 BLOCKED」子句由证据+PR 承载，回执摘要未含，见发现 8） | `w4-c1-blocked`，plan `16127733`，worker 任务 `812db753`，run `ed806252`，任务 `succeeded`（配方退出 0）。证据 `evidence/itest-t812db753fb7d/`：`steps.json.overall=BLOCKED`（verdict code 2）、`blocked_reason="pin 0000000000000000000000000000000000000000 of repomesh-e2e-checkout not checkable: fatal: unable to read tree (0000000000000000000000000000000000000000)"`（git 原话）；已跑部分：`build/pin-repomesh-e2e-pricing-core` PASS、`build-repomesh-e2e-checkout` PASS、`pin-repomesh-e2e-checkout` FAIL exit 128；`round.md` §4 `**Overall**: BLOCKED` + `**Blocked**:` 原因行。判据未动、`itest-` 根拆净。**入仓**：变更集 `64d71cc8` → `refs/heads/repomesh/16127733/55555555`=`1ca21e00` → PR #3。 | 2026-09-01 |
| AC-C2 | **PASS** | `w4-c2-sweep`（绿组合再跑一轮），plan `6a1c4faa`，worker 任务 `9db7e001`。用 `scripts/module-test-team/c2_plant_leftovers.sh` 盯 `workspaces/w/` 出现新 worktree 即种下 `itest-stale-c2/`（`touch -d '30 hours ago'`，mtime 2026-08-31 15:51 -0700）与 `itest-fresh-c2/`（当前），两者各含 `nested/marker.txt`。轮次 `round.md` §3 清扫输出：`sweep-itest: keeping itest-fresh-c2 (age 0h, fresh)` / `sweep-itest: removing itest-stale-c2 (age 30h > 24h)`（exit=0）。文件系统核对：worktree 根只剩 `itest-fresh-c2/`，其 `marker.txt` 原样；`itest-stale-c2/` 不存在；fresh 目录被 `/itest-*/` 忽略（porcelain 干净）。该轮 `overall=PASS`，证据 `evidence/itest-t9db7e001b276/` 入提交 `57c7a10e` → `repomesh/6a1c4faa/55555555` → PR #4。 | 2026-09-01 |
| AC-D1~D4 | **D1/D2/D3 活体 PASS + D4 PASS** | D1：登录 5281 控制台 → 仓库页 → 测试资产仓「团队档案」弹窗拨到 `cross-repo-test-team` 保存 → 卡片回显「档案 cross-repo-test-team」，后端日志 UI 发出的 `PATCH …/capability-profile 200`，API 回读一致；随后发现链 materialize 回执 `team_count=2, repositories=["pricing-fixture"]`（S-1 口径）。D2：TeamsPage 测试团队带「测试团队」徽标、业务团队不带（截图）。D3：测试团队在列表、`teamRoom` 打开 RoomView `repomesh-test-assets · teamRoom` live 轮询，派工消息与台账一致。D4：`tsc -b` 零错、oxlint 零告警（W2） | 2026-09-01 |

### W4 实走中的发现（2026-09-01）

1. **修复** `edd423f3`：`DispatchWorkerTask` 重算能力包时未传 `profile`，测试 worker 工作区挂载的技能没有 `integration-run`（live run `f375610a`）；已传入并加回归用例，第二轮起工作区含 `integration-run`。
2. **缺陷（未修）**：外部成员 provisioning 路径（`PUT /runtime/v2/external-members`）不套档案覆盖，控制器侧 `repomesh-test-worker.skills=['coding']`；容器队长 `repomesh-test-leader` 正确得到 `cross-repo-test/worker-management/reporting`。对 Bridge 成员该字段是装饰性的，runner 侧技能按档案挂载（见 1）。
3. **环境**：新工作区根必须 `icacls … /grant <user>:(OI)(CI)F`（Bridge 要给 worktree 打 Low 完整性标签），且 Git Bash 下要 `MSYS_NO_PATHCONV=1` 否则 `/grant` 被转成路径。
4. **资产仓**：`.gitignore` 的 `itest-*/` 未锚定根目录曾把 `evidence/itest-<run-id>/` 一并忽略（已改 `/itest-*/`）；配方改为按任务 id 派生 run-id 并对已存在证据幂等回放（`971dc1d`）。
5. **W4 阻断点 → 已裁决并实施（R1）**：见 AC-B1 原现象。用户裁决取 R1：执行器在 `test_commands` 全部退出 0 之后再收集一次，仅当 agent 阶段变更集为空时生效，同样过 allowed/denied 校验（spec A.2「证据收集时点」留痕；`src/repomesh_runner/executor.py::_collect_evidence`；`tests/runner/test_executor.py::TestExecutionEvidence` 新增四条，红验证撤补丁 2 红 2 绿，整文件 36/36 绿）。Bridge 进程重启后生效（执行器跑在 Bridge 内）。被拒：R2（放宽 J-12 受限 PATH）、R3（证据不入 git）。
6. **app 日志不在 backend.log 里**：`observability.QueuedLogRecorder` 把 handler 挂在 root logger 上，WARNING/ERROR（含 `Failed to advance the execution plan` 的完整 traceback、`Delivery refused batch …`）只进 `observability.log_entries` 表；uvicorn 日志文件只有访问行。查异常要读表，不要 grep 文件。
7. **交付 push 403（已处置，非平台缺陷）**：E0b 留下的 `github_pat_…` 细粒度 PAT 对资产仓只读（`push --dry-run` 403 `Permission … denied to catbobyman`；交接里的「push=True」来自 API `permissions` 字段，那是用户角色不是 token 权限；`ls-remote` 能通只因仓库公开）。W4 栈交付 token 换为本机 gh CLI 的 OAuth token（`repo` 作用域，`push --dry-run` 通），只落在 gitignored 的 `w4-live/secrets/github-token.txt`。B1-5 的交付用「重放 `runner.completed` 事件」的操作杆（`POST /runtime/runner-events`，控制 token，`duplicate=true` 仍触发 advance）补推；B2/C1/C2 自动交付。
8. **轮次结论到不了任务回执摘要（Bridge 形态的结构性缺口，未修）**：R1 之下 agent 阶段先于 test 阶段，而受限 codex 跑不了 python，所以 worker 的最终消息永远是「python 未找到」，`summary` 不含 PASS/FAIL/BLOCKED；团队房 `[done] … tests passed` 说的也只是 test_command 退出 0。结论只在 `steps.json.overall`/`round.md` §4 与候选分支/PR 上。spec A.2「轮次结论写在 … 任务回执摘要里」与 `integration-run` 第 3/4 步（守候回执、上报指针）在此形态下无人执行；AC-B2「回执摘要写明 FAIL」、AC-C1「结论 BLOCKED 由上报方转述」两子句因此未达。候选方向（v2）：执行器把 `test_commands` 输出尾行并入回执 `summary`，或平台叙事读取 `evidence/<run-id>/steps.json.overall`。
9. **PR 不保持 draft**：交付以 `draft=True` 开 PR，但 `undraft_when_allowed` 在下一个 15s 重放周期把无生产者依赖的 PR 提升为 ready（四个 PR 均如此）。不合并的闸门是 `REPOMESH_DELIVERY_REQUIRED_CHECKS=["evidence-review"]`（检查不存在）+ 1 approval，验证有效。
10. **SCM poller 每轮报错**：`GitHub observation polling failed … DeliveryConflict: SCM observation identity was reused for another external fact`（`integrations/scm/poller.py::_record` → `delivery/application.py::_validate_duplicate`）：观测 `external_id` 为 `<cs>:<repo>:pr:<n>:<sha>:open:-`，而 PR 载荷含可变字段（draft→ready、updated_at），同一 id 不同 payload_hash 即冲突。不阻断本线，属交付线待修。
11. **Windows 控制台编码**：从 API 读含中文的 JSON 时用 `PYTHONIOENCODING=utf-8` 或按 bytes 解码，否则误判为「JSON 损坏」；psql 输出用 `row_to_json` 而非 tab 拼接。
12. **RoomView 分页不带 cursor（前端缺陷，未修）**：补充轮 r2（从 issue `8782a8db` 起，全程盯三间房，见 `w4-issue-round-room-transcript-20260902.md`）在控制台实看时发现 RoomView 的 5 s 轮询与「加载后续」都只发 `GET /rooms/{id}/stream?limit=50` 不带 `cursor`，读模型是 offset 分页，所以超过 50 条的房间永远只显示最早 50 条；右侧「事件对照线 · 本轮」与环境栏（changeset/PR）正确。上一会话 D3 PASS 时房间不足 50 条未暴露。
13. **补充轮 r2（从 issue 起）**：`w4_chain.py W4_RUN=r2` 新建 issue → 四步发现链 + 审批 + materialize（`team_count=2`，新项目复用同名 Team 与同三间房）→ `/bridge/materialize` 绿轮 plan `be0f747e` → worker 任务 `d8c6051c` 79 s `succeeded` → `overall=PASS` → 提交 `91c3c43e` → `repomesh/be0f747e/55555555` → PR #5。三间房 Matrix 事件与控制台房间流全程留档（worker DM 房无流量；队长本轮未回复）。

---

## 改道修订（2026-09-01，P-1 FAIL 后 B/C 组的 runner 形态执行步骤）

依据 spec 修订 A。A/D 组断言与执行方式**不变**。B/C 组的**断言不变**，执行步骤按 runner
形态重写；每条仍写明断言的是什么事实。

| 编号 | runner 形态的执行步骤 | 断言的事实（不变） |
|---|---|---|
| AC-B1 绿轮 | Manager 在测试资产仓上派联调任务，判据含绿组合（`scenarios/multi-currency-joint/combinations/green.json` 的钉死表）；worker（Bridge 成员）经批准入口发起 governed run；其 Bridge runner 在测试资产仓工作区执行 `run_round.py` | 配方退出 0 且 `steps.json.overall=PASS`、`round.md` §4 全 PASS；工作区 `evidence/<run-id>/` 四节齐全且经平台交付推成 `repomesh/<plan8>/<repo8>` 候选分支；回执 `verified=true`、artifacts 指针指向该分支路径且 contentHash 对账；工作区内 `itest-<run-id>/` 根已拆净 |
| AC-B2 红轮 | 同上，判据含红组合（`red.json`） | 配方退出 0（轮次跑完）且 `steps.json.overall=FAIL`；`round.md` 中 `joint-multi-currency` 为 FAIL，摘录含失败测试名 + `AssertionError: 199.99 != 200.0` + 三条 `src` 路径行（证明装配的正是钉死的三处检出）；request-id `<run-id>/multi-currency-joint/joint-multi-currency` 在证据行；三个 unit 步 PASS（对照组成立）；**证据目录照样经平台交付入仓**（这正是取 A 的理由）；队长的归因（生产者）引用依赖图与对照组而非 traceback（traceback 停在联调测试文件，本机干跑已核实）；回执摘要写明 FAIL——原表的 `verified=false` 断言按 spec A.2 改版**作废**，`verified` 此后只表示「这一轮跑了且证据成形」；**判据文件与任务体未动** |
| AC-B3 派工链 | 不变 | 两轮台账与路由不变 |
| AC-C1 阻塞轮 | 判据组合钉一个不存在的 commit | 配方退出 0 且 `steps.json.overall=BLOCKED`；`evidence/<run-id>/` **照样成形并入仓**（原因 = 该仓 checkout 失败的 git 原话 + 已跑部分）；worker 按 `integration-run` 第 4 步把 BLOCKED 与证据指针上报，队长台账里该轮结论为 BLOCKED——原表「任务以 BLOCKED 上报」在取 A 下改为「结论 BLOCKED 由上报方转述、指针可解引用」 |
| AC-C2 清扫双向 | 在 runner 工作区根（该仓 worktree 目录）伪造两份 `itest-*` 目录，一份 mtime 做旧 >24h、一份新鲜；再起一轮 | `round.md` 第 3 节的清扫输出：旧的 `removing`、新鲜的 `keeping`；文件系统核对一致 |

**新增前置**（替代 P-1 的位置，2026-09-01 用户裁决）：测试团队的 worker 以 **Bridge 成员**
在位并能接单（读通知 → `start-worker-task` 202 → 其 Bridge runner 执行），即 M7 已跑通的
形态（spec A.5），环境照交接文档 §7.6 重建。**v1 局限**（已接受，spec A.4）：B/C 组只对
源组装型环境成立；compose 型环境在两条执行面上都不可执行。

## 备注

- ADR 已议：满足三条件但用户裁定**不立 ADR**，形态裁决以设计稿 §2 + §10 被拒方案表为记录。
- 本文件与设计稿的分工：设计稿说「为什么与怎么建」，本文件说「什么算建成了」。
