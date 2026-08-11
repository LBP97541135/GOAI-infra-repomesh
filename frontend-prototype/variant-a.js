// PROTOTYPE — Variant A：流水叙事（置顶阶段管道 + 纵向分段档案）
window.renderVariantA = function (root) {
  const M = window.MOCK;
  const d = M.delivery;

  const stages = d.stages
    .map(
      (s) => `
      <button class="va-stage s-${s.status}" data-jump="sec-${s.key}">
        <span class="nm"><span class="dot s-${s.status === "done" ? "succeeded" : s.status}"></span>${s.name}</span>
        <span class="nt">${s.note}</span>
      </button>`
    )
    .join("");

  const accept = M.contract.acceptance.map((a) => `<li>${a}</li>`).join("");
  const qa = M.clarifications
    .map(
      (c) => `
      <div class="item">
        <div class="q">Q：${c.q}</div>
        <div class="a">A：${c.a}</div>
        <div class="by">${c.by} · ${c.at}</div>
      </div>`
    )
    .join("");

  const repoCards = M.repos
    .map(
      (r) => `
      <div class="va-repo">
        <div class="nm">${r.id}</div>
        <div class="role">${r.role}</div>
        <div class="ev">${r.evidence}</div>
      </div>`
    )
    .join("");

  const tasks = M.tasks
    .map((t) => {
      const row = `
      <div class="va-task">
        <span class="dot s-${t.status}"></span>
        <span class="tid">${t.id}</span>
        <span class="repo">${t.repo}</span>
        <span>${t.title}</span>
        <span class="chip agent">${t.agent}${t.attempt > 1 ? ` · 第${t.attempt}次` : ""}</span>
        <span class="why">${t.detail}</span>
      </div>`;
      const repair = t.repair
        ? `<div class="va-repair">${t.repair
            .map((r) => `<div class="st"><span class="t">${r.at}</span>${r.what}</div>`)
            .join("")}</div>`
        : "";
      return row + repair;
    })
    .join("");

  const deny = M.events.find((e) => e.kind === "deny");

  const gates = M.gates
    .map(
      (g) => `
      <div class="va-gate">
        <div class="hd">
          <span class="rp">${g.repo}</span>
          <span class="badge g-${g.state}">${{ open: "门禁通过", blocked: "受阻", running: "验证中", waiting: "等待" }[g.state]}</span>
        </div>
        ${g.checks
          .map(
            (c) => `<div class="ck"><span class="dot s-${c.s}"></span>${c.name}<span class="nt">${c.note}</span></div>`
          )
          .join("")}
        <div class="pr">${g.pr}</div>
      </div>`
    )
    .join("");

  const ev = M.evidence;
  const evCells = [
    ["提交", ev.commits.join("；")],
    ["Pull Request", ev.prs.join("；")],
    ["测试", ev.tests],
    ["安全", ev.security],
    ["Trace", ev.trace],
    ["成本", ev.cost],
  ]
    .map(([k, v]) => `<div class="cell"><div class="k">${k}</div><div class="v">${v}</div></div>`)
    .join("");

  root.innerHTML = `
  <div class="va">
    <header class="va-top">
      <div class="va-titlebar">
        <span class="id">${d.id}</span>
        <h1>${d.title}</h1>
        <span class="chip va-state">执行中</span>
        <span class="meta">${d.requester} · 发起于 ${d.createdAt}</span>
      </div>
      <nav class="va-pipe">${stages}</nav>
    </header>

    <main class="va-main">
      <section id="sec-contract">
        <div class="va-sec-head"><span class="no">阶段 1</span><h2>交付契约</h2><span class="chip va-state tag">已冻结 v3</span></div>
        <div class="va-panel">
          <div class="va-grid2">
            <div>
              <div class="kv"><h3>目标</h3><p>${M.contract.goal}</p></div>
              <div class="kv"><h3>验收标准</h3><ul class="accept">${accept}</ul></div>
              <div class="kv"><h3>不做什么</h3><p class="paths">${M.contract.nonGoals.join(" · ")}</p></div>
            </div>
            <div>
              <div class="kv"><h3>变更范围</h3>
                <div class="chips">${M.contract.scope.repositories.map((r) => `<span class="chip mono">${r}</span>`).join("")}</div>
                <p class="paths" style="margin-top:8px">允许 ${M.contract.scope.allowedPaths.join("  ")}<br/><span class="fb">禁止 ${M.contract.scope.forbiddenPaths.join("  ")}</span></p>
              </div>
              <div class="kv"><h3>质量门禁（必过）</h3>
                <div class="chips">${M.contract.gatesRequired.map((g) => `<span class="chip">${g}</span>`).join("")}</div>
              </div>
              <div class="kv"><h3>发布规则</h3>
                <p>生产发布需人工审批 · 回滚条件：${M.contract.release.rollbackCondition}</p>
              </div>
            </div>
          </div>
          <div class="va-qa"><h3>澄清记录（${M.clarifications.length}）</h3>${qa}</div>
        </div>
      </section>

      <section id="sec-plan">
        <div class="va-sec-head"><span class="no">阶段 2</span><h2>跨仓计划</h2></div>
        <div class="va-panel">
          ${window.renderDag()}
          <div class="va-repos">${repoCards}</div>
        </div>
      </section>

      <section id="sec-execute">
        <div class="va-sec-head"><span class="no">阶段 3</span><h2>执行</h2></div>
        <div class="va-panel">
          ${tasks}
          ${deny ? `<div class="va-deny"><span class="b">治理拦截</span><span>${deny.text}（${deny.at}）</span></div>` : ""}
        </div>
      </section>

      <section id="sec-validate">
        <div class="va-sec-head"><span class="no">阶段 4</span><h2>独立验证与门禁</h2></div>
        <div class="va-gates">${gates}</div>
      </section>

      <section id="sec-release">
        <div class="va-sec-head"><span class="no">阶段 5</span><h2>发布与证据</h2></div>
        <div class="va-approve">
          <div class="tx"><b>saleor-core 已通过全部门禁，等待你批准合并</b>
          <span>合并顺序 core → dashboard / apps → docs · 批准后自动按序推进</span></div>
          <button data-toast="原型演示：不会真正合并">批准合并</button>
          <button class="ghost" data-toast="原型演示：跳转证据包">查看证据</button>
        </div>
        <div class="va-ev">${evCells}</div>
        <div class="va-panel va-roll">
          <h3>回滚预案（Recovery Saga）</h3>
          <ol>${M.rollback.map((r) => `<li>${r}</li>`).join("")}</ol>
        </div>
      </section>
    </main>
  </div>`;

  root.querySelectorAll("[data-jump]").forEach((b) =>
    b.addEventListener("click", () => {
      const el = document.getElementById(b.dataset.jump);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    })
  );
};
