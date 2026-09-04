import { Fragment, useEffect, useRef, useState } from "react";
import type { DeliveryAggregate, IssueListItemView, IssueRepositoryRef } from "../../api/contract";
import { parseRequirementDocument } from "../../api/issues";
import { fetchIssueDetail, fetchRooms } from "../../api/rooms";
import {
  fetchDecisionDeck,
  fetchRoundDecisionHistory,
  resolveGovernanceAgent,
  submitGovernanceDecision,
  type GovernanceAgent,
} from "../../api/decisions";
import { resolveDataSourceMode } from "../../api/source";
import {
  fetchDiscovery,
  newIdempotencyKey,
  triggerAnalysis,
  triggerCandidates,
  triggerClassification,
  triggerPlan,
} from "../../api/discovery";
import type { DiscoveryView } from "../../api/contract";
import { AssistantFlow } from "./AssistantFlow";
import { autoTrigger } from "./autoTrigger";
import { useIssueFlowState } from "./useIssueFlowState";
import { ErrorPanel, LoadingLine } from "../../components/StatusBlocks";
import { dayLabel, errText, shortId } from "../../display";
import type { Decision, EvidenceView } from "../../types";
import { approvalForDecision, evidenceFromAggregate } from "../../viewmodel";
import {
  buildWorkStream,
  newSessionStream,
  workCardAnchor,
  type RoundTaskRow,
  type WorkCard,
} from "./streamModel";
import { EvidenceModal } from "../../components/EvidenceModal";
import { RoomPanel } from "./RoomPanel";

/** IDE 式工作台（期 1 骨架 + 期 2 卡片体系）。
 *
 *  一个对话 = 一个 issue：中央列是交付主线的对话流（顶部折叠 DAG 条 + 卡片流 +
 *  吸底输入框），右侧是仓库房间面板（期 3 接房间数据）。
 *
 *  数据节奏（B 定稿「先复用现有接口」）：详情 + 房间 + 活跃轮决策夹 + 各轮任务
 *  明细每 5s 静默轮询一次，卡片按 streamModel 重建——新卡依次出现、轮次任务
 *  tick 原地更新。这是「轮询拼装出的流式」，真·事件流等后端立项。
 *
 *  流内审批（期 2）：approve 类决策卡就地批准——授权单按点击的卡构建（S1），
 *  head-bound 提交（409 = SHA 漂移，错误显示在卡内不静默）；回放模式不写后端，
 *  就地演示并如实注明。驳回不在决策卡上：治理写入只有 ready，拒绝走回滚 saga
 *  （另一条回路，入口在轮次操作里）。
 *
 *  两态：`issueId === null` 新会话（发送即 createIssue）；有值 = 既有会话，输入框
 *  按已知缺口置灰（后端还没有「往 issue 追加说明」的端点）。 */

const DOC_ACCEPT = ".txt,.md,.docx,.pdf,.odt,.rtf";
const POLL_MS = 5000;
/** 发现链步号 → 触发端点的幂等键前缀（与 DiscoveryPanel 同一套键位）。 */
const STEP_KEY_BY_STEP = {
  1: "analysis",
  2: "candidates",
  3: "classification",
  4: "plan",
} as const;

interface ActiveDeck {
  roundId: string;
  roundIndex: number;
  decisions: Decision[];
  aggregate: DeliveryAggregate;
}

