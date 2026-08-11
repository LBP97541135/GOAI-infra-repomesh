import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  Check,
  ChevronRight,
  Eye,
  Gauge,
  Hand,
  Plus,
  Trash2,
} from "lucide-react";
import { api, type Account, type AgentPrincipal } from "./api";

const checkpoints = [
  ["repository_scope", "仓库范围", "确认哪些仓库与目录参与修改"],
  ["specification", "工程 Spec", "确认目标、契约和验收标准"],
  ["execution", "开始执行", "放行 Worker 调用 Coding Agent"],
  ["validation", "测试验证", "确认单测、集成测试和联调证据"],
  ["delivery", "PR 交付", "确认 ChangeSet 与多仓库 PR"],
  [
    "exception_escalation",
    "异常升级",
    "Team Manager 上报异常时始终推送人工审核",
  ],
];
const modes = [
  {
    id: "auto",
    name: "自动执行",
    icon: Gauge,
    copy: "Agent 自动完成全流程，不等待人工确认。",
  },
  {
    id: "supervised",
    name: "人工监督",
    icon: Eye,
    copy: "Agent 自动执行，只在选定检查点等待确认。",
  },
  {
    id: "manual_controlled",
    name: "人工控制",
    icon: Hand,
    copy: "所有关键阶段均需人工明确放行。",
  },
];
type Repo = {
  repository_id: string;
  leader_agent_id: string;
  worker_agent_ids: string[];
};
const newRepo = (): Repo => ({
  repository_id: "",
  leader_agent_id: "",
  worker_agent_ids: [""],
});

