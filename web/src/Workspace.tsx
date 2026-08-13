import { useEffect, useState } from "react";
import {
  Bot,
  CheckCircle2,
  ChevronRight,
  FileText,
  Loader2,
  Send,
  Users,
} from "lucide-react";
import {
  api,
  type AgentPrincipal,
  type IntegratedPlan,
  type MaterializeResult,
} from "./api";
import { PrdPlanner } from "./PrdPlanner";

type Tab = "plan" | "dispatch";

export function Workspace({
  projectId,
  leaderAgentId,
}: {
  projectId: string;
  leaderAgentId: string;
}) {
  const [tab, setTab] = useState<Tab>("plan");
  const [agents, setAgents] = useState<AgentPrincipal[]>([]);
  const [plan, setPlan] = useState<IntegratedPlan | null>(null);
  const [planRequirement, setPlanRequirement] = useState("");
  const [dispatching, setDispatching] = useState(false);
  const [dispatched, setDispatched] = useState<MaterializeResult | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    api
      .agents()
      .then(setAgents)
      .catch(() => null);
  }, []);

  const leader = agents.find((item) => item.id === leaderAgentId);
  const teamLeaders = agents.filter(
    (item) =>
      item.role === "repository_leader" &&
      item.organization_id === leader?.organization_id &&
      item.status === "active",
  );

  const handleDispatch = async () => {
    if (!plan) return;
    setMessage("");
    setDispatching(true);
    setDispatched(null);
    try {
      setDispatched(
        await api.materialize({
          engineering_spec: plan.engineering_spec,
          contracts: plan.contracts,
          task_dag: plan.task_dag,
          execution_batches: plan.execution_batches,
          requirement: planRequirement,
          project_id: projectId,
          leader_agent_id: leaderAgentId,
          idempotency_prefix: `ui-${projectId}`,
        }),
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setDispatching(false);
    }
  };

  return (
    <section className="content workspace">
      {/* 项目信息条 */}
      <div className="workspace-bar">
        <div className="workspace-bar-title">
          <span className="workspace-mark">
            <Bot size={18} />
          </span>
          <div>
            <h2>项目工作台</h2>
            <p>
              组织 Leader：{leader?.agentteams_resource_name ?? "—"}
              <code> 项目 ID {projectId}</code>
            </p>
          </div>
        </div>
        <span className="badge">
          {dispatched ? "已派发执行" : plan ? "方案已就绪" : "待提交方案"}
        </span>
      </div>

      {/* 选项卡 */}
      <div className="workspace-tabs">
        <button
          className={tab === "plan" ? "active" : ""}
          onClick={() => setTab("plan")}
        >
          <FileText size={16} />
          方案制定（提交 PRD）
        </button>
        <button
          className={tab === "dispatch" ? "active" : ""}
          onClick={() => setTab("dispatch")}
        >
          <Send size={16} />
          派发执行 · 与 Leader 交互
        </button>
      </div>

      <div className="workspace-tab">
        {tab === "plan" && (
          <PrdPlanner
            onPlanReady={(result, requirement) => {
              setPlan(result);
              setPlanRequirement(requirement);
              setDispatched(null);
              setMessage("");
            }}
          />
        )}
        {tab === "dispatch" && (
          <div className="dispatch-panel">
            {/* 交互对象 */}
            <div className="dispatch-section">
              <div className="plan-section-head">
                <Users size={16} />
                <strong>Leader 与仓库团队</strong>
              </div>
              <div className="dispatch-agents">
                <div className="dispatch-agent">
                  <span className="agent-role">组织 Leader</span>
                  <strong>{leader?.agentteams_resource_name ?? "—"}</strong>
                  <small>{leader ? "负责接收方案并编排仓库团队" : "未找到绑定身份"}</small>
                </div>
                {teamLeaders.map((item) => (
                  <div className="dispatch-agent" key={item.id}>
                    <span className="agent-role">仓库 Leader</span>
                    <strong>{item.agentteams_resource_name}</strong>
                    <small>仓库 {item.repository_id ?? "—"}</small>
                  </div>
                ))}
              </div>
            </div>

            {/* 派发执行 */}
            <div className="dispatch-section">
              <div className="plan-section-head">
                <Send size={16} />
                <strong>派发集成方案给 Leader 执行</strong>
              </div>
              {!plan ? (
                <div className="empty-state">
                  <FileText size={26} />
                  <h3>还没有集成方案</h3>
                  <p>请先切换到「方案制定」标签，提交 PRD 并生成集成方案。</p>
                </div>
              ) : (
                <>
                  <div className="dispatch-summary">
                    <div>
                      <strong>{plan.task_dag.length}</strong>
                      <span>任务</span>
                    </div>
                    <div>
                      <strong>{plan.contracts.length}</strong>
                      <span>契约</span>
                    </div>
                    <div>
                      <strong>{plan.execution_batches.length}</strong>
                      <span>执行批次</span>
                    </div>
                    <div>
                      <strong>
                        {new Set(plan.task_dag.map((task) => task.repository)).size}
                      </strong>
                      <span>涉及仓库</span>
                    </div>
                  </div>
                  <p className="dispatch-note">
                    派发后，任务将进入 AgentTeams 由仓库 Leader 接收执行；生成的对接文档会推送到人工审核台。
                  </p>
                  <div className="step-actions">
                    <button
                      className="primary"
                      disabled={dispatching || !!dispatched}
                      onClick={handleDispatch}
                    >
                      {dispatching ? (
                        <Loader2 size={15} className="spin" />
                      ) : dispatched ? (
                        <CheckCircle2 size={15} />
                      ) : (
                        <ChevronRight size={15} />
                      )}
                      {dispatching
                        ? "派发中…"
                        : dispatched
                          ? "已派发"
                          : "派发给仓库 Leader 执行"}
                    </button>
                  </div>
                  {dispatched && (
                    <div className="dispatch-result">
                      <div className="dispatch-result-head">
                        <CheckCircle2 size={16} />
                        <strong>派发成功</strong>
                      </div>
                      <div className="dispatch-result-grid">
                        <div>
                          <span>计划 ID</span>
                          <code>{dispatched.plan_id ?? "—"}</code>
                        </div>
                        <div>
                          <span>任务数</span>
                          <code>{dispatched.task_ids.length}</code>
                        </div>
                        <div>
                          <span>对接文档</span>
                          <code>{dispatched.handoff_doc_ids.length}</code>
                        </div>
                        <div>
                          <span>跳过仓库</span>
                          <code>{dispatched.skipped_repos.join("、") || "—"}</code>
                        </div>
                      </div>
                    </div>
                  )}
                  {message && <p className="form-message">{message}</p>}
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
