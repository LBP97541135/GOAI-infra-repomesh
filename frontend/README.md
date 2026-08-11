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
- `src/viewmodel.ts` —— 契约聚合 → 组件视图模型派生（DAG 布局、标签、降级回退；
  **不做状态映射**：display_status / gate_display / phase 由后端或夹具给出）。
- `src/types.ts` —— 视图模型类型（状态枚举复用契约类型）。
- `src/index.css` —— 设计令牌（`@theme`）：暖黑 / 琥珀 / 奶油纸 / 牛皮纸 / 2px 硬朗圆角等。
- `src/components/`
  - `Sidebar` 左栏项目树（契约 §2 分组列表 + phase 徽标）；
  - `MessageStream` + `artifacts` 对话主线程与结构化 artifact 卡；
  - `PlanView` 阿波罗飞行计划纸面文档；`Dag` 四泳道 DAG SVG（room/paper 双皮肤）；
  - `DecisionDeck` VARIADEX 牛皮纸决策夹；`EnvPanel` Codex 式悬浮环境窗；
  - `ApprovalModal` 快照绑定授权单（未勾选确认框不可批准）。

## 已知边界

- 决策夹写回路（`POST governance-decisions`）与三视图 live 接入属 CONS-11/12，待后端
  CONS-03 就绪后接入；当前审批仅前端演示。
- Trace 瀑布留二期（trace_id 非 null 时展示）。
- Demo 回放模式（场景状态机）见 CONS-13。
