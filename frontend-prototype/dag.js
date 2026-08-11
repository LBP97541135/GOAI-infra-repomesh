// PROTOTYPE — 共享 DAG SVG 生成器（结构共享，皮肤由各 variant 的 CSS 决定）
window.renderDag = function renderDag() {
  const tasks = window.MOCK.tasks;
  const repos = window.MOCK.repos;
  const X = (col) => 150 + col * 285;
  const Y = (lane) => 52 + lane * 72;
  const W = 235, H = 54;

  const byId = Object.fromEntries(tasks.map((t) => [t.id, t]));

  const lanes = repos
    .map((r, i) => {
      const y = Y(i);
      return `
        <line class="dag-lane" x1="18" y1="${y}" x2="962" y2="${y}"></line>
        <text class="dag-lane-label" x="18" y="${y - 14}">${r.id}</text>`;
    })
    .join("");

  const edges = tasks
    .flatMap((t) =>
      (t.deps || []).map((d) => {
        const s = byId[d];
        const x1 = X(s.col) + W, y1 = Y(s.lane);
        const x2 = X(t.col), y2 = Y(t.lane);
        const mx = (x1 + x2) / 2;
        const active = s.status === "succeeded" && t.status !== "pending";
        return `<path class="dag-edge ${active ? "edge-active" : ""}"
          d="M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}"></path>`;
      })
    )
    .join("");

  const STATUS_LABEL = {
    succeeded: "已完成", running: "执行中", repairing: "修复中 2/3",
    pending: "等待依赖", failed: "失败",
  };

  const nodes = tasks
    .map((t) => {
      const x = X(t.col), y = Y(t.lane);
      return `
      <g class="dag-node st-${t.status}" transform="translate(${x}, ${y - H / 2})">
        <rect class="dag-box" width="${W}" height="${H}" rx="8"></rect>
        <circle class="dag-dot" cx="18" cy="${H / 2}" r="5"></circle>
        <text class="dag-id" x="34" y="22">${t.id} · ${t.agent}</text>
        <text class="dag-title" x="34" y="40">${t.title}</text>
        <text class="dag-status" x="${W - 10}" y="22" text-anchor="end">${STATUS_LABEL[t.status] || t.status}</text>
      </g>`;
    })
    .join("");

  return `<svg class="dag" viewBox="0 0 980 322" role="img" aria-label="跨仓任务 DAG">
    ${lanes}${edges}${nodes}
  </svg>`;
};
