# RepoMesh 交付控制台（正式前端）

Variant D「融合工作台 · 复古未来主义」的正式实现，设计定稿见
`frontend-prototype/DESIGN-DECISION.md`。React + Vite + TypeScript + Tailwind v4。

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
- 数据源开关：URL 带 `?source=live` 走 8100；默认 `replay` 走本地夹具，无后端也能预览。
- Windows PowerShell 写法：先 `$env:REPOMESH_AGENT_ACTION_TOKEN = "console-dev-token"`
  再执行 uvicorn 行（bash 的前缀式赋值在 PowerShell 不可用）。

## 数据源

契约：`docs/contracts/delivery-read-model-v0.1.md`（唯一事实，禁止前端猜字段）。

- 模式开关：URL 参数 `?source=live|replay` > 环境变量 `VITE_DATA_SOURCE` > 默认 `replay`。
- `replay`：本地夹具（Saleor 四仓回放案例 DLV-0042），数据形状 = 契约聚合；
  契约未覆盖的演示叙事（对话流、clarify 决策、±行数、成本）在 `PresentationOverlay`。
- `live`：打 `GET /api/v1/deliveries*` 读模型 API；`VITE_API_BASE` 指定后端源
  （默认同源），`VITE_API_TOKEN` 注入 Bearer。后端未就绪时显示顶部失败条 + 空态，不白屏。

nullable 降级路径（契约 §6）：`diffstat` 缺失只列文件名；`cost`/`trace_id`/
`matrix_room_id`/快照为 null 时对应行隐藏；`non_goals`/`release_rules` 为 null 隐藏区块；
回滚预案无契约实体时隐藏计划纸面 5.0。

## 结构

- `src/api/contract.ts` —— 契约 v0.1 类型转写（§2/§3/§4）。
- `src/api/client.ts` —— typed fetch client（Bearer、ApiError）。
- `src/api/source.ts` —— `live | replay` 数据源开关与统一取数接口。
- `src/data/replay.ts` —— DLV-0042 replay 夹具（契约形状 + 演示叙事覆盖层）。
- `src/data/scenes.ts` —— 回放场景状态机：契约冻结 → 执行 → 失败修复 → 审批合并。
- `src/viewmodel.ts` —— 契约聚合 → 组件视图模型派生（DAG 布局、标签、降级回退；
  **不做状态映射**：display_status / gate_display / phase 由后端或夹具给出）。
- `src/types.ts` —— 视图模型类型（状态枚举复用契约类型）。
- `src/index.css` —— 设计令牌（`@theme`）：暖黑 / 琥珀 / 奶油纸 / 牛皮纸 / 2px 硬朗圆角等。
- `src/components/`
  - `Sidebar` 左栏项目树（契约 §2 分组列表 + phase 徽标）；
  - `MessageStream` + `artifacts` 对话主线程与结构化 artifact 卡；
  - `PlanView` 阿波罗飞行计划纸面文档；`Dag` 四泳道 DAG SVG（room/paper 双皮肤）；
  - `DecisionDeck` VARIADEX 牛皮纸决策夹；`EnvPanel` Codex 式悬浮环境窗；
  - `ApprovalModal` 快照绑定授权单（未勾选确认框不可批准）；
  - `ReplayBar` 回放控制条（仅 replay 模式：▶ 一键回放 / ⏸ 暂停 / ↺ 重置 / 场景跳转）。

## 回放模式（Demo 叙事）

replay 数据源默认停在终态（审批合并）；`▶ 回放` 从「契约冻结」起每 7 秒推进一个场景，
可暂停、重置、点场景章节跳转。clarify 决策（澄清）只存在于回放模式——契约 §6.5 无后端
实体，live 模式决策夹只有 approve/watch 两类。

## 已知边界

- 决策夹写回路（`POST governance-decisions`）与三视图 live 接入属 CONS-11/12，待后端
  CONS-03 就绪后接入；当前审批仅前端演示。
- Trace 瀑布留二期（trace_id 非 null 时展示）。
- Demo 回放模式（场景状态机）见 CONS-13。
