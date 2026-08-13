import { Boxes, Check, Layers } from 'lucide-react'
import { PlanFlowTimeline } from './PlanFlowTimeline'
import type { IntegratedPlan, PlanDiff } from './api'

/** 本地演示数据：来自 2026-08-13 真实 LLM 链路实测（requirement-analysis → discovery → confirmation → integration） */
const DEMO = {
  requirement: '实现用户登录功能：支持邮箱密码登录和手机验证码登录，签发 JWT，并记录登录日志。',
  analysis: {
    sufficient: true,
    confidence: 0.9,
    missing: [] as string[],
    questions: ['登录失败是否需要锁定策略？', '验证码是否需要图形验证码作为第一道防线？', '登录日志是否需要上报审计中心？'],
    keywords: ['JWT', '登录', '验证码', '用户认证', '安全'],
  },
  candidates: [
    { name: 'ts-auth-service', score: 0.95, terms: ['auth', 'jwt', 'login'], reason: '认证服务，直接负责 JWT 签发与登录校验，是登录功能的唯一权威实现方。' },
    { name: 'ts-user-service', score: 0.9, terms: ['user', 'account'], reason: '用户信息服务，负责账号数据与用户维度登录历史，需要接入新的登录链路。' },
    { name: 'ts-verification-code-service', score: 0.9, terms: ['code', 'verification'], reason: '验证码服务，提供手机验证码的生成与校验，支撑手机号登录流程。' },
    { name: 'ts-notification-service', score: 0.75, terms: ['notification', 'sms'], reason: '通知服务，负责短信/邮件触达，为手机验证码提供下发通道。' },
  ],
  confirmations: [
    { repository: 'ts-notification-service', confidence: 0.72, risk: 'medium', summary: '新增短信验证码通知支持：暴露 phone-code 发送接口、增加 SMS sender 与 Freemarker 模板、AMQP 消费 PhoneCodeSendRequest，保持既有邮件通知不变。', depends: [] },
    { repository: 'ts-verification-code-service', confidence: 0.78, risk: 'medium', summary: '实现验证码生成与校验接口：按 phone+scene 存储、5 分钟 TTL、单次消费；生成后经 AMQP 异步通知通知服务下发短信，不引入构建期依赖。', depends: [] },
    { repository: 'ts-auth-service', confidence: 0.95, risk: 'medium', summary: '新增邮箱/手机号登录端点：手机号登录先调验证码服务校验、通过后才签发 JWT；邮箱登录接收用户服务已校验的断言；登录成功与失败均写入登录日志。', depends: ['ts-verification-code-service'] },
    { repository: 'ts-user-service', confidence: 0.9, risk: 'medium', summary: '新增邮箱/手机号登录入口：本地校验密码后向认证服务换取 JWT；手机号登录委托认证服务完成验证码校验；新增 LoginLog 实体与仓库记录登录历史。', depends: ['ts-auth-service'] },
  ],
  plan: {
    engineering_spec:
      '新增两种登录流程：邮箱密码登录与手机验证码登录。服务拆分上，ts-user-service 作为对外用户接口与用户数据归属方；ts-auth-service 负责 JWT 签发与权威登录日志；ts-verification-code-service 负责一次性验证码生命周期；ts-notification-service 负责短信下发。权威依赖图仅允许 ts-user-service → ts-auth-service → ts-verification-code-service，ts-user-service 不得直接依赖验证码服务。邮箱登录由用户服务本地校验密码后携带已签名断言换取 JWT；手机号登录由认证服务调验证码服务校验后签发 JWT。构建顺序：通知服务与验证码服务先行，认证服务次之，用户服务最后。',
    contracts: [
      { producer: 'ts-auth-service', consumer: 'ts-user-service', interface: 'POST /api/v1/auth/login, /api/v1/auth/login/email, /api/v1/auth/login/mobile', agreement: '邮箱登录由 ts-user-service 提交已验证断言换取 JWT；手机号登录由 ts-user-service 提交 phone+code，ts-auth-service 调验证码服务校验后签发 JWT；每次登录尝试记录一条 LoginLog。' },
      { producer: 'ts-verification-code-service', consumer: 'ts-auth-service', interface: 'POST /verification-code/verify', agreement: '请求 {phone, code, scene=LOGIN}；成功返回 {valid:true} 且验证码原子消费不可复用；无效或过期返回 {valid:false}；验证码单次有效、5 分钟过期；校验失败不得签发 JWT。' },
    ],
    task_dag: [
      { repository: 'ts-notification-service', instruction: '新增短信验证码通知支持：暴露 POST /api/v1/notifications/phone-code 与 /send，增加 SMS sender、登录验证码 Freemarker 模板与 AMQP 消费者，保持既有邮件通知不变，可独立部署。', depends_on: [], parallelizable_with: ['ts-verification-code-service'], tests: [] },
      { repository: 'ts-verification-code-service', instruction: '实现 POST /verification-code/generate 与 /verify：按 phone+scene 存储，5 分钟 TTL、单次消费；生成后通过 AMQP 发布 PhoneCodeSendRequest 通知短信下发，不引入对通知服务的构建期依赖。', depends_on: [], parallelizable_with: ['ts-notification-service'], tests: [] },
      { repository: 'ts-auth-service', instruction: '新增 POST /api/v1/auth/login/email、/mobile、/login：手机号登录先调验证码服务校验，通过后才经 JwtTokenProvider 签发 JWT；邮箱登录校验用户服务提供的已验证断言；成功与失败均写入 LoginLogRepository。', depends_on: ['ts-verification-code-service'], parallelizable_with: [], tests: [] },
      { repository: 'ts-user-service', instruction: '新增 POST /api/v1/userservice/login/email 与 /phone：邮箱登录本地校验密码哈希、构造已验证断言调认证服务换取 JWT；手机号登录携带 phone/code/subject 委托认证服务；新增 LoginLog 实体与仓库；不得直接依赖验证码服务。', depends_on: ['ts-auth-service'], parallelizable_with: [], tests: [] },
    ],
    execution_batches: [
      ['ts-notification-service', 'ts-verification-code-service'],
      ['ts-auth-service'],
      ['ts-user-service'],
    ],
    // PR-5 单源投影：图（nodes + edges + 内嵌投影）是唯一数据源，顶层三列
    // 只是它的投影；Timeline 组件只消费这份 graph。
    graph: {
      plan_version: 1,
      nodes: [
        { repository: 'ts-notification-service', instruction: '新增短信验证码通知支持：暴露 POST /api/v1/notifications/phone-code 与 /send，增加 SMS sender、登录验证码 Freemarker 模板与 AMQP 消费者，保持既有邮件通知不变，可独立部署。', tests: [] },
        { repository: 'ts-verification-code-service', instruction: '实现 POST /verification-code/generate 与 /verify：按 phone+scene 存储，5 分钟 TTL、单次消费；生成后通过 AMQP 发布 PhoneCodeSendRequest 通知短信下发，不引入对通知服务的构建期依赖。', tests: [] },
        { repository: 'ts-auth-service', instruction: '新增 POST /api/v1/auth/login/email、/mobile、/login：手机号登录先调验证码服务校验，通过后才经 JwtTokenProvider 签发 JWT；邮箱登录校验用户服务提供的已验证断言；成功与失败均写入 LoginLogRepository。', tests: [] },
        { repository: 'ts-user-service', instruction: '新增 POST /api/v1/userservice/login/email 与 /phone：邮箱登录本地校验密码哈希、构造已验证断言调认证服务换取 JWT；手机号登录携带 phone/code/subject 委托认证服务；新增 LoginLog 实体与仓库；不得直接依赖验证码服务。', tests: [] },
      ],
      edges: [
        { from: 'ts-verification-code-service', to: 'ts-auth-service', status: 'confirmed', source: 'llm', interface: 'POST /verification-code/verify', agreement: '请求 {phone, code, scene=LOGIN}；成功返回 {valid:true} 且验证码原子消费不可复用；无效或过期返回 {valid:false}；验证码单次有效、5 分钟过期；校验失败不得签发 JWT。' },
        { from: 'ts-auth-service', to: 'ts-user-service', status: 'confirmed', source: 'llm', interface: 'POST /api/v1/auth/login, /api/v1/auth/login/email, /api/v1/auth/login/mobile', agreement: '邮箱登录由 ts-user-service 提交已验证断言换取 JWT；手机号登录由 ts-user-service 提交 phone+code，ts-auth-service 调验证码服务校验后签发 JWT；每次登录尝试记录一条 LoginLog。' },
      ],
      execution_batches: [
        ['ts-notification-service', 'ts-verification-code-service'],
        ['ts-auth-service'],
        ['ts-user-service'],
      ],
      contracts: [
        { producer: 'ts-auth-service', consumer: 'ts-user-service', interface: 'POST /api/v1/auth/login, /api/v1/auth/login/email, /api/v1/auth/login/mobile', agreement: '邮箱登录由 ts-user-service 提交已验证断言换取 JWT；手机号登录由 ts-user-service 提交 phone+code，ts-auth-service 调验证码服务校验后签发 JWT；每次登录尝试记录一条 LoginLog。' },
        { producer: 'ts-verification-code-service', consumer: 'ts-auth-service', interface: 'POST /verification-code/verify', agreement: '请求 {phone, code, scene=LOGIN}；成功返回 {valid:true} 且验证码原子消费不可复用；无效或过期返回 {valid:false}；验证码单次有效、5 分钟过期；校验失败不得签发 JWT。' },
      ],
      task_dag: [
        { repository: 'ts-notification-service', instruction: '新增短信验证码通知支持：暴露 POST /api/v1/notifications/phone-code 与 /send，增加 SMS sender、登录验证码 Freemarker 模板与 AMQP 消费者，保持既有邮件通知不变，可独立部署。', depends_on: [], tests: [] },
        { repository: 'ts-verification-code-service', instruction: '实现 POST /verification-code/generate 与 /verify：按 phone+scene 存储，5 分钟 TTL、单次消费；生成后通过 AMQP 发布 PhoneCodeSendRequest 通知短信下发，不引入对通知服务的构建期依赖。', depends_on: [], tests: [] },
        { repository: 'ts-auth-service', instruction: '新增 POST /api/v1/auth/login/email、/mobile、/login：手机号登录先调验证码服务校验，通过后才经 JwtTokenProvider 签发 JWT；邮箱登录校验用户服务提供的已验证断言；成功与失败均写入 LoginLogRepository。', depends_on: ['ts-verification-code-service'], tests: [] },
        { repository: 'ts-user-service', instruction: '新增 POST /api/v1/userservice/login/email 与 /phone：邮箱登录本地校验密码哈希、构造已验证断言调认证服务换取 JWT；手机号登录携带 phone/code/subject 委托认证服务；新增 LoginLog 实体与仓库；不得直接依赖验证码服务。', depends_on: ['ts-auth-service'], tests: [] },
      ],
    },
  } satisfies IntegratedPlan,
  // PR-4 diff 演示：v1 → v2 的 replan 将验证码依赖纳入认证服务（preview 足迹）
  diff: {
    from_version: 1,
    to_version: 2,
    added_edges: [
      { from: 'ts-verification-code-service', to: 'ts-auth-service', status: 'confirmed', source: 'llm', interface: 'POST /verification-code/verify', agreement: '请求 {phone, code, scene=LOGIN}；成功返回 {valid:true} 且验证码原子消费不可复用；无效或过期返回 {valid:false}；验证码单次有效、5 分钟过期；校验失败不得签发 JWT。' },
    ],
    removed_edges: [],
    changed_edges: [],
    added_repos: ['ts-auth-service'],
    removed_repos: [],
    affected_repos: ['ts-auth-service'],
  } satisfies PlanDiff,
}

