# Room-Native Bridge 终局验收 · 并行编排关联计划

> 日期:2026-08-28
> 状态:**已校正,待波次 0 基线提交后下 GO**
> 主计划:[room-native-bridge-final-acceptance-execution-plan-20260828.md](room-native-bridge-final-acceptance-execution-plan-20260828.md)
>(PR 定义、改动范围、验收条款、裁决 D-1~D-12 全部以主计划为准;本文只回答「什么可以同时做」)
> 验收口径:[room-native-bridge-final-acceptance-standard-20260827.md](../room-native-bridge-final-acceptance-standard-20260827.md)
> 执行模式:沿用本线 PR 5 惯例——主脑定界/裁决/验收,子代理按工单实现,逐单亲审 diff、独立复跑、按路径提交
> 可交互编排图:[room-native-bridge-parallel-orchestration-plan-20260828.html](room-native-bridge-parallel-orchestration-plan-20260828.html)(源规范:[overview.workflow.json](room-native-bridge-parallel-orchestration-plan-20260828.overview.workflow.json))

## 0. 一句话

主计划把「PR 5.5 → E0a → V1 → PR 6 → PR 7 → PR 8」写成一条串行链,其中开发与活体验证被混在一起。校正后按真实依赖重排为**波次 0–3 + 一条独占活体队列**:三条开发线最多同时 3 张工单,主脑同时只评审/合并 1 张。双线约 **4–5.5 周**、三线约 **3–4 周**(串行为 6–9 周),人日总量仍为 30–45。唯一开工硬门是**tracked 的波次 0 可执行契约基线**。

### 0.1 校正后总图(文字版)

```text
波次0:提交计划基线 + 冻结契约/fixture/invariants + E0a
   ├─ A:PR5.5A→5.5B ──────────────┐
   ├─ B:PR7核心纵切→PR7完整状态机 ├─► M8→M7 ─► E1 soak ─► E0b ─► V2/Q3b
   └─ C:V1→PR6→PR9+Q2→PR10+Q1/Q3a┘
                    └─ PR8对fake开发→集成 ─┘
```

---

## 1. 过度串行边的判定(逐边审查)

| 主计划中的边 | 真依赖? | 判定依据 |
|---|---|---|
| PR 5.5 → E0a → **V1** | **否** | V1 是单 **Worker** 治理活体,全部走 PR 5 已收口代码;PR 5.5 修的是 **Leader** 契约,零交集。V1 只需要 tracked 基线 + E0a(半天) |
| V1 → **PR 6** | **半真** | PR 6 是纯 Bridge 代码 + 逐字 fixture 测试,开发不需要 V1;只有「自动接单**活体**验证」要借 V1 环境。开发并行,验证顺延 |
| PR 6 → **PR 7** | **否** | Bridge supervisor 与服务端 task_orchestration 文件零交集;「PR 6 后合入」是合并次序偏好,不是开发依赖。**PR 7 是全案最长单项(7–10 人日),每晚开工一天,终点晚一天** |
| PR 7 → **PR 8** | **半真** | PR 8 消费 PR 7 的 HTTP 契约;门禁 #9 本就要求先冻结 producer contracts,`LeaderActionPort` 自带 memory fake。**契约冻结后 PR 8 对 fake 开发**,只有 M7 集成 smoke 等 PR 7 合入 |
| PR 5.5B → **PR 7** | **半真** | PR 7 的核心 domain/application 行为只依赖冻结的 `decomposition_mode` contract,可先用 memory reader 开发;只有 project adapter、真实 mode 读取与最终合入等待 PR 5.5B |

**保留的真依赖**(不可动摇):

```text
tracked 契约基线 ──► 所有开发工单
PR 5.5A ──► PR 5.5B ──► PR 7 集成/合入(leader 模式字段)
契约基线 ──► PR 7 核心开发(memory reader)
契约基线 ──► PR 8 port/adapter/session 开发(memory fake)
PR 5.5A 部署 ──► E1 的 leader 身份开通
PR 6 合入 ──► PR 8 的 supervisor.py 集成(同文件,见 §4)
V2 ◄── {M7, PR 9, PR 10, Q1–Q3, E1, E0b}   ← 显式 join,主计划已写对,不动
```

---

## 2. 波次编排

### 波次 0(第 1 天)— tracked 可执行基线 + E0a(唯一开工硬门)

主脑先把主计划、本文、验收标准与契约基线提交到 Git;所有工单记录同一个 baseline commit。契约不能放 gitignored 文档,必须是 tracked producer contracts/schema + 双端共享 fixture/契约测试。冻结内容:

1. v2 enrollment/binding schema(增 `role`,PR 5.5A 范围),含 v1/v2 round-trip 与 role/room 错误 fixture;
2. leader agent-actions HTTP 契约:`RepositoryAssignmentPackage` / `RepositoryPlanDecision` /
   `RepositoryReviewDecision` 的 wire 形状、结构化错误体与错误矩阵(401/403/404/409/200);
