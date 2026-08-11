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

## 结构

- `src/types.ts` —— 前端数据模型（对齐原型 data.js；未来由 `GET /api/v1/deliveries*` 读模型 API 提供）。
- `src/data/mock.ts` —— Saleor 四仓回放案例 DLV-0042 的 mock 数据；API 就绪后由 fetch 层替换。
- `src/index.css` —— 设计令牌（`@theme`）：暖黑 / 琥珀 / 奶油纸 / 牛皮纸 / 2px 硬朗圆角等。
- `src/components/`
  - `Sidebar` 左栏项目树；`MessageStream` + `artifacts` 对话主线程与结构化 artifact 卡；
  - `PlanView` 阿波罗飞行计划纸面文档；`Dag` 四泳道 DAG SVG（room/paper 双皮肤）；
  - `DecisionDeck` VARIADEX 牛皮纸决策夹；`EnvPanel` Codex 式悬浮环境窗；
  - `ApprovalModal` 快照绑定授权单（未勾选确认框不可批准）。

## 已知边界

- 数据全部为 mock，无后端调用；读模型 API 契约另行同步。
- Trace 瀑布留二期（先展示 trace_id）。
- Demo 回放模式（原型的 4 状态机场景切换器）尚未移植。
