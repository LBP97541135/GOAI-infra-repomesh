# main ↔ feat/console-v2 功能级合并分析报告

- 日期:2026-08-14
- 结论:**能合,且值得合——互补性远大于冲突性**
- 性质:合并前分析 + 执行方案。本文的每一条事实都经 git 对象核实(方法:`git merge-tree` 试合并、
  双向三点 diff、迁移文件全文对读、origin/main 文档与前端源码通读),非印象式判断。

---

## 0. 分叉事实

| 项 | 值 |
| --- | --- |
| 分叉点(merge-base) | `80ba960` |
| 本分支领先 | 292 提交(243 非 merge:78 fix / 75 feat / 71 docs / 其余) |
| main 领先 | 5 提交(2026-08-12 ~ 08-13) |
| 总差异 | 247 文件,+53,698 / −515 行 |
| 双方共同触碰 | 26 文件,其中 **13 个真实内容冲突**(`git merge-tree` 实测) |
| 迁移链 | `20260811_0018` 后分叉:main 走 2 个,本分支走 9 个(revision id 不撞,但成 alembic 双头) |

main 的 5 个提交:

| 提交 | 内容 |
| --- | --- |
| `1a02a16` | `web/` 项目工作区 PRD 流程(纯前端) |
| `2e24838` | 平台安装与仓库接入产品化 |
| `8a8b13b` | 平台安装向导 + 团队配置 |
| `b451f5e` | merge:PRD workspace 与 platform setup 集成 |
| `f6fc082` | repository-intelligence:单一依赖图作为 plan 事实源(PR-1~5)+ 交付策略 |

---

## 1. 两边功能面对照

一句话概括:**main 长的是"进场与规划层",本分支长的是"运营与交付层"**。同一个产品在两条线上
各自长出了不同的器官,不是两个抢地盘的版本。

| 层面 | main 新增 | 本分支(console-v2) | 关系 |
| --- | --- | --- | --- |
| 数据基座 | **单一依赖图**:`PlanGraph` 聚合(nodes/edges + execution_batches/contracts/task_dag 三投影列,类型层保证「读图 ≡ 投影列」);`plan_snapshots.graph_edges` 从死列变活;replan 支持 `preview/commit` + 版本 diff 端点 | **发现链**:同一张 `plan_snapshots` 表加 `discovery` 列(迁移 0023),读投影 + 四个写触发 + 分档审批(契约 v0.4) | **互补**——同表不同列;一个管「图怎么算」,一个管「人怎么走流程」 |
| 交付 | `delivery.delivery_policies` 表 + `DeliveryPolicy`(auto_merge / base_branch / required_checks / required_approvals / contract_gate),**环境变量 → 组织 → 仓库**三级回退,注入 `PlanDeliveryFinalizer` | 交付归档(`delivery_archives`,0019)、拒收(refusal,0026)、`deliveries` 读端点、scm revert/rework/recovery | **正交互补**——配置面 vs 运行面 |
| 平台进场 | `/api/v1/setup/*`:status 9 项就绪检查、coding-agent 探测、组织级仓库批量 onboard(复用 human_control 的 agent-team onboard,幂等键 `repository-onboarding:{repo_id}`) | `/repositories/scan-org\|scan-repo\|scan-tasks/{id}`:扫描任务式接入(console.py);`identity_access/organizations`(0021) | **功能重叠**——同一件事两个入口,端点路径不撞、可共存,产品上需收敛 |
| 前端 | `web/`(React+Vite,无 Tailwind,依赖全 latest 未锁):SetupWizard / TeamSetup / ReviewWorkbench / PrdPlanner 四步向导 / Workspace / PlanFlowTimeline | `frontend/`(React 19 + Vite 8 + Tailwind 4 + oxlint,版本锁定):v2 控制台——issue 详情、发现面板、图形化 DAG、重新派工、拒收、验收 8/8 实走 | **产品级重复建设**——目录不冲突,入口分裂 |
| 装配层 | `platform_setup_router` 1 个新路由;settings 加 `REPOMESH_REPLAN_AUTO_COMMIT`、delivery 策略默认、`GITHUB_APP_PRIVATE_KEY_BASE64` | 9 个新路由(read_models / deliveries / issues / rooms / grid / round_dispatch / identity_console / issue_discovery / console_repositories) | **纯加性**——文本冲突,机械可解 |

