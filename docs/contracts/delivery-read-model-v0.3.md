# 交付读模型契约 v0.3 增量（issue 写入 / 工作区注册表）

- 状态：**已裁决 · 生效**（起草：检测验收1/修复位，2026-08-11；五项开放问题同日主脑
  全部裁决采纳，附两条补充要求已折入 §1.3/§2.3；对应验收报告缺陷 B-1、B-2）
- 版本：0.3（**增量**：v0.1/v0.2 全文继续有效，本文件只定义两个写入面与一个读端点）
- 基线：`docs/contracts/delivery-read-model-v0.2.md`（截至 §7.3 勘误）
- 消费方：`frontend/`（新建 issue 弹窗、工作区切换器）
- 体例沿用：诚实数据、状态映射唯一实现在读模型、写端点幂等与审计（v0.1 §4.4 风格）

## 0. 定位与边界

v0.2 §0 语义等式不变（issue = Project、工作区 = Organization、**零新展示实体**）。
本增量补的是两条**写路径**与其配套读端点，销掉「从界面发起需求」主动线的两个先决缺口
（验收报告 B-1/B-2）：

| 缺口 | 本文契约 | 事实源与 Owner |
| --- | --- | --- |
| B-1 新建 issue 不真创建 | `POST /api/v1/issues`（§1） | `repository_intelligence.plan_snapshots`（该表唯一生产方） |
| B-2 工作区无列表/创建 | `GET/POST /api/v1/console/organizations`（§2） | **新表** `identity_access.organizations`（模块地图：organizations 归 identity_access） |

明确不做（沿用既有裁决）：issue 级归档实体（v0.2 §2.4）、Project 注册表本体
（§6.1 backlog——本文的 issue 创建**不是** Project 注册表：它只物化「最早
PlanSnapshot」这一既有事实源，`issue_key` 依旧恒 null，诚实缺口位置不变）、
会话票据鉴权（Q1 维持动作 token）。

## 1. `POST /api/v1/issues`（B-1：创建 issue = 落第一份虚拟草稿快照）

### 1.1 语义

创建一个新 issue，即为新 `project_id` 写入 `plan_version=1`、`execution_plan_id=null`
的 PlanSnapshot（与种子场景 D 同构）。**零新实体**：不建 Project 行，不新增列；
读模型 §2 的全部派生（title 截断、state 规则 4「存在虚拟草稿 → open」、phase 规则 3、
organization_id 三级取值链第三级）在写入完成后自动生效。

### 1.2 请求

```json
{ "requirement_text": "string",          // 必填，去空白后非空；即弹窗需求文本
  "created_by_agent_id": "uuid",         // 必填：处理者（设计稿=Org Leader）；bearer 为共享
                                         // 动作 token 无法承载身份，风格同 v0.1 §4.4
  "idempotency_key": "string",           // 必填：内容重放去重；最短 8 字符（§6 S-5）
  "organization_id": "uuid|null" }       // 可选：交叉校验位（§6 S-4），非事实源
```

- `created_by_agent_id` 必须是**活跃的 ORGANIZATION_LEADER**（agent_directory 校验，
  否则 403）——与设计稿「处理者=Org Leader」一致，也与治理写端点的主体校验同风格。
  前端沿用 CONS-44 的花名册派生（decisions.ts 单点实现），不新增取数路径。
- **`organization_id` 只是交叉校验位，不是事实源**（§6 S-4 修订原「不收」条款）：
  工作区归属仍由 `created_by_agent_id` 所属组织唯一决定（即 §2 取值链第三级）。
  调用方带上它声明「我以为自己在哪个工作区操作」；与主体所属组织不一致 → 403
  （典型场景：leader id 取自别的工作区的花名册）。省略该字段合法（不做校验）。
- 不收 `title`：标题是读模型对 `requirement_text` 的截断派生（v0.2 §0），存两份即两个事实源。
- 快照其余字段服务端固定：`engineering_spec=""`、`contracts=[]`、`task_dag=[]`、
  `execution_batches=[]`、`graph_edges=[]`。**草稿不预填 DAG**——规划产物属于后续
  plan 环节，写占位 DAG 即编造。

### 1.3 幂等与并发

- `project_id` 由服务端稳定派生：**UUIDv5（命名空间常量，`{主体所属组织 id}:{key}`）**
  （§6 S-5 修订：作用域从全局收窄到工作区；组织 id 取服务端事实——主体的目录行——
  绝不取请求体）。**相同组织 + 相同 key 重放天然落在同一 (project_id, plan_version=1)
  上**，撞 `PlanSnapshotAlreadyExists` 唯一约束 → 返回既有 issue，**不重复创建**
  （响应 200，首次创建 201；两种响应体同形）。
