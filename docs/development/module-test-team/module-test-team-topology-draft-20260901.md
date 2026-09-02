# 测试团队接入拓扑设计稿（独立盲写版）

日期：2026-09-01（同日经拷问会审定八项裁决，本稿为审定后版本）
输入：`docs/development/module-test-team-design.md`（/module-test 团队规程）+ 现行建房/拉队代码
约束：按要求**未读**其他任何测试团队设计稿，本稿为独立推导；以「最简单实现」为第一目标。

---

## 0. 一句话结论

**不发明新概念。测试团队 = 一个以「测试资产仓」为 repository 的 `RepositoryTeam`，**
挂在 Manager（organization leader）之下，与各仓库团队同级。它的「测试团队身份」
不写在拓扑结构里，而写在 catalog 的 `capability_profile` 字段上——这个接缝本分支已经
铺到底了（`cross-repo-test-team` profile、技能覆盖、控制台改档 API 全部就位）。
v1 需要新写的平台代码只有一处：materialize 的 `_ensure_topology` 里追加约 10 行。
联调执行采用**一次性隔离环境**：每轮新建、参数化命名空间、测完即拆、证据入资产仓。

---

## 1. 现状机制速写(建房/拉队链路)

一个团队从无到有要过四道手，全部现成、全部幂等：

| 步骤 | 模块 | 关键代码 |
|---|---|---|
| 1. 铸 principal | `agent_directory` | `ProvisionRepositoryAgentTeam.provision`：每仓 ensure 一个 leader（全局单例 `repository:{id}:leader`，名 `agt-leader-<hex12>`）+ ≥1 worker |
| 2. 建拓扑 | `project` | `EnsureProjectAgentTopology.ensure(repository_ids)` → `CreateProjectAgentTopology`：org leader + 每仓一个 `RepositoryTeam` |
| 3. 投运行时 | `integrations/agentteams` | `ProjectRuntimeProjection.project`：Manager/Worker 资源 read-first 注册，技能经 `agentteams_skills(role, base, profile=仓库档案)` 覆盖 |
| 4. 建 Team+房间 | 同上 | `ReconcileProjectAgentTopology`：每仓 ensure 一个 AgentTeams Team（名 `repomesh-team-<repo hex>`），控制器随之建**两间 Matrix 房**（team room + leader room），回写行上 |

入口在 `DiscoveryMaterializationService.materialize`：从计划 DAG 取仓库名集 →
`_ensure_topology`（查 catalog 换 id → 步骤 1+2）→ `runtime.project`（步骤 3+4）→ 铸任务。

路由与派工全部以「团队」为单位、与团队是什么内容无关：

- `collaboration._route`：org leader ↔ 队长走 `leader_room_id`；队内走 `room_id`；
  worker↔worker 目前一律拒绝（`workers cannot communicate directly`）。
- `task_orchestration`：任务按 `task.repository_id` 找团队，队长再指派 worker。

**要点：整条链路对「这个团队是干什么的」零感知。** 团队的性格只在两处表达：
catalog 行的 `capability_profile`（本分支已有 `CROSS_REPO_TEST_TEAM_PROFILE = "cross-repo-test-team"`）
和 `team_skills._PROFILE_SKILLS` 的按角色技能覆盖（测试队长
`("cross-repo-test", "worker-management", "reporting")`，测试 worker
`("integration-run", "task-execution")`），对应技能文档 `capabilities/skills/{cross-repo-test,integration-run,tdd}/` 也已在树上。

---

## 2. 关键裁决：测试团队为什么应该「是」一个 RepositoryTeam

### 2.1 删除测试（deletion test）

设想反方案：拓扑里加一等公民 `TestTeam` 类型（或 `ProjectAgentTopology.test_team` 字段）。
把它删掉，会有复杂度散落回调用方吗？——不会，因为 `RepositoryTeam` 已经把
principal 单例、Team 铸名、双房 reconcile、路由、派工校验、运行时状态回写、控制台视图、
迁移**全部**做完了，且全部与角色内容无关。`TestTeam` 只能是这套东西的浅拷贝：
大接口（拓扑不变式、reconcile、路由、read model 全要各改一份）、薄实现。典型浅模块，拒绝。

### 2.2 测试团队天然有自己的仓库——这不是 hack，是领域事实

