# 第五批迁移：让人工审核台真的能收到东西

- 日期：2026-08-14
- 前置：前四批迁移（`cebc8fe`→`9aaebb8`）已完成，见
  `docs/development/handoff-20260814-merge-and-web-migration.md`
- 结论：**`web/` 现在不能删**。它还留着三样 `frontend/` 没有的能力，其中三样里的两样
  卡在同一条链上——不补齐，上一批刚迁进来的人工审核台就是个永远为空的摆设。

---

## 0. 这批要解决的问题，一句话

上一批把**审核台**搬进了控制台。但审核单不是凭空来的：它由项目的
`required_checkpoints`（人工检查点）触发，检查点写在项目拓扑上，而
**控制台里没有任何地方能设置它**。

活体库佐证（5533，08-14 实测）：

| 拓扑来源 | 数量 | `execution_mode` | `required_checkpoints` | `human_grants` |
| --- | --- | --- | --- | --- |
| 控制台物化路径自动建的 | **12** | `auto` | `[]` | 无 |
| `web/` 的 ProjectSetup 建的 | 2 | `supervised` | 有 | 有 |

`project.human_review_requests` 共 **0 行**。不是没人用，是这条路没通。

代码里也是这么写的——`EnsureProjectAgentTopology` 的类文档
（`src/repomesh/modules/project/application.py:190`）原话：

> `execution_mode` 和 `required_checkpoints` 刻意留在默认值。在通往干活的路上顺手
> 建的拓扑，不是决定一个项目监管策略的地方；管理面（`POST /projects/topologies`）
> 仍然拥有那件事。

所以交接文档 §5.2 那句「ProjectSetup 已被 issue 物化路径涵盖」**是错的**，据此删
`web/` 会把审核链掐断。本文取代那一条。

---

## 1. 清点：`web/` 还剩什么没有对应物

搜过 `frontend/` 全目录，`auth/accounts`、`projects/topologies`、`agent-teams`
三个字符串**一次都没出现**。

| # | `web/` 组件 | 能力 | `frontend/` 有没有 | 处置 |
| --- | --- | --- | --- | --- |
| 5-1 | `ProjectSetup.tsx`（402 行） | 项目监管策略：执行方式 + 人工检查点 + 审核人授权 + 仓库团队绑定 | **没有** | **迁** |
| 5-2 | `App.tsx` 里的 `AccountPanel` | 新建本地账号（审核人） | **没有**（只有登录/登出/看自己） | **迁** |
| 5-3 | `TeamSetup.tsx`（61 行） | 自由命名、任选 Leader + Worker 手工组 Team | **没有**（已迁的建团是按仓库铸名、一仓一团，不是一回事） | 待议，见 §5 |
| —— | `PrdPlanner.tsx`（565 行） | 四步方案制定（不落库） | 有，且更完整（发现链，issue 化 + 持久化 + 分档审批 + 幂等） | **删** |
| —— | `DagGraph.tsx`（182 行） | —— | 全仓库无 import，孤儿组件 | **删** |
| —— | `api.ts` 的 `planDiff` | `GET /plans/{id}/diff` | 无调用方 | 与 DAG 版本对比一并立项（交接文档 §5.3-7） |

**5-1 和 5-2 有依赖顺序**：授权一个审核人需要先有这个人的账号
（`human_grants[].human_principal_id` 指向 `identity_access.local_human_accounts`），
所以 **5-2 必须先做**。

---

## 2. 迁移 5-2：本地账号管理（先做）

### 端点

