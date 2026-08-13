import { useEffect, useState } from 'react'
import { Boxes, ChevronRight, FolderGit2, LoaderCircle, Plus } from 'lucide-react'
import { api, type ProjectTopology } from './api'

export function ProjectList({ onCreate, onOpen }: { onCreate: () => void; onOpen: (project: ProjectTopology) => void }) {
  const [projects, setProjects] = useState<ProjectTopology[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => { api.projects().then(setProjects).catch((reason) => setError(reason.message)).finally(() => setLoading(false)) }, [])
  return <section className="content project-list-page">
    <div className="section-head project-list-head"><div><h2>协作项目</h2><p>打开已持久化项目，继续方案制定、任务派发和人工审核。</p></div><button className="primary" onClick={onCreate}><Plus size={15} />创建项目</button></div>
    {error && <div className="error">{error}</div>}
    {loading ? <div className="project-list-empty"><LoaderCircle className="spin" size={25} />正在读取项目</div> : projects.length === 0 ? <div className="project-list-empty"><Boxes size={28} /><strong>还没有协作项目</strong><p>完成启动向导和 Team 接入后创建第一个项目。</p><button className="primary" onClick={onCreate}>创建项目</button></div> : <div className="project-table"><div className="project-table-head"><span>项目</span><span>执行模式</span><span>仓库团队</span><span>状态</span><span /></div>{projects.map((project) => <button key={project.project_id} onClick={() => onOpen(project)}><span className="project-identity"><i><FolderGit2 size={17} /></i><span><strong>{project.project_id.slice(0, 8)}</strong><small>组织 {project.organization_id.slice(0, 8)}</small></span></span><em>{modeLabel(project.execution_mode)}</em><b>{project.repository_teams.length}</b><span className={`project-status ${project.operational_status}`}>{statusLabel(project.operational_status)}</span><ChevronRight size={15} /></button>)}</div>}
  </section>
}

const modeLabel = (mode: ProjectTopology['execution_mode']) => mode === 'auto' ? '自动执行' : mode === 'supervised' ? '人工监督' : '人工控制'
const statusLabel = (status: ProjectTopology['operational_status']) => status === 'active' ? '进行中' : status === 'paused' ? '已暂停' : '已取消'