/module-test 规程 §11 的终点是：**通过审批的测试要「串行合并入库」成为正式测试资产**。
入库要有库。测试资产仓（放场景库、环境定义、联调证据的 git 仓）本来就是这套流程的
必要交付物。所以「每团队一仓」在这里不是需要绕开的约束，而恰好是正确的模型：
测试团队的 repository 就是它的资产仓，它在自己仓里做工、往自己仓里入库，
与仓库团队在业务仓里做工完全同构。

### 2.3 深模块视角

拓扑供给链（provision → create → project → reconcile）是一个深模块：接口是
「repository_ids 进，配好人、有房间、可派工的团队出」。测试团队作为 `repository_ids`
里多出的一个元素**从接口上骑过去**，调用方无需学任何新东西——深度不变，杠杆变大。
「团队性格」的变化点落在已经存在的接缝（catalog 行的 `capability_profile` + 技能覆盖
adapter）上，不新开接缝。一个接缝、两个真实档位（default / cross-repo-test-team），
接缝是真的，不是假设的。

---

## 3. 职责边界与角色映射【裁决 1、4】

### 3.1 职责边界：v1 只做跨仓联调

单仓/模块级测试仍归各仓库团队自己（`tdd`、`self-test` 技能已在册）。测试团队只测
「组合」：三个仓各钉死一个 commit 一起跑场景。三仓契约夹具（repomesh-e2e-*，
「三仓单测全绿而联调红」）正是这支团队的靶子。/module-test 的三角纪律
（判据冻结、写审分离、三轮升级）作为团队内部规程套用在联调任务上。

### 3.2 触发与钉组合：Manager 手工派，组合写进任务体

v1 由 Manager 决定时机与组合（三仓各钉一个 commit），随联调任务下发；测试队长核实、
拆解，worker 执行。这与规程「主 Agent 冻结判据」的位置一致——**commit 组合就是联调
任务判据的一部分**，冻结在任务体里，团队不得自行改钉。自动触发（批次完成自动出
联调任务、接交付门禁）留给 v4。

### 3.3 角色映射（/module-test 三角 → 平台层级）

规程的三角：主 Agent（定规格、监督、容错、三轮升级）↑，test-engineer ⇄ reviewer ↓。

#### v1 映射（零规则改动）

```text
Manager (organization leader)      ←→ 规程的「主 Agent」
        │  leader room（下发任务 + 冻结的判据：spec 与 commit 组合）
        ▼
测试团队 leader = reviewer          ←→ 规程的「reviewer」+ 平台队长职责
        │  team room（计划/提交/审查意见/修改稿直接往返）
        ▼
测试团队 worker = test-engineer     ←→ 规程的「test-engineer」
```

- **编写者 ≠ 最终审查者**：engineer 写、leader 审，天然满足。
- **engineer ⇄ reviewer 直接对话、无人转述**：正是平台允许的 leader⇄worker 方向，
  走 team room，一条消息规则都不用改。
- **三轮不收敛升级**：复用平台已有的 `HumanReviewRequest` 检查点——规程里
  「升级给用户」在平台上就是「发人审请求」，映射现成。
- 平台队长本来就带「验收 worker 结果」的语义（repo 队长有 `code-review`
  `worker-result-evaluation`），reviewer 当队长与平台的队长语义同构。

**代价（如实说）**：reviewer 同时持有队长的调度职责；规程里主 Agent 冻结 spec 的职责
上移给了 Manager。规程禁止的是「主 Agent 代替审查」和「编写者自审」，两条都没破。

#### v2 映射（贴满三角，作为增量）

leader = 主 Agent（规格与流程负责人），两个 worker = test-engineer + reviewer。需要两处小改：

1. **每队第二个 worker**：`RepositoryAgentTeamProvisioner.provision` 增
   `worker_count: int = 1`（或显式资源名表），测试档案仓给 2；
2. **同队 worker 互通**：`_validate_message_direction` 放行
   「sender/recipient 同 `repository_id` 且同 `leader_agent_id`」的 worker↔worker
   （principal 视图上两字段都有，一个条件的事）。现有仓库团队默认 1 worker，
   该放行对它们实际不可达，行为兼容。
3. 技能差异化：v2 先给两个 worker 同一份并集技能，写测试/审测试的分工由任务与
   skill 指令约束；按 worker 细分技能覆盖是更后面的事。

### 3.4 编制规模：v1 配 1 个 worker【裁决 6】