- **重放校验归属**（§6 S-5）：撞既有快照时，服务端核对该快照创建者所属组织与本次
  主体所属组织一致才回放；不一致（只可能来自旧全局命名空间时代的遗留行）→ 403，
  响应体不含任何 issue 投影——猜键读他人 issue 全文的通道关闭。
- **幂等键生成责任在客户端**（主脑裁决补充）：每次**逻辑创建**生成一个新键（建议随机
  UUID），**重试沿用同键**。键最短 8 字符（§6 S-5）。键空间按工作区隔离后，低熵键
  （如需求文本 hash、固定字符串）的碰撞归并半径缩小到**同工作区内**——仍会把同工作区
  两条 issue 归并成一条，前端接线必须按「随机 UUID」实现，服务端不做键熵校验（校验不了）。
- 不同 key、相同文本 → 允许创建两个 issue（用户确实可能提两条同文需求，服务端不做
  文本级去重）。

### 1.4 响应

`201`（首建）/ `200`（幂等重放）：v0.2 §2 的**单条 issue 形状**（复用读模型同一投影，
禁止另写第二套序列化）。前端拿到即可插入列表或跳详情。

错误：422 文本为空 / 403 主体非活跃 Org Leader / 404 主体不存在。

### 1.5 审计与落位

- 每次首建写 platform 审计事件（风格同治理决策写端点；重放 no-op 不重复写）。
- 路由落位：**挂 `repository_intelligence` 模块 api**（表的唯一生产方；api 层
  read_models 保持只读聚合无写行为）。路径占用已查：`/api/v1/issues` 现仅注册 GET
  （read_models `issues_router`），POST 空闲，同路径不同方法不冲突（§4.5 教训已核）。
- 契约位置：`repository_intelligence/contracts.py` 增 `CreateIssueIntake` 协议
  （请求/结果 dataclass），api 层与前端都只依赖该契约。

## 2. 工作区注册表（B-2）

### 2.1 缺口与新表

当前**没有 Organization 实体**：`organization_id` 只是散落各表的 UUID 列，无名称、
无列表来源——工作区切换器因此无数据可显。按模块地图（organizations 归 identity_access）
新增：

```text
identity_access.organizations
  id          UUID PK
  name        TEXT UNIQUE NOT NULL     -- 显示名，去空白非空
  created_at  TIMESTAMPTZ NOT NULL
```

（Alembic 新迁移，编号接现链尾 0020 之后；不加其他列——用途只有「列出可切换的
工作区」，多余字段无消费方即编造。）

**种子回填**：`scripts/seed-console-demo.py` 幂等补一行
`(stable_id("organization"), "console-demo")`——既有种子组织当前无名，不回填则
列表端点对现网数据返回空集，工作区切换器依旧不可用。

### 2.2 `GET /api/v1/console/organizations`

```json
{ "organizations": [ {
  "organization_id": "uuid", "name": "string", "created_at": "...",
  "agent_count": 9                       // agent_directory 派生（活跃 principal 数）
} ] }
```

- 路径进 `console` 命名空间（§4.5 裁决的通用做法：新增通名端点先查占用、统一前缀）。
- 按 `created_at` 升序；无分页（工作区数量与「一屏读完」前提一致，风格同 v0.1 §4.2）。
- **注册表之外的 organization_id 不出现在列表里**：若某历史 org 无注册行，列表如实
  缺席（该数据不可达工作区切换器），不做「从散落列反推组织」的编造式聚合——补齐
  方式是种子/管理侧回填注册行。

### 2.3 `POST /api/v1/console/organizations`

```json
{ "name": "string",                      // 必填，去空白非空；UNIQUE
  "leader_resource_name": "string|null", // 可选；默认服务端派生（见下）
  "idempotency_key": "string" }
```

- **创建工作区 = 建组织 + 绑定 Org Leader**（设计稿原文语义）。缺 Org Leader 的
  组织开不了 issue（§1.2 校验），只建组织行是把闭环断在下一步，故本端点**同事务**
  创建一个活跃 ORGANIZATION_LEADER principal：
  `agentteams_resource_name = leader_resource_name ?? "rm-org-leader-{name 的 slug}"`
  （经 agent_directory `CreateAgent` 契约创建，幂等键沿用本请求的 idempotency_key）。
