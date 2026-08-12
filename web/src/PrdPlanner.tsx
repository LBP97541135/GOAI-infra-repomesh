import { useState } from "react";
import {
  AlertTriangle,
  Boxes,
  Check,
  CheckCircle2,
  ChevronRight,
  FileText,
  Layers,
  ListChecks,
  Loader2,
  RefreshCw,
  Search,
  Star,
} from "lucide-react";
import {
  api,
  type ConfirmationResult,
  type ConfirmationSummary,
  type DiscoveryCandidate,
  type IntegratedPlan,
  type RequirementAnalysis,
} from "./api";

const MAX_PRD_LENGTH = 20000;

function riskLabel(risk: string): { text: string; tone: string } {
  if (risk === "high") return { text: "高风险", tone: "high" };
  if (risk === "low") return { text: "低风险", tone: "low" };
  return { text: "中风险", tone: "medium" };
}

function confidenceText(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

export function PrdPlanner({
  onPlanReady,
}: {
  onPlanReady?: (plan: IntegratedPlan, requirement: string) => void;
} = {}) {
  const [requirement, setRequirement] = useState("");
  const [message, setMessage] = useState("");

  // Step 1：需求分析
  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<RequirementAnalysis | null>(null);

  // Step 2：仓库发现
  const [discovering, setDiscovering] = useState(false);
  const [candidates, setCandidates] = useState<DiscoveryCandidate[] | null>(null);
  const [selectedRepos, setSelectedRepos] = useState<Record<string, boolean>>({});
  const [evidence, setEvidence] = useState<Record<string, [string, number][]>>({});

  // Step 3：TM 确认
  const [confirming, setConfirming] = useState(false);
  const [confirmation, setConfirmation] = useState<ConfirmationSummary | null>(null);

  // Step 4：集成方案
  const [integrating, setIntegrating] = useState(false);
  const [plan, setPlan] = useState<IntegratedPlan | null>(null);

  const prdValid = requirement.trim().length >= 3;
  const reachedDiscovery = candidates !== null;
  const reachedConfirmation = confirmation !== null;
  const reachedPlan = plan !== null;

  const handleAnalyze = async () => {
    setMessage("");
    setAnalyzing(true);
    setAnalysis(null);
    try {
      setAnalysis(await api.requirementAnalysis(requirement.trim()));
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setAnalyzing(false);
    }
  };

  const handleDiscover = async () => {
    setMessage("");
    setDiscovering(true);
    setCandidates(null);
    setSelectedRepos({});
    setEvidence({});
    setConfirmation(null);
    setPlan(null);
    try {
      const items = await api.discovery(requirement.trim());
      setCandidates(items);
      setSelectedRepos(
        Object.fromEntries(items.map((item) => [item.repository_name, true])),
      );
      setEvidence(
        Object.fromEntries(
          items.map((item) => [
            item.repository_name,
            item.matched_terms.map((term): [string, number] => [term, 1]),
          ]),
        ),
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setDiscovering(false);
    }
  };

  const selectedList =
    candidates?.filter((item) => selectedRepos[item.repository_name]) ?? [];

  const handleConfirm = async () => {
    if (selectedList.length === 0) {
      setMessage("请至少勾选一个候选仓库");
      return;
    }
    setMessage("");
    setConfirming(true);
    setConfirmation(null);
    setPlan(null);
    try {
      setConfirmation(
        await api.confirmation(
          requirement.trim(),
          selectedList.map((item) => item.repository_name),
          evidence,
        ),
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setConfirming(false);
    }
  };

  const handleIntegrate = async () => {
    if (!confirmation) return;
    setMessage("");
    setIntegrating(true);
    setPlan(null);
    try {
      const result = await api.integration(requirement.trim(), confirmation);
      setPlan(result);
      onPlanReady?.(result, requirement.trim());
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setIntegrating(false);
    }
  };

  const handlePrdChange = (value: string) => {
    setRequirement(value);
    setMessage("");
    // 修改 PRD 会使所有下游分析结果失效
    if (analysis || candidates || confirmation || plan) {
      setAnalysis(null);
      setCandidates(null);
      setSelectedRepos({});
      setEvidence({});
      setConfirmation(null);
      setPlan(null);
    }
  };

  const renderAnalysis = () => {
    if (!analysis) return null;
    return (
      <div className="analysis-panel">
        <div className="analysis-status">
          <span className={`verdict ${analysis.sufficient ? "ok" : "bad"}`}>
            {analysis.sufficient ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
            {analysis.sufficient ? "信息充分，可进入仓库发现" : "信息不充分，建议补充"}
          </span>
          <span className="analysis-confidence">
            置信度 {confidenceText(analysis.confidence)}
          </span>
        </div>
        {analysis.missing_dimensions.length > 0 && (
          <div className="analysis-row">
            <strong>缺失维度</strong>
            <div className="chip-row">
              {analysis.missing_dimensions.map((item) => (
                <span className="chip chip-warn" key={item}>{item}</span>
              ))}
            </div>
          </div>
        )}
        {analysis.questions.length > 0 && (
          <div className="analysis-row">
            <strong>待澄清问题</strong>
            <ul className="question-list">
              {analysis.questions.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="analysis-row">
          <strong>提取关键词</strong>
          <div className="chip-row">
            {analysis.extracted_keywords.map((item) => (
              <span className="chip" key={item}>{item}</span>
            ))}
          </div>
        </div>
      </div>
    );
  };

  const renderCandidates = () => {
    if (!candidates) return null;
    return (
      <div className="candidate-list">
        {candidates.map((item: DiscoveryCandidate) => {
          const checked = !!selectedRepos[item.repository_name];
          return (
            <label
              className={`candidate ${checked ? "checked" : ""}`}
              key={item.repository_id}
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={(event) =>
                  setSelectedRepos({
                    ...selectedRepos,
                    [item.repository_name]: event.target.checked,
                  })
                }
              />
              <span className="fake-check"><Check size={13} /></span>
              <div className="candidate-body">
                <div className="candidate-head">
                  <strong>{item.repository_name}</strong>
                  {item.is_entry_point && (
                    <em><Star size={12} /> 入口点</em>
                  )}
                  <span className="candidate-score">
                    <i style={{ width: `${item.score}%` }} />
                  </span>
                  <b>{item.score}</b>
                </div>
                <div className="chip-row">
                  {item.matched_terms.map((term) => (
                    <span className="chip" key={term}>{term}</span>
                  ))}
                </div>
                <p>{item.rationale}</p>
              </div>
            </label>
          );
        })}
      </div>
    );
  };

  const renderConfirmationGroup = (
    title: string,
    tone: string,
    results: ConfirmationResult[],
  ) => {
    if (results.length === 0) return null;
    return (
      <div className="confirm-group">
        <div className="confirm-group-head">
          <span className={`confirm-tone ${tone}`} />
          <strong>{title}</strong>
          <small>{results.length} 个仓库</small>
        </div>
        {results.map((result) => (
          <div className="confirm-card" key={result.repository}>
            <div className="confirm-card-head">
              <strong>{result.repository}</strong>
              <span className="confirm-confidence">
                置信度 {confidenceText(result.confidence)}
              </span>
              {result.plan && (
                <span className={`risk-tag ${riskLabel(result.plan.risk).tone}`}>
                  {riskLabel(result.plan.risk).text}
                </span>
              )}
            </div>
            <p className="confirm-reason">{result.reason}</p>
            {result.plan_summary && (
              <p className="confirm-summary">{result.plan_summary}</p>
            )}
            {result.plan && (
              <div className="plan-details">
                {result.plan.changed_modules.length > 0 && (
                  <div className="plan-detail-row">
                    <span>变更模块</span>
                    <div className="chip-row">
                      {result.plan.changed_modules.map((item) => (
                        <span className="chip" key={item}>{item}</span>
                      ))}
                    </div>
                  </div>
                )}
                {result.plan.changed_apis.length > 0 && (
                  <div className="plan-detail-row">
                    <span>变更 API</span>
                    <div className="chip-row">
                      {result.plan.changed_apis.map((item) => (
                        <span className="chip" key={item}>{item}</span>
                      ))}
                    </div>
                  </div>
                )}
                {result.plan.depends_on.length > 0 && (
                  <div className="plan-detail-row">
                    <span>依赖</span>
                    <div className="chip-row">
                      {result.plan.depends_on.map((item) => (
                        <span className="chip" key={item}>{item}</span>
                      ))}
                    </div>
                  </div>
                )}
                {result.missing_dependencies.length > 0 && (
                  <div className="plan-detail-row">
                    <span>缺失依赖</span>
                    <div className="chip-row">
                      {result.missing_dependencies.map((item) => (
                        <span className="chip chip-warn" key={item}>{item}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    );
  };

  const renderPlan = () => {
    if (!plan) return null;
    return (
      <div className="plan-block">
        <div className="plan-section">
          <div className="plan-section-head">
            <Boxes size={16} />
            <strong>工程 Spec</strong>
          </div>
          <pre className="spec-pre">{plan.engineering_spec || "（无）"}</pre>
        </div>
        <div className="plan-section">
          <div className="plan-section-head">
            <Layers size={16} />
            <strong>契约（{plan.contracts.length}）</strong>
          </div>
          <table className="contract-table">
            <thead>
              <tr>
                <th>生产者</th>
                <th>消费者</th>
                <th>接口</th>
                <th>约定</th>
              </tr>
            </thead>
            <tbody>
              {plan.contracts.map((contract, index) => (
                <tr key={index}>
                  <td><code>{contract.producer}</code></td>
                  <td><code>{contract.consumer}</code></td>
                  <td>{contract.interface}</td>
                  <td>{contract.agreement}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="plan-section">
          <div className="plan-section-head">
            <ListChecks size={16} />
            <strong>Task DAG（{plan.task_dag.length}）</strong>
          </div>
          {plan.task_dag.map((task, index) => (
            <div className="dag-node" key={index}>
              <div className="dag-node-head">
                <strong>{task.repository}</strong>
                {task.depends_on.length > 0 && (
                  <span>依赖：{task.depends_on.join("、")}</span>
                )}
                {task.parallelizable_with.length > 0 && (
                  <span>可与 {task.parallelizable_with.join("、")} 并行</span>
                )}
              </div>
              <p>{task.instruction}</p>
              {task.tests.length > 0 && (
                <div className="chip-row">
                  {task.tests.map((test) => (
                    <span className="chip chip-mono" key={test}>{test}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="plan-section">
          <div className="plan-section-head">
            <Layers size={16} />
            <strong>执行批次</strong>
          </div>
          <div className="batch-list">
            {plan.execution_batches.map((batch, index) => (
              <div className="batch-row" key={index}>
                <span>批次 {index + 1}</span>
                <div className="chip-row">
                  {batch.map((repo) => (
                    <span className="chip" key={repo}>{repo}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  };

  return (
    <section className="content prd-planner">
      <div className="section-head">
        <div>
          <h2>提交 PRD，制定多仓库方案</h2>
          <p>输入产品需求，Agent 依次完成仓库发现、团队确认与集成方案生成。</p>
        </div>
        <span className="step">方案制定链路</span>
      </div>

      {/* Step 1：提交 PRD */}
      <div className="form-section">
        <div className="section-head compact">
          <div>
            <h2>1 · 提交 PRD</h2>
            <p>需求文本将作为 requirement 传入方案制定链路。</p>
          </div>
          <span className="step">01 / 04</span>
        </div>
        <div className="prd-editor">
          <textarea
            value={requirement}
            maxLength={MAX_PRD_LENGTH}
            placeholder="粘贴产品需求文档（PRD）文本，例如：为订单服务增加对账能力，需暴露对账查询接口，并与其他结算仓库联动…"
            onChange={(event) => handlePrdChange(event.target.value)}
          />
          <div className="prd-editor-foot">
            <span>{requirement.length} / {MAX_PRD_LENGTH}</span>
            <button
              className="secondary"
              disabled={!prdValid || analyzing}
              onClick={handleAnalyze}
            >
              {analyzing ? <Loader2 size={15} className="spin" /> : <Search size={15} />}
              {analyzing ? "分析中…" : "需求分析"}
            </button>
            <button
              className="primary"
              disabled={!prdValid || discovering}
              onClick={handleDiscover}
            >
              {discovering ? <Loader2 size={15} className="spin" /> : <FileText size={15} />}
              {discovering ? "发现中…" : "开始发现仓库"}
            </button>
          </div>
        </div>
        {renderAnalysis()}
      </div>

      {/* Step 2：仓库发现 */}
      {reachedDiscovery && (
        <div className="form-section">
          <div className="section-head compact">
            <div>
              <h2>2 · 仓库发现</h2>
              <p>按相关性打分匹配候选仓库，勾选参与确认的范围。</p>
            </div>
            <span className="step">02 / 04</span>
          </div>
          {renderCandidates()}
          <div className="step-actions">
            <button
              className="primary"
              disabled={selectedList.length === 0 || confirming}
              onClick={handleConfirm}
            >
              {confirming ? <Loader2 size={15} className="spin" /> : <ChevronRight size={15} />}
              {confirming ? "确认中…" : `进入 TM 确认（${selectedList.length}）`}
            </button>
          </div>
        </div>
      )}

      {/* Step 3：TM 确认 */}
      {reachedConfirmation && (
        <div className="form-section">
          <div className="section-head compact">
            <div>
              <h2>3 · TM 确认</h2>
              <p>各仓库团队评估变更范围与风险。</p>
            </div>
            <span className="step">03 / 04</span>
          </div>
          {renderConfirmationGroup("必须参与", "ok", confirmation.required)}
          {renderConfirmationGroup("可能参与", "maybe", confirmation.maybe)}
          {renderConfirmationGroup("不参与", "no", confirmation.excluded)}
          <div className="step-actions">
            <button className="primary" disabled={integrating} onClick={handleIntegrate}>
              {integrating ? <Loader2 size={15} className="spin" /> : <ChevronRight size={15} />}
              {integrating ? "生成中…" : "生成集成方案"}
            </button>
          </div>
        </div>
      )}

      {/* Step 4：集成方案 */}
      {reachedPlan && (
        <div className="form-section">
          <div className="section-head compact">
            <div>
              <h2>4 · 集成方案</h2>
              <p>工程 Spec、跨仓库契约与任务执行计划。</p>
            </div>
            <span className="step">04 / 04</span>
          </div>
          {renderPlan()}
        </div>
      )}

      {/* 底部操作栏 */}
      <footer className="action-bar">
        <div>
          <strong>PRD 字数</strong>
          <code>{requirement.length} / {MAX_PRD_LENGTH}</code>
        </div>
        <div>
          {message && <span className="form-message error-text">{message}</span>}
          {reachedPlan ? (
            <button
              className="secondary"
              onClick={() => {
                setRequirement("");
                setAnalysis(null);
                setCandidates(null);
                setSelectedRepos({});
                setEvidence({});
                setConfirmation(null);
                setPlan(null);
                setMessage("");
              }}
            >
              <RefreshCw size={15} />
              重新开始
            </button>
          ) : (
            <span className="step">
              {!prdValid
                ? "请先输入至少 3 个字符的 PRD"
                : reachedConfirmation
                  ? "下一步：生成集成方案"
                  : reachedDiscovery
                    ? "下一步：TM 确认"
                    : "下一步：需求分析 / 发现仓库"}
            </span>
          )}
        </div>
      </footer>
    </section>
  );
}