并发能力（§5.3）按标准铺好但暂由单 worker 使用；v2 扩编制时直接可用，不返工。
平台改动面维持在 `_ensure_topology` 十行。

---

## 4. v1 改动清单（全部）

### 改动 1（唯一的平台代码改动）：materialize 时把测试资产仓并入拓扑

位置：`DiscoveryMaterializationService._ensure_topology`
（`src/repomesh/modules/repository_intelligence/application/discovery_materialization.py:421`）。
该方法已经 `await self._catalog.list()` 拿到了全部仓库档案（含 `capability_profile`），追加：

```python
test_repository_ids = tuple(
    profile.id
    for profile in profiles
    if profile.capability_profile == CROSS_REPO_TEST_TEAM_PROFILE
    and profile.id not in repository_ids
)
# repository_ids + test_repository_ids 一并交给 provisioner.ensure(...)
```

- 不加新端口、不加新查询：数据本来就在手里。
- **开关就是档案本身**：控制台把某仓改档为 `cross-repo-test-team`
  （`PATCH` 路由已在 `repository_intelligence/api/router.py:388`），此后每个新
  materialize 的项目自动多出一支测试团队；改回 default 即关。不引入新配置面。
- 已有拓扑的项目走 `existing is not None` 早退，完全不受影响——向后兼容免费。

### 改动 0（运维动作，非代码）：准备测试资产仓

建一个真实 git 仓（如 `repomesh-test-assets`），注册进 catalog，打档
`cross-repo-test-team`。它同时就是 §11 入库流程的目的仓。仓内结构见 §5.1。

### 改动 2（技能文档修订，非平台代码）：见 §9 待办清单

`integration-run` 技能文档有两处表述与本稿裁决冲突（固定端口、证据只走回执），
需随本稿一并修订。

### 改动 3（前端最小施工两件，验收拷问会追加）

- RepositoriesPage 加**改档入口**（`default` / `cross-repo-test-team` 切换，抄本分支
  verification 弹窗形状，附供给侧语义提示）——兑现「控制台改档」的承诺，运维不靠 curl；
- TeamsPage 给测试团队加**徽标**，数据源 = 前端 join 仓库档案（contract 补
  `capability_profile` 字段）；已知局限：撕档后存量团队失徽标不失功能，详见同目录
  验收标准文件 §D 组。

验收标准全表（P/A/B/C/D 五组）独立成文：同目录
`module-test-team-acceptance-criteria-20260901.md`。

### 顺下来自动发生的事（零改动）

- principal：`agt-leader-<hex>` / `agt-worker-<hex>`（挂测试仓 id），leader 单例防重；
- AgentTeams Team `repomesh-team-<测试仓 hex>` + team room + leader room，
  reconcile 自动建、自动回写；
- 技能：leader/worker 分别拿到测试档案的覆盖技能（§1 表）；
- 路由：Manager→测试队长走 leader room，队内走 team room；
- 派工：Manager 以 `repository_id=测试仓` 建任务 → 队长指派 → worker 执行，
  与业务任务同一套 API 与状态机。

计划 DAG 里没有测试仓的节点，materializer 不会给它自动铸任务——v1 就该如此：
测试团队的工作由 Manager 显式派（§3.2），任务体携带冻结判据。

---

## 5. 联调隔离环境【裁决 2、3、5、8】

### 5.1 形态：一次性目录+环境，测完即拆【裁决 2】

每轮联调新建一个干净文件夹：三仓按钉死 commit 检出为一次性 worktree，跨仓依赖本地
改写（Go `replace`、npm `file:`、pip editable，按生态），从测试资产仓的环境定义起
环境，跑场景、收证据、**整体拆除**。不搞常驻联调环境。

理由：① 联调测的是「钉死的组合」，常驻环境必然攒状态残留（上轮数据库数据、改写过的
依赖指向、没杀干净的进程），第二轮红灯说不清是代码脏还是环境脏；② 规程「连续三次
稳定」闸门的前提就是每次起点干净；③ `integration-run` 技能已按一次性写好纪律
（worktree 用完即弃、改写不许回流主干、业务仓只读）。

成本控制：本地 bare 仓缓存 + `git worktree` 把每轮 clone 成本压到秒级
（平台 `integrations/workspace/git_worktree.py` 已有 worktree 机械可参照）。

