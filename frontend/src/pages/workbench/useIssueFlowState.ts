import { useCallback, useEffect, useState } from "react";
import type { PlanDagState } from "../../components/PlanDagPanel";
export type PolicyGate = "resolving" | "open" | "sealed" | "unknown";
import type { PlanAnchor } from "../../types";
import type { IssueDetailView } from "../../api/contract";
import { fetchPlanGraphEdges, fetchRepositoryPlan } from "../../api/rooms";
import { fetchProjectTopology, type ProjectAgentTopologyView } from "../../api/humanControl";
import { AuthError } from "../../api/auth";
import { ApiError } from "../../api/client";
import { errText } from "../../display";
import { resolveDataSourceMode } from "../../api/source";

/** 监管策略取数态（原 IssueDetailPage 的定义随旧页退役迁到这里）。
 *  401 单独一态：会话过期重试永远不会成功，不能混进可重试的 error。 */
export type SupervisionState =
  | { status: "loading" }
  | { status: "ready"; topology: ProjectAgentTopologyView }
  | { status: "absent" }
  | { status: "forbidden"; detail: string }
  | { status: "unauthenticated"; detail: string }
  | { status: "error"; message: string }
  | { status: "replay" };

/** 工作台的「推动」状态：发现/物化面板需要的两份容器级事实。
 *
 *  取数逻辑与旧 IssueDetailContainer 逐段同源（监督拓扑 404/403/401 分态、
 *  计划纸面的锚点仓两来路回退），抽成 hook 是为了让工作台不复制容器 700 行——
 *  旧容器在期 5 退役，届时这里成为唯一实现。
 *
 *  给 DiscoveryPanel 的门槛判定：拓扑 404 = 还没档案（草稿窗口开着），
 *  200/403 = 档案已锁死（只读），加载中 = 不出卡片不押注。 */
export function policyGateOf(state: SupervisionState): PolicyGate {
  switch (state.status) {
    case "absent":
      return "open";
    case "ready":
    case "forbidden":
      return "sealed";
    case "loading":
      return "resolving";
    default:
      return "unknown";
  }
}

