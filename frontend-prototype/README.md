# RepoMesh 交付控制台 — UI 原型（THROWAWAY）

> 一次性原型，不进生产。回答的问题：交付控制台（Delivery Run 叙事页）应该长什么样。
> 计划：单入口 `index.html`，`?variant=` 切换 3 个结构不同的方案，底部悬浮条可循环切换。

- A — 流水叙事：置顶阶段管道，纵向分段讲完一条交付
- B — 指挥中心：暗色三栏，DAG 主视觉 + 实时事件流 + 决策收件箱
- C — 决策收件箱（默认）：PM 视角，待决策卡片优先，细节收进标签页

## 运行

```bash
python -m http.server 8788 --directory frontend-prototype
```

打开 http://localhost:8788/（默认 C），或用 `?variant=a|b|c` 切换方案。
页面底部切换条和键盘左右方向键也可以循环切换。

数据全部为 mock（`data.js`），场景取自 docs/agentic-delivery-product-brief.md 第 9 节的
Saleor 四仓回放案例。任何按钮都不产生真实副作用。