### 1.1 平行演化的铁证:双方独立修了同一个 bug

计划快照序列化曾用 `dict(c)` 处理 slots dataclass,抛 `TypeError` 且被 `except Exception`
吞掉——**生产上计划快照从未真正落库**。main(`f6fc082`)和本分支**各自独立发现并修复**
(都改成 `dataclasses.asdict`;本分支 `change_orchestration/application.py:105` 还留了成因注释)。
两条线连修的 bug 都一样,说明底层认知一致,合并没有方向性风险。

---

## 2. 四个必须人工裁决的硬冲突

### 硬点 1:同一个数据库约束的两种改法(最硬)

双方都动了 `project.repository_agent_teams` 的唯一约束 `uq_project_agentteams_team_name`,
动机相同(修「Team 误绑拓扑行」缺陷,让 Team 归属仓库),裁决不同:

| | main `20260812_0019` | 本分支 `20260812_0024` |
| --- | --- | --- |
| 操作 | drop UNIQUE → 建普通索引 | drop UNIQUE → 建复合 UNIQUE `(project_id, agentteams_team_name)` |
| 语义 | 完全放开:长命仓库 Team 可被任意项目复用 | 跨项目可复用,但**同项目内不同仓库必须不同 Team**(防两仓库流量塌进一个房间) |
| 配套 | `repository_agentteams_team_name(repository_id)` 按仓库确定性铸名;setup 向导批量 onboard | reconcile 收养仓库真实 Team、写回名字(`project_topology.py`);不重写存量行 |

**裁决建议:保留本分支复合约束 + 采纳 main 的确定性命名与复用逻辑。**
理由:复合约束兼容 main 的全部场景——main 的确定性命名保证「不同仓库 → 不同名字、同仓库跨项目
→ 同名字」,复合唯一恰好允许同名出现在多个项目行、禁止同项目内撞名,即 main 想要的复用 +
本分支想守的防线,二者同时成立。完全放开(main 版)反而丢掉防线且无所得。

⚠ 执行细节:两个迁移同名操作互相打架(先跑 0024 再跑 main 0019,复合约束会被 drop,main 语义
胜出;反序则 main 0019 建的是 index 而非 constraint,0024 的 drop_constraint 直接报错)。
必须重写而不是简单排序,见 §4。

### 硬点 2:迁移链分叉

- main:`0018 → 20260812_0019(reuse teams) → 20260812_0020(delivery_policies)`
- 本分支:`0018 → 20260811_0019 → … → 20260812_0027`(9 个)

revision 字符串不撞车,但 alembic 出现双头。方案见 §4。

### 硬点 3:`repository_intelligence` 管线缝合(工作量主体)

同一批端点(`/requirement-analysis`、`/discovery`、`/confirmation`、`/integration`、
`/bridge/materialize`、`/bridge/replan`)两边同时演进:

- main:数据层革命——`contracts.py` **R100 包化**为 `contracts/`(graph.py / diff.py /
  integration.py / repository.py),`materialize()` 走 `plan.graph or plan_to_graph(plan)` →
  `normalize_plan` → 写完整 `graph_edges` + `integration_method`;`replan(mode)` 影响分析切到
  最新快照方案层图。
- 本分支:交互层包装——发现链(issue_intake / discovery_chain / discovery_materialization)、
  console 扫描接入、快照版本语义 v0.4。