const STEPS = ['需求分析', '仓库发现', 'TM 确认', '集成方案']

export function PlanFlowDemo() {
  const { analysis, candidates, confirmations, plan, requirement } = DEMO
  return (
    <div className="flow-shell">
      <header className="flow-topbar">
        <div className="flow-brand">
          <div className="brand-mark"><Boxes size={21} /></div>
          <div><strong>RepoMesh</strong><span>方案流程展示</span></div>
        </div>
        <div className="flow-live"><i className="pulse" />流程进行中 · 正在生成集成方案</div>
      </header>

      <main className="flow-main">
        <div className="flow-project">
          <div className="flow-project-head">
            <h1>演示项目 · 用户登录</h1>
            <span className="chip">本地实测数据</span>
          </div>
          <div className="flow-stepper">
            {STEPS.map((label, index) => (
              <div key={label} className={`flow-step ${index < 3 ? 'done' : ''} ${index === 3 ? 'active' : ''}`}>
                <span className="flow-step-dot">{index < 3 ? <Check size={13} /> : index + 1}</span>
                <span className="flow-step-label">{label}</span>
                {index === 3 && <span className="flow-step-tag">进行中</span>}
              </div>
            ))}
          </div>
        </div>

        <section className="card">
          <div className="section-head"><div><h2>提交的需求</h2><p>方案制定入口 · 需求文本</p></div></div>
          <div className="prd-editor"><p className="req-text">{requirement}</p></div>
        </section>

        <section className="card">
          <div className="section-head"><div><h2>① 需求分析</h2><p>LLM 判断需求信息充分性</p></div><span className="step">01 / 04</span></div>
          <div className="analysis-panel">
            <div className="analysis-status">
              <span className="verdict ok">信息充分</span>
              <span className="analysis-confidence">置信度 {Math.round(analysis.confidence * 100)}%</span>
            </div>
            <div className="analysis-row"><div className="label">提取关键词</div><div className="chip-row">{analysis.keywords.map((item) => <span className="chip" key={item}>{item}</span>)}</div></div>
            <div className="analysis-row"><div className="label">待澄清问题</div><ul className="question-list">{analysis.questions.map((item) => <li key={item}>{item}</li>)}</ul></div>
          </div>
        </section>

        <section className="card">
          <div className="section-head"><div><h2>② 仓库发现</h2><p>LLM 从仓库目录选出候选并打分</p></div><span className="step">02 / 04</span></div>
          <div className="candidate-list">
            {candidates.map((c) => (
              <div className="candidate checked" key={c.name}>
                <span className="fake-check"><Check size={13} /></span>
                <div className="candidate-body">
                  <div className="candidate-head"><strong>{c.name}</strong><span className="flow-score">{Math.round(c.score * 100)} 分</span></div>
                  <div className="chip-row">{c.terms.map((t) => <span className="chip" key={t}>{t}</span>)}</div>
                  <p>{c.reason}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="card">
          <div className="section-head"><div><h2>③ TM 确认</h2><p>各仓库负责人给出改动计划</p></div><span className="step">03 / 04</span></div>
          <div className="confirm-group">
            {confirmations.map((r) => (
              <div className="confirm-card" key={r.repository}>
                <div className="confirm-card-head">
                  <span className="confirm-tone required" />
                  <strong>{r.repository}</strong>
                  <span className="confirm-confidence">REQUIRED · {Math.round(r.confidence * 100)}%</span>
                  <span className={`risk-tag ${r.risk}`}>中风险</span>
                </div>
                <p className="confirm-summary">{r.summary}</p>
                {r.depends.length > 0 && <div className="plan-detail-row"><div className="label">依赖</div><div className="chip-row">{r.depends.map((d) => <span className="chip" key={d}>{d}</span>)}</div></div>}
              </div>
            ))}
          </div>
        </section>

        <section className="card">
          <div className="section-head"><div><h2>④ 集成方案</h2><p>方案说明 · 契约 · 任务 · 流程图 · 执行批次</p></div><span className="step">04 / 04</span></div>
          <div className="plan-block">
            <div className="plan-section">
              <div className="plan-section-head"><Layers size={16} /><strong>方案说明</strong></div>
              <pre className="spec-pre">{plan.engineering_spec}</pre>
            </div>
            <div className="plan-section">
              <div className="plan-section-head"><Layers size={16} /><strong>契约列表</strong><span className="muted">（{plan.contracts.length}）</span></div>
              <table className="contract-table">
                <thead><tr><th>生产者</th><th>消费者</th><th>接口</th><th>约定</th></tr></thead>
                <tbody>
                  {plan.contracts.map((c) => (
                    <tr key={c.producer + c.consumer}>
                      <td><span className="chip">{c.producer}</span></td>
                      <td><span className="chip">{c.consumer}</span></td>
                      <td className="mono">{c.interface}</td>
                      <td>{c.agreement}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="plan-section">
              <div className="plan-section-head"><Layers size={16} /><strong>执行流程</strong><span className="muted">（Stage 泳道 · 依赖关系以标签标注）</span></div>
              <PlanFlowTimeline plan={plan} diff={DEMO.diff} activeBatchIndex={1} />
            </div>
            <div className="plan-section">
              <div className="plan-section-head"><Layers size={16} /><strong>执行批次</strong><span className="muted">（{plan.execution_batches.length}）</span></div>
              <div className="batch-list">
                {plan.execution_batches.map((batch, index) => (
                  <div className="batch-row" key={index}>
                    <span>批次 {index + 1}</span>
                    <div className="chip-row">{batch.map((repo) => <span className="chip" key={repo}>{repo}</span>)}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>
      </main>
    </div>
  )
}
