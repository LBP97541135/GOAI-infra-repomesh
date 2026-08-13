import { useEffect, useState } from 'react'
import { Bell, Boxes, ListChecks, LogOut, Network, Plus, ShieldCheck, Users, UsersRound } from 'lucide-react'
import { api, type Account, type ReviewRequest } from './api'
import { Login } from './Login'
import { ProjectSetup } from './ProjectSetup'
import { ReviewWorkbench } from './ReviewWorkbench'
import { TeamSetup } from './TeamSetup'
import { SetupWizard } from './SetupWizard'

type View = 'setup' | 'reviews' | 'projects' | 'teams' | 'accounts'

export function App() {
  const [account, setAccount] = useState<Account | null>(null)
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState<View>('setup')
  const [reviews, setReviews] = useState<ReviewRequest[]>([])
  const [connected, setConnected] = useState(false)

  useEffect(() => { api.me().then(setAccount).catch(() => null).finally(() => setLoading(false)) }, [])
  useEffect(() => {
    if (!account) return
    api.reviews().then(setReviews).catch(() => null)
    const source = new EventSource('/api/v1/review-requests/events', { withCredentials: true })
    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.addEventListener('review-requests', (event) => setReviews(JSON.parse((event as MessageEvent).data)))
    return () => source.close()
  }, [account])

  if (loading) return <div className="page-loader"><Network size={26} /> 正在连接控制平面</div>
  if (!account) return <Login onAuthenticated={setAccount} />

  const pending = reviews.filter((item) => item.status === 'pending').length
  const nav = [
    { key: 'setup' as const, label: '启动向导', icon: ListChecks },
    { key: 'reviews' as const, label: '人工审核', icon: Bell, badge: pending },
    { key: 'projects' as const, label: '创建项目', icon: Plus },
    { key: 'teams' as const, label: '团队配置', icon: UsersRound },
    { key: 'accounts' as const, label: '人员与权限', icon: Users },
  ]
  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Boxes size={21} /></div><div><strong>RepoMesh</strong><span>Control Plane</span></div></div>
      <nav>{nav.map(({ key, label, icon: Icon, badge }) => <button key={key} className={view === key ? 'active' : ''} onClick={() => setView(key)}><Icon size={18} /><span>{label}</span>{badge ? <b>{badge}</b> : null}</button>)}</nav>
      <div className="sidebar-foot"><div className="account-chip"><span>{account.display_name.slice(0, 1)}</span><div><strong>{account.display_name}</strong><small>{account.is_admin ? '本地管理员' : '审核人员'}</small></div></div><button className="icon-button" title="退出登录" onClick={() => api.logout().finally(() => setAccount(null))}><LogOut size={18} /></button></div>
    </aside>
    <main>
      <header className="topbar"><div><span className="crumb">组织 / GOAI</span><h1>{view === 'setup' ? '启动与接入' : view === 'reviews' ? '人工审核台' : view === 'projects' ? '创建协作项目' : view === 'teams' ? '团队配置' : '人员与权限'}</h1></div><div className={`live ${connected ? 'online' : ''}`}><i />{connected ? '实时推送已连接' : '正在重连'}</div></header>
      {view === 'setup' && <SetupWizard onReady={() => setView('projects')} />}
      {view === 'reviews' && <ReviewWorkbench reviews={reviews} onRefresh={() => api.reviews().then(setReviews)} />}
      {view === 'projects' && <ProjectSetup onCreated={() => setView('reviews')} />}
      {view === 'teams' && <TeamSetup />}
      {view === 'accounts' && <AccountPanel current={account} />}
    </main>
  </div>
}

function AccountPanel({ current }: { current: Account }) {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [form, setForm] = useState({ username: '', display_name: '', password: '', is_admin: false })
  const [message, setMessage] = useState('')
  const load = () => api.accounts().then(setAccounts).catch((error) => setMessage(error.message))
  useEffect(() => { load() }, [])
  if (!current.is_admin) return <section className="empty-state"><ShieldCheck size={28} /><h2>仅管理员可管理本地账户</h2></section>
  return <section className="content"><div className="section-head"><div><h2>审核人员</h2><p>人员只有被授予项目权限后，才会收到对应 Agent 的审核待办。</p></div></div><div className="split-panel"><div className="people-list">{accounts.map((item) => <div className="person" key={item.id}><span>{item.display_name.slice(0, 1)}</span><div><strong>{item.display_name}</strong><small>@{item.username}</small></div><em>{item.is_admin ? '管理员' : '审核人'}</em></div>)}</div><form className="side-form" onSubmit={(event) => { event.preventDefault(); api.createAccount(form).then(() => { setForm({ username: '', display_name: '', password: '', is_admin: false }); setMessage('账户已创建'); load() }).catch((error) => setMessage(error.message)) }}><h3>新增本地账户</h3><label>显示名称<input value={form.display_name} onChange={(e) => setForm({...form, display_name:e.target.value})} required /></label><label>用户名<input value={form.username} onChange={(e) => setForm({...form, username:e.target.value})} required /></label><label>初始密码<input type="password" minLength={12} value={form.password} onChange={(e) => setForm({...form, password:e.target.value})} required /></label><label className="check"><input type="checkbox" checked={form.is_admin} onChange={(e) => setForm({...form, is_admin:e.target.checked})} />组织管理员</label><button className="primary">创建账户</button>{message && <p className="form-message">{message}</p>}</form></div></section>
}