3. leader interface 不变量:planning/review_due 转换、幂等重复响应、DAG 无环/覆盖、evidence 必备字段、rework revision;
4. team `decomposition_mode` view/reader contract(PR 5.5B / PR 7 共用);
5. migration 预留唯一 revision id:PR 5.5B、PR 7、PR 9 各一个;topic branch 先指向自己的基线 head,合并时只重写 `down_revision`。

同日完成 **E0a**(LLM key、单 Worker 身份与 token;delivery 保持关闭)。

> 波次 0 的出口不是「写过半页设计」,而是任一工作树从 baseline commit 都能运行同一组契约 fixture/测试。没有这个结果不得下 GO。

### 波次 1(最多三张 IN_PROGRESS,约 1 周)

| 线 | 工单 | 内容 | 备注 |
|---|---|---|---|
| A(External/服务端) | W-A1 | PR 5.5A → PR 5.5B | **关键路径头部**;先 server role,再 adoption/mode |
| B(LeaderDecision) | W-B1 | PR 7 核心纵向切片 | 对冻结 contract + memory reader 开发;至少交付 planning GET 的可观察行为,禁止只合持久化骨架 |
| C(活体/Bridge) | W-C1 | **V1 → PR 6** | V1 三处对账;PR 6 开发并先合,随后占队列做自动接单 smoke |

等待队列(不是同时开工):W-D1(PR 9+Q2)、W-E1(PR 10+Q1+Q3a)、W-F1(PR 8 非 supervisor 部分)。任一 A/B/C 工单进入评审后,空出的开发槽才能领取下一张。

### 波次 2(最多三张 IN_PROGRESS,约 1.5–2 周)

| 线 | 工单 | 内容 | 同步点 |
|---|---|---|---|
| A | W-A2 | PR 7 完整状态机:plan/review evidence/rework + project adapter + HTTP | 等 PR 5.5B 只为真实 mode 集成/合入,核心实现沿用 W-B1 |
| B | W-B2 | PR 8 对 memory fake 开发(port/HTTP adapter/Codex session/测试);最后接 supervisor | port/session 只等波次 0;`supervisor.py` 集成等 PR 6 合入;M7 等 PR 7 |
| C | W-C2 | PR 9 + Q2 → PR 10 + Q1 + Q3a | Q2 同 collaboration 工单并新增 eligibility reader port;PR 10 在 PR 5.5B/PR 9 read-model 基线上 rebase;Q3a 仅静态审计/观测准备 |

E1 启停/预检脚本可由已空闲的执行线编写,但不计为第四张并发实现工单;leader 真开通等 PR 5.5A 部署。

### 波次 3(串行收口,约 1 周)

```text
M8 smoke(Room 真实消息 + Agents/Teams External UI,独占活体环境)
  → M7 smoke(一 leader 一 worker,独占活体环境)
  → E1 扩六实例(soak)
  → E0b(隔离环境短时开 delivery,三仓白名单)
  → V2 终局验收 + Q3b 运行证据(六前置显式核验后才可开始;取证后关闭 delivery)
```

---

## 3. 活体环境队列(唯一的硬串行资源)

本机只有一套栈(端口、Docker、Matrix、一次性 postgres)。**并行的是开发;活体验证排一条独立队列**:

```text
V1 ──► PR 6 活体验证 ──► M8(Room/UI) ──► M7(Leader/Worker) ──► E1 六实例 soak ──► V2/Q3b
```

排队纪律:占用方结束必须按 PR 4 交接 §7.5 拆环境(按 PID 收尾,`pkill -f` 杀不掉 nohup 链);
下一占用方从干净基线起。环境坑三条全程适用:`MSYS_NO_PATHCONV=1`、控制面与 Bridge 同跑
Windows 宿主、5432 活体库谱系不符不得触碰。

---

## 4. 文件冲突矩阵与合并次序

| 热点文件 | 触碰方 | 次序裁决 |
|---|---|---|
| `src/repomesh_agent_bridge/supervisor.py` | PR 6、PR 8 | **PR 6 先合**,PR 8 在其上开发 |
| `src/repomesh/api/read_models/service.py` / `sources.py` | PR 5.5B(mode/role)、PR 9(room source)、PR 10(runtime fields) | **PR 5.5B → PR 9 → PR 10**;后合者 rebase,按 read-model interface 行为复测,不得只做文本冲突处理 |
| Teams 前端 contract/display/page | PR 5.5B(mode/role)、PR 10(External runtime) | PR 5.5B 先给真实字段,PR 10 统一完成视觉呈现与浏览器实走 |
| `src/repomesh/bootstrap/container.py` | PR 5.5A/B、PR 7、PR 9、PR 10(各自组装段) | 指定**一名 integration owner**逐个接线;每次合入独立复跑全量测试,不得把 constructor/port 变化视为纯机械冲突 |
| `src/repomesh/modules/collaboration/application.py` | PR 9、Q2 | 已并单(W-D1),无冲突 |
| `task_orchestration` eligibility view | PR 9/Q2、PR 7 | Q2 只消费冻结的 `task_orchestration.contracts` reader;不得直查 task schema;若 PR 7 扩同一 view,先改 producer contract 再广播 |
| `migrations/versions/`(链尾 0036) | PR 5.5B(模式字段)、PR 7(leader 产物)、PR 9(timeline) | 波次 0 **预留三个唯一 revision id**;topic branch 以自身基线 head 测试,合并 captain rebase 后只调整 `down_revision` 并跑 upgrade/head 检查 |