需逐函数缝的文件:`change_orchestration/application.py`(main +434 行 graph/policy 主题 vs
本分支 +319 行 round/refusal 主题,**主题正交**)、`plan_integration.py`(main 218 vs 本分支 77)、
`plan_snapshot_store.py`(main 加 `plan_graph_from_snapshot`/`get_latest_graph`/`next_version`,
本分支加 discovery 物化,**加性重叠**)。`contracts.py` 因 main 侧 R100 移动,本分支的修改要
手工跟到 `contracts/repository.py`。

两条红线合并后都必须成立:main 的「任何时刻读图 ≡ 投影列」+ 本分支的「`plan_snapshots`
唯一生产方是 repository_intelligence」。

### 硬点 4:前端归一(可后置,不阻塞合并)

- `web/` 的 PrdPlanner 四步向导与本分支发现面板是**同一条管线的两个 UI**,本分支版本更完整
  (审批、留痕、幂等、读投影;web 版文档明言「不做 PRD 落库」)。**产品上以 `frontend/` 发现面板为准。**
- main 的 `PlanFlowTimeline` 只消费 `plan.graph`(边带 interface/agreement),比控制台 C-2 DAG
  现用的 v0.2 §5.4 数据源更丰富,**值得移植吸收**。
- SetupWizard / TeamSetup / ReviewWorkbench 是本分支没有的功能,短期**双前端并存**
  (web = 装机向导,frontend = 运营控制台),长期移植进 frontend 收敛。
- main 侧自身线头(合并时不必处理,记账即可):`DagGraph.tsx` 是孤儿组件(无 import)、
  `api.planDiff` 已封装无调用方、vite 代理写 8000 而文档写 8001。

---

## 3. 13 个真实冲突文件分级

| 级别 | 文件 | 冲突性质 |
| --- | --- | --- |
| 机械加性(5) | `api/router.py`、`bootstrap/container.py`、`settings.py`、`modules/delivery/__init__.py`、`change_orchestration/contracts.py` | 双方各自追加路由/工厂/配置/导出;并集即可(contracts.py 中 main 加 ReplanMode/diff 字段,本分支加 round/refusal 字段,不相交) |
| 中等(4) | `modules/project/application.py`、`modules/project/domain.py`、`modules/project/infrastructure.py`、`repository_intelligence/api/router.py` | main 加 CreateAutomaticProjectTopology + 确定性铸名;本分支加 organization 接线。语义按硬点 1 裁决对齐 |
| 细缝(4) | `change_orchestration/application.py`、`repository_intelligence/application/plan_integration.py`、`repository_intelligence/infrastructure/plan_snapshot_store.py`、`tests/test_plan_execution_bridge.py` | 同函数双改,逐函数缝合;注意 §1.1 的重复修复取一 |

冲突之外的隐性缝合点(git 不报冲突但语义相关):`integrations/agentteams/project_topology.py`
(双方都改,reconcile vs 确定性命名,git 能自动合但**必须人工复核**)、`contracts.py` 的
rename-follow、`api/human_control.py`(main 的 onboard 复用其处理函数)。

---

## 4. 迁移链收敛方案

1. 本分支链 `20260811_0019 … 20260812_0027` **保持原样不动**(已应用于 8100 验收环境)。
2. main 的 `20260812_0020_delivery_policies` **重排到 `20260812_0027` 之后**
   (改 down_revision;表内容原样保留)。
3. main 的 `20260812_0019_reuse_repository_agentteams_teams` **重写**:去掉
   drop-unique(0024 已以复合约束落定该语义),只保留 `agentteams_team_name` 普通索引的
   创建(按名查找有用、无害),同样重排到链尾。
4. ⚠ 风险声明:重排即改写 main 已发布的迁移历史。**若有任何数据库已按 main 原序执行过这两个
   迁移,该库需要人工对账**(本地已知:5432 活体库谱系本就不符,不在本分支迁移射程内,维持既有
   纪律不动它)。