- **诚实边界（主脑裁决补充）**：自动创建的 Org Leader 是 agent_directory 的**期望态
  登记行，不是已拉起的运行时**——花名册上它以 runtime 三态如实呈现（未配置 →
  `null` → 显「未接入」）。本端点响应与前端文案**不得暗示「已生成一个可工作的智能体」**；
  它的语义只是「该工作区有了可承接 issue 与治理决策的登记主体」。
- **实现注记（2026-08-11 主脑追认，随实现同批入文本）**：「同事务」实现为**顺序双幂等**
  而非单一物理事务——组织行与 leader 登记行都从同一 idempotency_key 派生，两写之间
  崩溃留下的夹缝由同键重放修复（org 已存在则跳过、leader 幂等创建）。理由：跨模块共享
  DB 会话/事务破坏模块边界（identity_access 不得进入 agent_directory 的存储），代价大于
  「同事务」字面收益；用户可见保证等价——不存在「重放也修不好」的中间态。
- `organization_id` 由 `idempotency_key` UUIDv5 稳定派生；重放 → 200 返回既有行。
  `name` 撞 UNIQUE（不同 key 同名）→ 409。
- 响应 `201`/`200`：`{ "organization_id", "name", "created_at",
  "leader_agent_id" }`。
- 写 platform 审计事件（同 §1.5）。

### 2.4 前端接线约束（随实现验收）

- 切换工作区后 `GET /issues?organization_id=` 必须带上当前工作区 id——§2.5 两计数
  受 organization_id 影响，不带则计数与列表打架（前端 handoff 防坑第 2 条）。
- 「全部工作区」仍是合法态（`organization_id` 省略），切换器需保留该项。

## 3. 鉴权（两端点一致）

沿用 Q1 裁决：`Authorization: Bearer` 动作 token。会话票据（本地登录）与之未打通
是既有 backlog，不在本文范围；前端在已登录 shell 内发起这两个写请求与既有治理写
端点同模式。

## 4. 裁决记录（2026-08-11，五项全部裁决 · 生效）

| # | 问题 | 裁决 |
| --- | --- | --- |
| Q1 | §1.2 主体校验收窄为 ORGANIZATION_LEADER，还是放宽到该组织任意活跃 agent | **收窄**（设计稿「处理者=Org Leader」+ 治理端点同风格） |
| Q2 | §2.1 新表放 identity_access 还是 agent_directory | **identity_access**（模块地图明文：organizations 归它） |
| Q3 | §2.3 自动建 Org Leader 是否接受 | **接受**（「建完工作区仍开不了 issue」的中间态不可接受）；附加诚实边界注记（§2.3） |
| Q4 | 种子组织回填名 `console-demo` 是否接受 | **接受**（种子锚点文档同批更新） |
| Q5 | §1.4 幂等重放 200 vs 恒 201 | **200/201 区分**（信息量大于一致性收益） |

附加裁决：§1.3 幂等键生成责任条款（客户端新键/重试同键/低熵键碰撞警示）折入文本；
POST 与既有 GET 同路径不同方法不冲突的核对获批准；「不收 organization_id/title
防双源」确认正确。

## 5. 实现顺序（裁决后）

1. §1 issue 写端点（B-1，关键路径：前端弹窗接线在等）；
2. §2 organizations 注册表 + 两端点 + 种子回填（B-2）；
3. 前端接线（弹窗真创建、切换器列表/创建/传参）随各自后端落地即接。

每项独立任务分支、独立提交、定向测试（pytest + curl 实调 + 前端 tsc -b/oxlint/实走）。

## 6. 安全修订（2026-08-11，后端 ③ 阶段独立审查 S-4~S-8，主脑裁决必修）

背景：审查确认共享动作 token 不承载主体/租户（S-4 根因）。**主体化凭据（per-principal
token / 会话票据统一鉴权）是已立项 backlog，属架构变更不在本轮**；本节是推送前的
止血与正确性修复，随实现同批入文本：

| # | 修订 | 落点 |
| --- | --- | --- |
| S-4 | 止血「主体由请求体决定」：§1.2 增可选 `organization_id` 交叉校验位（不一致 403）；鉴权现状（token 无主体、主体来自 body、组织来自目录行）在代码注释与本节明文 | §1.2 |
| S-5 | 幂等键空间按工作区隔离（派生加组织前缀）+ 重放校验归属（不一致 403 不回投影）+ 键最短 8 字符 | §1.3 |
