// PROTOTYPE — 入口：?variant= 切换 + 悬浮切换条 + toast
(function () {
  const VARIANTS = [
    { key: "a", name: "流水叙事", render: window.renderVariantA },
    { key: "b", name: "指挥中心", render: window.renderVariantB },
    { key: "c", name: "决策收件箱", render: window.renderVariantC },
    { key: "d", name: "融合工作台", render: window.renderVariantD },
  ];

  const root = document.getElementById("root");

  function current() {
    const k = new URLSearchParams(location.search).get("variant") || "d";
    return Math.max(0, VARIANTS.findIndex((v) => v.key === k.toLowerCase()));
  }

  function go(idx) {
    const i = (idx + VARIANTS.length) % VARIANTS.length;
    const v = VARIANTS[i];
    history.replaceState(null, "", "?variant=" + v.key);
    v.render(root);
    bindToasts();
    renderBar(i);
    window.scrollTo(0, 0);
  }

  function renderBar(i) {
    let bar = document.querySelector(".proto-bar");
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "proto-bar";
      document.body.appendChild(bar);
    }
    const v = VARIANTS[i];
    bar.innerHTML = `
      <button aria-label="上一个方案">←</button>
      <span class="lb"><b>${v.key.toUpperCase()}</b> — ${v.name}（${i + 1}/${VARIANTS.length}）</span>
      <button aria-label="下一个方案">→</button>`;
    const [prev, next] = bar.querySelectorAll("button");
    prev.onclick = () => go(i - 1);
    next.onclick = () => go(i + 1);
  }

  let toastEl, toastTimer;
  window.showToast = function (msg) {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "proto-toast";
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 1800);
  };

  function bindToasts() {
    root.querySelectorAll("[data-toast]").forEach((b) =>
      b.addEventListener("click", () => window.showToast(b.dataset.toast))
    );
  }

  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
    if (e.key === "ArrowLeft") go(current() - 1);
    if (e.key === "ArrowRight") go(current() + 1);
  });

  go(current());
})();
