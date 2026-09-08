import { Fragment, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, FileText, X } from "lucide-react";
import type { DeliveryAggregate, IssueDetailView, IssueListItemView, IssueRepositoryRef } from "../../api/contract";
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
import { fetchConsoleRepositories } from "../../api/grid";
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
import { PlanDagCapsule } from "../../components/PlanDagCapsule";
import { ErrorPanel, LoadingLine } from "../../components/StatusBlocks";
import { AIChatInput } from "../../components/ui/ai-chat-input";
import { dayLabel, errText, shortId } from "../../display";
import type { Decision, EvidenceView } from "../../types";
import type { RedispatchScope, RollbackScopeView } from "../../api/contract";
import { RedispatchModal } from "../../components/RedispatchModal";
import { RollbackModal } from "../../components/RollbackModal";
import { archiveRound, redispatchRound } from "../../api/decisions";
import { submitRollback } from "../../api/rollback";
import { approvalForDecision, dagExecutionFromAggregate, evidenceFromAggregate } from "../../viewmodel";
import {
  buildWorkStream,
  composeRequirementText,
  newSessionStream,
  typedRequirementText,
  workCardAnchor,
  type RoundTaskRow,
  type WorkCard,
} from "./streamModel";
import { EvidenceModal } from "../../components/EvidenceModal";
import { Modal } from "../../components/Modal";
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
/** 发现链步号 → 触发端点的幂等键前缀（与发现链四步触发同一套键位）。 */
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

  // ── 各轮任务明细 + 回滚范围（轮次卡的 tick 行与轮次操作的数据源） ──
  const [tasksByRound, setTasksByRound] = useState<Record<string, import("../../api/contract").DeliveryTaskView[]>>({});
  const [rollbackByRound, setRollbackByRound] = useState<Record<string, import("../../api/contract").RollbackScopeView | null>>({});
  const historyEpoch = useRef(0);
  useEffect(() => {
    if (isNew || !detail || detail.rounds.length === 0) {
      setTasksByRound({});
      setRollbackByRound({});
      return;
    }
    const epoch = ++historyEpoch.current;
    detail.rounds.forEach((round) => {
      fetchRoundDecisionHistory(round.round_id)
        .then((data) => {
          // A6 同款：换代后在途响应不落桶
          if (epoch !== historyEpoch.current) return;
          setTasksByRound((prev) => ({ ...prev, [round.round_id]: data.tasks }));
          setRollbackByRound((prev) => ({ ...prev, [round.round_id]: data.rollback }));
        })
        .catch(() => {
          // replay 夹具未覆盖历史轮等：该轮没有明细就明说，不摆假进度
        });
    });
  }, [isNew, detail, reload]);

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

  // ── 审批可见性：分档门上摆出每个仓库的地址 host，占位域名一眼可见 ──
  const [repoHosts, setRepoHosts] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!showDiscovery) return;
    let cancelled = false;
    fetchConsoleRepositories()
      .then((repos) => {
        if (cancelled) return;
        const map: Record<string, string> = {};
        for (const r of repos) {
          try {
            map[r.name] = new URL(r.url).host;
          } catch {
            map[r.name] = r.url;
          }
        }
        setRepoHosts(map);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [showDiscovery]);

  // ── 处理员对话组（纯对话式定稿：卡片与大面板均已退役）──
  const clarifyPending =
    !!discovery &&
    discovery.analysis !== null &&
    !discovery.analysis.sufficient &&
    discovery.analysis.questions.length > 0;
  const [clarifySending, setClarifySending] = useState(false);

  // ── 顶部折叠 DAG 条（期 4）：计划 DAG 由悬浮胶囊承载（PlanDagCapsule，
  //    2026-09-08 用户确认保留胶囊形态）──
  /** 八相 → 四阶段进度点（规划/执行/审核/交付）。failed 不点亮进度，由 phase 标签自己说话。 */
  const stageOfPhase = (phase: IssueDetailView["phase"]): number => {
    switch (phase) {
      case "contract":
      case "plan":
        return 0;
      case "execute":
        return 1;
      case "validate":
        return 2;
      case "release":
      case "delivered":
        return 3;
      default:
        return -1;
    }
  };
  const STAGES = ["规划", "执行", "审核", "交付"] as const;
  /** 阶段点 → 对话流锚点：规划落在计划卡，其余落在当前轮次卡，交付落回阶段卡。 */
  const stageAnchor = (stage: number): string => {
    if (stage === 0) return "work-card-plan";
    if (stage === 3) return "work-card-phase";
    return `work-card-round-${Math.max(activeRoundIndex, 1)}`;
  };
  const scrollToCard = (anchorId: string) => {
    document.getElementById(anchorId)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const handleRetryStep = (step: 1 | 2 | 3 | 4) => {
    if (!detail) return;
    if (resolveDataSourceMode() === "replay") {
      onToast("回放模式不写后端：失败重试同样是一次真实触发，加 ?source=live 后可执行。");
      return;
    }
    if (!principal) {
      onToast("决策主体未接入，无法重试。");
      return;
    }
    // 失败步的投影是 failed 不是 idle，而驱动器的第一道闸就是「非 idle 不开火」——
    // 「清防重发表 + 刷新等驱动器重发」等的是一个永远不会来的信号（重试失灵的根源）。
    // 这里换新幂等键**直接**重发：failed 态意味着没有在跑的任务，single-flight 不会拦；
    // 先占住键，窗口期里投影即便短暂回到 idle，驱动器也会被防重发表挡住、不会双发。
    const key = `${detail.issue_id}:${step}`;
    autoTrigger.set(key, newIdempotencyKey(STEP_KEY_BY_STEP[step]));
    const payload = {
      created_by_agent_id: principal.agentId,
      idempotency_key: autoTrigger.get(key)!,
    };
    const fire =
      step === 1
        ? triggerAnalysis(detail.issue_id, payload)
        : step === 2
          ? triggerCandidates(detail.issue_id, payload)
          : step === 3
            ? triggerClassification(detail.issue_id, payload)
            : triggerPlan(detail.issue_id, payload);
    fire
      .then(() => setReload((n) => n + 1))
      .catch((err: unknown) => {
        autoTrigger.delete(key);
        onToast(`重试失败：${errText(err)}`);
      });
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
  }, [loading, error, cards.length]);

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

  // ── 轮次操作（期 5）：重新派工 / 归档 / 回滚 ──
  const [redispatch, setRedispatch] = useState<{
    open: boolean;
    roundId: string | null;
    roundLabel: string;
    tasks: import("../../api/contract").DeliveryTaskView[];
    scope: RedispatchScope;
    submitting: boolean;
    error: string | null;
  }>({ open: false, roundId: null, roundLabel: "", tasks: [], scope: "unfinished", submitting: false, error: null });
  const [rollback, setRollback] = useState<{
    open: boolean;
    roundId: string | null;
    roundLabel: string;
    scope: RollbackScopeView | null;
    submitting: boolean;
    error: string | null;
  }>({ open: false, roundId: null, roundLabel: "", scope: null, submitting: false, error: null });
  /** 归档两步确认（与旧容器同一交互）：第一步只点亮「确认？」，8s 无第二击自动复位 */
  const [archiveConfirmId, setArchiveConfirmId] = useState<string | null>(null);
  const [archivingId, setArchivingId] = useState<string | null>(null);
  useEffect(() => {
    if (!archiveConfirmId) return;
    const timer = window.setTimeout(() => setArchiveConfirmId(null), 8000);
    return () => window.clearTimeout(timer);
  }, [archiveConfirmId]);

  const roundLabelOf = (index: number) => `第 ${index} 轮`;

  const handleRedispatchOpen = (card: Extract<WorkCard, { kind: "round" }>) => {
    setRedispatch({
      open: true,
      roundId: card.roundId,
      roundLabel: roundLabelOf(card.index),
      tasks: tasksByRound[card.roundId] ?? [],
      scope: "unfinished",
      submitting: false,
      error: null,
    });
  };
  const handleRedispatchConfirm = () => {
    if (!redispatch.roundId) return;
    setRedispatch((prev) => ({ ...prev, submitting: true, error: null }));
    redispatchRound(redispatch.roundId, redispatch.scope)
      .then((receipt) => {
        setRedispatch((prev) => ({ ...prev, open: false }));
        onToast(
          `已重发 ${receipt.task_ids.length} 个任务的任务包与点名` +
            (receipt.reopened_task_ids.length > 0 ? `，${receipt.reopened_task_ids.length} 个已完成任务被送回重做` : "") +
            "；agent 是否响应看房间事件流。",
        );
        setReload((n) => n + 1);
      })
      .catch((err: unknown) => {
        // 409（本轮无可派任务）/503（执行面接不住）——原文留在弹窗里不飘走
        setRedispatch((prev) => ({ ...prev, error: errText(err) }));
      })
      .finally(() => setRedispatch((prev) => ({ ...prev, submitting: false })));
  };
  const handleArchive = (card: Extract<WorkCard, { kind: "round" }>) => {
    if (archiveConfirmId !== card.roundId) {
      setArchiveConfirmId(card.roundId);
      return;
    }
    if (resolveDataSourceMode() === "replay") {
      setArchiveConfirmId(null);
      onToast("已归档（回放演示，未写入后端）");
      return;
    }
    setArchivingId(card.roundId);
    archiveRound(card.roundId)
      .then(() => {
        setArchiveConfirmId(null);
        onToast(`${roundLabelOf(card.index)}已归档（轮次级；issue 的开关状态仍由服务端派生）`);
        setReload((n) => n + 1);
      })
      .catch((err: unknown) => {
        // 409 = 活跃轮次拒绝归档等，原文上 toast 不静默
        setArchiveConfirmId(null);
        onToast(`归档失败：${errText(err)}`);
      })
      .finally(() => setArchivingId(null));
  };
  const handleRollbackOpen = (card: Extract<WorkCard, { kind: "round" }>) => {
    const scope = rollbackByRound[card.roundId] ?? null;
    if (!scope) {
      onToast("回滚范围未取到（§4.6 投影缺失或该轮无可回滚项）");
      return;
    }
    setRollback({ open: true, roundId: card.roundId, roundLabel: roundLabelOf(card.index), scope, submitting: false, error: null });
  };
  const handleRollbackConfirm = (reason: string) => {
    if (!rollback.roundId || !rollback.scope) return;
    if (resolveDataSourceMode() === "replay") {
      setRollback((prev) => ({ ...prev, error: "回放模式不写后端：回滚会关 PR、开 revert PR、动 base 分支。加 ?source=live 后可真实执行。" }));
      return;
    }
    if (!principal) {
      setRollback((prev) => ({ ...prev, error: "决策主体未接入，无法提交。" }));
      return;
    }
    setRollback((prev) => ({ ...prev, submitting: true, error: null }));
    submitRollback(rollback.roundId, rollback.scope, reason, principal.agentId)
      .then((receipt) => {
        setRollback((prev) => ({ ...prev, open: false }));
        onToast(
          receipt.replayed
            ? "同一份回滚请求已记录过，本次为重放（后端零写入）；执行进度看房间事件流。"
            : "回滚决策已记录，merge gate 已堵死；执行由回滚 saga 接管（每 30s 一轮），进度看房间事件流。",
        );
        setReload((n) => n + 1);
      })
      .catch((err: unknown) => setRollback((prev) => ({ ...prev, error: errText(err) })))
      .finally(() => setRollback((prev) => ({ ...prev, submitting: false })));
  };

  // ── 输入框（新会话可用；既有会话按已知缺口置灰） ──
  const [draft, setDraft] = useState("");
  /** 附件（真上传形态）：文档解析文本**不进输入框**，挂在附件位上随消息发送。
   *  requirement_text 携带解析文本（规划要读的就是它，parse 端点截断 20k），
   *  聊天里只显示文件卡片——点开看全文。 */
  const [attachment, setAttachment] = useState<{ filename: string; text: string } | null>(null);
  /** 新会话欢迎区上传卡片的拖拽高亮 */
  const [docDragging, setDocDragging] = useState(false);
  const [sending, setSending] = useState(false);
  const idempotencyKey = useRef(crypto.randomUUID());
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const dragDepth = useRef(0);

  const handleDraftChange = (text: string) => {
    setDraft(text);
    // 契约 §1.3：每次逻辑创建一个新键——文本变了就是新的逻辑创建；
    // 重试（文本不变）沿用同键，超时重点不会建出两个 issue。
    idempotencyKey.current = crypto.randomUUID();
  };

  const handleSend = () => {
    const typed = draft.trim();
    if (sending) return;
    if (!isNew) {
      // 追问待答时，输入框属于处理员的对话：发送即提交补充，不建新 issue
      if (!clarifyPending || !typed) return;
      handleClarifySubmit(typed);
      return;
    }
    if (!typed && !attachment) return;
    // 文档解析文本必须随 requirement_text 交给规划（契约没有独立的文档字段），
    // 但对话流只展示用户自己的话——分界符由 streamModel 的展示侧拆开：
    // 没打字时气泡只留文件卡，不擅自复述文档内容。
    const text = attachment
      ? composeRequirementText(typed, attachment.text)
      : typed;
    setSending(true);
    onCreateIssue(text, idempotencyKey.current, attachment?.filename ?? null)
      .then(() => {
        setDraft("");
        setAttachment(null);
        idempotencyKey.current = crypto.randomUUID();
      })
      .catch((err: unknown) => onToast(`创建失败：${errText(err)}`))
      .finally(() => setSending(false));
  };

  const handlePickDocument = (file: File | undefined) => {
    if (!file) return;
    parseRequirementDocument(file)
      .then((parsed) => {
        setAttachment({ filename: parsed.filename, text: parsed.text });
        idempotencyKey.current = crypto.randomUUID();
        if (parsed.truncated) onToast(`文档较长，已截断为前 ${parsed.chars} 字`);
      })
      .catch((err: unknown) => onToast(`文档解析失败：${errText(err)}`))
      .finally(() => {
        if (fileInputRef.current) fileInputRef.current.value = "";
      });
  };

  const hour = new Date().getHours();
  const greeting = hour < 12 ? "上午好" : hour < 18 ? "下午好" : "晚上好";

  return (
    <div className="flex h-full min-w-0 flex-1">
      {/* ── 中央列 ── */}
      <div className="relative flex min-w-0 flex-1 flex-col">
        {isNew ? (
          /* ── 新会话：极简欢迎式（2026-09-08 二次裁决）——时段问候 + 居中输入框；
             上传收进回形针，整页也接受拖拽（松开即解析）；创建后路由切会话工作台 ── */
          <div
            className="relative flex min-h-0 flex-1 flex-col items-center justify-center gap-5 px-6 pb-24"
            onDragEnter={(e) => {
              e.preventDefault();
              dragDepth.current += 1;
              setDocDragging(true);
            }}
            onDragOver={(e) => e.preventDefault()}
            onDragLeave={() => {
              dragDepth.current -= 1;
              if (dragDepth.current <= 0) setDocDragging(false);
            }}
            onDrop={(e) => {
              e.preventDefault();
              dragDepth.current = 0;
              setDocDragging(false);
              handlePickDocument(e.dataTransfer.files?.[0]);
            }}
          >
            {docDragging && (
              <div className="absolute inset-4 z-10 grid place-items-center rounded-[12px] border-2 border-dashed border-amber bg-panel/80">
                <p className="text-[13px] text-tx2">松开以解析需求文档</p>
              </div>
            )}
            <div className="flex flex-col items-center gap-2 text-center">
              <h1 className="text-[19px] font-medium text-cream">{greeting}，要规划什么需求？</h1>
              <p className="text-[12px] text-tx2">发送即创建 issue 并开始规划</p>
            </div>
            <div className="w-full max-w-[720px]">
              <AIChatInput
                value={draft}
                onValueChange={handleDraftChange}
                onSend={handleSend}
                sending={sending}
                placeholder="输入需求 —— 发送即创建 issue 并开始规划（Ctrl ⏎ 发送）"
                onAttach={() => fileInputRef.current?.click()}
                attachTitle="上传需求文档 · 支持 .txt / .md / .docx / .pdf / .odt / .rtf"
                sendDisabled={draft.trim() === "" && attachment === null}
                attachment={
                  attachment ? (
                    <div className="flex items-center gap-2 border-t border-line px-3 py-1.5">
                      <FileText size={13} className="flex-none text-tx2" />
                      <span className="min-w-0 truncate font-mono text-[11px] text-tx2" title={attachment.filename}>
                        {attachment.filename}
                      </span>
                      <button
                        type="button"
                        className="ml-auto flex-none text-[11px] text-tx3 hover:text-salmon"
                        title="移除附件"
                        onClick={() => setAttachment(null)}
                      >
                        <X size={12} />
                      </button>
                    </div>
                  ) : null
                }
              />
            </div>
          </div>
        ) : (
          <>
        {/* 顶部折叠 DAG 条（期 4）：阶段点点击滚到对话流对应卡片；DAG 展开为计划图 */}
        <div className="flex-none border-b border-line bg-ink px-6">
          <div className="flex h-11 items-center gap-3">
            {!isNew && onBack && (
              <button
                className="flex flex-none items-center gap-0.5 rounded-hard border border-line px-2 py-0.5 text-[10.5px] text-tx2 hover:border-amber hover:text-amber-hi"
                onClick={onBack}
                title="返回会话列表"
              >
                <ChevronLeft size={12} strokeWidth={2} />
                issue 列表
              </button>
            )}
            <span className="eyebrow">流程</span>
            {detail ? (
              <>
                {/* 阶段进度点：点一下滚到对话流里对应的卡片 */}
                <div className="flex items-center gap-1">
                  {STAGES.map((title, i) => {
                    const currentStage = detail.phase === "failed" ? -1 : stageOfPhase(detail.phase);
                    const done = i < currentStage;
                    const now = i === currentStage && detail.phase !== "failed";
                    return (
                      <button
                        key={title}
                        className="flex items-center gap-1"
                        title={`定位到「${title}」相关消息`}
                        onClick={() => scrollToCard(stageAnchor(i))}
                      >
                        {i > 0 && <span className="mx-0.5 h-px w-3 bg-line-strong" />}
                        <span
                          className={`grid size-[15px] place-items-center rounded-full border-[1.5px] text-[8.5px] font-bold ${
                            now
                              ? "border-amber bg-amber text-on-amber"
                              : done
                                ? "border-olive bg-olive text-on-amber"
                                : "border-line-strong text-tx3"
                          }`}
                        >
                          {done ? "✓" : now ? "●" : i + 1}
                        </span>
                        <span className={`text-[10.5px] ${now ? "text-tx" : "text-tx3"}`}>{title}</span>
                      </button>
                    );
                  })}
                </div>
                {detail.phase === "failed" && (
                  <span className="rounded-hard border border-salmon px-1.5 py-px font-mono text-[10px] text-salmon">failed</span>
                )}
              </>
            ) : (
              <span className="text-[11.5px] text-tx3">{"…"}</span>
            )}
            {/* 计划 DAG 胶囊（参照 ZCode 顶部任务胶囊）：钉在顶栏右侧，点开悬浮面板、
                点外部或再点胶囊收起。批次图在展开面板里（PlanDagPanel）。 */}
            <PlanDagCapsule
              state={flow.planState}
              execution={
                activeDeck
                  ? dagExecutionFromAggregate(activeDeck.aggregate, `第 ${activeDeck.roundIndex} 轮`)
                  : null
              }
              onRetry={flow.reloadPlan}
              resetKey={issueId ?? "new"}
            />
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
                    roundOps={
                      detail
                        ? {
                            redispatch: handleRedispatchOpen,
                            archive: handleArchive,
                            rollback: handleRollbackOpen,
                          }
                        : undefined
                    }
                    archiveConfirmId={archiveConfirmId}
                    archivingId={archivingId}
                  />
                  {card.anchor === "requirement" && showDiscovery && detail && (
                    <AssistantFlow
                      detail={detail}
                      discovery={discovery}
                      principal={principal}
                      clarifySending={clarifySending}
                      repoHosts={repoHosts}
                      onAdvanced={() => setReload((n) => n + 1)}
                      onRetryStep={handleRetryStep}
                      onToast={onToast}
                      planBatches={
                        flow.planState.status === "ready"
                          ? flow.planState.plan.execution_batches
                          : null
                      }
                    />
                  )}
                </Fragment>
              ))}
          </div>
        </div>

        {/* 吸底输入框（AIChatInput）：单行静态框、右侧仅发送；业务态（幂等键、
            追问回答、附件解析）沿用原有回路 */}
        <div className="flex-none bg-ink px-6 pb-4 pt-2.5">
          <div className="mx-auto max-w-[720px]">
            <AIChatInput
              value={draft}
              onValueChange={handleDraftChange}
              onSend={handleSend}
              sending={sending || clarifySending}
              disabled={!isNew && !clarifyPending}
              placeholder={
                isNew
                  ? "输入需求 —— 发送即创建 issue 并开始规划（Ctrl ⏎ 发送）"
                  : clarifyPending
                    ? "回答处理员的追问 —— 发送后它会带着你的补充继续分析（Ctrl ⏎ 发送）"
                    : "会话内补充说明待后端立项，暂不可发送（新建需求请回侧栏「＋ 新会话」）"
              }
              onAttach={() => fileInputRef.current?.click()}
              attachDisabled={!isNew}
              attachTitle={isNew ? "上传需求文档（作为附件随消息发送）" : "仅新会话可用"}
              sendTitle={
                isNew ? "发送（Ctrl+Enter）" : clarifyPending ? "发送回答（Ctrl+Enter）" : "会话内补充说明待后端立项"
              }
              sendDisabled={draft.trim() === "" && attachment === null}
              attachment={
                attachment ? (
                  <div className="flex items-center gap-2 border-b border-line px-3 py-1.5">
                    <FileText size={13} className="flex-none text-tx2" />
                    <span className="min-w-0 truncate font-mono text-[11px] text-tx2" title={attachment.filename}>
                      {attachment.filename}
                    </span>
                    <button
                      type="button"
                      className="ml-auto flex-none text-[11px] text-tx3 hover:text-salmon"
                      title="移除附件"
                      onClick={() => setAttachment(null)}
                    >
                      <X size={12} />
                    </button>
                  </div>
                ) : null
              }
            />
          </div>
        </div>
          </>
        )}
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept={DOC_ACCEPT}
        className="hidden"
        onChange={(e) => handlePickDocument(e.target.files?.[0])}
      />

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
                  <X size={12} />
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

      <RedispatchModal
        open={redispatch.open}
        roundLabel={redispatch.roundLabel}
        tasks={redispatch.tasks}
        scope={redispatch.scope}
        submitting={redispatch.submitting}
        errorText={redispatch.error}
        onScopeChange={(scope) => setRedispatch((prev) => ({ ...prev, scope }))}
        onCancel={() => setRedispatch((prev) => ({ ...prev, open: false }))}
        onConfirm={handleRedispatchConfirm}
      />
      <RollbackModal
        open={rollback.open}
        roundLabel={rollback.roundLabel}
        scope={rollback.scope}
        principal={
          resolveDataSourceMode() === "replay"
            ? { state: "replay", label: "回放演示（不写后端）" }
            : principalResolving
              ? { state: "resolving", label: "解析中…" }
              : principal
                ? { state: "ready", label: `AGENT ${principal.label}` }
                : { state: "missing", label: "决策主体未接入" }
        }
        submitting={rollback.submitting}
        errorText={rollback.error}
        onCancel={() => setRollback((prev) => ({ ...prev, open: false }))}
        onConfirm={handleRollbackConfirm}
      />
    </div>
  );
}