export function WorkbenchPage({
  issueId,
  workspaceName,
  onCreateIssue,
  onOpenRoom,
  onBack,
  onToast,
}: {
  /** null = 新会话；否则为既有 issue 的 id */
  issueId: string | null;
  workspaceName: string | null;
  onCreateIssue: (
    text: string,
    idempotencyKey: string,
    documentFilename: string | null,
  ) => Promise<IssueListItemView>;
  /** 右栏「⤢ 放大」：跳转全页房间视图（外壳负责路由） */
  onOpenRoom: (roomId: string) => void;
  /** 顶栏「‹ 议题列表」：回 issue 列表（外壳负责路由）。新会话态不渲染。 */
  onBack?: () => void;
  onToast: (text: string) => void;
}) {
  const isNew = issueId === null;

  const [detail, setDetail] = useState<Awaited<ReturnType<typeof fetchIssueDetail>> | null>(null);
  const [rooms, setRooms] = useState<Awaited<ReturnType<typeof fetchRooms>>>([]);
  const [loading, setLoading] = useState(!isNew);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [lastSyncAt, setLastSyncAt] = useState<Date | null>(null);
  /** 静默轮询与首载的界线：换 issue 才整页 loading，轮询只换数据不闪屏 */
  const loadedIssueRef = useRef<string | null>(null);

  // ── 对话流自动滚底：进入会话 / 新卡出现时跟随到底部；用户上翻阅读时不抢滚动 ──
  const streamRef = useRef<HTMLDivElement | null>(null);
  const nearBottomRef = useRef(true);
  const handleStreamScroll = () => {
    const el = streamRef.current;
    if (!el) return;
    nearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };
  const scrollToBottom = () => {
    const el = streamRef.current;
    // 平滑滚动：瞬移读起来像闪跳，新卡片是「滑进来」而不是「砸上来」
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };

  useEffect(() => {
    if (isNew) {
      loadedIssueRef.current = null;
      setDetail(null);
      setRooms([]);
      setLoading(false);
      setError(null);
      return;
    }
    const firstVisit = loadedIssueRef.current !== issueId;
    let cancelled = false;
    if (firstVisit) {
      loadedIssueRef.current = issueId;
      setLoading(true);
      setError(null);
    }
    Promise.all([fetchIssueDetail(issueId), fetchRooms(issueId)])
      .then(([d, r]) => {
        if (cancelled) return;
        setDetail(d);
        setRooms(r);
        setLoading(false);
        setLastSyncAt(new Date());
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

  // 流式节奏：5s 静默轮询（B 定稿路线；事件流另立项）
  useEffect(() => {
    if (isNew) return;
    const timer = window.setInterval(() => setReload((n) => n + 1), POLL_MS);
    return () => window.clearInterval(timer);
  }, [isNew]);

  const activeRoundId = detail ? (detail.active_round_id ?? detail.latest_round_id ?? null) : null;
  const activeRoundIndex = detail && activeRoundId ? detail.rounds.findIndex((r) => r.round_id === activeRoundId) + 1 : 0;

  // ── 活跃轮决策夹（审批卡的数据源；每次轮询都重取，别人批掉的卡会消失） ──
  const [activeDeck, setActiveDeck] = useState<ActiveDeck | null>(null);
  useEffect(() => {
    if (isNew || !detail || !activeRoundId || activeRoundIndex === 0) {
      setActiveDeck(null);
      return;
    }
    let cancelled = false;
    fetchDecisionDeck(activeRoundId)
      .then((data) => {
        if (cancelled) return;
        setActiveDeck({ roundId: activeRoundId, roundIndex: activeRoundIndex, decisions: data.deck, aggregate: data.aggregate });
      })
      .catch(() => {
        if (!cancelled) setActiveDeck(null);
      });
    return () => {
      cancelled = true;
    };
  // reload 不入依赖：detail 身份每轮轮询必变，本 effect 已随它重跑；
  // 再叠 reload 会造成每轮双倍请求。
  }, [isNew, detail, activeRoundId, activeRoundIndex]);

  // ── 各轮任务明细（轮次卡的 tick 行） ──
  const [tasksByRound, setTasksByRound] = useState<Record<string, import("../../api/contract").DeliveryTaskView[]>>({});
  const historyEpoch = useRef(0);
  useEffect(() => {
    if (isNew || !detail || detail.rounds.length === 0) {
      setTasksByRound({});
      return;
    }
    const epoch = ++historyEpoch.current;
    detail.rounds.forEach((round) => {
      fetchRoundDecisionHistory(round.round_id)
        .then((data) => {
          // A6 同款：换代后在途响应不落桶
          if (epoch !== historyEpoch.current) return;
          setTasksByRound((prev) => ({ ...prev, [round.round_id]: data.tasks }));
        })
        .catch(() => {
          // replay 夹具未覆盖历史轮等：该轮没有明细就明说，不摆假进度
        });
    });
  }, [isNew, detail]);

  // ── 治理决策主体（流内批准的「谁在批」） ──
  const organizationId = detail?.organization_id ?? null;
  const [principal, setPrincipal] = useState<GovernanceAgent | null>(null);
  const [principalResolving, setPrincipalResolving] = useState(true);
  useEffect(() => {
    if (isNew || !detail) return;
    let cancelled = false;
    setPrincipalResolving(true);
    resolveGovernanceAgent(organizationId)
      .then((agent) => !cancelled && setPrincipal(agent))
      .catch(() => !cancelled && setPrincipal(null))
      .finally(() => !cancelled && setPrincipalResolving(false));
    return () => {
      cancelled = true;
    };
  }, [isNew, detail, organizationId]);

  const flow = useIssueFlowState(issueId ?? "", detail, reload);
  // 推动卡只在「尚未物化」的会话出现：发现→计划→物化整条回路都在面板里，
  // 物化成功后轮次卡接管叙事（roundCount>0 时面板里的按钮本来也会消失）。
  const showDiscovery = !isNew && detail !== null && detail.rounds.length === 0;

  // ── 处理员自动推进（B 定稿的对话式体验）──
  // 需求一发出去，处理员就开始干活：发现链哪一步「待开始」，就自动触发哪一步，
  // 不让人对着「开始分析」按钮点头。人审门不动：第 3 步分档审批的提交、以及
  // 物化确认，仍由人操作——读模型在门没过前不会把步进器往前走，所以这里的
  // 自动触发天然越不过门。
  const [discovery, setDiscovery] = useState<DiscoveryView | null>(null);
  useEffect(() => {
    if (!showDiscovery || !detail) {
      setDiscovery(null);
      return;
    }
    let cancelled = false;
    const tick = () =>
      fetchDiscovery(detail.issue_id)
        .then((view) => !cancelled && setDiscovery(view))
        .catch(() => undefined);
    tick();
    const timer = window.setInterval(tick, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [showDiscovery, detail, reload]);

  useEffect(() => {
    if (resolveDataSourceMode() === "replay") return; // 回放里写入口一律如实拒绝，不空转
    if (!discovery) return;
    if (discovery.step_state !== "idle") return;
    // 读投影滞后保护：任务句柄还在，就是有一步在跑——不重发（409 的根源）
    if (discovery.running_task_id !== null) return;
    if (!principal) return; // 解析不出主体时步骤会停住，等花名册恢复
    const key = `${discovery.issue_id}:${discovery.step}`;
    if (autoTrigger.has(key)) return; // 本 visit 已触发过（失败由 FailLine 的重试入口接管，避免循环开火）
    autoTrigger.set(key, newIdempotencyKey(STEP_KEY_BY_STEP[discovery.step]));
    const payload = {
      created_by_agent_id: principal.agentId,
      idempotency_key: autoTrigger.get(key)!,
    };
    const fire =
      discovery.step === 1
        ? triggerAnalysis(discovery.issue_id, payload)
        : discovery.step === 2
          ? triggerCandidates(discovery.issue_id, payload)
          : discovery.step === 3
            ? triggerClassification(discovery.issue_id, payload)
            : triggerPlan(discovery.issue_id, payload);
    fire.catch(() => {
      // 失败后读模型会把步进器打成 failed（FailLine 给原因与重试入口）；
      // 清掉记录让「重试」能用新键重跑
      autoTrigger.delete(key);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [discovery, principal]);

  // ── 处理员对话组（纯对话式定稿：卡片与大面板均已退役）──
  const clarifyPending =
    !!discovery &&
    discovery.analysis !== null &&
    !discovery.analysis.sufficient &&
    discovery.analysis.questions.length > 0;
  const [clarifySending, setClarifySending] = useState(false);
  const handleRetryStep = (step: 1 | 2 | 3 | 4) => {
    if (!detail) return;
    autoTrigger.delete(`${detail.issue_id}:${step}`);
    setReload((n) => n + 1);
  };
  /** 追问回答走底部输入框：一条回答附到全部分析问题后（服务端拼接规则唯一实现） */
  const handleClarifySubmit = (text: string) => {
    if (!discovery || !principal || !detail || discovery.analysis === null) return;
    autoTrigger.delete(`${detail.issue_id}:1`);
    setClarifySending(true);
    triggerAnalysis(detail.issue_id, {
      created_by_agent_id: principal.agentId,
      idempotency_key: newIdempotencyKey("analysis"),
      answers: [{ question: discovery.analysis.questions.join(" ／ "), answer: text }],
    })
      .then(() => {
        setDraft("");
        idempotencyKey.current = crypto.randomUUID();
        onToast("已回答，处理员继续分析");
        setReload((n) => n + 1);
      })
      .catch((err: unknown) => onToast(`提交回答失败：${errText(err)}`))
      .finally(() => setClarifySending(false));
  };

  const cards: WorkCard[] = isNew
    ? newSessionStream(workspaceName)
    : detail
      ? buildWorkStream({ detail, rooms, tasksByRound, activeDeck })
      : [];

  // 新内容到达（进入会话 / 新卡入流 / 轮询同步）且用户本就贴底时跟随到底部——
  // 上翻读历史时不抢滚动（nearBottom 由 onScroll 维护）。
  useEffect(() => {
    if (loading || error) return;
    if (nearBottomRef.current) scrollToBottom();
  }, [loading, error, cards.length, lastSyncAt]);

  // ── 右栏（期 3 接房间数据；本期先做壳与开合） ──
  const [panelRepo, setPanelRepo] = useState<(IssueRepositoryRef & { roomId: string | null }) | null>(null);
  /** 窄化用局部量：state 变量的 narrowing 传不进 JSX 里的回调，const 局部量可以 */
  const panelRoomId = panelRepo?.roomId ?? null;

  // ── 流内审批：就地消化 + 已处理态（不靠整页刷新才消失） ──
  const [resolvedDecisions, setResolvedDecisions] = useState<Record<string, "approved">>({});
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const [decisionErrors, setDecisionErrors] = useState<Record<string, string>>({});
  const [evidence, setEvidence] = useState<EvidenceView | null>(null);
  const [evidenceOpen, setEvidenceOpen] = useState(false);

  const handleApprove = (card: Extract<WorkCard, { kind: "decision" }>) => {
    if (!activeDeck || !activeDeck.roundId) return;
    // 授权单按**点击的这张卡**重建 Decision（S1）：repositoryId 是授权单的锚，
    // 多仓同时待批时绝不拿别的卡顶替。
    const decision: Decision = {
      id: card.decisionId,
      kind: card.decisionKind,
      title: card.title,
      body: card.body,
      actions: [],
      actionKinds: null,
      repositoryId: card.repositoryId,
      headSha: card.headSha,
    };
    const built = approvalForDecision(activeDeck.aggregate, decision);
    if (!built) {
      onToast("授权单不可用：该决策未指向仓库");
      return;
    }
    if (resolveDataSourceMode() === "replay") {
      // 与详情页同款语义：就地演示，如实注明未写后端
      setResolvedDecisions((prev) => ({ ...prev, [decision.id]: "approved" }));
      onToast("已批准（回放演示，未写入后端）");
      return;
    }
    if (!principal) {
      setDecisionErrors((prev) => ({ ...prev, [decision.id]: "决策主体未接入，无法提交。" }));
      return;
    }
    setApprovingId(decision.id);
    setDecisionErrors((prev) => ({ ...prev, [decision.id]: "" }));
    submitGovernanceDecision(activeDeck.roundId, built, "", principal.agentId)
      .then(() => {
        setResolvedDecisions((prev) => ({ ...prev, [decision.id]: "approved" }));
        onToast("治理决策已记录：READY（head-bound），merge gate 放行");
        setReload((n) => n + 1);
      })
      .catch((err: unknown) => {
        // 409 = head 漂移，留在卡内不静默
        setDecisionErrors((prev) => ({ ...prev, [decision.id]: errText(err) }));
      })
      .finally(() => setApprovingId(null));
  };

  const handleEvidence = (card: Extract<WorkCard, { kind: "decision" }>) => {
    if (!activeDeck || !card.repositoryId) {
      onToast("证据不可用：本轮聚合未取到或决策未指向仓库");
      return;
    }
    setEvidence(evidenceFromAggregate(activeDeck.aggregate, card.repositoryId));
    setEvidenceOpen(true);
  };

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
    // 追问待答时，输入框属于处理员的对话：发送即提交补充，不建新 issue
    if (!isNew) {
      if (!clarifyPending) return;
      handleClarifySubmit(text);
      return;
    }
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
            {!isNew && onBack && (
              <button
                className="flex-none rounded-hard border border-line px-2 py-0.5 text-[10.5px] text-tx2 hover:border-amber hover:text-amber-hi"
                onClick={onBack}
                title="返回议题列表"
              >
                ‹ 议题列表
              </button>
            )}
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
        <div className="min-h-0 flex-1 overflow-y-auto" ref={streamRef} onScroll={handleStreamScroll}>
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
            {!loading &&
              !error &&
              cards.map((card) => (
                <Fragment key={card.anchor}>
                  <WorkCardView
                    card={card}
                    onOpenRepo={setPanelRepo}
                    approvingId={approvingId}
                    decisionErrors={decisionErrors}
                    resolvedDecisions={resolvedDecisions}
                    principalReady={!principalResolving && principal !== null}
                    principalResolving={principalResolving}
                    onApprove={handleApprove}
                    onEvidence={handleEvidence}
                  />
                  {card.anchor === "requirement" && showDiscovery && detail && (
                    <AssistantFlow
                      detail={detail}
                      discovery={discovery}
                      principal={principal}
                      principalResolving={principalResolving}
                      materialize={flow.materialize}
                      clarifySending={clarifySending}
                      onAdvanced={() => setReload((n) => n + 1)}
                      onRetryStep={handleRetryStep}
                      onToast={onToast}
                    />
                  )}
                </Fragment>
              ))}
            {!loading && !error && !isNew && detail && (
              <p className="pt-1 text-center font-mono text-[10px] text-tx3">
                每 5s 自动同步{lastSyncAt ? ` · 上次 ${lastSyncAt.toLocaleTimeString()}` : " · 首次同步中…"}
              </p>
            )}
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
                    : clarifyPending
                      ? "回答处理员的追问 —— 发送后它会带着你的补充继续分析（Ctrl ⏎ 发送）"
                      : "会话内补充说明待后端立项，暂不可发送（新建需求请回侧栏「＋ 新会话」）"
                }
                disabled={!isNew && !clarifyPending}
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
                  title={
                    isNew
                      ? "需求归属当前工作区；未选工作区时由花名册唯一活跃 Org Leader 处理，交付范围由发现链确定"
                      : "本会话的交付范围（由服务端派生）"
                  }
                >
                  <span className="h-1.5 w-1.5 flex-none rounded-full bg-bluegray" />
                  {isNew
                    ? workspaceName ?? "全部工作区"
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
                  title={
                    isNew
                      ? "发送（Ctrl+Enter）"
                      : clarifyPending
                        ? "发送回答（Ctrl+Enter）"
                        : "会话内补充说明待后端立项"
                  }
                  disabled={(!isNew && !clarifyPending) || sending || clarifySending || draft.trim() === ""}
                  onClick={handleSend}
                >
                  {sending || clarifySending ? "…" : "➤"}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── 右栏：仓库房间面板（只读；写路径在 ⤢ 放大的全页房间） ── */}
      <aside
        className={`flex-none overflow-hidden border-line bg-panel transition-[width] duration-150 ${
          panelRepo ? "w-[352px] border-l" : "w-0"
        }`}
      >
        {panelRepo !== null && detail !== null && panelRoomId !== null ? (
          <RoomPanel
            issueId={detail.issue_id}
            rooms={rooms}
            selectedRoomId={panelRoomId}
            onSelectRoom={(roomId) => {
              const match = rooms.find((r) => r.room_id === roomId);
              if (match) setPanelRepo({ ...panelRepo, roomId, name: match.repository_name ?? panelRepo.name });
            }}
            onClose={() => setPanelRepo(null)}
            onExpand={() => onOpenRoom(panelRoomId)}
          />
        ) : (
          <div className="flex h-full w-[352px] flex-col">
            <div className="border-b border-line px-3.5 pb-2.5 pt-2.5">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[13px] font-bold text-cream">{panelRepo?.name ?? "…"}</span>
                <button
                  className="ml-auto h-6 w-6 rounded-hard border border-line-strong text-[11px] text-tx2 hover:border-amber hover:text-tx"
                  title="收起"
                  onClick={() => setPanelRepo(null)}
                >
                  ✕
                </button>
              </div>
            </div>
            <div className="flex flex-1 items-center justify-center bg-well px-6 text-center text-[11.5px] leading-[1.8] text-tx2">
              {panelRepo === null ? null : (
                <p>该仓库尚未建团，房间会在计划物化后出现。</p>
              )}
            </div>
          </div>
        )}
      </aside>

      <EvidenceModal
        open={evidenceOpen}
        roundLabel={activeDeck ? `第 ${activeDeck.roundIndex} 轮` : ""}
        evidence={evidence}
        onClose={() => setEvidenceOpen(false)}
      />
    </div>
  );
}

/** 任务 tick：display_status 原值决定符号与配色（前端不翻译状态，只挑皮肤）。 */
function taskTick(status: string): { char: string; cls: string; spin: boolean } {
  switch (status) {
    case "succeeded":
      return { char: "✓", cls: "border-olive bg-olive text-on-amber", spin: false };
    case "running":
    case "repairing":
      return { char: "▶", cls: "border-bluegray text-bluegray", spin: true };
    case "failed":
      return { char: "✕", cls: "border-salmon bg-salmon text-on-amber", spin: false };
    case "blocked":
      return { char: "■", cls: "border-salmon text-salmon", spin: false };
    default:
      return { char: "", cls: "border-line-strong text-tx3", spin: false };
  }
}

/** 卡片渲染：样式与原型一致，判定逻辑全部在 streamModel（本组件不写映射）。 */
function WorkCardView({
  card,
  onOpenRepo,
  approvingId,
  decisionErrors,
  resolvedDecisions,
  principalReady,
  principalResolving,
  onApprove,
  onEvidence,
}: {
  card: WorkCard;
  onOpenRepo: (repo: IssueRepositoryRef & { roomId: string | null }) => void;
  approvingId: string | null;
  decisionErrors: Record<string, string>;
  resolvedDecisions: Record<string, "approved">;
  principalReady: boolean;
  principalResolving: boolean;
  onApprove: (card: Extract<WorkCard, { kind: "decision" }>) => void;
  onEvidence: (card: Extract<WorkCard, { kind: "decision" }>) => void;
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
          {card.tasks === null ? (
            <p className="mt-1.5 text-[11px] text-tx3">任务明细未取到（夹具未覆盖或取用失败），不摆假进度。</p>
          ) : card.tasks.length > 0 ? (
            <div className="mt-1.5">
              {card.tasks.map((task) => (
                <TaskRow key={task.taskId} task={task} />
              ))}
            </div>
          ) : (
            <p className="mt-1.5 text-[11px] text-tx3">本轮还没有任务（尚未派工或计划未生成）。</p>
          )}
        </div>
      );
    case "decision": {
      const resolved = resolvedDecisions[card.decisionId];
      const errorTextFor = decisionErrors[card.decisionId] || "";
      const submitting = approvingId === card.decisionId;
      return (
        <div
          id={workCardAnchor(card)}
          className={
            resolved
              ? "rounded-hard border border-olive bg-[color-mix(in_oklab,var(--color-olive)_10%,var(--color-panel))] px-3.5 py-2.5"
              : card.decisionKind === "approve"
                ? "rounded-hard border border-amber bg-amber-well px-3.5 py-2.5"
                : "rounded-hard border border-line bg-panel px-3.5 py-2.5 shadow-card"
          }
        >
          <div className="mb-1.5 flex items-baseline gap-2">
            <span className={`microlabel ${resolved ? "" : card.decisionKind === "approve" ? "text-[#b08a2e]" : ""}`}>
              {resolved ? "已批准" : card.decisionKind === "approve" ? "待人审" : "观察项"}
            </span>
            <span className="text-[12.5px] font-bold text-cream">{card.title}</span>
            <span className="ml-auto font-mono text-[10px] text-tx3">第 {card.roundIndex} 轮</span>
          </div>
          <p className="text-[12px] leading-[1.7] text-tx">{card.body}</p>
          <p className="mt-1 font-mono text-[10.5px] text-tx2">
            {card.repositoryName ?? "仓库未指向"} · {card.headSha ? `head ${shortId(card.headSha)}` : "head 未记录"}
          </p>
          {card.unverifiedCount > 0 && !resolved && (
            <p className="mt-1.5 border-l-2 border-amber bg-panel px-2.5 py-1.5 text-[11.5px] leading-[1.7] text-tx2">
              A-18：该仓有 {card.unverifiedCount} 个任务，agent 自述「未验证」——确认前请先看证据。
            </p>
          )}
          {resolved ? (
            <p className="mt-2 text-[11.5px] text-olive">✓ 治理决策已记录（READY）· merge gate 放行</p>
          ) : card.decisionKind === "approve" ? (
            <>
              {errorTextFor && (
                <p className="mt-2 rounded-hard border border-salmon/60 bg-salmon/10 px-2.5 py-1.5 text-[11.5px] text-salmon">
                  {errorTextFor}
                </p>
              )}
              <div className="mt-2.5 flex gap-2">
                <button
                  className="rounded-hard bg-amber px-3.5 py-1.5 text-[11.5px] font-bold text-on-amber hover:bg-amber-hi disabled:opacity-40"
                  disabled={submitting || !principalReady}
                  title={
                    principalReady
                      ? "head-bound 授权：提交 READY 治理决策，放行 merge gate"
                      : "决策主体未接入（花名册无活跃 Org Leader）"
                  }
                  onClick={() => onApprove(card)}
                >
                  {submitting ? "提交中…" : "批准合并"}
                </button>
                <button
                  className="rounded-hard border border-line-strong bg-panel px-3.5 py-1.5 text-[11.5px] text-tx hover:border-amber disabled:opacity-40"
                  disabled={!card.repositoryId}
                  onClick={() => onEvidence(card)}
                >
                  查看证据
                </button>
              </div>
              {!principalReady && principalResolving && (
                <p className="mt-1.5 text-[10.5px] text-tx3">决策主体解析中…</p>
              )}
            </>
          ) : null}
        </div>
      );
    }
    case "note":
      return (
        <div className="rounded-hard bg-panel-2 px-3.5 py-2.5 text-[11.5px] leading-[1.7] text-tx2" id={workCardAnchor(card)}>
          {card.text}
        </div>
      );
  }
}

function TaskRow({ task }: { task: RoundTaskRow }) {
  const tick = taskTick(task.displayStatus);
  return (
    <div className="flex items-center gap-2 py-0.5 text-[12px]">
      <span
        className={`grid h-[15px] w-[15px] flex-none place-items-center rounded-full border-[1.5px] text-[9px] font-bold ${tick.cls} ${tick.spin ? "blink" : ""}`}
      >
        {tick.char}
      </span>
      <span className="min-w-0 truncate font-mono text-[11.5px] text-tx">{task.title}</span>
      {task.attempt > 1 && (
        <span className="flex-none rounded-hard border border-line px-1 font-mono text-[9px] text-tx3">
          第{task.attempt}次
        </span>
      )}
      <span className="ml-auto flex-none font-mono text-[10px] text-tx3">
        {task.lastDispatchedAt === null
          ? "从未派工"
          : task.agent
            ? task.agent
            : task.displayStatus}
      </span>
    </div>
  );
}