测试资产仓同时承载环境定义与场景库，建议结构：

```text
repomesh-test-assets/
  environments/    # compose/dev 入口、端口与 env 的参数化定义（§5.3）
  scenarios/       # 场景库：输入、断言、触点清单
  evidence/        # 轮次证据目录（§6），evidence/<run-id>/
```

### 5.2 执行者：测试 worker 自建自拆【裁决 3，带前提】

worker 在自己容器里照 `integration-run` 技能完成建拆全程，平台零改动。

**前提（落地第一验证项）**：AgentTeams worker 容器必须能起 Docker（联调环境大概率
compose 起）。实测不过，本裁决升级为「runner 执行面代建」——联调任务下 runner 轨，
平台在独立容器里检出组合、跑命令、逐条回执。规程 §10 的「平台独立重跑」外环本来就
计划由 runner 承接（v3），前提失败只是把这一步提前。

**代价（如实说）**：v1 的执行事实是 worker 自报的。规程「不直接采信 Agent 报告」的
外环在 v1 缺位，靠证据纪律（§6）部分补偿，v3 补齐。

> **2026-09-01 预案生效（P-1 FAIL）**：在运行中的控制器上亲建 worker 容器实测——容器无 socket、
> 非特权、镜像无 docker/compose；控制器的 `/docker/` 受限直通对 worker 角色整体 403，且即便
> 管理员也拿不到建网络/建卷（compose 的前提）。本裁决按预案升级为「runner 执行面代建」：
> 联调任务下 runner 轨，组合由测试资产仓的配方脚本在 runner 工作区内检出，证据由脚本写、
> 由平台交付推成候选分支。上面「代价」一段随之**部分消解**：证据实体已由平台侧写入
> （§7 表 v3 那一格提前），但 runner 只回传退出码，摘录仍出自脚本。**新局限**：runner 镜像
> 同样无 Docker，v1 只能执行源组装型环境（§5.3 的 compose 并发配方可写不可跑）。
> 条文细节见 spec 修订 A。

### 5.3 并发：参数化命名空间 + 动态端口【裁决 5，用户裁定，推翻串行推荐】

环境配方从第一天按并发标准写：

- 每轮独立 compose project name，统一 `itest-<run-id>` 前缀命名空间
  （容器、network、volume 同前缀）；
- 端口一律参数化：环境变量注入或随机分配后回读，**场景断言禁止写死端口**，
  入口地址从环境元数据取；
- 每轮实际分配的端口/命名空间记入证据（§6），否则并发轮次日志无法归因；
- 并发度设显式上限（宿主机内存是真实约束：一轮 = 三份构建 + 一套依赖服务）。

v1 单 worker 下并发暂不实际发生（§3.4），配方先行是为了 v2 扩编制时零返工。

### 5.4 残留收尸：开工前 TTL 清扫【裁决 8】

worker 中途死掉（限流、容器重启、任务中断）会留下起了一半的环境。处理纪律：

- 所有资源统一 `itest-<run-id>` 前缀（§5.3 已保证）；
- **每轮开工第一步**：清扫同前缀且存活超过 TTL（24h，联调单轮远短于此）的残留，
  再建自己的——「下一轮给上一轮收尸」；
- 不建守护进程、不加定时任务：无常驻组件要维护，纪律写进技能文档即可。
- 已知代价：长期无新轮次时残留会躺到下一轮开工才被清，本地/开发环境可接受。

TTL 判据而非「清掉所有旧的」，是因为并发模式下可能另一轮正在跑。

---

## 6. 证据链【裁决 7，改版：实体单源 + 结论指针】

> 本节裁决经过一次重开：初版「回执全量 + 资产仓摘要」被两个代码事实推翻——
> ① 任务回执没有诚实的全量通道：`TaskEvidenceView`（task_orchestration/contracts.py:49）
> 的 artifacts 是 `{kind, uri, contentHash}` 引用而非内容，往 `summary_text` 塞 JSON
> 正是 A-18 批判过的「未声明形状塞散文字段」；② 技能定义的证据本来就是**最小证据集**
> （命令/退出码/输出摘录/request-id），摘录不是全量日志，体积被纪律封顶，入 git 可行。

### 6.1 实体单源：测试资产仓

每轮联调在资产仓提交 `evidence/<run-id>/` 轮次目录：

