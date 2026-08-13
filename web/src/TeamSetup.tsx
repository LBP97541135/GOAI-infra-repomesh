import { useEffect, useMemo, useState } from 'react'
import { Check, Crown, Plus, UserRoundPlus, UsersRound, X } from 'lucide-react'
import { api, type AgentPrincipal, type AgentTeamResult } from './api'

type FormState = { organizationId: string; name: string; description: string; leaderId: string; memberIds: string[] }
const shortId = (value: string) => value.slice(0, 8)

export function TeamSetup() {
  const [agents, setAgents] = useState<AgentPrincipal[]>([])
  const [orgNames, setOrgNames] = useState<Record<string, string>>({})
  const [created, setCreated] = useState<AgentTeamResult[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => { api.agents().then(setAgents).catch((reason) => setError(reason.message)).finally(() => setLoading(false)) }, [])
  useEffect(() => { api.organizations().then((orgs) => setOrgNames(Object.fromEntries(orgs.map((org) => [org.id, org.name])))).catch(() => {}) }, [])
  const leaders = agents.filter((agent) => agent.role === 'repository_leader')
  const workers = agents.filter((agent) => agent.role === 'worker')
  const persistedTeams = leaders.map((leader) => ({
    name: leader.repository_id ? `repository-${shortId(leader.repository_id)}` : leader.agentteams_resource_name,
    leader,
    workers: workers.filter((worker) => worker.leader_agent_id === leader.id),
  }))

  return <section className="content team-page">
    <div className="section-head team-page-head"><div><h2>Repository Teams</h2><p>从已注册的 Agent 中选择负责人和成员，一次完成 AgentTeams 团队装配。</p></div><button className="primary" onClick={() => setOpen(true)}><Plus size={15} />一键配置 Team</button></div>
    {error && <p className="error">{error}</p>}
    <div className="team-stats"><Stat value={new Set(agents.map((agent) => agent.organization_id)).size} label="已接入组织" /><Stat value={leaders.length} label="Repository Leader" /><Stat value={workers.length} label="可分配 Worker" /></div>
    <div className="team-board">
      <div className="team-board-head"><div><h3>长期仓库团队</h3><p>从持久化 Agent 目录恢复 Leader、Worker 与仓库绑定。</p></div><span>{persistedTeams.length} Teams</span></div>
      {loading ? <div className="team-empty"><UsersRound size={27} /><strong>正在读取 Agent 目录</strong></div> : persistedTeams.length === 0 ? <div className="team-empty"><UsersRound size={27} /><strong>还没有 Repository Team</strong><p>先通过启动向导接入仓库，或点击右上角手动配置。</p></div> : <div className="team-list">{persistedTeams.map((team) => <div className="team-card" key={team.leader.id}><div className="team-card-icon"><UsersRound size={19} /></div><div><strong>{team.name}</strong><small>{team.leader.agentteams_resource_name}</small></div><div className="team-member-count"><b>{team.workers.length}</b><span>Workers</span></div><span className="ready"><Check size={12} />已注册</span></div>)}{created.filter((result) => !persistedTeams.some((team) => team.leader.id === result.leader.id)).map((result) => <div className="team-card" key={result.team.name}><div className="team-card-icon"><UsersRound size={19} /></div><div><strong>{result.team.name}</strong><small>Leader · {shortId(result.leader.id)}</small></div><div className="team-member-count"><b>{result.members.length}</b><span>Workers</span></div><span className="ready"><Check size={12} />已创建</span></div>)}</div>}
    </div>
    {open && <TeamDialog agents={agents} orgNames={orgNames} onClose={() => setOpen(false)} onCreated={(result) => { setCreated((current) => [result, ...current]); setOpen(false) }} />}
  </section>
}

function Stat({ value, label }: { value: number; label: string }) { return <div><strong>{value}</strong><span>{label}</span></div> }

