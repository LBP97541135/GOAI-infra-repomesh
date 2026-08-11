// PROTOTYPE — Variant B：指挥中心（暗色三栏 · DAG 主视觉 + 事件流 + 决策收件箱）
window.renderVariantB = function (root) {
  const M = window.MOCK;
  const d = M.delivery;

  const stageRail = d.stages
    .map((s, i) => {
      const dot = s.status === "done" ? "succeeded" : s.status;
      return `${i > 0 ? '<div class="vb-rail-line"></div>' : ""}
      <div class="vb-stage ${s.status === "running" || s.status === "attention" ? "cur" : ""}">
        <span class="dot s-${dot}"></span>
        <div><div class="nm">${s.name}</div><div class="nt">${s.note}</div></div>
      </div>`;
    })
    .join("");

  const lights = M.gates
    .map(
      (g) => `<div class="vb-light"><span class="dot s-${g.state}"></span>${g.repo}
        <span class="nt">${{ open: "OPEN", blocked: "BLOCKED", running: "CI…", waiting: "WAIT" }[g.state]}</span></div>`
    )
    .join("");

  const feed = M.events
    .map(
      (e) => `<div class="vb-ev k-${e.kind}"><span class="t">${e.at}</span><span class="k">${e.kind}</span><span>${e.text}</span></div>`
    )
    .join("");

  const cards = M.decisions
    .map(
      (dc) => `
      <div class="vb-card">
        <div class="hd"><b>${dc.title}</b><span class="urg u-${dc.urgency}">${{ now: "立即", soon: "关注", later: "稍后" }[dc.urgency]}</span></div>
        <p>${dc.body}</p>
        <div class="acts">${dc.actions
          .map((a, i) => `<button class="${i === 0 ? "pri" : ""}" data-toast="原型演示：「${a}」未真正执行">${a}</button>`)
          .join("")}</div>
      </div>`
    )
    .join("");

  const ev = M.evidence;
  const evd = [
    ["提交", ev.commits.join("；")],
    ["PR", ev.prs.join("；")],
    ["测试", ev.tests],
    ["安全", ev.security],
    ["Trace", ev.trace],
  ]
    .map(([k, v]) => `<div class="row"><span class="k">${k}</span><span>${v}</span></div>`)
    .join("");

  root.innerHTML = `
  <div class="vb">
    <header class="vb-bar">
      <span class="logo">REPOMESH</span>
      <span class="id">${d.id}</span>
      <h1>${d.title}</h1>
      <span class="live">● LIVE</span>
      <span class="cost">${ev.cost}</span>
    </header>
    <div class="vb-grid">
      <aside class="vb-col">
        <h2>交付阶段</h2>
        ${stageRail}
        <h2 style="margin-top:20px">合并门禁</h2>
        ${lights}
        <h2 style="margin-top:20px">运行</h2>
        <div class="vb-costbox">
          run <b>${d.runId}</b><br/>
          发起 <b>${d.requester.split(" ")[0]}</b><br/>
          开始 <b>${d.createdAt.slice(6)}</b><br/>
          人工介入 <b>0 次</b>
        </div>
      </aside>

      <main class="vb-col">
        <div class="vb-panel">
          <h2>跨仓任务 DAG · 合并顺序 core → dashboard / apps → docs</h2>
          ${window.renderDag()}
        </div>
        <div class="vb-panel">
          <h2>实时事件流（runner / gate / matrix / 治理）</h2>
          <div class="vb-feed">${feed}</div>
        </div>
      </main>

      <aside class="vb-col">
        <h2>等待决策（${M.decisions.length}）</h2>
        ${cards}
        <h2 style="margin-top:18px">证据包（实时累积）</h2>
        <div class="vb-evd">${evd}</div>
      </aside>
    </div>
  </div>`;
};