- 组合清单（仓库 → 钉死 commit）；
- 逐场景最小证据集：命令、退出码、输出摘录、request-id；
- 每轮实际端口/命名空间（并发归因用，§5.3）；
- 轮次结论（PASS/FAIL/INCONCLUSIVE 逐场景 + 总评）。

体积两条硬纪律封顶：**禁止原始全量日志入仓**；每场景摘录限 64KB，超出截断并注明。
主分支永久保留，不做归档/独立分支/TTL 删除（量级估算：一轮几 MB，一年 GB 以下，
git 扛得住；花样等真胖了再说）。

### 6.2 回执带结论+指针，用既有结构不造新形状

- `test_results`：逐场景命令+退出码——现成的 `verified` 属性（有命令且全零退出码）
  直接对联调轮生效；
- `summary_text`：队长可读的结论散文；
- artifacts 通道：`{kind, uri, contentHash}` 指向资产仓轮次目录的 commit 路径，
  contentHash 对账。

一个通道存实体（资产仓），一个通道存结论+指针（回执），**无双台账同步问题**；
规程 §10「逐条执行事实展示给人」有了可翻的实地。

### 6.3 异常轮次纪律（机械推论，写进技能文档）

- **BLOCKED 轮次同样提交轮次目录**（阻塞原因 + 已跑部分的证据）——「失败要有名字」；
- worker 暴毙、连提交都没有的轮次：**台账以任务为准**，资产仓缺目录本身就是
  「该轮无证据」的诚实记录；恢复后用新 run-id 重跑，不许复用旧轮次号补交。

### 6.4 worker 收尾流程（汇总 §5、§6）

```text
跑完场景 → 整理轮次目录 → 提交并推送资产仓 → 拆除环境（§5.1）
→ 提交任务回执（结论 + artifacts 指针）
```

先证据后拆除：环境拆了日志就没了，顺序不可逆。资产仓是测试团队自己的仓，
写权限天然有，不违反「业务仓只读」红线。

---

## 7. 规程纪律如何落在平台既有机制上

| 规程要求 | 平台落点 |
|---|---|
| 判据冻结（spec + commit 组合），不许照当前实现改 | 随任务下发（任务体/交接文档），团队只读；改判据 = Manager 重发任务，留痕（§3.2） |
| 只认 DONE/BLOCKED，不催 idle | 任务状态机已是显式完成/阻塞上报（`blocker-reporting` 技能在册） |
| 以产物恢复、不信活体 | 场景库/环境定义/证据全在资产仓 + 任务与消息在库；轮次以任务台账为准（§6.3） |
| 三轮不收敛升级 | 队长发 `HumanReviewRequest`；轮数纪律写进 `cross-repo-test` 队长技能文档 |
| Agent 自报结果不可信、平台独立重跑 | v1 缺位（§5.2 如实声明），证据纪律部分补偿；v3 由 runner 执行面承接，届时证据实体可由平台侧写入 |
| 逐条执行事实展示给人 | 资产仓 `evidence/<run-id>/` 可直接翻阅；回执 `verified` 给结构化判定（§6.2） |
| 红灯优先判业务 Bug | 写进队长技能文档的审查纪律；平台不需要为此改代码 |

---

## 8. 增量路线

1. **v1（本稿）**：测试资产仓 + 档案开关 + `_ensure_topology` 追加 + 技能文档修订。
   团队成立、可派工、联调闭环（一次性环境 + 证据入仓）。
2. **v2**：双 worker + 同队 worker 互通（§3.3），三角贴满；「审查任务的受派人 ≠ 编写
   任务的受派人」可作为 `task_orchestration` 的一条派工校验；并发能力（§5.3 已铺）
   随编制扩大启用。
3. **v3**：平台独立重跑外环——闸门 1-4（有产出/lint/能执行/三连稳定）作为 runner 侧
   的独立重跑任务模板，执行事实由平台侧持久化；变异测试（闸门 5）只做离线评测
   不进日常门禁（规程 §7 自己也这么说）。
4. **v4**：规划器感知——materialize 产出计划时为执行批次追加联调节点、自动钉组合，
   测试团队从「手工派活」升级为「计划内编制」。

---

## 9. 待办：技能文档修订清单（随 v1 落地）

`capabilities/skills/integration-run/SKILL.md`（在树未提交，改动窗口正好）：

