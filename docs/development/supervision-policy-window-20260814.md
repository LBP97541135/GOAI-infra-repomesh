# 监管策略只有一个设定窗口，而窗口两侧各有一道门

- 日期：2026-08-14
- 状态：**待裁决**。方案已选定（下文 C），但尚未动工。
- 关联：`web-to-frontend-batch5-20260814.md` §3（迁移 5-1b），本文取代其中「落点」一节的初步设想
- 触发：迁移 5-1a 落地后摸后端时发现

---

## 0. 一句话

设监管策略要求仓库**已经**有 AI 成员，而 AI 成员是**开始派活时才创建**的；派活同时会把策略钉成
「全自动 + 零卡点」，而全仓**没有任何接口能改一份已存在的策略**。于是可设定的窗口只有
「仓库已建团、该 issue 尚未派活」这一段。

---

## 1. 为什么非解决不可

审核台自迁入起一直是空的（`project.human_review_requests` = 0 行）。原因不是没人用：

- 审核单由项目的 `required_checkpoints` 触发；
- 活体库 14 个项目里 **12 个是 `auto` + 零卡点 + 零授权**；
- 控制台**没有任何地方**能设置它。

迁移 5-1a 已经把这个事实显示出来了（issue 详情页的「监管策略」段）。5-1b 是让它可设。
不做这一步，前面搬进来的审核台与账号管理都只是摆设。

---

## 2. 死结

### 2.1 设策略的接口不建团，只解析

`POST /projects/automatic-topologies` → `CreateAutomaticProjectTopology.execute`
（`modules/project/application.py:266-330`）**从目录里解析已有 agent**，不创建任何东西。
它的前置检查逐条会拒：

| 检查 | 不满足时抛 |
| --- | --- |
| 组织内恰好 1 个 active organization leader | `organization requires exactly one active organization leader` |
| 每个仓库恰好 1 个 active repository leader（且归属该 org leader） | `repository {id} requires exactly one active repository leader` |
| 每个仓库 ≥1 个 active worker | `repository {id} requires at least one active worker` |

### 2.2 那些 agent 是派活时才被创建的

`EnsureProjectAgentTopology.ensure`（同文件 `:206` 起）对每个仓库调
`self._teams.provision(...)` —— 这才是造出 repository leader 与 worker 的地方。
它的唯一调用点是
`modules/repository_intelligence/application/discovery_materialization.py:447`，
即**物化**路径。

### 2.3 而它顺手把策略钉死

同一个 `ensure` 的类文档（`:190-193`）写得很清楚：`execution_mode` 与
`required_checkpoints` **刻意留在默认值**（`auto` / 空），「一个在通往干活的路上顺手建的
拓扑，不是决定监管策略的地方」。

配套事实（迁移 5-1a 的审查逐条核过）：

- `project.agent_topologies` 有 `UniqueConstraint("project_id")`，二次写抛
  `ProjectTopologyConflict`；
- `modules/project/infrastructure.py` 的 `save()` **只写** `operational_status` 与 teams，
  完全不碰 `execution_mode` / `required_checkpoints` / `human_grants`，且在本模块内无调用方；
- 全仓**没有** PATCH/PUT 路由。三个 topology 相关路由只有两个 `POST`（创建）与一个 `GET`。

### 2.4 合起来

```
仓库没建团        →  设不了策略（没有 agent 可解析）
一旦物化          →  agent 有了，但策略已被钉成 auto + 零卡点，且改不动
```

**可设定的窗口 = 仓库已建团 ∧ 该 issue 尚未物化。**

好消息：第一批迁移的「建团」入口（仓库页，`POST /repositories/{id}/agent-team`）正好能把
仓库送进这个窗口——它建的就是 repository leader + worker，而且**不建拓扑**，所以不会误关窗。

---

## 3. 三个方案与裁决

### A. 纯前端，要求先建团

入口只在「无拓扑 ∧ 计划里的仓库都已建团」时出现。

**问题**：出现条件苛刻且难解释——按钮时有时无，而「为什么现在没有」需要用户理解
2.1~2.3 整条链。

### B. 让策略搭物化的车（**已否决**）

给 `POST /issues/{issue_id}/discovery/materialize` 加可选的
`execution_mode` / `required_checkpoints` / `human_grants`，一次调用搞定。

**否决理由：鉴权不对称，这会是一个权限绕过。**

| 端点 | 守卫 | 出处 |
| --- | --- | --- |
| `POST /issues/{id}/discovery/materialize` | `dependencies=[ACTION_TOKEN]`（**全控制台共享**的动作令牌） | `repository_intelligence/api/discovery_chain.py:469-472` |
| `POST /projects/automatic-topologies` | `_account(request)` + `if not actor.is_admin: 403` | `api/human_control.py:473-480` |

