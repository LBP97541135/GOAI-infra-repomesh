# RepoMesh 交付控制台（正式前端）

Variant D「融合工作台 · 复古未来主义」的正式实现，设计定稿见
`frontend-prototype/DESIGN-DECISION.md`。React + Vite + TypeScript + Tailwind v4。

> **只想把界面跑起来**：仓库根 `scripts/dev-up.ps1` / `scripts/dev-up.sh` 一条命令
> 起全套（库→迁移→8100→5280→开浏览器，已在跑的组件自动跳过），或者只装 Docker 走
> `docker compose --profile console up -d --build`（同源托管，开 8100）。见根 README
> 「Open the console」。下面是这条命令背后的手工步骤，排障和改脚本时看。

## 运行

```bash
npm install
npm run dev     # http://127.0.0.1:5280（strictPort）
npm run build   # 产物在 dist/
npm run lint
```

## 联调后端起法（console v2 · 端口 8100）

`npm run dev` 的开发代理把 `/api` 打到 **127.0.0.1:8100**（`vite.config.ts`；后端未开
CORS，必须走同源代理）。根 README「Run locally」起的是 **8000 的 v1 平台 API**，不是
本前端连的实例——8100 要单独起。全新 clone 只需 docker + uv + node，四步（均在仓库根）：

```bash
# 1. 起 Postgres：compose 服务映射宿主 5432:5432，
#    与默认 DSN postgresql+asyncpg://repomesh:repomesh@localhost:5432/repomesh 一一对应
docker compose up -d postgres

# 2. 依赖 + 迁移到头（alembic 读 REPOMESH_DATABASE_URL，未设置即用上述默认值，
#    正好指向第 1 步的库）
uv sync --extra dev
uv run alembic upgrade head

# 3. 起读模型 + 本地身份实例（8100）。token 必须与 frontend/.env.development 的
#    VITE_API_TOKEN=console-dev-token 相同，否则读模型接口全部 401
REPOMESH_AGENT_ACTION_TOKEN=console-dev-token uv run uvicorn repomesh.main:app --host 127.0.0.1 --port 8100

# 4. 起前端
cd frontend && npm install && npm run dev    # http://127.0.0.1:5280
```

- **首次进入**：5280 登录页点「首次部署？初始化管理员」创建本地管理员
  （`POST /auth/bootstrap`，**仅首次**——已存在管理员时后端会拒绝），之后都走登录。
- **库不在默认位置**（换端口/容器）：第 2、3 步带同一个
  `REPOMESH_DATABASE_URL=postgresql+asyncpg://repomesh:repomesh@<host>:<port>/repomesh`——
  迁移与服务必须指向同一个库。
- **可选演示数据**：`REPOMESH_DATABASE_URL=<同上> uv run python scripts/seed-console-demo.py`。
  注意脚本不带 env 时默认打 `127.0.0.1:5533`（一次性联调库约定），**不会命中第 1 步
  的 5432**；不灌种子时 live 模式列表为空态，属正常。
- 数据源开关：默认 `live` 走 8100；URL 带 `?source=replay` 走本地夹具，无后端也能预览。
- Windows PowerShell 写法：先 `$env:REPOMESH_AGENT_ACTION_TOKEN = "console-dev-token"`
  再执行 uvicorn 行（bash 的前缀式赋值在 PowerShell 不可用）。

## 数据源

契约：`docs/contracts/delivery-read-model-v0.1.md` 至 `v0.4`（增量叠加、逐版生效；
唯一事实，禁止前端猜字段）。

- 模式开关：URL 参数 `?source=live|replay` > 环境变量 `VITE_DATA_SOURCE` > 默认 `live`
  （2026-08-12 由 `replay` 改为 `live`，用户裁决：8100 已常驻，「打开就是真数据」，
  早期「后端未就绪先看夹具」的取舍不再成立）。
- `live`：打读模型 API（契约各版 §1 端点）；`VITE_API_BASE` 指定后端源（默认同源），
  `VITE_API_TOKEN` 注入 Bearer。后端未就绪时显示顶部失败条 + 空态，不白屏。
- `replay`：本地夹具（`src/data/` 四份：issues / issueDetail / grid / discovery，
  沿用 #7f3d2a10「结账价格修改原因」同一 issue 世界，自洽）。数据形状 = 契约冻结形状，
  live 与 replay 走同一套类型，切换零改动；它渲染的是编好的演示剧本，不是本机事实。