| 方法 | 路径 | 守卫 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/accounts` | 会话 + `is_admin` | 建账号 |
| `GET` | `/api/v1/auth/accounts` | 会话 + `is_admin` | 列账号 |

**凭据走 cookie 会话**，即 `frontend/src/api/auth.ts` 的 `sessionRequest`，
**不是**动作 token。理由见 `auth.ts` 文件头：给 human_control 面的请求带
`Authorization` 头会让后端拿动作 token 去验会话，cookie 有效也 401，且**失败是静默的**。

### 请求体

```
AccountCreate: { username, password, display_name, is_admin=false }
```

### ⚠ 落地前必须先修的一处报错映射

> **更正（08-14）**：本节初稿断言「后端对密码长度没有任何校验」，**那是错的**，
> 提交 `e3e8b5c2` 的提交信息里也照抄了这个错误说法（提交已推送，无法改写，以本节为准）。
> 成因是一条 `grep` 被 `head` 截断，没看到校验那一行。

**校验是有的，而且位置正确。** `LocalAccountService._create`
（`modules/identity_access/local_accounts.py:137`）强制密码至少 12 个字符，
另有显示名非空、用户名格式（≥3 字符、只允许字母数字与 `. _ -`、转小写）、
用户名不重复三项。`bootstrap_admin` 与 `create_account` **都**走这个
`_create`，所以绕过表单直接打接口也拦得住。前端表单写「至少 12 位」是诚实的。

**真正的缺陷是这些校验失败被当成权限失败报出去了：**

| 路由 | 现状 | 问题 |
| --- | --- | --- |
| `POST /auth/accounts` | 所有 `LocalAuthenticationError` → **403** | 「密码太短」「用户名已存在」和「你不是管理员」同一个码 |
| `POST /auth/bootstrap` | 所有 `LocalAuthenticationError` → **409** | 校验失败被说成状态冲突 |

前端表单据此没法决定是让用户改输入，还是告诉他没权限——而这两件事的下一步完全不同。
这与上一批修 `onboard_repository_agent_team`（`AgentDirectoryError` 被吞成 503，
实为永久拒绝，改判 409）是同一族缺陷，改法也一致。

**改法**：在 `local_accounts.py` 给错误分型，加两个
`LocalAuthenticationError` 的**子类**——`LocalAccountValidationError`（输入不合规）
与 `LocalAccountConflict`（用户名已存在、bootstrap 已完成），基类留给真正的认证/授权
失败。路由按 422 / 409 / 403 拆开捕获。

⚠ **必须是子类，且子类要写在基类前面**。`login` 会经过 `_normalize_username`，
它的处理器现在 `except LocalAuthenticationError → 401`：用子类，`login` 行为分毫不变；
用并列类型，格式非法的用户名登录会变成 **500**。捕获顺序写反则永远命中基类，等于没改
——这正是原缺陷的成因。

附带澄清一处**与本次改动无关的既有行为**：`login` 的 handler 是 `detail=str(error)`，
所以拿一个格式非法的用户名登录，返回的一直是 `401 {"detail": "username format is
invalid"}`。子类化前后这句措辞完全一样，它**不是**本次引入的。如果认为这句本身是不该
露的信息（它把「用户名不存在」和「用户名格式不对」区分开了），那是一个独立的裁决，
不在本批范围内。

### 落点

设置页（`SettingsPage.tsx`）新增一段「人员与权限」。不建议开新导航项：账号管理是
低频管理动作，跟已经在设置页的平台就绪、适配器清单是同一类。

非管理员账号打开时按现有诚实数据体例呈现——显示「需要管理员权限」，不要把区块藏掉。

---

## 3. 迁移 5-1：项目监管策略（本批的核心）

### 端点

| 方法 | 路径 | 守卫 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/v1/projects/topologies` | 会话 + `is_admin` | 手工指定每仓 Leader/Worker |
| `POST` | `/api/v1/projects/automatic-topologies` | 会话 + `is_admin` | 只给仓库 id，团队自动配 |
| `GET` | `/api/v1/projects/{project_id}/topology` | 会话（管理员或本项目被授权人） | 读当前策略 |

### 请求体要点

```
ProjectTopologyCreate:
  organization_id, project_id, organization_leader_id,
  repository_teams: [{ repository_id, leader_agent_id, worker_agent_ids[≥1] }] (≥1)
  execution_mode:        auto | supervised | manual_controlled          默认 auto
  required_checkpoints:  repository_scope | specification | execution
                       | validation | delivery | exception_escalation   默认 []
  human_grants: [{ human_principal_id, role, code_access,
                   control_actions[≥1], repository_id?, path_patterns[] }]
  idempotency_key
```

枚举取值（照抄，不要在前端另立一套）：

- `HumanProjectRole`：`organization_supervisor` / `project_supervisor` / `repository_supervisor`
- `CodeAccessLevel`：`none` / `read` / `write`
- `HumanControlAction`：`view_decisions` / `approve_checkpoint` / `request_changes` /
  `pause_project` / `resume_project` / `cancel_project` / `edit_specification`

`automatic-topologies` 只要 `repository_ids`，其余同上——**多数场景用它就够了**，
手工版只在需要指定具体 Worker 时才用。

### 三个必须先想清楚的设计点

#### (a) `project_id` 从哪来：是 issue，不是新造一个

`web/` 的 ProjectSetup 用 `crypto.randomUUID()` 现造一个 project，因为它那套
流程里项目是先于需求存在的。**控制台不是这样**：契约 §0 的语义等式是
**`issue_id` 即 `project_id`**（`frontend/src/api/rooms.ts:78` 有注）。

照抄会造出没有任何 issue 指向的孤儿项目——库里已经有一个了
（`09764c07-23eb-544e-ba1a-dc842edb81c9`，有拓扑、有 2 个团队，却不在 issue 列表里）。

**所以这个页面在控制台里不叫「创建项目」，而是「给这个需求配监管策略」，
`project_id` 取自当前 issue。**

#### (b) ⚠ 时序死线：过了首次物化就再也配不上了

`project.agent_topologies` 上有 `uq_project_agent_topologies_project`
（`project_id` 唯一），**且全仓没有任何更新拓扑的端点**——只有两个 `POST` 创建和
一个 `GET`。

而控制台的物化路径会在干活途中自动建一个 `auto` + 空检查点的拓扑
（`EnsureProjectAgentTopology`，且「已有拓扑就原样返回，不管你要什么」）。

**结论：配置入口必须出现在该 issue 的首次物化之前。** 过了那个点再 POST，会撞唯一
约束拿到 `ProjectTopologyConflict("project topology already exists")`。

建议落点：**发现链的分档审批那一步之后、点物化之前**，或 issue 详情页在
`round_count === 0` 时露出该入口。入口出现条件要按「该 issue 还没有拓扑」判断
（`GET /projects/{id}/topology` 404），不要按轮次数猜。

#### (c) 后端小修：这个冲突现在答 422，应该是 409

`create_project_topology` 只 `except ProjectTopologyError → 422`，而
`ProjectTopologyConflict` 是它的子类，于是「已经有拓扑了」和「你参数填错了」
返回同一个码。前端没法据此分辨该提示「改一下重填」还是「来晚了，这个项目已经定了」。

这与上一批修 `onboard_repository_agent_team` 的 503→409 是同一类缺陷
（见 `api/human_control.py` 该处注释）。**改法一致：把 `ProjectTopologyConflict`
提到前面单独捕获，转 409。**

### 建议先做的一小步（独立有价值、零风险）

先只做**只读**：把 `GET /projects/{project_id}/topology` 的
`execution_mode` 与 `required_checkpoints` 显示在 issue 详情页。

现在控制台完全不显示这两项，用户不知道自己的项目是全自动跑的、一个人工卡点都没有。
光是把这件事说出来就有价值，且不涉及任何写路径。

---

## 4. 迁移 5-3：手工组队（待议，建议排在最后）

| 方法 | 路径 | 守卫 |
| --- | --- | --- |
| `POST` | `/api/v1/agent-teams` | 会话 + `is_admin` |
| `GET` | `/api/v1/agents` | 会话 |
| `POST` | `/api/v1/agents/native` | 会话 + `is_admin` |

```
ManualAgentTeamCreate: { organization_id, name(3-100, ^[a-zA-Z0-9][a-zA-Z0-9_-]+$),
                         description(≤255), leader_agent_id,
                         member_agent_ids(≤20), idempotency_key }
```

**为什么排最后**：它和上一批已迁的建团**语义不同但用途重叠**——已迁的那个按仓库
确定性铸名（`rm-team-{repository_id.hex}`）、一仓一团、避开 repository leader 的
目录单例；这个是自由命名、任意组合。两个入口并存，正是交接文档 §5.2-4 已经记账的
「同一件事两个入口」问题的又一例。

**先回答这个问题再动手**：控制台需不需要自由组队？如果答案是「日常运营不需要，只有
装机时才用」，那它就该留在装机面，而不是搬进运营控制台——那样 `web/` 也就不必删干净。

---

## 5. 顺序与验收

### 顺序

```
5-2 建账号  →  5-1(只读：显示监管策略)  →  5-1(写：配检查点 + 授权)  →  5-3(待议)
```

### 验收：一条端到端的证据链

计数不证明这批迁移有意义。唯一能证明的是**审核台从空变成非空**：

1. 用管理员账号新建一个审核人账号（5-2）；
2. 给一个**还没物化过**的 issue 配 `supervised` + `specification` 检查点，
   并把上一步的账号授权为 `project_supervisor`、`approve_checkpoint`（5-1）；
3. 走发现链到物化；
4. **审核台出现一条待审**——用第 2 步那个账号登录，确认它看得见
   （管理员看全部，其他账号只看指派给自己的）；
5. 做一次决策，确认流程接着往下走。

第 4 步是这批迁移唯一的成立条件。走不到那一步，前面全是白做。

### 纪律

- 前端 = 浏览器实走 + `tsc -b` + `oxlint` 受影响文件。
  ⚠ `tsc --noEmit` 在本项目是空转桩，永远退出 0，用它等于没测。
- 后端只跑受影响面的定向测试，不跑全量套件。
- 两套凭据不能混用：本批**全部三项都走 cookie 会话**（`sessionRequest`），
  一个都不走动作 token。

---

## 6. 做完之后

三项做完（或 5-3 明确不做），`web/` 就只剩 `PrdPlanner`、`DagGraph`、`PlanFlowDemo`
这些已有替代或无人引用的东西，**那时才可以整个目录删掉**，并同时销掉交接文档
§5.2 的记账。