/** 任务 tick：display_status 原值决定符号与配色（前端不翻译状态，只挑皮肤）。 */
/** 用户需求气泡：带附件时渲染成**文件卡片**（点开看全文预览），只展示**用户自己
 *  打的字**——附件文档的解析全文是规划要读的，不是聊天气泡要复述的；用户没打字
 *  就只留文件卡，一个字都不擅自替他说。无附件时照旧显示全文。 */
function RequirementBubble({ card }: { card: Extract<WorkCard, { kind: "requirement" }> }) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const typedText = typedRequirementText(card.text);

  return (
    <div className="flex justify-end" id={workCardAnchor(card)}>
      <div className="max-w-[78%] rounded-[10px_10px_3px_10px] bg-amber px-3.5 py-2.5 text-on-amber">
        {card.documentFilename ? (
          <>
            <button
              className="flex w-full items-center gap-2.5 rounded-hard bg-black/15 px-2.5 py-2 text-left transition-colors hover:bg-black/25"
              onClick={() => setPreviewOpen(true)}
              title="点击查看文档内容"
            >
              <FileText size={17} className="flex-none text-tx2" />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-[11.5px] font-bold">{card.documentFilename}</span>
                <span className="block text-[10px] opacity-70">需求文档 · 点击查看内容</span>
              </span>
            </button>
            {typedText && (
              <div className="mt-1.5 whitespace-pre-wrap text-[11.5px] leading-[1.6] opacity-90">{typedText}</div>
            )}
          </>
        ) : (
          <div className="whitespace-pre-wrap text-[12.5px] leading-[1.65]">{card.text}</div>
        )}
      </div>

      <Modal
        open={previewOpen}
        className="m-auto w-[min(640px,92vw)] rounded-[3px] border border-line-strong bg-panel p-0 text-tx shadow-pop"
        onClose={() => setPreviewOpen(false)}
      >
        <div className="flex items-baseline gap-2 border-b border-line px-4 py-2.5">
          <FileText size={14} className="flex-none text-tx2" />
          <h2 className="min-w-0 truncate font-mono text-[13px] font-bold text-cream">
            {card.documentFilename ?? "需求全文"}
          </h2>
          <button
            className="ml-auto flex-none text-[12px] text-tx3 hover:text-tx"
            onClick={() => setPreviewOpen(false)}
          >
            <X size={12} />
          </button>
        </div>
        <div className="max-h-[60vh] overflow-y-auto px-4 py-3">
          <pre className="whitespace-pre-wrap break-words font-sans text-[12.5px] leading-[1.8] text-tx">
            {card.text}
          </pre>
        </div>
      </Modal>
    </div>
  );
}

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
  roundOps,
  archiveConfirmId,
  archivingId,
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
  /** 轮次操作（期 5）：活跃轮=重派/回滚，非活跃轮=归档；由页面提供，缺省不渲染 */
  roundOps?: {
    redispatch: (card: Extract<WorkCard, { kind: "round" }>) => void;
    archive: (card: Extract<WorkCard, { kind: "round" }>) => void;
    rollback: (card: Extract<WorkCard, { kind: "round" }>) => void;
  };
  archiveConfirmId: string | null;
  archivingId: string | null;
}) {
  switch (card.kind) {
    case "requirement":
      return <RequirementBubble card={card} />;
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
          <p className="text-[11.5px] text-tx2">第 {card.roundIndex} 轮快照 · 任务级 DAG 在顶部「DAG」展开查看。</p>
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
                <ChevronRight size={9} className="text-tx3" />
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
          {roundOps && (
            <div className="mt-2 flex gap-2 border-t border-dashed border-line pt-2">
              {card.active ? (
                <>
                  <button
                    className="rounded-hard border border-line-strong bg-panel px-2.5 py-1 text-[11px] text-tx hover:border-amber disabled:opacity-40"
                    disabled={(card.tasks?.length ?? 0) === 0}
                    title="重发这一轮未完成任务的包与点名"
                    onClick={() => roundOps.redispatch(card)}
                  >
                    重新派工
                  </button>
                  <button
                    className="rounded-hard border border-salmon/50 bg-panel px-2.5 py-1 text-[11px] text-salmon hover:bg-salmon/10 disabled:opacity-40"
                    title="对这一轮发起回滚（范围见 §4.6 投影）"
                    onClick={() => roundOps.rollback(card)}
                  >
                    回滚
                  </button>
                </>
              ) : (
                <button
                  className="rounded-hard border border-line-strong bg-panel px-2.5 py-1 text-[11px] text-tx2 hover:border-amber disabled:opacity-40"
                  disabled={archivingId === card.roundId}
                  title="轮次级归档；活跃轮次服务端会拒绝"
                  onClick={() => roundOps.archive(card)}
                >
                  {archivingId === card.roundId
                    ? "归档中…"
                    : archiveConfirmId === card.roundId
                      ? "确认归档？（8 秒内再点一次）"
                      : "归档本轮"}
                </button>
              )}
            </div>
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