function TeamDialog({ agents, orgNames, onClose, onCreated }: { agents: AgentPrincipal[]; orgNames: Record<string, string>; onClose: () => void; onCreated: (result: AgentTeamResult) => void }) {
  const organizations = useMemo(() => [...new Set(agents.map((agent) => agent.organization_id))], [agents])
  const [form, setForm] = useState<FormState>(() => ({ organizationId: organizations.length === 1 ? organizations[0] : '', name: '', description: '', leaderId: '', memberIds: [] }))
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const leaders = agents.filter((agent) => agent.organization_id === form.organizationId && agent.role === 'repository_leader')
  const workers = agents.filter((agent) => agent.organization_id === form.organizationId && agent.role === 'worker' && agent.leader_agent_id === form.leaderId)

  useEffect(() => { const close = (event: KeyboardEvent) => event.key === 'Escape' && onClose(); window.addEventListener('keydown', close); return () => window.removeEventListener('keydown', close) }, [onClose])
  const toggleMember = (id: string) => setForm((current) => ({ ...current, memberIds: current.memberIds.includes(id) ? current.memberIds.filter((item) => item !== id) : [...current.memberIds, id] }))
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setSubmitting(true); setError('')
    try { onCreated(await api.createAgentTeam({ organization_id: form.organizationId, name: form.name.trim(), description: form.description.trim(), leader_agent_id: form.leaderId, member_agent_ids: form.memberIds, idempotency_key: `manual-team:${form.organizationId}:${form.name.trim()}` })) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Team 创建失败') }
    finally { setSubmitting(false) }
  }

  return <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><form className="team-dialog" role="dialog" aria-modal="true" aria-labelledby="team-dialog-title" onSubmit={submit}>
    <div className="dialog-head"><div className="dialog-title-icon"><UsersRound size={22} /></div><div><h2 id="team-dialog-title">一键配置 Team</h2><p>选择现有 Agent，创建由 Repository Leader 协调的执行团队。</p></div><button type="button" className="icon-button" title="关闭" onClick={onClose}><X size={18} /></button></div>
    <div className="dialog-body">
      <div className="dialog-grid"><label>Team 名称<input autoFocus required minLength={3} maxLength={100} pattern="[A-Za-z0-9][A-Za-z0-9_-]+" placeholder="例如：saleor_backend" value={form.name} onChange={(event) => setForm({...form, name: event.target.value})} /><small>使用英文、数字、短横线或下划线</small></label><label>所属组织<select required value={form.organizationId} onChange={(event) => setForm({...form, organizationId: event.target.value, leaderId: '', memberIds: []})}><option value="">选择组织</option>{organizations.map((id) => <option key={id} value={id}>{orgNames[id] || shortId(id)}</option>)}</select></label></div>
      <label>描述<textarea maxLength={255} rows={3} placeholder="这个 Team 负责什么？" value={form.description} onChange={(event) => setForm({...form, description: event.target.value})} /><small className="char-count">{form.description.length} / 255</small></label>
      <fieldset><legend><Crown size={15} />Leader Agent</legend><p>接收该 Team 的任务，负责拆分、协调和结果汇报。</p><select required value={form.leaderId} onChange={(event) => setForm({...form, leaderId: event.target.value, memberIds: []})}><option value="">选择一个 Repository Leader</option>{leaders.map((agent) => <option key={agent.id} value={agent.id}>{shortId(agent.id)} · {agent.repository_id ? `仓库 ${shortId(agent.repository_id)}` : '未绑定仓库'}</option>)}</select>{form.organizationId && leaders.length === 0 && <small className="field-warning">该组织还没有可用的 Repository Leader</small>}</fieldset>
      <fieldset><legend><UserRoundPlus size={15} />附加成员 <em>可选</em></legend><p>只显示由当前 Leader 管理的 Worker，避免越权组队。</p>{!form.leaderId ? <div className="member-placeholder">请先选择 Leader Agent</div> : workers.length === 0 ? <div className="member-placeholder">该 Leader 暂无可分配 Worker，可先创建空 Team</div> : <div className="member-picker">{workers.map((agent) => <label key={agent.id} className={form.memberIds.includes(agent.id) ? 'selected' : ''}><input type="checkbox" checked={form.memberIds.includes(agent.id)} onChange={() => toggleMember(agent.id)} /><span><strong>Worker · {shortId(agent.id)}</strong><small>{agent.agentteams_resource_name}</small></span><i><Check size={13} /></i></label>)}</div>}</fieldset>
      {error && <p className="error">{error}</p>}
    </div>
    <div className="dialog-actions"><span>{form.memberIds.length} 个 Worker 已选择</span><button type="button" className="secondary" onClick={onClose}>取消</button><button className="primary" disabled={!form.name.trim() || !form.organizationId || !form.leaderId || submitting}>{submitting ? '正在创建…' : '创建 Team'}</button></div>
  </form></div>
}