搭车等于让任何持有共享动作令牌的调用方设定监管策略，把后端刻意设的 `is_admin` 门槛
绕过去。**为省一次点击拆掉权限边界，不划算。**

（若将来确实要合流，正确做法是让物化路径也认会话身份，而不是让策略降到动作令牌的级别。
那是独立立项。）

### C. 两次调用，前置失败如实报（**选定**）

- **入口位置**：迁移 5-1a 那个「监管策略」段的 `absent`（404）分支。那段文案现在写着
  「配置入口随迁移 5-1b 迁入」，承诺就落在那儿，兑现在同一处最自然；且 404 本身就是
  「窗口还开着」的准确判据（比按轮次数猜可靠）。
- **提交路径**：弹窗选好 → `POST /projects/automatic-topologies`（**管理员会话**，
  `sessionRequest`，不带 `Authorization` 头）→ 成功后本段从 `absent` 翻成 `ready`。
- **前置不满足**：后端会答 422 并给出 2.1 表里的原文。界面**原文上抛**，另起一行译成
  可执行的下一步：「这些仓库还没建团，先去仓库页建团」+ 链接。不要把它归并成「设置失败」。

**C 的取舍**：多一次点击、多一个前置，换来鉴权边界完整 + 失败可自助解决。

---

## 4. 界面形状由后端域不变量决定，不是设计偏好

`ProjectAgentTopology.__post_init__`（`modules/project/domain.py:265-277`）：

| 执行方式 | `required_checkpoints` | `human_grants` |
| --- | --- | --- |
| `auto` | **必须为空**，非空即拒（`automatic projects cannot require human checkpoints`） | — |
| `supervised` | **≥1**，为空即拒（`human-controlled projects require checkpoints`） | **≥1**，为空即拒（`human-controlled projects require a human grant`） |
| `manual_controlled` | **必须等于全部六个**，少一个即拒（`manual-controlled projects require every human checkpoint`） | 同上 ≥1 |

所以**三档不是程度递进，是三种形状**。「一个下拉框 + 一排自由勾选的复选框」每次提交必 422。

界面必须：

- 选 `auto` → 整个卡点区禁用（并说明为什么：全自动带卡点后端直接拒）；
- 选 `manual_controlled` → 六个自动全选并锁死；
- 只有 `supervised` → 才是真正的自由勾选。

另有两条同源约束（`:277-281` 与 `:252-262`）：

- `human_grants` 按 `(human_principal_id, repository_id)` 判重，同一人可有多条不同范围的授权；
- `repository_teams` 至少一支、一仓一队、同一 agent 不得跨队。

补充事实：非 `auto` 时 `exception_escalation` **恒需人工**，哪怕它不在 `required_checkpoints`
里（`api/human_control.py` 的 `requires_human_checkpoint`）。这一条要在界面上说出来，
否则用户会以为不勾它就不会被打断。

---

## 5. 尚未裁决 / 需要拿主意的点

1. **是否接受 C 的前置**（用户要先去仓库页建团）。若不接受，替代路径是给后端加一个
   「更新已有拓扑的监管策略」的端点——那能把窗口从「物化前」放宽到「任何时候」，
   是更彻底的解法，但属于后端立项，且要想清楚改一个**正在跑的**项目的卡点意味着什么。
2. **授权人选择器要不要显示人名**。要显示就得调 `GET /auth/accounts`，而它只有管理员能调
   ——好在 `POST /projects/topologies` 本来就要 `is_admin`，所以这里查账号表不会白查
   （这与 5-1a 的取舍不同，5-1a 那一段非管理员也要看，故只显示短 id）。
3. ~~**`organization_id` 与 `repository_ids` 的来源**~~ —— **已验，见下节 §5.1。结论不乐观：
   物化前拿不到仓库 id，只有仓库名，且能推出的仓库集合与物化实际会建团的集合不保证相等。**

---

## 5.1 补验（08-14）：物化前拿不到 `repository_id`，只有仓库名

`POST /projects/automatic-topologies` 要 `repository_ids: list[UUID] (min_length=1)`
（`api/human_control_models.py:60`）。物化前这两个字段各自的处境：

### `organization_id` —— 拿得到

issue 详情读模型对未物化 issue 有专门的回落：没有轮次也没有拓扑时，取「开这个 issue 的
agent 所属组织」（`read_models/service.py:776-782`）。那段的注释写明了理由——没有这个回落，
工作区过滤会把所有未物化 issue 静默丢掉。所以这一栏在窗口期内非空。

### `repository_ids` —— **拿不到 id**

issue 详情的 `repositories` 由两个来源并集而成（`service.py:752-759`）：

| 来源 | 物化前有没有 |
| --- | --- |
| 各轮次 `plan.batches` 里的 planned repository | **无**（轮次是物化产物） |
| `topology.repository_teams` | **无**（拓扑正是我们要建的东西） |

