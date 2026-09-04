import { useEffect, useRef, useState } from "react";
import type { IssueListItemView, IssueRepositoryRef } from "../../api/contract";
import { parseRequirementDocument } from "../../api/issues";
import { fetchIssueDetail, fetchRooms } from "../../api/rooms";
import { ErrorPanel, LoadingLine } from "../../components/StatusBlocks";
import { dayLabel, errText, shortId } from "../../display";
import {
  buildWorkStream,
  newSessionStream,
  workCardAnchor,
  type WorkCard,
} from "./streamModel";

/** IDE 式工作台（改造期 1 骨架）。
 *
 *  一个对话 = 一个 issue：中央列是交付主线的对话流（顶部折叠 DAG 条 + 卡片流 +
 *  吸底输入框），右侧是仓库房间面板（期 1 只有壳，期 3 接房间数据）。
 *
 *  两态：
 *   - `issueId === null`：新会话。输入框可用，发送即 createIssue（幂等键/附件
 *     解析与原 NewIssueModal 同一套契约），成功后由外壳路由进该 issue；
 *   - `issueId` 有值：既有会话。输入框按已知缺口**置灰**（后端还没有「往 issue
 *     追加说明」的端点），只读地呈现卡片流。
 *
 *  取数是详情 + 房间的一次并发（与 IssueDetailContainer 同源同端点）；期 2 才引入
 *  轮询让卡片「流」起来，本期先把骨架与数据中枢接对。 */

const DOC_ACCEPT = ".txt,.md,.docx,.pdf,.odt,.rtf";