> 迁移链是合并串行点,不是开发串行点。revision id 必须在代码中真实存在并可测试;禁止多人临时抢同一个顺序号。integration owner 按 `PR 5.5B → PR 7 → PR 9` 的关键路径优先级串成单 head。

### 4.1 WIP 与评审队列

| 模式 | `IN_PROGRESS` 上限 | 主脑评审/合并上限 | 活体环境占用 |
|---|---:|---:|---:|
| 双线 | 2 | 1 | 1 |
| 三线 | 3 | 1 | 1 |

工单进入 `REVIEW` 就释放开发槽,但不允许领取会修改同一热点文件的新单,直到前单合入并公布新 baseline。主脑每次只亲审一张 diff;评审积压时所有线停止扩大 WIP。

---

## 5. 收益测算

| 模式 | 日历工期 | 说明 |
|---|---|---|
| 主计划串行(单人) | 6–9 周 | 现状 |
| **双线**(两条有效实现线,主脑兼评审) | **约 4–5.5 周** | 推荐稳妥档;评审/rebase/活体占用计入日历,等待单不冒充并行 |
| 三线 | 约 3–4 周 | 需要三条有效实现线 + 主脑持续集成;关键路径 5.5A→5.5B→PR7集成→PR8集成→M7→V2 |
| 单人重排 | 省 15–20% 日历 | V1 提到第 1 天,活体排障等待窗穿插写 PR 5.5A |

**最大单点收益:V1、PR 7 核心和 PR 5.5 同周开工;PR 8 的 port/adapter/session 也只等波次 0,不等 PR 7 完成。**

## 6. 并行红线(什么不许并行)

1. **tracked 契约基线提交前不得开工**(波次 0 是硬前置;gitignored 设计稿不算产物;裁决 D-1~D-12、invariants、fixtures 同批冻结)。
2. **活体环境不得并发占用**(§3 队列;两个会话同时改一批文件的事故本线出过——
   发现编排视野外的写者,后到者退为只读复核,主会话看 `git status` 与 mtime 裁决归属)。
3. **V2 不得因关键路径完成而提前**:六前置(M7、PR 9、PR 10、Q、E1、E0b)显式核验
   (主计划门禁 #10)。
4. **提交纪律不变**:按路径 stage;工作区 M/?? 他线文件不读不动;每工单独立评审后合入,
   不攒大批一次合。
5. **WIP 不得超限**:双线最多 2 张、三线最多 3 张 `IN_PROGRESS`;主脑同时只评审/合并 1 张。
6. **Q3 不得提前宣称完成**:波次 2 的 Q3a 只做静态路由审计和观测准备;最终 mock 零触达必须由 V2 的 Q3b 运行证据确认。

## 7. 工单台账样式(供开工时直接抄)

```text
W-0   基线提交      [波次0·主脑] 依赖: —            验收: 任一 worktree 可跑同一契约 fixtures
W-A1  PR 5.5A+B    [波次1·A线]  依赖: W-0          验收: 主计划 §PR5.5 总验收
W-B1  PR 7 核心纵切 [波次1·B线]  依赖: W-0          验收: planning GET 纵向行为 + memory/Postgres 一致
W-C1  V1→PR6       [波次1·C线]  依赖: W-0+E0a      验收: 三处对账 + AC-03 + PR6 smoke
W-A2  PR 7 完整集成 [波次2·A线]  依赖: W-A1+W-B1   验收: 主计划 §PR7 全部行为
W-B2  PR 8         [波次2·B线]  依赖: W-0;supervisor 等 W-C1;M7 等 W-A2
W-C2  PR 9+Q2      [波次2·C线]  依赖: W-0          验收: PR9 + eligibility reader port + Q2
W-C3  PR10+Q1+Q3a  [波次2·等待] 依赖: W-A1+W-C2    验收: PR10 实走 + traceability + mock 静态审计
W-E1  E1 脚本       [空闲槽]      依赖: W-A1 部署   验收: 一L一W 子集可起,binding 预检就绪
M8 → M7 → E1 soak → E0b → V2/Q3b 按 §2 波次 3 串行收口
```
