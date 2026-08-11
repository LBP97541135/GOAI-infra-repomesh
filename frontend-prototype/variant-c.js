// PROTOTYPE — Variant C：决策收件箱（PM 视角 · 待决策卡片优先 + 标签页细节）
window.renderVariantC = function (root) {
  const M = window.MOCK;
  const d = M.delivery;

  const others = [
    { t: "购物车库存提示优化", m: "已发布 · 08-06", s: "succeeded" },
    { t: "订单导出增加税率列", m: "契约澄清中 · 08-10", s: "waiting" },
  ];

  const prog = d.stages
    .map((s, i) => {
      const dot = s.status === "done" ? "succeeded" : s.status;
      const cur = s.status === "running" || s.status === "attention";
      return `${i > 0 ? '<span class="arrow">›</span>' : ""}
        <span class="seg ${cur ? "cur" : ""}"><span class="dot s-${dot}"></span>${s.name}</span>`;
    })
    .join("");

  const cards = M.decisions
    .map(
      (dc) => `
      <div class="vc-dc u-${dc.urgency}">
        <span class="kind">${{ approve: "审批", watch: "关注", clarify: "澄清" }[dc.kind]} · ${{ now: "现在需要你", soon: "可能需要你", later: "不着急" }[dc.urgency]}</span>
        <b>${dc.title}</b>
        <p>${dc.body}</p>
        <div class="acts">${dc.actions
          .map((a, i) => `<button class="${i === 0 ? "pri" : ""}" data-toast="原型演示：「${a}」未真正执行">${a}</button>`)
          .join("")}</div>
      </div>`
    )
    .join("");

  const accept = M.contract.acceptance.map((a) => `<li>${a}</li>`).join("");
  const tabContract = `
    <div class="vc-cols">
      <div>
        <h3>目标</h3><p class="soft">${M.contract.goal}</p>
        <h3 style="margin-top:16px">验收标准</h3><ul class="accept">${accept}</ul>
      </div>
      <div>
        <h3>变更范围</h3>
        <div class="chips">${M.contract.scope.repositories.map((r) => `<span class="chip mono">${r}</span>`).join("")}</div>
        <p class="paths" style="margin-top:8px">允许 ${M.contract.scope.allowedPaths.join("  ")}<br/><span class="fb">禁止 ${M.contract.scope.forbiddenPaths.join("  ")}</span></p>
        <h3 style="margin-top:16px">发布规则</h3>
        <p class="soft">生产发布需人工审批 · 回滚条件：${M.contract.release.rollbackCondition}</p>
        <h3 style="margin-top:16px">澄清记录</h3>
        ${M.clarifications.map((c) => `<p class="soft" style="margin-bottom:6px"><b>Q</b> ${c.q}<br/><b>A</b> ${c.a}</p>`).join("")}
      </div>
    </div>`;

  const tabPlan = `${window.renderDag()}
    <p class="soft" style="margin-top:10px">合并顺序：core → dashboard / apps → docs。任何关键仓失败，整体交付不会标记为成功。</p>`;

  const tabExec = M.tasks
    .map(
      (t) => `
      <div class="vc-task">
        <span class="dot s-${t.status}"></span>
        <span class="tid">${t.id}</span>
        <span class="mono" style="font-size:12px">${t.repo}</span>
        <span>${t.title}</span>
        <span class="why">${t.detail}</span>
      </div>`
    )
    .join("");

  const tabGates = M.gates
    .map((g) => {
      const sum = g.checks.map((c) => `${c.name}${c.s === "pass" ? "✓" : c.s === "fail" ? "✗" : "…"}`).join(" · ");
      return `
      <div class="vc-gate">
        <span class="dot s-${g.state}"></span>
        <span class="rp">${g.repo}</span>
        <span class="sum">${sum}</span>
        <span class="badge g-${g.state}">${{ open: "可合并", blocked: "受阻", running: "验证中", waiting: "等待" }[g.state]}</span>
      </div>`;
    })
    .join("");

  const ev = M.evidence;
  const tabEvidence = `
    <div class="vc-ev">
      ${[
        ["提交", ev.commits.join("；")],
        ["Pull Request", ev.prs.join("；")],
        ["测试", ev.tests],
        ["安全", ev.security],
        ["Trace", ev.trace],
        ["成本", ev.cost],
        ["回滚预案", M.rollback.join(" → ")],
      ]
        .map(([k, v]) => `<div class="row"><span class="k">${k}</span><span>${v}</span></div>`)
        .join("")}
    </div>`;

  const tabs = [
    ["契约", tabContract],
    ["计划", tabPlan],
    ["执行", tabExec],
    ["门禁", tabGates],
    ["证据", tabEvidence],
  ];

  root.innerHTML = `
  <div class="vc">
    <aside class="vc-side">
      <div class="brand">Repo<span>Mesh</span> 交付台</div>
      <h2>进行中</h2>
      <button class="vc-dl cur">
        <span class="t"><span class="dot s-running"></span>${d.title.slice(0, 14)}…</span>
        <span class="m">${d.id} · 执行中 · 3 项待决策</span>
      </button>
      <h2 style="margin-top:14px">其他交付</h2>
      ${others
        .map(
          (o) => `<button class="vc-dl"><span class="t"><span class="dot s-${o.s}"></span>${o.t}</span><span class="m">${o.m}</span></button>`
        )
        .join("")}
    </aside>

    <main class="vc-main">
      <div class="vc-head">
        <h1>${d.title}</h1>
        <div class="meta">${d.id} · ${d.requester} · 发起于 ${d.createdAt}</div>
        <div class="vc-prog">${prog}</div>
      </div>

      <h2 class="sec">等待你的决策 <span class="n">${M.decisions.length}</span></h2>
      <div class="vc-decisions">${cards}</div>

      <div class="vc-tabs">
        ${tabs.map(([n], i) => `<button class="vc-tab ${i === 0 ? "cur" : ""}" data-tab="${i}">${n}</button>`).join("")}
      </div>
      <div class="vc-tabbody" id="vc-tabbody">${tabs[0][1]}</div>
    </main>
  </div>`;

  const body = root.querySelector("#vc-tabbody");
  root.querySelectorAll(".vc-tab").forEach((b) =>
    b.addEventListener("click", () => {
      root.querySelectorAll(".vc-tab").forEach((x) => x.classList.remove("cur"));
      b.classList.add("cur");
      body.innerHTML = tabs[Number(b.dataset.tab)][1];
    })
  );
};