export function ProjectSetup({ onCreated }: { onCreated: () => void }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [agents, setAgents] = useState<AgentPrincipal[]>([]);
  const [mode, setMode] = useState("supervised");
  const [selected, setSelected] = useState<string[]>([
    "repository_scope",
    "specification",
    "delivery",
    "exception_escalation",
  ]);
  const [reviewer, setReviewer] = useState("");
  const [repos, setRepos] = useState<Repo[]>([newRepo()]);
  const [organizationId, setOrganizationId] = useState("");
  const [organizationLeaderId, setOrganizationLeaderId] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    api
      .accounts()
      .then((items) => {
        setAccounts(items);
        setReviewer(items[0]?.id || "");
      })
      .catch(() => null);
  }, []);
  useEffect(() => {
    api
      .agents()
      .then(setAgents)
      .catch(() => null);
  }, []);
  useEffect(() => {
    if (mode === "auto") setSelected([]);
    if (mode === "supervised")
      setSelected((items) => [...new Set([...items, "exception_escalation"])]);
    if (mode === "manual_controlled")
      setSelected(checkpoints.map((item) => item[0]));
  }, [mode]);
  const projectId = useMemo(() => crypto.randomUUID(), []);
  const submit = async () => {
    if (mode !== "auto" && !reviewer)
      return setMessage("请先创建并选择审核人员");
    if (
      !organizationId ||
      !organizationLeaderId ||
      repos.some(
        (repo) =>
          !repo.repository_id ||
          !repo.leader_agent_id ||
          repo.worker_agent_ids.some((id) => !id),
      )
    )
      return setMessage("请完整绑定现有 AgentTeams 组织与仓库团队");
    setMessage("正在创建项目拓扑…");
    try {
      await api.createTopology({
        organization_id: organizationId,
        project_id: projectId,
        organization_leader_id: organizationLeaderId,
        repository_teams: repos,
        execution_mode: mode,
        required_checkpoints: selected,
        human_grants:
          mode === "auto"
            ? []
            : [
                {
                  human_principal_id: reviewer,
                  role: "project_supervisor",
                  code_access: "write",
                  control_actions: [
                    "view_decisions",
                    "approve_checkpoint",
                    "request_changes",
                    "pause_project",
                    "resume_project",
                    "edit_specification",
                  ],
                  repository_id: null,
                  path_patterns: [],
                },
              ],
        idempotency_key: `ui-project-${projectId}`,
      });
      setMessage("项目已创建，Agent 到达检查点后会自动推送审核待办。");
      setTimeout(onCreated, 900);
    } catch (error) {
      setMessage((error as Error).message);
    }
  };
  return (
    <section className="content project-setup">
      <div className="section-head">
        <div>
          <h2>选择执行方式</h2>
          <p>模式决定 Agent 在哪些阶段暂停，以及谁可以查看或控制代码。</p>
        </div>
        <span className="step">01 / 03</span>
      </div>
      <div className="mode-grid">
        {modes.map(({ id, name, icon: Icon, copy }) => (
          <button
            key={id}
            className={mode === id ? "selected" : ""}
            onClick={() => setMode(id)}
          >
            <span className="mode-icon">
              <Icon size={21} />
            </span>
            <strong>{name}</strong>
            <p>{copy}</p>
            {mode === id && (
              <i>
                <Check size={14} />
              </i>
            )}
          </button>
        ))}
      </div>
      <div className="form-section">
        <div className="section-head compact">
          <div>
            <h2>人工检查点</h2>
            <p>Agent 到达所选节点时自动创建审核单，并暂停后续动作。</p>
          </div>
          <span className="step">02 / 03</span>
        </div>
        <div className="checkpoint-list">
          {checkpoints.map(([id, name, copy], index) => (
            <label className={selected.includes(id) ? "checked" : ""} key={id}>
              <span className="checkpoint-number">
                {String(index + 1).padStart(2, "0")}
              </span>
              <div>
                <strong>{name}</strong>
                <small>{copy}</small>
              </div>
              <input
                type="checkbox"
                disabled={
                  mode === "auto" ||
                  mode === "manual_controlled" ||
                  id === "exception_escalation"
                }
                checked={selected.includes(id)}
                onChange={() =>
                  setSelected(
                    selected.includes(id)
                      ? selected.filter((item) => item !== id)
                      : [...selected, id],
                  )
                }
              />
              <span className="fake-check">
                <Check size={13} />
              </span>
            </label>
          ))}
        </div>
      </div>
      <div className="form-section">
        <div className="section-head compact">
          <div>
            <h2>参与者与仓库团队</h2>
            <p>
              绑定已创建的 AgentTeams 身份；系统不会生成无法运行的占位 Agent。
            </p>
          </div>
          <span className="step">03 / 03</span>
        </div>
        <div className="agent-binding">
          <label>
            组织 Leader
            <select
              value={organizationLeaderId}
              onChange={(event) => {
                const leader = agents.find(
                  (item) => item.id === event.target.value,
                );
                setOrganizationLeaderId(event.target.value);
                setOrganizationId(leader?.organization_id || "");
                setRepos([newRepo()]);
              }}
            >
              <option value="">请选择已注册的组织 Leader</option>
              {agents
                .filter(
                  (item) =>
                    item.role === "organization_leader" &&
                    item.status === "active",
                )
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.agentteams_resource_name}
                  </option>
                ))}
            </select>
          </label>
          <label>
            组织 ID
            <input
              readOnly
              placeholder="选择 Leader 后自动填写"
              value={organizationId}
            />
          </label>
        </div>
        <div className="assignment-row">
          <div>
            <label>
              项目审核负责人
              <select
                value={reviewer}
                disabled={mode === "auto"}
                onChange={(e) => setReviewer(e.target.value)}
              >
                <option value="">请选择人员</option>
                {accounts.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.display_name} · @{item.username}
                  </option>
                ))}
              </select>
            </label>
            <div className="permission-summary">
              <Eye size={16} />
              <span>代码权限</span>
              <strong>{mode === "auto" ? "无需人工" : "可读写"}</strong>
            </div>
          </div>
          <div className="repo-stack">
            {repos.map((repo, index) => (
              <div className="repo-binding" key={index}>
                <div className="repo-binding-head">
                  <span>
                    <Bot size={18} />
                  </span>
                  <strong>仓库团队 {String.fromCharCode(65 + index)}</strong>
                  {repos.length > 1 && (
                    <button
                      title="删除仓库团队"
                      className="icon-button"
                      onClick={() =>
                        setRepos(
                          repos.filter((_, itemIndex) => itemIndex !== index),
                        )
                      }
                    >
                      <Trash2 size={16} />
                    </button>
                  )}
                </div>
                <select
                  value={repo.leader_agent_id}
                  onChange={(e) => {
                    const leader = agents.find(
                      (item) => item.id === e.target.value,
                    );
                    setRepos(
                      repos.map((item, itemIndex) =>
                        itemIndex === index
                          ? {
                              ...item,
                              repository_id: leader?.repository_id || "",
                              leader_agent_id: e.target.value,
                              worker_agent_ids: [""],
                            }
                          : item,
                      ),
                    );
                  }}
                >
                  <option value="">选择仓库 Leader</option>
                  {agents
                    .filter(
                      (item) =>
                        item.role === "repository_leader" &&
                        item.organization_id === organizationId &&
                        item.status === "active",
                    )
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.agentteams_resource_name}
                      </option>
                    ))}
                </select>
                <select
                  value={repo.worker_agent_ids[0]}
                  onChange={(e) =>
                    setRepos(
                      repos.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, worker_agent_ids: [e.target.value] }
                          : item,
                      ),
                    )
                  }
                >
                  <option value="">选择 Worker</option>
                  {agents
                    .filter(
                      (item) =>
                        item.role === "worker" &&
                        item.leader_agent_id === repo.leader_agent_id &&
                        item.status === "active",
                    )
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.agentteams_resource_name}
                      </option>
                    ))}
                </select>
                <code>{repo.repository_id || "等待选择仓库 Leader"}</code>
              </div>
            ))}
            <button
              className="add-row"
              onClick={() => setRepos([...repos, newRepo()])}
            >
              <Plus size={16} />
              添加仓库团队
            </button>
          </div>
        </div>
      </div>
      <footer className="action-bar">
        <div>
          <strong>项目 ID</strong>
          <code>{projectId}</code>
        </div>
        <div>
          {message && <span className="form-message">{message}</span>}
          <button className="primary" onClick={submit}>
            创建项目并启动 Agent <ChevronRight size={17} />
          </button>
        </div>
      </footer>
    </section>
  );
}
