import { useCallback, useRef, useState } from "react";
import { ApprovalModal } from "./components/ApprovalModal";
import { DecisionDeck } from "./components/DecisionDeck";
import { EnvPanel } from "./components/EnvPanel";
import { MessageStream } from "./components/MessageStream";
import { PlanView } from "./components/PlanView";
import { Sidebar } from "./components/Sidebar";
import { decisions, delivery, initialMessages } from "./data/mock";
import type { ChatMessage, Decision } from "./types";

type View = "room" | "plan";

/** 决策夹数组末位为最前的文件夹（与原型语义一致）。 */
const initialDeck = decisions.slice().reverse();

let msgSeq = 0;

export default function App() {
  const [view, setView] = useState<View>("room");
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [deck, setDeck] = useState<Decision[]>(initialDeck);
  const [deckHidden, setDeckHidden] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const toastTimer = useRef<number | undefined>(undefined);

  const showToast = useCallback((text: string) => {
    setToast(text);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 2600);
  }, []);

  const bringToFront = (id: string) =>
    setDeck((prev) => {
      const item = prev.find((x) => x.id === id);
      if (!item) return prev;
      return prev.filter((x) => x.id !== id).concat(item);
    });

  const handleDecisionAction = (decision: Decision, actionIdx: number) => {
    if (decision.id === "D-1" && actionIdx === 0) {
      setApprovalOpen(true);
      return;
    }
    const label = decision.actions[actionIdx];
    if (decision.kind === "clarify" && actionIdx < 2) {
      setDeck((prev) => prev.filter((x) => x.id !== decision.id));
      showToast(`已记录：「${label}」，结论回写契约（mock）`);
    } else {
      showToast(`MVP：「${label}」尚未接入`);
    }
  };

  const handleApprovalClose = (approved: boolean) => {
    setApprovalOpen(false);
    if (approved) {
      setDeck((prev) => prev.filter((x) => x.id !== "D-1"));
      showToast("已批准：授权 30 分钟内有效，合并按序推进（mock）");
    }
  };

  const handleSend = (e: React.FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    setMessages((prev) => [
      ...prev,
      { id: `u${++msgSeq}`, author: "王倩", role: "HUMAN", time: "now", tone: "user", text },
    ]);
    window.setTimeout(() => {
      setMessages((prev) => [
        ...prev,
        {
          id: `a${++msgSeq}`,
          author: "Project Manager",
          role: "AGENT",
          time: "now",
          tone: "agent",
          text: "收到。我会把结论结构化回写到契约或任务，并同步给相关 Worker（mock，无真实执行）。",
        },
      ]);
    }, 600);
  };

  return (
    <div className="flex h-screen overflow-hidden bg-ink text-tx">
      <Sidebar onToast={showToast} />

      <main className="relative flex min-w-0 flex-1 flex-col bg-ink">
        <header className="ticks-amber flex-none border-b border-line px-[22px] pt-3 pb-2.5">
          <div className="font-mono text-[10.5px] tracking-[0.1em] text-tx2">
            SALEOR COMMERCE › {delivery.id} · MATRIX #dlv-0042
          </div>
          <div className="mt-[7px] flex items-center gap-3">
            <span className="inline-grid size-7 flex-none place-items-center rounded-hard bg-amber text-[12px] font-extrabold text-[#1c170c]">
              PM
            </span>
            <h1 className="text-[16px] font-semibold text-cream">{delivery.title}</h1>
            <span className="inline-flex flex-none items-center gap-1.5 rounded-hard bg-cream px-2.5 py-[3px] font-mono text-[11.5px] font-extrabold tracking-[0.08em] text-[#1c170c]">
              <i className="not-italic text-[#b3402a] blink-fast">●</i>RUN 2H09M
            </span>
            <div className="ml-auto flex flex-none overflow-hidden rounded-hard border border-line">
              {(
                [
                  ["room", "房间"],
                  ["plan", "DAG · 计划"],
                ] as Array<[View, string]>
              ).map(([key, label]) => (
                <button
                  key={key}
                  className={
                    view === key
                      ? "bg-amber px-4 py-1.5 text-[12.5px] font-bold text-[#191308]"
                      : "bg-transparent px-4 py-1.5 text-[12.5px] text-tx2 hover:text-amber-hi"
                  }
                  onClick={() => setView(key)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </header>

        {view === "room" ? (
          <MessageStream messages={messages} onApprove={() => setApprovalOpen(true)} />
        ) : (
          <PlanView />
        )}

        <DecisionDeck
          deck={deck}
          hidden={deckHidden}
          onToggleHidden={() => setDeckHidden((v) => !v)}
          onBringToFront={bringToFront}
          onAction={handleDecisionAction}
        />

        <form
          className="relative z-[6] flex-none border-t border-line bg-[#191510] px-[22px] pt-3 pb-3.5 shadow-[0_-10px_24px_rgba(0,0,0,0.45)]"
          onSubmit={handleSend}
        >
          <textarea
            rows={2}
            className="w-full resize-none rounded-hard border border-line bg-panel px-3 py-2.5 font-sans text-[13px] text-tx placeholder:text-[#6b6046] focus:outline focus:outline-amber"
            placeholder="询问状态、调整范围，或要求 Project Manager 解释决策…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                e.currentTarget.form?.requestSubmit();
              }
            }}
          />
          <div className="mt-2 flex items-center">
            <span className="text-[10px] tracking-[0.1em] text-tx2">MSG → MATRIX · 关键结论结构化回写事实库</span>
            <button
              type="submit"
              className="ml-auto rounded-hard bg-amber px-[18px] py-[7px] text-[12.5px] font-extrabold tracking-[0.04em] text-[#191308] hover:bg-amber-hi"
            >
              发送 ↑
            </button>
          </div>
        </form>

        <EnvPanel onToast={showToast} />
      </main>

      <ApprovalModal open={approvalOpen} onClose={handleApprovalClose} />

      {toast && (
        <div className="fixed bottom-[72px] left-1/2 z-[999] -translate-x-1/2 rounded-lg border border-white/15 bg-[#14161c] px-4 py-2 text-[12.5px] text-white">
          {toast}
        </div>
      )}
    </div>
  );
}
