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
| P-1 | 未执行（W0a 待活体新栈，另行安排） | | |
| AC-A1~A6 | **PASS**（双档） | in-memory 档：`tests/api/test_issue_materialize_test_team.py` 6 用例全绿（AC-A1/A2 合测、A3 拆 replay/换 key 两测、A4 双断言、A5 逐字段、另含 S-1 守卫序与去重两条冻结规则）；红验证：撤实现 4 红 2 绿（绿的两条守边界，独立于追加成立）。postgres 档：`tests/integration/test_postgres.py::test_postgres_test_team_supply_chain_converges_by_id` 于一次性 postgres:17（alembic head）全绿。AC-A6 对照组：`tests/api/test_issue_materialize.py` 35 用例全绿 | 2026-09-01 |
| AC-B1 | 待 W4 | | |
| AC-B2 | 待 W4 | | |
| AC-B3 | 待 W4 | | |
| AC-C1 | 待 W4 | | |
| AC-C2 | 待 W4 | | |
| AC-D1~D4 | **D4 静态半场 PASS**：`tsc -b` 零错、oxlint 受影响 6 文件零告警；replay 夹具浏览器点检过弹窗（两句冻结文案、双选项、选中态回显）、仓库卡档案回显、TeamsPage「测试团队」徽标正反向。**D1~D3 活体断言留 W4**，本波不冒充完成 | 2026-09-01 |

---

## 备注

- ADR 已议：满足三条件但用户裁定**不立 ADR**，形态裁决以设计稿 §2 + §10 被拒方案表为记录。
- 本文件与设计稿的分工：设计稿说「为什么与怎么建」，本文件说「什么算建成了」。