**所以窗口期内 `IssueDetailView.repositories` 恒为空数组。** 不能从这里取。

发现链投影里确实有仓库，但**全部是名字**，一个 id 都没有：
`DiscoveryEffectiveTier.repository: string`、`ConfirmationResultView.repository: string`、
`DiscoveryAdjustmentRecord.repository: string`（`contract.ts:1040 / 982 / 994`）。

### 那后端自己是怎么拿到 id 的

它也是靠名字查出来的。`materialize` 取
`repositories = tuple(sorted({node.repository for node in plan.task_dag}))`
（`discovery_materialization.py:206`），再在 `_ensure_topology` 里
`by_name = {profile.name: profile.id for profile in await self._catalog.list()}`
按名字解析（`:434-438`）。

### 于是方案 C 多了一步，且这一步不精确

前端要自己做同一件事：调 `GET /console/repositories`（`api/grid.ts:45`
`fetchConsoleRepositories`）拿到 `ConsoleRepositoryView{ repository_id, name }`，
按名字把发现链的仓库名解析成 id。两处必须注意：

1. **两套凭据会在同一个弹窗里同时出现。** 仓库目录走**动作令牌**（`api/client.ts`），
   建拓扑走**管理员会话**（`api/auth.ts` 的 `sessionRequest`）。这不违反「双凭据不可混用」
   ——那条纪律说的是别给某个端点带错凭据——但代码里要写明白，否则下一个人会以为其中一处
   是笔误而「修正」它，然后撞上静默 401。

2. ⚠ **前端能推出的仓库集，和物化实际会建团的仓库集，不保证相等。**
   后端用的是 `plan.task_dag` 里的仓库（真正要动的），前端只能用 `effective_tiers`
   （分档结果）——`task_dag` 的内容前端拿不到，读投影只给
   `integration.task_dag_count` 一个计数（`contract.ts:1048`）。
   前者通常是后者的子集，于是按 `effective_tiers` 建拓扑会**要求比实际所需更多的仓库
   先建团**，把本已苛刻的前置卡得更死。

   后端对「名字不在 catalog」是**静默跳过**（`:437` 的 `if name in by_name`），
   只有全部落空才报错。前端不应照抄这个静默：解析不到的名字要**列出来**，否则用户
   会看到一个自己没选过的仓库集合，且不知道少了谁。

**这条改变了方案 C 的成本估计**：原以为 C 只是「多一次点击」，实际还多一次目录查询、
一段名字解析、以及一个集合可能偏大的既知不精确。这一点在动工前值得和「给后端加更新端点」
（§5 第 1 条的替代路径）重新比一次——那条路可以让前端在**物化之后**再设策略，
那时 `repositories` 有了真 id，上面整段麻烦全部消失。

---

## 6. 附：本文每条断言的出处

| 断言 | 出处 | 怎么验的 |
| --- | --- | --- |
| 设策略接口不建团、只解析 | `modules/project/application.py:266-330` | 通读 `execute`，无任何 provision 调用 |
| agent 由物化路径创建 | 同文件 `:206` 起 + `discovery_materialization.py:447` | `ensure` 内 `self._teams.provision`；grep 该 provisioner 唯一调用点 |
| 策略被刻意留默认值 | 同文件 `:190-193` 类文档原文 | 直读 |
| 无更新端点 | OpenAPI 路径表 + `infrastructure.py` 的 `save()` | 列全部 `projects/*` 路由；读 save 实现 |
| `project_id` 唯一约束 | `project.agent_topologies` 表定义 | `\d project.agent_topologies` 实查 |
| 物化端点走共享动作令牌 | `discovery_chain.py:469-472` | 直读 `dependencies=[ACTION_TOKEN]` |
| 建拓扑端点要管理员会话 | `api/human_control.py:473-480` | 直读 `_account` + `is_admin` |
| 三档域不变量 | `modules/project/domain.py:265-277` | 直读四条 `raise` |
| 12/14 项目为 auto + 零卡点 | 5533 库 | `select execution_mode, required_checkpoints, count(*) ... group by` |
| 审核单 0 行 | 5533 库 | `select count(*) from project.human_review_requests` |
| 物化前 issue 的 `repositories` 恒空 | `read_models/service.py:752-759` | 直读并集的两个来源，二者都是物化产物 |
| `organization_id` 物化前非空 | 同文件 `:776-782` | 直读三级回落，末级取开单 agent 的组织 |
| 发现链投影只有仓库名没有 id | `contract.ts:982 / 994 / 1040` | 通读三个带 `repository` 的接口，字段类型均为 `string` |
| 后端按名字查 catalog 解析 id | `discovery_materialization.py:206 / 434-438` | 直读 `node.repository` 与 `by_name` 映射 |
