// PROTOTYPE — Variant D：融合工作台（复古未来主义 · 对话主线程 + 视图切换 + 悬浮环境窗 + 堆叠决策夹）
window.renderVariantD = function (root) {
  const M = window.MOCK;
  const d = M.delivery;
  const t3 = M.tasks.find((t) => t.id === "T3");

  /* ---------- 决策夹本地状态（front = 数组末位） ---------- */
  const KIND = { approve: "审批", watch: "关注", clarify: "澄清" };
  let deck = M.decisions.slice().reverse();
  let deckHidden = false;

  /* ---------- 图标 ---------- */
  const I = {
    repo: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><rect x="2.5" y="2.5" width="11" height="11" rx="1"/><path d="M2.5 6.2h11"/></svg>',
    diff: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M8 2.8v5M5.5 5.3h5"/><path d="M5.5 12.4h5"/></svg>',
    pr: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><circle cx="4.4" cy="3.9" r="1.7"/><circle cx="4.4" cy="12.1" r="1.7"/><circle cx="11.6" cy="12.1" r="1.7"/><path d="M4.4 5.8v4.4M11.6 10.3V7.6a2 2 0 0 0-2-2H8.2"/></svg>',
    env: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><rect x="2" y="3" width="12" height="8.4" rx="1"/><path d="M6 13.8h4"/></svg>',
    term: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2.6" width="12" height="10.8" rx="1"/><path d="M4.8 6.4l2.4 1.9-2.4 1.9M8.8 10.4h2.6"/></svg>',
    plus: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M8 3.4v9.2M3.4 8h9.2"/></svg>',
    chev: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4.8 6.4L8 9.6l3.2-3.2"/></svg>',
  };

  /* ---------- 左侧项目树 ---------- */
  const side = `
    <aside class="vd-side">
      <div class="vd-brand"><span class="mk">R</span><div><strong>REPOMESH</strong><small>SALEOR COMMERCE</small></div></div>
      <button class="vd-new" data-toast="原型演示：进入新建交付流程">＋ 新建交付 <kbd>⌘K</kbd></button>
      <div class="vd-treelabel">进行中</div>
      <button class="vd-tree cur"><span class="dot s-running"></span><div><b>${d.title.slice(0, 13)}…</b><small>${d.id} · 3 项待决策</small></div></button>
      <div class="vd-treelabel">其他交付</div>
      <button class="vd-tree"><span class="dot s-succeeded"></span><div><b>购物车库存提示优化</b><small>已发布 · 08-06</small></div></button>
      <button class="vd-tree"><span class="dot s-waiting"></span><div><b>订单导出增加税率列</b><small>契约澄清中 · 08-10</small></div></button>
      <div class="vd-sidefoot">
        <div class="vd-runtime"><span class="pulse"></span><div><b>AGENT RUNTIME</b><small>1 Leader · 3 Workers 在线</small></div></div>
        <div class="vd-user"><span class="av">王</span><div><b>王倩</b><small>产品经理</small></div></div>
      </div>
    </aside>`;

  /* ---------- 对话流 ---------- */
  const msgHead = (name, role, time, cls) => `
    <div class="vd-msghead ${cls}"><span class="av">${name.slice(0, 1)}</span>
      <b>${name}</b><span class="role">${role}</span><time>${time}</time></div>`;

  const scopeArt = `
    <div class="vd-art">
      <div class="arthead"><span class="eyebrow">仓库范围 · 已由你确认冻结</span></div>
      <div class="chips">${M.repos.map((r) => `<span class="chip mono">${r.id}</span>`).join("")}</div>
      <div class="evrow">${M.repos.map((r) => `<span class="tag">${r.evidence}</span>`).join("")}</div>
      <div class="artfoot">证据：依赖扫描 17 FILES · 6 SYMBOLS · 2 API CONTRACTS</div>
    </div>`;

  const dagArt = `
    <div class="vd-art">
      <div class="arthead"><span class="eyebrow">跨仓任务 DAG · REV 2 · 合并顺序 core → dashboard / apps → docs</span></div>
      <div class="dagwrap">${window.renderDag()}</div>
    </div>`;

  const failArt = `
    <div class="vd-art fail">
      <div class="arthead"><span class="eyebrow">独立验证 · dashboard 门禁受阻</span></div>
      <p class="failp">隐藏验收测试 3/9 失败：<b>OrderPriceOverrideNote 空值渲染崩溃</b>。修复循环第 ${t3.attempt}/3 次，范围限定 src/orders/components/**。再失败一次将按契约升级人工接管。</p>
      <div class="repair">${t3.repair.map((r) => `<div><span class="t">${r.at}</span>${r.what}</div>`).join("")}</div>
      <div class="deny">治理拦截：开发 Agent 尝试自行运行验收测试被权限层拒绝——自证不算数，验收由 QA Guardian 受控执行。</div>
    </div>`;

  const approveArt = `
    <div class="vd-art ok">
      <div class="arthead"><span class="eyebrow">发布门禁 · 等待人工审批</span></div>
      <p class="okp"><b>saleor-core 已通过全部 5 项门禁</b>（单测 412 · 集成 58 · 隐藏 9/9 · 安全 0 高危 · 独立 Review 通过）。合并顺序第 1 位，批准后按序推进，任何关键仓失败整体不会标记成功。</p>
      <button class="artbtn" data-decide="D-1" data-label="批准合并">批准合并（打开授权单）</button>
    </div>`;

  const stream = `
    <div class="vd-stream" id="vd-stream">
      <div class="vd-msg">${msgHead("王倩", "HUMAN", "14:02", "user")}
        <div class="vd-bubble user">${d.summary}</div>
        <div class="vd-attach">PRD<b>checkout-price-override-reason.md</b><span>目标 · 验收标准 · 兼容约束 · 18KB</span></div>
      </div>
      <div class="vd-msg">${msgHead("Product Analyst", "AGENT", "14:31", "agent")}
        <div class="vd-bubble">有 2 个关键歧义需要你确认，已按你的回答写入契约：${M.clarifications
          .map((c) => `<div class="qa"><b>Q</b> ${c.q}<br/><b>A</b> ${c.a}</div>`)
          .join("")}</div>
      </div>
      <div class="vd-msg">${msgHead("Project Manager", "AGENT", "14:55", "agent")}
        <div class="vd-bubble">契约已冻结（v3）。仓库发现完成，自动选仓只提供证据，范围由你确认。</div>
        ${scopeArt}
      </div>
      <div class="vd-msg">${msgHead("Project Manager", "AGENT", "15:02", "agent")}
        <div class="vd-bubble">任务 DAG 已生成，4 个 Worker 在隔离 Worktree 中执行，全部提交只写入 Worktree。</div>
        ${dagArt}
      </div>
      <div class="vd-msg">${msgHead("QA Guardian", "AGENT", "16:02", "qa")}
        ${failArt}
      </div>
      <div class="vd-msg">${msgHead("Release Guardian", "AGENT", "16:10", "agent")}
        ${approveArt}
      </div>
    </div>`;

  /* ---------- DAG / 计划视图（阿波罗飞行计划纸面） ---------- */
  const STATUS_WORD = { succeeded: "COMPLETE", running: "IN PROGRESS", repairing: "REPAIR 2/3", pending: "HOLD" };
  const planView = `
    <div class="vd-plan" id="vd-plan" hidden>
      <div class="paper">
        <div class="p-head">
          <span class="pid mono">${d.id}</span>
          <div class="pt"><h2>交付计划 · ${d.title}</h2>
          <span class="rev mono">REV 2 · CONTRACT V3 FROZEN · ${d.createdAt.slice(0, 10)}</span></div>
        </div>

        <section class="p-sec">
          <div class="p-bar"><span class="p-num">1.0</span><h3>目标与验收</h3></div>
          <p class="p-goal">${M.contract.goal}</p>
          ${M.contract.acceptance
            .map((a, i) => `<div class="p-item"><span class="n mono">1.${i + 1}</span><span>${a}</span></div>`)
            .join("")}
          <div class="p-item dim"><span class="n mono neg">非目标</span><span>${M.contract.nonGoals.join("；")}</span></div>
        </section>

        <section class="p-sec">
          <div class="p-bar"><span class="p-num">2.0</span><h3>变更范围</h3></div>
          <div class="p-chips">${M.contract.scope.repositories.map((r) => `<span class="pchip mono">${r}</span>`).join("")}</div>
          <div class="p-item"><span class="n mono">2.1</span><span class="mono sm">允许 ${M.contract.scope.allowedPaths.join("  ")}</span></div>
          <div class="p-item"><span class="n mono neg">2.2</span><span class="mono sm neg-t">禁止 ${M.contract.scope.forbiddenPaths.join("  ")}</span></div>
        </section>

        <section class="p-sec">
          <div class="p-bar"><span class="p-num">3.0</span><h3>任务 DAG</h3><span class="p-note mono">MERGE ORDER: core → dashboard / apps → docs</span></div>
          <div class="p-dag">${window.renderDag()}</div>
        </section>

        <section class="p-sec">
          <div class="p-bar"><span class="p-num">4.0</span><h3>执行计划</h3></div>
          ${M.tasks
            .map(
              (t, i) => `<div class="p-item task"><span class="n mono">4.${i + 1}</span>
                <span class="mono tid">${t.id}</span><span class="mono rp">${t.repo}</span>
                <span class="tt">${t.title}</span><span class="mono ag">${t.agent}</span>
                <span class="mono st st-${t.status}">${STATUS_WORD[t.status]}</span></div>`
            )
            .join("")}
        </section>

        <section class="p-sec">
          <div class="p-bar salmon"><span class="p-num">5.0</span><h3>回滚预案</h3></div>
          ${M.rollback
            .map((r, i) => `<div class="p-item"><span class="n mono neg">5.${i + 1}</span><span>${r}</span></div>`)
            .join("")}
        </section>
      </div>
    </div>`;

  /* ---------- 悬浮环境窗（Codex 式） ---------- */
  const REPO_DIFFS = [
    {
      id: "saleor-core", add: 412, del: 18, note: "8825f6bb · 3c91d02a",
      files: [
        ["saleor/order/models.py", 38, 2],
        ["saleor/graphql/order/types.py", 54, 0],
        ["migrations/0042_price_override_reason.py", 61, 0],
        ["tests/（5 个文件）", 259, 16],
      ],
    },
    {
      id: "saleor-dashboard", add: 186, del: 24, note: "修复中 · wt-6f21",
      files: [
        ["src/orders/…/OrderPriceOverrideNote.tsx", 92, 0],
        ["src/graphql/types.generated.ts", 61, 24],
        ["src/orders/queries.ts", 33, 0],
      ],
    },
    {
      id: "saleor-apps", add: 48, del: 6, note: "执行中 · 变更采集中",
      files: [
        ["apps/payment-example/src/checkout.ts", 41, 6],
        ["apps/payment-example/README.md", 7, 0],
      ],
    },
    { id: "saleor-docs", add: 0, del: 0, note: "等待 T5 启动", files: [] },
  ];

  const PR_STATE = {
    open: ["待合并", "st-ok"],
    blocked: ["门禁受阻", "st-bad"],
    running: ["CI 运行中", "st-run"],
    waiting: ["未创建", "st-mut"],
  };

  const repoRows = REPO_DIFFS
    .map((r, i) => {
      const open = i === 0;
      const files = r.files.length
        ? `<div class="cx-files">${r.files
            .map(([f, a, x]) => `<div class="cx-file"><span class="fn">${f}</span><span class="st"><i class="plus">+${a}</i>${x ? `<i class="minus">-${x}</i>` : ""}</span></div>`)
            .join("")}</div>`
        : `<div class="cx-files"><div class="cx-file"><span class="fn empty">尚无变更</span></div></div>`;
      return `
      <div class="cx-group ${open ? "open" : ""}" data-repo="${r.id}">
        <button class="cx-row" data-cxtoggle="${r.id}">
          <span class="ic">${I.repo}</span><span class="nm mono">${r.id}</span>
          <span class="end">${r.add + r.del ? `<i class="plus">+${r.add}</i><i class="minus">-${r.del}</i>` : `<i class="mut">—</i>`}<span class="chev">${I.chev}</span></span>
        </button>
        <div class="cx-detail">
          ${files}
          <div class="cx-sub"><span class="ic">${I.diff}</span>${r.note}</div>
        </div>
      </div>`;
    })
    .join("");

  const csRows = M.gates
    .map((g) => {
      const [label, cls] = PR_STATE[g.state];
      const prNo = g.pr.split(" ")[0];
      return `<div class="cx-row static"><span class="ic">${I.pr}</span>
        <span class="nm mono">${prNo}</span><span class="end"><i class="${cls}">${label}</i></span></div>`;
    })
    .join("");

  const ctx = `
    <aside class="vd-ctx" id="vd-ctx">
      <div class="cx-head"><span>环境信息</span>
        <span class="cx-headend">
          <button class="cx-plus" data-toast="原型演示：挂载补充上下文"><span class="ic">${I.plus}</span></button>
          <button class="cx-plus" id="cx-min" title="收起/展开"><span class="ic">${I.chev}</span></button>
        </span>
      </div>
      <div class="cx-scroll">
        <div class="cx-label">关联仓库</div>
        ${repoRows}
        <div class="cx-label">CHANGESET · core → dash / apps → docs</div>
        ${csRows}
        <div class="cx-label">环境</div>
        <div class="cx-row static"><span class="ic">${I.env}</span><span class="nm">预发环境</span><span class="end"><i class="mut">未部署 · 等 core 合并</i></span></div>
        <div class="cx-row static"><span class="ic">${I.diff}</span><span class="nm">基线快照</span><span class="end"><i class="mono mut">snap-dlv0042-01</i></span></div>
        <div class="cx-row static"><span class="ic">${I.env}</span><span class="nm">Matrix 房间</span><span class="end"><i class="mono mut">#dlv-0042</i></span></div>
        <div class="cx-row static"><span class="ic">${I.diff}</span><span class="nm">成本</span><span class="end"><i class="mut">1.24M tok · ¥8.42 · 2h09m</i></span></div>
        <div class="cx-label">后台进程</div>
        <div class="cx-row static"><span class="ic">${I.term}</span><span class="nm mono sm">repomesh-runner · poll /runner-tasks/next</span></div>
        <div class="cx-row static"><span class="ic">${I.term}</span><span class="nm mono sm">otlp-exporter → localhost:3001/v1/traces</span></div>
      </div>
    </aside>`;

  /* ---------- 审批弹窗（快照绑定授权） ---------- */
  const modal = `
    <dialog class="vd-modal" id="vd-approval">
      <form method="dialog">
        <div class="mhead"><div><span class="eyebrow">DELIVERY GATE</span><h2>批准临时远程写入</h2></div>
          <button value="cancel" class="x" aria-label="关闭">×</button></div>
        <div class="mbody">
          <div class="msum">
            <div><span>授权主体</span><b>Release Guardian</b></div>
            <div><span>绑定快照</span><b>snap-dlv0042-01 · IMMUTABLE</b></div>
            <div><span>有效时间</span><b>30 分钟</b></div>
            <div><span>写入范围</span><b>saleor-core（仅合并 PR #19466）</b></div>
          </div>
          <label class="fld"><span>审批意见</span>
            <textarea rows="3">core 五项门禁全绿且独立 Review 通过，允许按冻结 SHA 合并。</textarea></label>
          <label class="ck"><input type="checkbox" required />
            <span>我确认本次授权仅绑定 snap-dlv0042-01；任一 SHA、契约或门禁结果变化都会使授权立即失效。</span></label>
        </div>
        <div class="macts"><button value="cancel" class="sec">拒绝 / 稍后处理</button>
          <button value="ok" class="pri" id="vd-approve-ok">批准并授权</button></div>
      </form>
    </dialog>`;

  /* ---------- 组装 ---------- */
  root.innerHTML = `
  <div class="vd">
    ${side}
    <main class="vd-conv">
      <header class="vd-convhead">
        <div class="crumbs mono">SALEOR COMMERCE › ${d.id} · MATRIX #dlv-0042</div>
        <div class="trow">
          <span class="av pm">PM</span><h1>${d.title}</h1>
          <span class="rec"><i>●</i>RUN 2H09M</span>
          <div class="vd-views">
            <button class="vtab cur" data-vv="room">房间</button>
            <button class="vtab" data-vv="plan">DAG · 计划</button>
          </div>
        </div>
      </header>
      ${stream}
      ${planView}
      <div class="vd-stackwrap" id="vd-stackwrap"></div>
      <form class="vd-composer" id="vd-composer">
        <textarea rows="2" placeholder="询问状态、调整范围，或要求 Project Manager 解释决策…"></textarea>
        <div class="cfoot"><span class="mono">MSG → MATRIX · 关键结论结构化回写事实库</span><button type="submit">发送 ↑</button></div>
      </form>
      ${ctx}
    </main>
    ${modal}
  </div>`;

  /* ---------- 视图切换 ---------- */
  const streamEl = root.querySelector("#vd-stream");
  const planEl = root.querySelector("#vd-plan");
  root.querySelectorAll(".vtab").forEach((b) =>
    b.addEventListener("click", () => {
      root.querySelectorAll(".vtab").forEach((x) => x.classList.remove("cur"));
      b.classList.add("cur");
      const isPlan = b.dataset.vv === "plan";
      streamEl.hidden = isPlan;
      planEl.hidden = !isPlan;
    })
  );

  /* ---------- 决策夹渲染（堆叠文件夹） ---------- */
  const wrap = root.querySelector("#vd-stackwrap");
  const dlg = root.querySelector("#vd-approval");

  function bindDecide(scope) {
    scope.querySelectorAll("[data-decide]").forEach((b) =>
      b.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const id = b.dataset.decide;
        if (id === "D-1") { dlg.showModal(); return; }
        if (b.dataset.resolve === "1") {
          deck = deck.filter((x) => x.id !== id);
          window.showToast(`已记录：「${b.dataset.label}」，结论回写契约（原型演示）`);
          renderStack();
        } else {
          window.showToast(`原型演示：「${b.dataset.label}」未真正执行`);
        }
      })
    );
  }

  function renderStack() {
    if (!deck.length) { wrap.innerHTML = ""; return; }
    if (deckHidden) {
      wrap.innerHTML = `
        <button class="vd-stackbar collapsed" id="vd-stacktoggle">
          <span class="ic">▤</span> ${deck.length} 项待决策 <span class="chev">▴</span>
        </button>`;
    } else {
      const folders = deck
        .map((dc, i) => {
          const front = i === deck.length - 1;
          const tabx = 18 + (M.decisions.length - 1 - M.decisions.findIndex((x) => x.id === dc.id)) * 118;
          return `
          <div class="vd-folder k-${dc.kind} ${front ? "front" : ""}" data-fid="${dc.id}" style="--tabx:${tabx}px">
            <span class="ftab">${KIND[dc.kind]}</span>
            ${front
              ? `<div class="fbody">
                   <b>${dc.title}</b><p>${dc.body}</p>
                   <div class="acts">${dc.actions
                     .map((a, j) => `<button class="${j === 0 ? "pri" : ""}" data-decide="${dc.id}" data-label="${a}"
                        ${dc.kind === "clarify" && j < 2 ? 'data-resolve="1"' : ""}>${a}</button>`)
                     .join("")}</div>
                 </div>`
              : `<button class="fhead" data-front="${dc.id}"><b>${dc.title}</b><span>点击置顶</span></button>`}
          </div>`;
        })
        .join("");
      wrap.innerHTML = `
        <button class="vd-stackbar" id="vd-stacktoggle">
          <span class="ic">▤</span> ${deck.length} 项待决策 <span class="chev">▾ 收起</span>
        </button>
        <div class="vd-folders">${folders}</div>`;
      wrap.querySelectorAll("[data-front]").forEach((b) =>
        b.addEventListener("click", () => {
          const id = b.dataset.front;
          const item = deck.find((x) => x.id === id);
          deck = deck.filter((x) => x.id !== id).concat(item);
          renderStack();
        })
      );
      bindDecide(wrap);
    }
    wrap.querySelector("#vd-stacktoggle").addEventListener("click", () => {
      deckHidden = !deckHidden;
      renderStack();
    });
  }
  renderStack();

  /* ---------- 悬浮窗交互 ---------- */
  root.querySelectorAll("[data-cxtoggle]").forEach((b) =>
    b.addEventListener("click", () => b.closest(".cx-group").classList.toggle("open"))
  );
  root.querySelector("#cx-min").addEventListener("click", () =>
    root.querySelector("#vd-ctx").classList.toggle("min")
  );

  bindDecide(streamEl);
  dlg.addEventListener("close", () => {
    if (dlg.returnValue === "ok") {
      deck = deck.filter((x) => x.id !== "D-1");
      renderStack();
      window.showToast("已批准：授权 30 分钟内有效，合并按序推进（原型演示）");
    }
  });

  const composer = root.querySelector("#vd-composer");
  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    const ta = composer.querySelector("textarea");
    const text = ta.value.trim();
    if (!text) return;
    streamEl.insertAdjacentHTML(
      "beforeend",
      `<div class="vd-msg">${msgHead("王倩", "HUMAN", "now", "user")}<div class="vd-bubble user">${text.replace(/</g, "&lt;")}</div></div>`
    );
    ta.value = "";
    streamEl.scrollTop = streamEl.scrollHeight;
    setTimeout(() => {
      streamEl.insertAdjacentHTML(
        "beforeend",
        `<div class="vd-msg">${msgHead("Project Manager", "AGENT", "now", "agent")}<div class="vd-bubble">收到。我会把结论结构化回写到契约或任务，并同步给相关 Worker（原型演示，无真实执行）。</div></div>`
      );
      streamEl.scrollTop = streamEl.scrollHeight;
    }, 600);
  });
};
