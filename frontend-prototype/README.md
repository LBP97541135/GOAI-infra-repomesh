# 交付控制台 UI 原型 — 归档

设计已定稿并在 `frontend/` 落地，本目录只留决策依据，不再是可运行的原型。

- `DESIGN-DECISION-V2.md` — **现行信息架构**（issue 中心）的定稿与决策链
- `DESIGN-DECISION.md` — v1（Delivery Run 叙事页）的方案对比与 Variant D 选型
- `redesign-issue-centric.html` — v2 可点击原型，**自包含单文件**，浏览器直接打开即可

v1 的四方案原型（`index.html` + `variant-a|b|c|d.js` + `styles.css` + mock 数据）已随 v1
控制台一同退役。Variant D 的视觉令牌不在原型里了，它们的现行归属是
`frontend/src/index.css` 的 `@theme` 段。