export function WorkbenchPage({
  issueId,
  workspaceName,
  onCreateIssue,
  onToast,
}: {
  /** null = 新会话；否则为既有 issue 的 id */
  issueId: string | null;
  /** 当前工作区名（新会话的作用范围提示；null = 未选工作区） */
  workspaceName: string | null;
  /** 新会话发送：与原 NewIssueModal 同一写回路（外壳持有幂等键语义之外的部分） */
  onCreateIssue: (
    text: string,
    idempotencyKey: string,
    documentFilename: string | null,
  ) => Promise<IssueListItemView>;
  onToast: (text: string) => void;
}) {
  const isNew = issueId === null;

  const [detail, setDetail] = useState<Awaited<ReturnType<typeof fetchIssueDetail>> | null>(null);
  const [rooms, setRooms] = useState<Awaited<ReturnType<typeof fetchRooms>>>([]);
  const [loading, setLoading] = useState(!isNew);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (isNew) {
      setDetail(null);
      setRooms([]);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([fetchIssueDetail(issueId), fetchRooms(issueId)])
      .then(([d, r]) => {
        if (cancelled) return;
        setDetail(d);
        setRooms(r);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(errText(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [issueId, isNew, reload]);

  const cards: WorkCard[] = isNew
    ? newSessionStream(workspaceName)
    : detail
      ? buildWorkStream(detail, rooms)
      : [];

  // ── 右栏（期 3 接房间数据；本期先做壳与开合） ──
  const [panelRepo, setPanelRepo] = useState<(IssueRepositoryRef & { roomId: string | null }) | null>(null);

  // ── 输入框（新会话可用；既有会话按已知缺口置灰） ──
  const [draft, setDraft] = useState("");
  const [documentFilename, setDocumentFilename] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const idempotencyKey = useRef(crypto.randomUUID());
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const handleDraftChange = (text: string) => {
    setDraft(text);
    // 契约 §1.3：每次逻辑创建一个新键——文本变了就是新的逻辑创建；
    // 重试（文本不变）沿用同键，超时重点不会建出两个 issue。
    idempotencyKey.current = crypto.randomUUID();
  };

  const handleSend = () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    onCreateIssue(text, idempotencyKey.current, documentFilename)
      .then(() => {
        setDraft("");
        setDocumentFilename(null);
        idempotencyKey.current = crypto.randomUUID();
      })
      .catch((err: unknown) => onToast(`创建失败：${errText(err)}`))
      .finally(() => setSending(false));
  };

  const handlePickDocument = (file: File | undefined) => {
    if (!file) return;
    parseRequirementDocument(file)
      .then((parsed) => {
        setDraft(parsed.text);
        setDocumentFilename(parsed.filename);
        idempotencyKey.current = crypto.randomUUID();
        if (parsed.truncated) onToast(`文档较长，已截断为前 ${parsed.chars} 字（可继续编辑）`);
      })
      .catch((err: unknown) => onToast(`文档解析失败：${errText(err)}`))
      .finally(() => {
        if (fileInputRef.current) fileInputRef.current.value = "";
      });
  };

  return (
    <div className="flex h-full min-w-0 flex-1">
      {/* ── 中央列 ── */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 顶部折叠 DAG 条（期 1 占位：真实阶段条与展开图在期 4 接入） */}
        <div className="flex-none border-b border-line bg-ink px-6">
          <div className="flex h-11 items-center gap-3">
            <span className="eyebrow">流程</span>
            {detail ? (
              <>
                <span className="rounded-hard border border-line px-2 py-px font-mono text-[10.5px] text-tx2">
                  {detail.phase}
                </span>
                <span className="truncate text-[11.5px] text-tx2">{detail.phase_note}</span>
                <span className="ml-auto font-mono text-[10.5px] text-tx3">
                  {detail.repositories.length} 仓 · {detail.round_count} 轮
                </span>
              </>
            ) : (
              <span className="text-[11.5px] text-tx3">{isNew ? "新会话 · 发送需求后开始规划" : "…"}</span>
            )}
            <button
              className="ml-2 flex-none rounded-hard border border-line px-2 py-0.5 text-[10.5px] text-tx3"
              disabled
              title="期 4 接入：折叠 DAG 条与展开图"
            >
              DAG ▾
            </button>
          </div>
        </div>

        {/* 对话流 */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-[720px] flex-col gap-3 px-6 py-5">
            {loading && <LoadingLine />}
            {!loading && error && (
              <ErrorPanel
                className=""
                title="会话加载失败"
                message={error}
                onRetry={() => setReload((n) => n + 1)}
              />
            )}
            {!loading && !error && cards.map((card) => <WorkCardView key={card.anchor} card={card} onOpenRepo={setPanelRepo} />)}
          </div>
        </div>

        {/* 吸底输入框 */}
        <div className="flex-none border-t border-line bg-ink px-6 pb-4 pt-2.5">
          <div className="mx-auto max-w-[720px]">
            <div className="rounded-[8px] border border-line-strong bg-panel focus-within:border-amber">
              <textarea
                className="block h-[46px] w-full resize-none bg-transparent px-3.5 pt-2.5 font-sans text-[12.5px] leading-[1.5] text-tx outline-none"
                placeholder={
                  isNew
                    ? "输入需求 —— 发送即创建 issue 并开始规划（可先 📎 附文档；Ctrl ⏎ 发送）"
                    : "会话内补充说明待后端立项，暂不可发送（新建需求请回侧栏「＋ 新会话」）"
                }
                disabled={!isNew}
                value={draft}
                onChange={(e) => handleDraftChange(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
              />
              <div className="flex items-center gap-1 px-2 pb-1.5 pl-2.5">
                <span
                  className="inline-flex items-center gap-1.5 rounded-full border border-line-strong px-2.5 py-[2.5px] font-mono text-[11px] text-tx2"
                  title={isNew ? "需求的作用范围：归属当前工作区，交付范围由发现链确定" : "本会话的交付范围（由服务端派生）"}
                >
                  <span className="h-1.5 w-1.5 flex-none rounded-full bg-bluegray" />
                  {isNew
                    ? workspaceName ?? "未选择工作区"
                    : detail
                      ? `${detail.repositories.length} 个仓库`
                      : "…"}
                </span>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={DOC_ACCEPT}
                  className="hidden"
                  onChange={(e) => handlePickDocument(e.target.files?.[0])}
                />
                <button
                  className="grid h-[27px] w-[27px] place-items-center rounded-hard text-[13px] text-tx3 hover:bg-panel-2 hover:text-tx disabled:opacity-40"
                  title={isNew ? "上传需求文档（解析为文本继续编辑）" : "仅新会话可用"}
                  disabled={!isNew}
                  onClick={() => fileInputRef.current?.click()}
                >
                  📎
                </button>
                <button
                  className="grid h-[27px] w-[27px] place-items-center rounded-hard text-[13px] text-tx3 hover:bg-panel-2 hover:text-tx"
                  disabled
                  title="截图 / 粘贴图片（二期）"
                >
                  🖼
                </button>
                <button
                  className="ml-auto grid h-7 w-7 place-items-center rounded-hard bg-amber text-[12px] text-on-amber hover:bg-amber-hi disabled:opacity-40"
                  title={isNew ? "发送（Ctrl+Enter）" : "会话内补充说明待后端立项"}
                  disabled={!isNew || sending || draft.trim() === ""}
                  onClick={handleSend}
                >
                  {sending ? "…" : "➤"}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── 右栏：仓库房间面板（期 3 接入房间数据） ── */}
      <aside
        className={`flex-none overflow-hidden border-line bg-panel transition-[width] duration-150 ${
          panelRepo ? "w-[352px] border-l" : "w-0"
        }`}
      >
        <div className="flex h-full w-[352px] flex-col">
          <div className="border-b border-line px-3.5 pb-2.5 pt-2.5">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[13px] font-bold text-cream">{panelRepo?.name ?? "…"}</span>
              <div className="ml-auto flex gap-1.5">
                <button
                  className="h-6 w-6 rounded-hard border border-line-strong text-[11px] text-tx2 hover:border-amber hover:text-tx disabled:opacity-40"
                  title={panelRepo?.roomId ? "放大到全页房间视图（期 3 接入）" : "该仓库尚未建团"}
                  disabled={!panelRepo?.roomId}
                >
                  ⤢
                </button>
                <button
                  className="h-6 w-6 rounded-hard border border-line-strong text-[11px] text-tx2 hover:border-amber hover:text-tx"
                  title="收起"
                  onClick={() => setPanelRepo(null)}
                >
                  ✕
                </button>
              </div>
            </div>
          </div>
          <div className="flex flex-1 items-center justify-center bg-well px-6 text-center text-[11.5px] leading-[1.8] text-tx2">
            {panelRepo === null ? null : panelRepo.roomId ? (
              <p>
                房间已定位：
                <br />
                <span className="font-mono text-[10.5px] text-tx3">{shortId(panelRepo.roomId)}</span>
                <br />
                消息流、agent 醒睡态与事件时间线在期 3 接入。
              </p>
            ) : (
              <p>该仓库尚未建团，房间会在计划物化后出现。</p>
            )}
          </div>
        </div>
      </aside>
    </div>
  );
}

/** 卡片渲染：样式与原型一致，判定逻辑全部在 streamModel（本组件不写映射）。 */
function WorkCardView({
  card,
  onOpenRepo,
}: {
  card: WorkCard;
  onOpenRepo: (repo: IssueRepositoryRef & { roomId: string | null }) => void;
}) {
  switch (card.kind) {
    case "requirement":
      return (
        <div className="flex justify-end" id={workCardAnchor(card)}>
          <div className="max-w-[78%] rounded-[10px_10px_3px_10px] bg-amber px-3.5 py-2.5 text-on-amber">
            <div className="mb-0.5 font-mono text-[10px] opacity-65">
              你 · {dayLabel(card.openedAt)}
              {card.openedByName ? ` · ${card.openedByName}` : ""}
            </div>
            <div className="whitespace-pre-wrap text-[12.5px] leading-[1.65]">{card.text}</div>
            {card.documentFilename && (
              <div className="mt-1.5 inline-flex items-center gap-1.5 rounded-hard bg-white/10 px-2 py-0.5 font-mono text-[10.5px]">
                📎 {card.documentFilename}
              </div>
            )}
          </div>
        </div>
      );
    case "phase":
      return (
        <div className="rounded-hard border border-line bg-panel px-3.5 py-2.5 shadow-card" id={workCardAnchor(card)}>
          <div className="mb-1.5 flex items-baseline gap-2">
            <span className="microlabel">阶段</span>
            <span className="rounded-hard border border-line px-1.5 py-px font-mono text-[10px] text-tx2">
              {card.phase}
            </span>
            <span className="ml-auto font-mono text-[10px] text-tx3">{dayLabel(card.updatedAt)}</span>
          </div>
          <p className="text-[12px] leading-[1.7] text-tx">{card.note}</p>
        </div>
      );
    case "plan":
      return (
        <div className="rounded-hard border border-line bg-panel px-3.5 py-2.5 shadow-card" id={workCardAnchor(card)}>
          <div className="mb-1.5 flex items-baseline gap-2">
            <span className="microlabel">规划</span>
            <span className="text-[12.5px] font-bold text-cream">plan v{card.planVersion} 已冻结</span>
            <span className="ml-auto font-mono text-[10px] text-tx3">{dayLabel(card.at)}</span>
          </div>
          <p className="text-[11.5px] text-tx2">第 {card.roundIndex} 轮快照 · 任务级 DAG 在顶部「DAG」展开查看（期 4 接入）。</p>
        </div>
      );
    case "teams":
      return (
        <div className="rounded-hard border border-line bg-panel px-3.5 py-2.5 shadow-card" id={workCardAnchor(card)}>
          <div className="mb-1.5"><span className="microlabel">建团</span></div>
          <div className="grid gap-1">
            {card.teams.map((team) => (
              <div key={team.teamId} className="flex items-baseline gap-2 text-[12px]">
                <span className="font-mono text-[11.5px] text-tx">{team.name}</span>
                <span className="text-tx2">{team.repositoryName ?? "仓库未解析"}</span>
                <span className="ml-auto font-mono text-[10.5px] text-tx3">{team.runtimeStatus}</span>
              </div>
            ))}
          </div>
        </div>
      );
    case "repositories":
      return (
        <div className="rounded-hard border border-line bg-panel px-3.5 py-2.5 shadow-card" id={workCardAnchor(card)}>
          <div className="mb-2"><span className="microlabel">房间</span><span className="ml-2 text-[11.5px] text-tx2">点击仓库在右侧打开房间面板</span></div>
          <div className="flex flex-wrap gap-2">
            {card.repos.map((repo) => (
              <button
                key={repo.repository_id}
                className="inline-flex items-center gap-1.5 rounded-hard border border-line-strong bg-ink px-2.5 py-1 font-mono text-[11.5px] text-tx hover:border-amber hover:shadow-card disabled:opacity-60"
                disabled={repo.roomId === null}
                title={repo.roomId ? "打开右侧房间面板" : "尚未建团，物化后出现房间"}
                onClick={() => onOpenRepo(repo)}
              >
                <span className={`h-[7px] w-[7px] rounded-full ${repo.team_id ? "bg-olive" : "bg-tx3"}`} />
                {repo.name}
                <span className="text-[9px] text-tx3">▸</span>
              </button>
            ))}
          </div>
        </div>
      );
    case "round":
      return (
        <div className="rounded-hard border border-line bg-panel px-3.5 py-2.5 shadow-card" id={workCardAnchor(card)}>
          <div className="flex items-baseline gap-2">
            <span className="microlabel">轮次 {card.index}</span>
            <span className="text-[12.5px] font-bold text-cream">{card.status}</span>
            {card.active && (
              <span className="rounded-hard border border-bluegray px-1.5 py-px font-mono text-[9.5px] text-bluegray">
                当前
              </span>
            )}
            <span className="ml-auto font-mono text-[10px] text-tx3">
              {card.planVersion !== null ? `plan v${card.planVersion} · ` : ""}
              {dayLabel(card.updatedAt)}
            </span>
          </div>
          <p className="mt-1 text-[11px] text-tx3">任务明细与原地进度在期 2 接入（fetchRoundDecisionHistory）。</p>
        </div>
      );
    case "note":
      return (
        <div className="rounded-hard bg-panel-2 px-3.5 py-2.5 text-[11.5px] leading-[1.7] text-tx2" id={workCardAnchor(card)}>
          {card.text}
        </div>
      );
  }
}
