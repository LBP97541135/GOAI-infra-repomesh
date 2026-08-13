import { useCallback, useEffect, useMemo, useState } from 'react'
import { Bot, Check, ChevronRight, CircleAlert, GitBranch, LoaderCircle, RefreshCw, ServerCog, UserRoundCog } from 'lucide-react'
import { api, type AgentPrincipal, type CodingAgentProbe, type OnboardingJob, type RepositoryOnboardResult, type SetupStatus } from './api'

const checkMeta = {
  model: ['模型连接', 'RepoMesh 与 AgentTeams 共用的模型配置'],
  database: ['PostgreSQL', '项目、上下文与交付记录存储'],
  agentteams: ['AgentTeams', 'Agent 创建、团队与通讯控制平面'],
  matrix: ['Matrix', 'Leader 与 Worker 消息通道'],
  internal_auth: ['内部凭证', 'Runner、Agent Action 与 MCP Token'],
  github_app: ['GitHub App', 'PR 交付时需要，可稍后配置'],
} as const

type Phase = 'idle' | 'queued' | 'scanning' | 'registering' | 'teaming' | 'done'

export function SetupWizard({ onReady }: { onReady: () => void }) {
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [adapters, setAdapters] = useState<CodingAgentProbe[]>([])
  const [agents, setAgents] = useState<AgentPrincipal[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [jobId, setJobId] = useState('')
  const [retryJob, setRetryJob] = useState<OnboardingJob | null>(null)
  const [results, setResults] = useState<RepositoryOnboardResult['repositories']>([])
  const [organizationId, setOrganizationId] = useState<string>(() => crypto.randomUUID())
  const [orgName, setOrgName] = useState('')
  const [orgProvider, setOrgProvider] = useState<'github' | 'gitlab'>('github')
  const [leaderName, setLeaderName] = useState('organization-leader')
  const [orgUrl, setOrgUrl] = useState('')
  const [token, setToken] = useState('')
  const [workerCount, setWorkerCount] = useState(1)

  const refresh = useCallback(async () => {
    setRefreshing(true); setError('')
    try {
      const [nextStatus, probeResult, principals, jobs] = await Promise.all([api.setupStatus(), api.codingAgents(), api.agents(), api.onboardingJobs()])
      setStatus(nextStatus); setAdapters(probeResult.adapters); setAgents(principals)
      const latest = jobs[0]
      if (latest && latest.status !== 'completed') {
        setJobId(latest.id); setResults(latest.results || [])
        setOrgUrl(latest.org_url || '')
        if (latest.organization_id) setOrganizationId(latest.organization_id)
        if (latest.status === 'failed' || latest.status === 'interrupted') { setRetryJob(latest); setPhase('idle'); setError(latest.error || (latest.status === 'interrupted' ? '上次接入被服务重启中断，请重新授权后重试' : '上次仓库接入失败，可重新授权后重试')) }
        else setPhase(latest.phase as Phase)
      }
      const orgLeader = principals.find((agent) => agent.role === 'organization_leader')
      if (orgLeader) setOrganizationId(orgLeader.organization_id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '检测失败') }
    finally { setLoading(false); setRefreshing(false) }
  }, [])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    if (!jobId || phase === 'done' || phase === 'idle') return
    const timer = window.setInterval(async () => {
      try {
        const job = await api.onboardingJob(jobId)
        setResults(job.results || [])
        if (job.status === 'completed') { setPhase('done'); setToken(''); await refresh() }
        else if (job.status === 'failed' || job.status === 'interrupted') { setRetryJob(job); setPhase('idle'); setError(job.error || (job.phase === 'authorization' ? '任务需要重新输入访问 Token' : '仓库接入失败')) }
        else setPhase(job.phase === 'queued' ? 'queued' : job.phase as Phase)
      } catch (reason) { setError(reason instanceof Error ? reason.message : '无法读取接入进度') }
    }, 900)
    return () => window.clearInterval(timer)
  }, [jobId, phase, refresh])

  const organizationLeader = agents.find((agent) => agent.role === 'organization_leader' && agent.organization_id === organizationId)
  const requiredKeys = ['model', 'database', 'agentteams', 'matrix', 'internal_auth'] as const
  const passedRequired = status ? requiredKeys.filter((key) => status.checks[key]).length : 0
  const progress = Math.round((passedRequired / requiredKeys.length) * 100)
  const authorizedAdapters = adapters.filter((item) => item.installed && item.auth_status === 'authorized')
  const canOnboard = Boolean(status?.ready_for_project_creation && organizationLeader && orgUrl && phase === 'idle')

  const createLeader = async () => {
    setError('')
    try {
      const organization = await api.createOrganization({ name: orgName.trim(), scm_provider: orgProvider })
      setOrganizationId(organization.id)
      await api.createNativeAgent({ organization_id: organization.id, role: 'organization_leader', resource_name: leaderName, idempotency_key: `setup-org-leader:${organization.id}` })
      await refresh()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Organization Leader 创建失败') }
  }
  const onboard = async () => {
    setError(''); setResults([]); setPhase('queued')
    try {
      const platformToken = orgUrl.includes('gitlab') ? { gitlab_token: token } : { github_token: token }
      const payload = { organization_id: organizationId, org_url: orgUrl, ...platformToken, default_worker_count: workerCount, scan_workers: 5 }
      const job: OnboardingJob = retryJob ? await api.retryOnboardingJob(retryJob.id, payload) : await api.createOnboardingJob(payload)
      setRetryJob(null)
      setJobId(job.id); setPhase(job.phase as Phase)
    } catch (reason) { setPhase('idle'); setError(reason instanceof Error ? reason.message : '仓库接入失败') }
  }

  if (loading) return <div className="setup-loader"><LoaderCircle size={24} />正在检测运行环境</div>
  return <section className="content setup-page">
    <div className="setup-summary">
      <div><h2>{status?.ready_for_project_creation ? '平台基础能力已就绪' : '完成平台启动配置'}</h2><p>{status?.ready_for_project_creation ? '可以继续接入仓库并创建 Repository Team。' : '先完成必需依赖，再开始仓库和 Agent 初始化。'}</p></div>
      <div className="setup-progress" aria-label={`必需配置完成 ${progress}%`}><div><span style={{width: `${progress}%`}} /></div><strong>{passedRequired} / {requiredKeys.length}</strong><small>必需服务</small></div>
      <button className="secondary" disabled={refreshing} onClick={refresh}><RefreshCw size={14} className={refreshing ? 'spin' : ''} />重新检测</button>
    </div>
    {error && <div className="setup-error"><CircleAlert size={16} />{error}</div>}

    <div className="setup-layout">
      <div className="setup-steps" aria-label="启动步骤">
        <Step number="01" title="平台检测" state={status?.ready_for_project_creation ? 'done' : 'active'} />
        <Step number="02" title="Coding Agent" state={authorizedAdapters.length ? 'done' : 'active'} />
        <Step number="03" title="组织负责人" state={organizationLeader ? 'done' : 'active'} />
        <Step number="04" title="仓库与团队" state={status?.counts.repositories ? 'done' : 'active'} />
      </div>

      <div className="setup-main">
        <SetupSection icon={ServerCog} number="01" title="平台运行检测" copy="项目执行所需的基础服务和连接状态。">
          <div className="health-grid">{Object.entries(checkMeta).map(([key, [name, copy]]) => { const passed = status?.checks[key as keyof SetupStatus['checks']]; const optional = key === 'github_app'; return <div key={key} className={passed ? 'passed' : optional ? 'optional' : 'failed'}><i>{passed ? <Check size={14} /> : <CircleAlert size={14} />}</i><div><strong>{name}</strong><small>{copy}</small></div><em>{passed ? '正常' : optional ? '稍后配置' : '待处理'}</em></div> })}</div>
          {!status?.ready_for_project_creation && <div className="setup-note">必需配置需要在服务启动环境中补齐，修改后点击“重新检测”。</div>}
        </SetupSection>

        <SetupSection icon={Bot} number="02" title="Coding Agent 检测" copy="检测 API 所在环境中的 CLI 安装和登录状态。">
          <div className="adapter-list">{adapters.map((item) => <div key={item.adapter_id}><span className={item.installed && item.auth_status === 'authorized' ? 'adapter-ok' : ''}><Bot size={16} /></span><div><strong>{item.display_name}</strong><small>{item.installed ? item.executable : '未检测到 CLI'}</small></div><em>{!item.installed ? '未安装' : item.auth_status === 'authorized' ? '已登录' : '需要登录'}</em></div>)}</div>
        </SetupSection>

        <SetupSection icon={UserRoundCog} number="03" title="Organization Leader" copy="每个组织保留一个长期负责人，负责项目级拆解和仓库分工。">
          {organizationLeader ? <div className="leader-ready"><span><Check size={17} /></span><div><strong>{organizationLeader.agentteams_resource_name}</strong><small>组织 {organizationLeader.organization_id.slice(0, 8)} · AgentTeams 已注册</small></div></div> : <div className="leader-form"><label>组织名称<input placeholder="例如：Acme Corp" value={orgName} onChange={(event) => setOrgName(event.target.value)} /></label><label>代码平台<select value={orgProvider} onChange={(event) => setOrgProvider(event.target.value as 'github' | 'gitlab')}><option value="github">GitHub</option><option value="gitlab">GitLab</option></select></label><label>Agent 名称<input value={leaderName} onChange={(event) => setLeaderName(event.target.value)} /></label><button className="primary" disabled={!orgName.trim() || leaderName.length < 3 || !status?.checks.agentteams} onClick={createLeader}>创建负责人<ChevronRight size={14} /></button></div>}
        </SetupSection>

        <SetupSection icon={GitBranch} number="04" title="接入仓库并创建 Team" copy="扫描组织仓库，为每个仓库创建长期 Leader、默认 Worker 和 AgentTeams Team。">
          <div className="onboard-form"><label>GitHub / GitLab 组织地址<input placeholder="https://github.com/your-organization" value={orgUrl} onChange={(event) => setOrgUrl(event.target.value)} /></label><label>访问 Token <small>私有仓库需要</small><input type="password" placeholder="可选" value={token} onChange={(event) => setToken(event.target.value)} /></label><label>每仓库 Worker<select value={workerCount} onChange={(event) => setWorkerCount(Number(event.target.value))}>{[1,2,3,4].map((count) => <option value={count} key={count}>{count}</option>)}</select></label><button className="primary" disabled={!canOnboard} onClick={onboard}>{phase === 'idle' ? '开始接入' : phase === 'done' ? '接入完成' : '正在处理'}{phase !== 'idle' && phase !== 'done' ? <LoaderCircle size={14} className="spin" /> : <ChevronRight size={14} />}</button></div>
          {phase !== 'idle' && <OnboardProgress phase={phase} />}
          {results.length > 0 && <div className="onboard-results">{results.map((item) => <div key={item.repository_id}><span className={item.agent_team === 'ready' ? 'success' : 'failure'}>{item.agent_team === 'ready' ? <Check size={13} /> : <CircleAlert size={13} />}</span><div><strong>{item.repository_name}</strong><small>{item.scan === 'created' ? '已扫描并注册' : '复用已有仓库'} · {item.agent_team === 'ready' ? 'Team 已就绪' : item.detail}</small></div></div>)}</div>}
        </SetupSection>
      </div>
    </div>
    <div className="setup-footer"><div><strong>{status?.ready_for_project_creation && organizationLeader && status.counts.repositories ? '启动配置已完成' : '完成全部必需步骤后即可创建项目'}</strong><span>{status?.counts.repositories || 0} 个仓库 · {status?.counts.agents || 0} 个 Agent</span></div><button className="primary" disabled={!status?.ready_for_project_creation || !organizationLeader || !status.counts.repositories} onClick={onReady}>进入项目创建<ChevronRight size={15} /></button></div>
  </section>
}

function Step({ number, title, state }: { number: string; title: string; state: 'done' | 'active' }) { return <div className={state}><i>{state === 'done' ? <Check size={13} /> : number}</i><span>{title}</span></div> }
function SetupSection({ icon: Icon, number, title, copy, children }: { icon: typeof ServerCog; number: string; title: string; copy: string; children: React.ReactNode }) { return <section className="setup-section"><header><span><Icon size={18} /></span><div><small>STEP {number}</small><h3>{title}</h3><p>{copy}</p></div></header><div className="setup-section-body">{children}</div></section> }
function OnboardProgress({ phase }: { phase: Phase }) { const steps = [['scanning','扫描仓库'],['registering','注册仓库'],['teaming','创建 Agent Team']] as const; const current = phase === 'done' ? 3 : phase === 'queued' ? -1 : steps.findIndex(([id]) => id === phase); return <div className="onboard-progress">{steps.map(([id,label], index) => <div key={id} className={index < current || phase === 'done' ? 'done' : index === current ? 'running' : ''}><i>{index < current || phase === 'done' ? <Check size={11} /> : index + 1}</i><span>{phase === 'queued' && index === 0 ? '等待后台执行' : label}</span></div>)}</div> }