1. 端口表述从「pinned to the scenario's ports」改为**参数化注入**（§5.3）：
   compose project name = `itest-<run-id>`，端口环境变量注入/随机回读，
   断言从环境元数据取入口；
2. Workflow 增加**开工前 TTL 清扫**步骤（§5.4）；
3. Outputs/Workflow 增加**证据入仓**：先提交 `evidence/<run-id>/` 再拆环境，
   回执 artifacts 指向该 commit（§6.4）；
4. Failure Handling 增加 **BLOCKED 轮次也提交证据目录**、暴毙轮次新 run-id 重跑
   不补交（§6.3）；
5. 摘录上限 64KB、禁全量日志入仓写进 Validation（§6.1）。

`capabilities/skills/cross-repo-test/SKILL.md`（队长）：

6. 增加并发度上限与「组合判据来自 Manager 任务体、不得自行改钉」（§3.2、§5.3）；
7. 三轮不收敛 → `HumanReviewRequest` 的升级纪律（§7）。

---

## 10. 被拒方案

| 方案 | 拒绝理由 |
|---|---|
| 一等公民 `TestTeam` 类型 / `topology.test_team` 字段 | 浅模块（§2.1）；拓扑不变式、reconcile、路由、视图、迁移全要动，收益为零 |
| `RepositoryTeam.team_kind` 判别字段 | 「性格」已由 catalog 档案表达，再加一列是同一事实存两处，必然漂移；且要迁移 |
| 测试团队不挂仓、虚拟 repository_id | 打破「团队名/单例/房间全部键在仓上」的整套铸名（A-8 教训），且 §11 入库本来就需要真仓 |
| 在 `EnsureProjectAgentTopology`（project 模块）里查档案追加 | project 模块要新依赖 repository_intelligence 的端口；materialize 手里已有档案列表，在调用方追加更便宜、依赖方向不变 |
| 常驻联调环境每轮重置 | 状态残留毁归因，与「三连稳定」闸门的干净前提相悖（§5.1） |
| 联调+模块级测试都归测试团队 | 要给它业务仓写权限，与技能安全线「业务仓只读」冲突；v1 范围失控（§3.1） |
| 回执 `summary_text` 塞全量证据 JSON | 重踩 A-18「未声明形状塞散文字段」老路；体积上限不明（§6 引言） |
| 对象存储（MinIO）转正存证据 | 长期正确（v3 外环可再议），v1 新增部署与凭据面，与十行盘子不配 |
| 证据只留 N 轮 / 独立孤儿分支 | git 删目录不瘦身、检索变差；体积已被最小集+摘录上限封顶，问题不存在 |
| 残留清扫守护进程 | 多一个要维护的活体，且它也要碰 docker 权限——问题又绕回来（§5.4） |

---

## 11. 风险与验证点

1. **【第一验证项】worker 容器 Docker 能力**：§5.2 的前提。实测不过则联调执行整体
   改道 runner 轨，v1 工程量重估。落地前先做，别的都排它后面。
2. **回执口径**：materialize 回执的 `team_count` 会含测试团队而 `repositories`
   只列计划仓——语义上「计划涉及的仓」vs「编制的团队数」本就不同，接受；控制台文案留意。
3. **materializer 对「无任务团队」的容忍**：预期无影响（它按计划铸任务，多出的团队
   只是闲置），落地时用一次 materialize 实测确认，不臆断。
4. **每个项目都会带测试团队**：v1 的开关是全局档案。若出现「小项目不想要」的真实
   需求，再经 `TopologyPolicyDraft` 加 per-project 关闸，不预做。
5. **AgentTeams 侧资源共享**：测试仓的 Team/rooms 与业务仓同理跨项目共享（A-8 语义），
   多项目并发用一支测试团队时的排队表现待观察——与业务团队是同一个问题，不新增。
6. **宿主机资源**：并发上限（§5.3）要在环境定义里给出默认值并写明依据，
   不许「上限=没写」。

---

## 12. 备注

本稿按要求独立盲写。项目内已存在另一份测试团队路线 spec（未读）；两稿宜对读后再
定案，若结论相同则互为佐证，若相异则差异点即是真正要裁决的问题。

裁决过程记录：八项裁决经 2026-09-01 拷问会逐项审定；其中裁决 5（并发）推翻了
串行推荐、裁决 7（证据）经重开后以代码事实改版，均以用户裁定为准。