export function useIssueFlowState(issueId: string, detail: IssueDetailView | null, reload: number) {
  // ── 监督策略（迁移 5-1a）：物化会顺手建出拓扑，跟着 reload 重取 ──
  const [supervision, setSupervision] = useState<SupervisionState>({ status: "loading" });
  const [supervisionReload, setSupervisionReload] = useState(0);
  useEffect(() => {
    // 回放模式没有 human_control 面的夹具：发这个请求会对夹具 id 答 404，
    // 把「回放世界里设了检查点」渲染成「尚未设定」——拿取数失败反驳夹具事实。
    if (!detail) return;
    if (resolveDataSourceMode() === "replay") {
      setSupervision({ status: "replay" });
      return;
    }
    let cancelled = false;
    setSupervision({ status: "loading" });
    fetchProjectTopology(issueId)
      .then((topology) => !cancelled && setSupervision({ status: "ready", topology }))
      .catch((err: unknown) => {
        if (cancelled) return;
        const status = err instanceof AuthError ? err.status : 0;
        setSupervision(
          status === 404
            ? { status: "absent" }
            : status === 403
              ? { status: "forbidden", detail: errText(err) }
              : status === 401
                ? { status: "unauthenticated", detail: errText(err) }
                : { status: "error", message: errText(err) },
        );
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [issueId, detail?.issue_id, reload, supervisionReload]);
  const reloadSupervision = useCallback(() => setSupervisionReload((n) => n + 1), []);

  // ── 锚点回退中转：发现面板报上来的候选仓（undefined = 还没问过发现读投影） ──
  const [candidateAnchor, setCandidateAnchor] = useState<PlanAnchor | null | undefined>(undefined);
  useEffect(() => setCandidateAnchor(undefined), [issueId]);
  const handleCandidateAnchor = useCallback((anchor: PlanAnchor | null) => setCandidateAnchor(anchor), []);

  // ── 计划纸面（§5.4 单仓作用域，锚点两来路：拓扑冻结仓 > 发现候选仓） ──
  const [planState, setPlanState] = useState<PlanDagState>({ status: "loading" });
  const [planReload, setPlanReload] = useState(0);
  const scopeAnchor = detail?.repositories[0] ?? null;
  const anchorFromCandidate = scopeAnchor === null;
  const anchorRepositoryId = scopeAnchor?.repository_id ?? candidateAnchor?.repositoryId ?? null;
  const anchorRepositoryName = scopeAnchor?.name ?? candidateAnchor?.name ?? null;
  const anchorPending = scopeAnchor === null && candidateAnchor === undefined;
  const hasDetail = detail !== null;

  useEffect(() => {
    if (!hasDetail) return;
    if (anchorPending) {
      setPlanState({ status: "loading" });
      return;
    }
    if (!anchorRepositoryId || !anchorRepositoryName) {
      setPlanState({
        status: "absent",
        reason:
          "尚未确定交付范围，发现链也还没有候选仓库——两处都取不到锚点仓，" +
          "而 §5.4 计划纸面是按仓取数的，端点无从调用。",
      });
      return;
    }
    let cancelled = false;
    // 已有上一份结果就**静默刷新**，不把 ready 打回 loading——reload 每 5s 轮询
    // +1，打回一次 DAG 就塌成「加载中」再弹回一次，展开期间整图一闪一闪
    //（用户报的「突然自动关闭然后恢复」正是它）。
    setPlanState((prev) => (prev.status === "ready" ? prev : { status: "loading" }));
    fetchRepositoryPlan(issueId, anchorRepositoryId)
      .then(async (plan) => {
        if (cancelled) return;
        setPlanState((prev) => ({
          status: "ready",
          plan,
          // 同一版快照的静默刷新沿用已取到的边语义——别让粗线每 5s 灭一次再亮回来
          graphEdges:
            prev.status === "ready" && prev.plan.plan_version === plan.plan_version
              ? prev.graphEdges
              : null,
        }));
        // 边语义是给既有连线**加注**的（粗线 + hover），不是画图的前提：
        // 取不到（老快照 / 404 / 回放）就按 null 渲染，连线照画。
        const graphEdges = await fetchPlanGraphEdges(issueId, plan.plan_version);
        if (cancelled || graphEdges === null) return;
        setPlanState((prev) =>
          prev.status === "ready" && prev.plan.plan_version === plan.plan_version
            ? { ...prev, graphEdges }
            : prev,
        );
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // 上一份结果还在手上时，刷新失败不推翻它：计划纸面是低频变化的事实，
        // 已展示的就是服务端最后一次的正式回答，轮询噪音不该把图抖掉。
        // 404 = 无计划快照（issue 不存在已被详情排除，只剩从未规划这一种）
        setPlanState((prev) => {
          if (prev.status === "ready") return prev;
          return err instanceof ApiError && err.status === 404
            ? {
                status: "absent",
                reason: anchorFromCandidate
                  ? `以候选仓 ${anchorRepositoryName} 作回退锚点取计划纸面，服务端返回 404。`
                  : "本 issue 还没有计划快照（计划由发现链在分档审批后生成）。",
              }
            : { status: "error", message: errText(err) };
        });
      });
    return () => {
      cancelled = true;
    };
    // 与旧容器同一取舍：依赖锚点标识与刷新计数，不依赖 detail 整体身份
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    issueId,
    hasDetail,
    anchorRepositoryId,
    anchorRepositoryName,
    anchorFromCandidate,
    anchorPending,
    reload,
    planReload,
  ]);
  const reloadPlan = useCallback(() => setPlanReload((n) => n + 1), []);

  return {
    planState,
    reloadPlan,
    supervision,
    reloadSupervision,
    candidateAnchor,
    handleCandidateAnchor,
  };
}
