import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApprovalModal } from "./components/ApprovalModal";
import { DecisionDeck } from "./components/DecisionDeck";
import { EnvPanel } from "./components/EnvPanel";
import { MessageStream } from "./components/MessageStream";
import { PlanView } from "./components/PlanView";
import { Sidebar } from "./components/Sidebar";
import { ReplayBar } from "./components/ReplayBar";
import { createDataSource, resolveDataSourceMode } from "./api/source";
import type { DeliveryData } from "./api/source";
import { SCENES } from "./data/scenes";
import { deriveChat, deriveView } from "./viewmodel";
import type { ChatMessage, Decision } from "./types";

type View = "room" | "plan";

/** 场景自动推进间隔（回放模式） */
const SCENE_INTERVAL_MS = 7000;

let msgSeq = 0;

export default function App() {
  const mode = useMemo(resolveDataSourceMode, []);
  const source = useMemo(() => createDataSource(mode), [mode]);

  const [data, setData] = useState<DeliveryData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [view, setView] = useState<View>("room");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [deck, setDeck] = useState<Decision[]>([]);
  const [deckHidden, setDeckHidden] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const toastTimer = useRef<number | undefined>(undefined);

  // 回放场景状态机：默认停在终态（审批合并），▶ 从头推进完整闭环
  const sceneCount = source.sceneCount ?? 0;
  const [sceneIdx, setSceneIdx] = useState(Math.max(0, sceneCount - 1));
  const [playing, setPlaying] = useState(false);

  const delivery = useMemo(() => (data ? deriveView(data) : null), [data]);

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    source.setScene?.(sceneIdx);
    source
      .fetchAll()
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setMessages(deriveChat(d));
        const derived = deriveView(d);
        // 决策夹数组末位为最前的文件夹（与原型语义一致）
        setDeck(derived ? derived.decisions.slice().reverse() : []);
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [source, sceneIdx]);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setSceneIdx((i) => {
        if (i + 1 >= sceneCount) {
          setPlaying(false);
          return i;
        }
        return i + 1;
      });
    }, SCENE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [playing, sceneCount]);

  const handleTogglePlay = () => {
    if (playing) {
      setPlaying(false);
      return;
    }
    if (sceneIdx >= sceneCount - 1) setSceneIdx(0);
    setPlaying(true);
  };

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
    const actionKind = decision.actionKinds?.[actionIdx];
    if (decision.kind === "approve" && actionKind === "approve_merge") {
      setApprovalOpen(true);
      return;
    }
    const label = decision.actions[actionIdx];
    if (decision.kind === "clarify" && actionIdx < 2) {
      // clarify 仅回放模式存在（契约 §6.5），消化即移除
      setDeck((prev) => prev.filter((x) => x.id !== decision.id));
      showToast(`已记录：「${label}」，结论回写契约（回放演示）`);
    } else {
      showToast(`「${label}」将在决策夹写回路（CONS-12）接入`);
    }
  };

  const handleApprovalClose = (approved: boolean) => {
    setApprovalOpen(false);
    if (approved) {
      setDeck((prev) => prev.filter((x) => x.kind !== "approve"));
      // POST /governance-decisions 写回路属 CONS-12，当前仅前端演示
      showToast("已批准：授权 30 分钟内有效，合并按序推进（演示，未写回后端）");
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
          text: "收到。我会把结论结构化回写到契约或任务，并同步给相关 Worker（演示，无真实执行）。",
        },
      ]);
    }, 600);
  };

  return (
    <div className="flex h-screen overflow-hidden bg-ink text-tx">
      <Sidebar
        list={data?.list ?? { projects: [], next_cursor: null }}
        activeDeliveryId={data?.aggregate?.delivery_id ?? null}
        pendingCount={deck.length}
        onToast={showToast}
      />

      <main className="relative flex min-w-0 flex-1 flex-col bg-ink">
        {mode === "replay" && sceneCount > 0 && (
          <ReplayBar
            scenes={SCENES}
            current={sceneIdx}
            playing={playing}
            onSelect={(i) => {
              setPlaying(false);
              setSceneIdx(i);
            }}
            onTogglePlay={handleTogglePlay}
            onReset={() => {
              setPlaying(false);
              setSceneIdx(0);
            }}
          />
        )}
        {loadError && (
          <div className="flex-none border-b border-[#7a4530] bg-[#2b1712] px-[22px] py-2 text-[12.5px] text-[#e8a184]">
            <b className="mr-2 font-mono tracking-[0.08em]">LIVE 数据源不可用</b>
            {loadError} —— 后端读模型（CONS-03）未就绪时可在 URL 加{" "}
            <code className="font-mono text-amber-hi">?source=replay</code> 查看回放。
          </div>
        )}

        {delivery ? (
          <>
            <header className="ticks-amber flex-none border-b border-line px-[22px] pt-3 pb-2.5">
              <div className="font-mono text-[10.5px] tracking-[0.1em] text-tx2 uppercase">
                {data?.list.projects[0]?.title ?? ""} › {delivery.label}
                {delivery.matrixRoom ? ` · MATRIX ${delivery.matrixRoom}` : ""}
              </div>
              <div className="mt-[7px] flex items-center gap-3">
                <span className="inline-grid size-7 flex-none place-items-center rounded-hard bg-amber text-[12px] font-extrabold text-[#1c170c]">
                  PM
                </span>
                <h1 className="text-[16px] font-semibold text-cream">{delivery.title}</h1>
                {delivery.runLabel && (
                  <span className="inline-flex flex-none items-center gap-1.5 rounded-hard bg-cream px-2.5 py-[3px] font-mono text-[11.5px] font-extrabold tracking-[0.08em] text-[#1c170c]">
                    <i className="not-italic text-[#b3402a] blink-fast">●</i>
                    {delivery.runLabel}
                  </span>
                )}
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
              <MessageStream messages={messages} view={delivery} onApprove={() => setApprovalOpen(true)} />
            ) : (
              <PlanView view={delivery} />
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

            <EnvPanel view={delivery} onToast={showToast} />
          </>
        ) : (
          <div className="grid flex-1 place-items-center">
            <div className="text-center">
              <span className="mx-auto mb-3 grid size-[42px] place-items-center rounded-hard bg-amber font-mono text-[18px] font-extrabold text-[#16120a]">
                R
              </span>
              <p className="font-mono text-[12px] tracking-[0.14em] text-tx2 uppercase">
                {loadError ? "无交付数据 · 读模型不可达" : data ? "该项目暂无已物化交付" : "载入交付数据…"}
              </p>
            </div>
          </div>
        )}
      </main>

      <ApprovalModal open={approvalOpen} info={delivery?.approval ?? null} onClose={handleApprovalClose} />

      {toast && (
        <div className="fixed bottom-[72px] left-1/2 z-[999] -translate-x-1/2 rounded-lg border border-white/15 bg-[#14161c] px-4 py-2 text-[12.5px] text-white">
          {toast}
        </div>
      )}
    </div>
  );
}
