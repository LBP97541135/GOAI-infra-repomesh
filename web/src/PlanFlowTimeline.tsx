import { ArrowRight, Check, Circle, Play, Sparkles } from 'lucide-react'
import type { PlanDiff, IntegratedPlan } from './api'

type Props = {
  plan: IntegratedPlan
  /** PR-4 graph diff to visualise: added edges get green NEW highlights,
   * removed edges red strikethrough labels, added repos a NEW badge. */
  diff?: PlanDiff
  /** 正在执行的批次下标（0 起）。缺省表示方案已生成、等待派发。 */
  activeBatchIndex?: number
}

type TaskStatus = 'done' | 'running' | 'pending'

const STATUS_LABEL: Record<TaskStatus, string> = {
  done: '已完成',
  running: '执行中',
  pending: '待执行',
}

/**
 * 执行流程视图：对标 Cursor 3 / Claude Code 的 Agent 任务流范式。
 *
 * 单源投影（PR-5）：本组件只消费 ``plan.graph`` —— 批次泳道取自图的
 * execution_batches 投影，依赖标签 / 契约徽章 / 指令全部从图的 edges 与
 * nodes 推导，不再拼接顶层 execution_batches + task_dag 两份数据。
 * 传入 PR-4 的 ``diff`` 时叠加变更足迹：新增边绿色高亮 + NEW 徽标、
 * 删除边红色删除线标注。
 */
export function PlanFlowTimeline({ plan, diff, activeBatchIndex }: Props) {
  const graph = plan.graph

  if (!graph) {
    return (
      <div className="taskflow-empty">
        方案图数据缺失（graph 为空）——单源投影要求渲染数据来自统一依赖图
      </div>
    )
  }

  const batches = graph.execution_batches.filter((batch) => batch.length > 0)

  // ↓ 全部从图推导（单源）：
  const instructionOf = new Map(
    graph.nodes.map((node) => [node.repository, node.instruction ?? '']),
  )
  const confirmedEdges = graph.edges.filter((edge) => edge.status === 'confirmed')
  const dependsOf = new Map<string, string[]>()
  const interfacesOf = new Map<string, string[]>()
  for (const edge of confirmedEdges) {
    const deps = dependsOf.get(edge.to) ?? []
    deps.push(edge.from)
    dependsOf.set(edge.to, deps)
    if (edge.interface) {
      const ifaces = interfacesOf.get(edge.to) ?? []
      ifaces.push(edge.interface)
      interfacesOf.set(edge.to, ifaces)
    }
  }

  // ↓ diff 足迹集合（PR-4）：
  const addedKeys = new Set((diff?.added_edges ?? []).map((e) => `${e.from}->${e.to}`))
  const removedOf = new Map<string, string[]>()
  for (const edge of diff?.removed_edges ?? []) {
    const removed = removedOf.get(edge.to) ?? []
    removed.push(edge.from)
    removedOf.set(edge.to, removed)
  }
  const addedRepos = new Set(diff?.added_repos ?? [])

  const statusOf = (batchIndex: number): TaskStatus => {
    if (activeBatchIndex == null) return 'pending'
    if (batchIndex < activeBatchIndex) return 'done'
    if (batchIndex === activeBatchIndex) return 'running'
    return 'pending'
  }

  const total = batches.flat().length
  const doneCount = activeBatchIndex == null ? 0 : batches.slice(0, activeBatchIndex).flat().length
  const progress = total === 0 ? 0 : Math.round((doneCount / total) * 100)

  return (
    <div className="taskflow">
      <div className="taskflow-progress">
        <div className="taskflow-progress-track">
          <div className="taskflow-progress-fill" style={{ width: `${progress}%` }} />
        </div>
        <span className="taskflow-progress-text">
          {activeBatchIndex == null
            ? `${total} 个任务已就绪，等待派发`
            : `已完成 ${doneCount}/${total} · 批次 ${activeBatchIndex + 1}/${batches.length} 执行中`}
        </span>
      </div>

      <div className="taskflow-stages">
        {batches.map((batch, batchIndex) => {
          const status = statusOf(batchIndex)
          return (
            <div key={batchIndex} className={`taskflow-stage ${status}`}>
              <div className="taskflow-stage-head">
                <span className="taskflow-stage-no">Stage {batchIndex + 1}</span>
                {batchIndex < batches.length - 1 && <ArrowRight size={13} className="taskflow-stage-arrow" />}
                <span className="taskflow-stage-meta">{batch.length} 个任务</span>
                <span className={`taskflow-stage-badge ${status}`}>{STATUS_LABEL[status]}</span>
              </div>
              <div className="taskflow-tasks">
                {batch.map((repo) => {
                  const deps = dependsOf.get(repo) ?? []
                  const interfaces = interfacesOf.get(repo) ?? []
                  const removedDeps = removedOf.get(repo) ?? []
                  const instruction = instructionOf.get(repo) ?? ''
                  const isNew = addedRepos.has(repo)
                  return (
                    <div key={repo} className={`taskflow-task ${status}`}>
                      <span className="taskflow-task-icon">
                        {status === 'done' && <Check size={13} />}
                        {status === 'running' && <Play size={12} className="taskflow-run" />}
                        {status === 'pending' && <Circle size={11} />}
                      </span>
                      <div className="taskflow-task-main">
                        <div className="taskflow-task-head">
                          <strong>{repo}</strong>
                          {isNew && (
                            <span className="taskflow-diff-badge new">
                              <Sparkles size={10} /> NEW
                            </span>
                          )}
                          {deps.length > 0 && (
                            <span className="taskflow-task-deps">
                              {deps.map((dep) => (
                                <span
                                  key={dep}
                                  className={`taskflow-dep-chip ${
                                    addedKeys.has(`${dep}->${repo}`) ? 'added' : ''
                                  }`}
                                >
                                  {dep}
                                  {addedKeys.has(`${dep}->${repo}`) && (
                                    <em className="taskflow-dep-new">NEW</em>
                                  )}
                                </span>
                              ))}
                            </span>
                          )}
                          {removedDeps.length > 0 && (
                            <span className="taskflow-task-deps removed">
                              {removedDeps.map((dep) => (
                                <span key={dep} className="taskflow-dep-chip removed">
                                  <s>{dep}</s> 移除
                                </span>
                              ))}
                            </span>
                          )}
                          {interfaces.length > 0 && (
                            <span className="taskflow-contract-badges">
                              {interfaces.map((iface) => (
                                <span className="taskflow-contract-badge" key={iface}>
                                  {iface}
                                </span>
                              ))}
                            </span>
                          )}
                        </div>
                        <p>{instruction}</p>
                      </div>
                      <span className={`taskflow-task-badge ${status}`}>{STATUS_LABEL[status]}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>

      <div className="taskflow-legend">
        <span><i className="tl-done" />已完成</span>
        <span><i className="tl-run" />执行中</span>
        <span><i className="tl-pending" />待执行</span>
        {diff && (
          <>
            <span><i className="tl-added" />新增依赖（NEW）</span>
            <span><i className="tl-removed" />移除依赖</span>
          </>
        )}
        <span className="taskflow-edge-count">
          依赖关系来自统一依赖图 · 共 {total} 个任务
          {diff && ` · v${diff.from_version} → v${diff.to_version}`}
        </span>
      </div>
    </div>
  )
}
