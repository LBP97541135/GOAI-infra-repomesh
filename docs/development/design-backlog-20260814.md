# 设计待办

- 建立：2026-08-14
- 用途：查证属实、但**不在当前批次范围内**的能力缺口。每条都写明「现在是什么样」
  与「做的话要动哪里」，避免下次重新查一遍。
- 规矩：**只收查证过的**。凭印象觉得缺的东西不进这份表。

---

## D-1 新建 issue 时上传文件（PRD 文档等）

**现状**：`NewIssueModal` 只有一个多行文本框。底部那个 📎 按钮点下去弹一句
「附件（PRD 文档等）为二期能力」（`components/NewIssueModal.tsx:154`）——
它是个占位，不是入口。

全前端搜 `type="file"` / `FormData` / `upload`：**零命中**。连上传的地基都没有。

**做的话要动哪里**：
- 后端要有对象存储写路径与一个附件端点（MinIO 已在跑，`repomesh-minio-forwarder`）
- 发现链的分析步骤要能把附件内容作为需求文本的补充输入
- 契约要加附件的读投影（列表、下载地址、大小、类型）

**为什么现在不做**：与监管策略这条线无关，且它牵动发现链的输入契约。

---

## D-2 「一键生成 PRD」并产出可编辑文档

**现状**：分两半看——

| 这件事 | 有没有 |
| --- | --- |
| 从一句话自动分析出结构化方案（候选仓库 → 分档 → 任务图） | **有**，就是发现链前四步。`web/` 的 `PrdPlanner.tsx`（565 行、四步、不落库）正是被它取代的，且发现链落库、有分档审批、有幂等 |
| 产出一份人能打开、能编辑的 PRD **文档** | **没有** |

**做的话要动哪里**：需要先回答一个产品问题——这份文档是**发现链的输入**
（人先写 PRD，再让系统分析）还是**发现链的输出**（系统分析完生成 PRD 给人确认）？
两个答案会导出完全不同的数据流向，和 D-3 的关系也不同。

---

## D-3 规格（spec）生成后人可修改

**现状**：**看得到，改不了。**

- 看：`RepositoryPlanView.spec`（`api/contract.ts:310-320`）投影了完整规格——
  目标、验收标准、允许改的路径、禁止碰的路径、要跑的测试，外加状态与修订号。
- 改：`specification` 模块有 7 个 py 文件，**没有 api 目录、没有任何 HTTP 路由**。
  全仓没有规格的写端点。

**连带**：`HumanControlAction.EDIT_SPECIFICATION` 是一个**声明了权限、
但被守卫的那个功能不存在**的动作——见 D-4。

**做的话要动哪里**：
- `specification` 模块加 api 层与写用例（改哪些字段？改了之后修订号怎么走？
  已 `approved`/`frozen` 的还能不能改？）
- 写路径必须过 `authorize_human(action=EDIT_SPECIFICATION)`，否则这个动作继续是空的
- 规格卡点（`specification/application.py:208`）与「人改过规格」的关系要定：
  人改完是否要重新走一次卡点？

---

## D-4 两个控制动作是空的

**现状**：`authorize_human` 全仓仅两个调用点，覆盖不到七个动作里的两个。

| 动作 | 被强制执行吗 | 执行点 |
| --- | --- | --- |
| `approve_checkpoint` / `request_changes` | ✅ | `checkpoint_control.py:84-88` |
| `pause_project` / `resume_project` / `cancel_project` | ✅ | `lifecycle_control.py:25-29` |
| **`view_decisions`** | ❌ 全仓只在 `contracts.py:39` 的枚举定义里出现 | 无 |
| **`edit_specification`** | ❌ 同上（`contracts.py:45`） | 无 |

**当前批次的处置**（已写进 `supervision-policy-design-20260814.md` §4.5.1）：
「全权」角色不含 `edit_specification`；逐项配置里两项保留但标注
「该动作暂无对应功能」。**界面不假装它们存在。**

**做的话**：`edit_specification` 随 D-3 一起落地；`view_decisions` 要先想清楚
它该守卫什么——审核台可见性现在按人查（`list_for_human`），若改成按动作查，
是收紧现有行为，得先确认没有人依赖当前的宽松。

---

## D-5 改一份已存在的监管策略

**现状**：不可能。`project.agent_topologies` 有 `project_id` 唯一约束，
且全仓没有任何更新端点（三个 topology 路由 = 两个 `POST` + 一个 `GET`）。

当前批次的方案把「从来没人被问过」解决成「**被问过一次**」，改主意仍然做不到。

**做之前必须先回答的产品问题**（这是它一直没动的真正原因，不是技术难）：

> **中途给一个跑了一半的项目加卡点，已经跑过去的步骤算不算数？**

例如项目已经过了「验证」那一关（当时无人监管、自动放行），现在补一个「交付要人审」。
那么这一轮交付时，要不要回头把验证也重审一遍？还是只管往后？

不定这一条，端点设计不出来——它决定改完之后系统该做什么。

**连带**：不做这条，当前批次建下的项目也升不了级（策略在建档案那一刻定死）。

---

## D-6 手工组队（迁移 5-3）

