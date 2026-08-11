import { useState } from 'react'
import { ArrowRight, Boxes, CheckCircle2, KeyRound, RadioTower } from 'lucide-react'
import { api, type Account } from './api'

export function Login({ onAuthenticated }: { onAuthenticated: (account: Account) => void }) {
  const [setup, setSetup] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', displayName: '' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      if (setup) {
        await api.bootstrap(form.username, form.password, form.displayName)
      }
      const result = await api.login(form.username, form.password)
      onAuthenticated(result.account)
    } catch (reason) { setError((reason as Error).message) } finally { setBusy(false) }
  }
  return <div className="login-layout">
    <section className="login-context"><div className="brand light"><div className="brand-mark"><Boxes size={22} /></div><div><strong>RepoMesh</strong><span>Control Plane</span></div></div><div className="login-copy"><span className="eyebrow"><RadioTower size={15} /> HUMAN IN THE LOOP</span><h1>让 Agent 自动执行，<br />让关键决定回到人手中。</h1><p>所有需要人工确认的范围、Spec、执行、验证与交付节点，都会自动进入审核台。</p><ul><li><CheckCircle2 />按身份控制代码可见、可写范围</li><li><CheckCircle2 />按项目选择自动或人工控制模式</li><li><CheckCircle2 />审批与证据版本完整留痕</li></ul></div><small>Local deployment · Credentials stay on this machine</small></section>
    <section className="login-form-wrap"><form className="login-form" onSubmit={submit}><div className="form-icon"><KeyRound size={22} /></div><h2>{setup ? '初始化本地管理员' : '登录控制平面'}</h2><p>{setup ? '仅首次部署需要执行一次。' : '使用本地账户进入人工控制台。'}</p>{setup && <label>显示名称<input autoFocus value={form.displayName} onChange={(e) => setForm({...form, displayName:e.target.value})} placeholder="例如：吕祎晗" required /></label>}<label>用户名<input autoFocus={!setup} value={form.username} onChange={(e) => setForm({...form, username:e.target.value})} placeholder="username" required /></label><label>密码<input type="password" minLength={12} value={form.password} onChange={(e) => setForm({...form, password:e.target.value})} placeholder="至少 12 位" required /></label>{error && <div className="error">{error}</div>}<button className="primary large" disabled={busy}>{busy ? '正在连接…' : setup ? '初始化并登录' : '登录'}<ArrowRight size={17} /></button><button type="button" className="text-button" onClick={() => { setSetup(!setup); setError('') }}>{setup ? '已有账户，返回登录' : '首次部署？初始化管理员'}</button></form></section>
  </div>
}