replay 自检开关（仅回放模式生效，`?source=live` 下一律忽略、不参与取数）：

- `?discovery=<name>` —— 发现链夹具形态，取值表在 `src/data/discovery.ts`
  （`discoveryFixtures`），默认 `done`；
- `?issue=<name>` —— issue 详情夹具形态，取值表在 `src/data/issueDetail.ts`
  （`issueDetailFixtures`），取值 `default` / `draft`——`draft` 是「发现走完、
  尚未物化」的形态；
- `?tasks=<name>` —— 交付聚合的任务形态（驱动 DAG 执行着色），取值表在
  `src/data/issueDetail.ts`（`deliveryAggregateFixtures`），取值 `default` / `conflict`。

给了未知形态名，对应解析器直接抛错并列出全部合法取值——错拼不静默回退。

nullable 降级路径（契约 §6）：`diffstat` 缺失只列文件名；`cost`/`trace_id`/
`matrix_room_id`/快照为 null 时对应行隐藏；`non_goals`/`release_rules` 为 null 隐藏区块；
回滚预案无契约实体时隐藏计划纸面 5.0。

## 结构

- `src/api/contract.ts` —— 契约类型转写（v0.1~v0.4 冻结形状，形状唯一来源）。
- `src/api/client.ts` —— typed fetch client（Bearer、ApiError）。
- `src/api/source.ts` —— `live | replay` 数据源开关（只提供开关，各页自行取数）。
- `src/api/` 其余 —— 按域拆分的取数模块（`issues` / `rooms` / `grid` / `decisions` /
  `discovery` / `auth` / `workspaces` / `repositoryScan` / `rollback`），live 与
  replay 的分流都在这一层。
- `src/data/` —— replay 夹具四份：`issues.ts` / `issueDetail.ts` / `grid.ts` /
  `discovery.ts`（只有数据、不抄字段表；上文自检开关的形态表也在这里）。
- `src/routes.ts` + `src/pages/` —— 路由与页面（issue 列表 / issue 详情 / 房间 /
  仓库 / 团队 / Agent / 设置）。
- `src/viewmodel.ts` —— 契约聚合 → 组件视图模型派生（DAG 布局、标签、降级回退；
  **不做状态映射**：display_status / gate_display / phase 由后端或夹具给出）。
- `src/types.ts` —— 视图模型类型（状态枚举复用契约类型）。
- `src/index.css` —— 设计令牌（`@theme`）：暖黑 / 琥珀 / 奶油纸 / 牛皮纸 / 2px 硬朗圆角等。
- `src/components/`
  - `SidebarV2` v2 侧栏（工作区切换 → 新建 issue → 四导航 → 设置 → 用户块）；
  - `DiscoveryPanel` + `DiscoveryApproval` 发现链面板与分档审批；
  - `PlanDagPanel` 图形化 DAG 面板（执行着色吃交付聚合）；`MaterializeModal`
    「物化并开工」确认弹窗；
  - `RoundsPanel` 轮次面板；`EventTimeline` 环境窗事件时间线；
  - `DecisionDeck` VARIADEX 牛皮纸决策夹；`EvidenceModal` 证据面弹窗；
  - `ApprovalModal` 快照绑定授权单（任一 SHA / 契约 / 门禁变化即失效）；
    `RollbackModal` 回滚弹窗；
  - `AddRepositoryCard` 仓库接入卡；`NewIssueModal` 新建 issue 弹窗；
    `LoginPage` 登录门 / 首账号引导；
  - `RuntimeBadge` / `StatusBlocks` / `Modal` —— 运行时徽标、取数三态小块、
    统一弹窗外壳等共用基础件。

## 回放模式（Demo 叙事）

replay 数据源默认停在终态（审批合并）；`▶ 回放` 从「契约冻结」起每 7 秒推进一个场景，
可暂停、重置、点场景章节跳转。clarify 决策（澄清）只存在于回放模式——契约 §6.5 无后端
实体，live 模式决策夹只有 approve/watch 两类。

## 已知边界

- 决策夹写回路（`POST governance-decisions`）与三视图 live 接入属 CONS-11/12，待后端
  CONS-03 就绪后接入；当前审批仅前端演示。
- Trace 瀑布留二期（trace_id 非 null 时展示）。
- Demo 回放模式（场景状态机）见 CONS-13。