**现状**：`web/src/TeamSetup.tsx`（61 行）能自由命名、任选 Leader + Worker 组队；
`frontend/` 已迁的建团是按仓库确定性铸名（`rm-team-{repository_id.hex}`）、一仓一团。
**两者语义不同但用途重叠。**

**先回答再动手**：控制台需不需要自由组队？如果答案是「日常运营不需要，只有装机时才用」，
那它就该留在装机面，`web/` 也就不必删干净。

---

## D-7 DAG 版本对比

**现状**：`web/src/api.ts` 有 `planDiff`（`GET /plans/{id}/diff`），**无调用方**。
`frontend/` 里没有对应能力。

见交接文档 §5.3-7。

---

## D-8 「规格」卡点没有可达的触发点 ⚠ 缺陷

**发现于**：监管策略施工第 2 步的实测（2026-08-14）。

**现状**：`ProjectCheckpoint.SPECIFICATION` 配上去也不会停。三环查证：

1. 唯一 evaluate 在 `specification/application.py:208`，被
   `if specification.kind is not SpecificationKind.TASK:` 守着；
2. 全仓 `publish_to_context` 唯一调用方 `integrations/orchestration.py:129`
   发布的是 `kind=TASK`（`:109` 明写）→ 守卫恒假 → 卡点被跳过；
3. `change_orchestration/application.py:259` 造的 `ENGINEERING` 规格，
   此后只被 log（`:276`）和塞进视图（`:482`），**从未 approve、从未 publish**。

**实测佐证**：`supervised` + `["specification","delivery"]` 的项目物化返回 200、
审核单 0 行；换成含 `repository_scope` 的组合返回 409、审核单 1 行。

**当前处置**：监管策略的第二档预设已改为「仓库范围 + 交付」；界面仍列出「规格」
但标注「当前无可达触发点」（见 `supervision-policy-design-20260814.md` §4.3.1）。

**做的话要动哪里**：要么让 ENGINEERING 规格走一次 approve + publish（那样卡点自然可达），
要么把守卫的条件改对。**先回答**：一个项目的「规格」该在什么时刻定稿到需要人点头？
是每个仓库的工程规格，还是项目级的那一份？这决定改哪一头。

**连带**：与 **D-3**（spec 可编辑）强相关——如果人能改规格，那"改完要不要重新过卡点"
和这条是同一个问题的两面。

---

## D-9 幂等指纹对 `frozenset` 字段跨进程不稳定

**现状**：`shared/idempotency.py` 的 `command_fingerprint` 用 `asdict` +
`default=str`，`frozenset` 不被展开，落成 `str(frozenset)`；而 `Enum.__hash__`
哈希成员名、受 `PYTHONHASHSEED` 影响。实测 5 个 seed 出 3 个不同摘要。

**不是本批引入的**——`POST /projects/topologies` 一直走这条路径。

**当前不可达**：`EnsureProjectAgentTopology.ensure` 里 `store.get(project_id)`
早返回在前，同一 key 只可能对同一 project。但监管策略这批让**非空 frozenset
第一次流经这条路径**，所以记账。

**做的话**：`frozenset` 序列化前先排序成 list。改动小，但要确认不会让存量幂等键失效。

---

## D-10 一条既有测试失败（与本批无关，但一直红着）

`tests/api/test_issue_discovery.py::test_the_chain_walks_four_steps_and_lands_on_one_snapshot`
—— `batch_count` 实际 2、断言 1。

**已确认早于本批**：用 `git archive HEAD` 解出纯净 `3c3b1394` 的树单跑这一条，
**同样失败**。

放这里是为了让下一个看到红灯的人不必再查一遍。

---

## D-11 `handoff_docs` 表从来没有被创建过 ⚠ 缺陷

**发现于**：监管策略第 7 步端到端验收（2026-08-14）。物化成功的那一刻，日志里有：

```
Failed to generate handoff documents
UndefinedTableError: relation "repository_intelligence.handoff_docs" does not exist
```

**异常被吞掉，接口照样答 200。**

三环查证：

| 查什么 | 结果 |
| --- | --- |
| 模型在不在 | 在。`repository_intelligence/infrastructure/models.py:101` 的 `__tablename__ = "handoff_docs"` |
| 有没有迁移建它 | **没有**。`grep -rl handoff_docs migrations/versions/` 零命中 |
| `migrations/env.py` 注册了吗 | **没有**。所以 `alembic autogenerate` 也永远发现不了这张表缺失 |
| 5533 联调库里有吗 | **没有**。`to_regclass(...) is null` → `t`（只读查询确认） |

第三行是这个缺陷能潜伏这么久的原因：**漏注册让自动检查失明**，而异常被吞让运行时沉默。
两层遮蔽叠在一起，于是「交接文档生成」这个功能大概从来没有真正工作过，也没人发现。

**与本批无关**——本批一行代码都没碰它，是端到端走到那一步才撞见的。

**做的话要动哪里**：加一个建表迁移 + 在 `env.py` 的 `_REGISTERED_MODELS` 里注册。
**但先回答**：这个功能现在还要不要？如果要，它产出的交接文档给谁看、在哪显示？
一个从未工作过的功能，可能它的需求本身已经变了。