---

## 5. 合并执行计划

方向:**把 `origin/main` merge 进 `feat/console-v2`**(292 对 5,反向 rebase 不现实),
在本分支解冲突。

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| M1 | 落本报告 + 硬点 1 裁决记录 | 本文件 |
| M2 | `git merge origin/main`,按 §3 分级解 13 个冲突 + 隐性缝合点;`contracts.py` rename-follow | 可编译、可启动 |
| M3 | 迁移链按 §4 收敛 | 一次性 postgres 从空库跑通全链(沿用既有验法) |
| M4 | 定向回归:main 侧新测试(graph projections / plan diff / snapshot consistency / replan modes / delivery policy / repository_intelligence API)+ 本分支受影响面(plan_execution_bridge、delivery、project topology) | 全绿;不跑全量套件(既定纪律) |
| M5 | 前端不动(`frontend/` 无冲突);`web/` 原样并入 | `tsc -b` 两套各自过 |
| M6 | 合并提交 + 更新交接文档 | — |

前端归一(硬点 4)与仓库接入入口收敛(§1 平台进场行)**不在本次合并范围**,作为合并后的
独立工作项立项。

体量估计:M2~M4 约一到两个专注工作日量级;M1/M5/M6 半日内。

---

## 6. 裁决记录

| # | 决议 | 状态 |
| --- | --- | --- |
| D-1 | `repository_agent_teams` 取复合唯一 `(project_id, agentteams_team_name)` + main 的确定性铸名/复用逻辑;main 0019 重写为纯索引迁移 | **已按建议案执行**(2026-08-14,可复议) |
| D-2 | 迁移链:本分支链不动,main 两迁移重排链尾 | 已执行 |
| D-3 | 快照序列化修复:两边等价,合并取本分支版本(带成因注释) | 已执行 |
| D-4 | PRD 入口以 frontend 发现面板为准;web 双前端短期并存 | 合并后立项,本次不动 |

## 7. 执行结果(2026-08-14)

合并提交 `9780c75`(merge origin/main `f6fc082`)。全过程与 §5 计划一致,补充事实:

- **铸名统一**:双方连确定性铸名都独立实现了(本分支 `rm-team-{hex}`、main `rm-repo-{hex}`)。
  取本分支模板(存量房间用它铸的),main 的模块级函数 `repository_agentteams_team_name`
  改为委托 `RepositoryTeam.canonical_agentteams_team_name`,main 侧调用方零改动。
- **materialize 融合**:v0.4 草稿消费路径保留,两条路径(填草稿 / 开新版本)都写对齐
  `plan_version` 后的 `graph_edges` + 推断 `integration_method`——控制台回合的行与脚本
  save 遵守同一条单图不变量。`set_integration` 为此加了 `graph_edges` 参数(端口 + 存储 + 测试桩)。
- **rename-follow 缺口**:git 把本分支对 `contracts.py` 的修改自动跟进了
  `contracts/repository.py`,但 main 写的包 `__init__.py` 只导出自己认识的名字;
  发现链边界(`IssueIntakeCommand`、`GUI_STEP_OF` 等 14 个名)靠 AST 全量对账补齐。
- **迁移**:新链尾 `20260814_0028`(纯索引)、`20260814_0029`(delivery_policies 原文),
  一次性 postgres 从空库跑通全链,复合约束/索引/新表/快照三列并存确认。
- **测试适配 4 处**:main 的 `StubSnapshotWriter` 补 `current_draft`;main 的
  `/integration` 测试补 v0.4 动作 token;本分支两处快照断言从「按位置」改「按名」
  (normalize_plan 会排序投影、并把契约 consumer 折进图节点——这是 main 的既定语义,
  顺手把「草稿行也写 graph_edges」钉进断言)。
- **回归**:322 个定向测试全绿(两侧套件并集);`frontend/`、`web/` 各自 `tsc -b` 干净。
