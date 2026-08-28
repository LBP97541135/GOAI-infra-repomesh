# GOAI 初赛 PPT —— 竞品官方宣传文段对标（板块 1 / 5 / 7）

> 对标对象：Sourcegraph（Agentic Batch Changes）、Anthropic Claude（Claude Code / Agent SDK / Managed Agents）、Augment（企业 AI 编码助手）
> 三家产品均与我们同赛道（把编码 Agent 组织成受控、可观测、可审计的交付/变更系统），下面按我们负责的三个板块摘录其**官方宣传原文**（英文原文 + 中文要点），供撰写 PPT 文案时参考。
> 文段均为官方/官方通稿原话，出处附链接。

---

## 板块① 场景与价值

### Sourcegraph —— "代码洪流"叙事 + 大模型看不到全局的痛点

**首页主标语（定位一句话）**：
> "Take control of your codebase. Give humans and agents complete context to understand, oversee, and evolve the world's largest, most complex codebases."
> （掌控你的代码库。给人类和 Agent 完整上下文，去理解、监督、演进世界上最大最复杂的代码库。）
> —— Trusted by **200+ enterprise engineering teams**

**痛点文案（首页 The problem）**：
> "A tidal wave of code is coming. Code is growing faster than teams can understand or control it."
> "Agents see only fragments of the enterprise codebase, rebuilding context for each task. As agent adoption grows, that blind spot becomes inconsistency, missed changes, and risk at scale. **This is where engineering teams lose control.**"
> （代码洪流正在到来，代码增长速度超过团队能理解和控制的速度。Agent 只能看到企业代码库的碎片，每个任务都重建上下文——随着 Agent 普及，这个盲区变成不一致、漏改和大规模风险。**工程团队就是在这里失去控制。**）

**价值量化（首页 CodeScaleBench 报告数据）**：cost/task ▼ 30%（单任务成本降 30%）、exec speed ▲ 38%（执行速度升 38%）、retrieval ▲ 2–3×（检索能力 2-3 倍）

**首页对比 demo（核心卖点）**：普通 Coding Agent 只改了 `user.go`，漏掉 **Auth 中间件（无角色校验，任何人都能访问管理路由）/ API DTO / 审计日志 / 前端管理路由守卫 / 邀请流程 / 4 个集成测试**；而 Agent + Sourcegraph MCP 提出 8 步计划、跨 7 层改 12 个文件 —— "Nothing missed."（什么都没漏）

**Agentic Batch Changes 发布通稿（2026-07-03，public beta）**：
> "The problem with large-scale code change: The hard part is not deciding what change to make. **It's executing that change across hundreds of repositories without losing visibility, introducing inconsistency, or pulling engineers into weeks of repetitive manual work.**"
> （难的不是决定改什么，而是跨几百个仓库执行变更时不失去可见性、不引入不一致、不把工程师拖进数周的重复手工劳动。）

> 旧的四个选项："Make the changes manually. Write brittle one-off scripts. Ask a coding agent to clone hundreds of repositories locally and trust it to make it through the rollout. Or avoid the rollout entirely and let the backlog grow."
> （手动改 / 写脆弱的临时脚本 / 让编码 Agent 克隆几百个仓库并信任它跑完 / 干脆不推，让 backlog 增长。）

> Dan Adler（CEO）语录：
> "Agentic Batch Changes brings the capabilities of the best coding agents to the largest companies in the world. For years, the owners of the largest codebases have watched smaller competitors move faster because large scale changes were too slow and too risky. **...enabling engineering teams to roll out changes across thousands of repositories and directories with the speed and confidence of changing just one.**"
> （把最好的编码 Agent 能力带给全世界最大的公司。多年来大代码库的拥有者看着小公司跑得更快，因为大规模变更加太慢太险。现在团队可以以"改一个仓库"的速度和信心，在几千个仓库中推广变更。）

**客户场景（Mercari）**：一个 prompt 识别并修复 GitHub Actions 环境变量注入漏洞，"found around **80 potential repos** affected"。

**目标场景清单（官方列举）**：依赖升级（breaking changes）/ CVE 修复 / 需判断的代码模式更新 / 新 API 或库的按服务推广 / CI 流水线现代化。

### Anthropic Claude —— "基础设施是把原型变成生产级 Agent 的分水岭"

**博客定位（2026-06-10 "The evolution of agentic surfaces"）**：
> "infrastructure is what separates a prototype from a production agent. All too often, teams burn development cycles on security, state management, permissioning, and harness tuning."
> （是基础设施把"原型"和"生产级 Agent"分开。团队常常把开发周期烧在安全、状态管理、权限和 harness 调优上。）

> "Getting an agent into production takes more than a good prompt. The agent needs somewhere to run the code it writes, credentials to reach your data, **observable sessions**, and infrastructure that scales with usage."
> （把 Agent 送上生产不止需要一个好提示词。它需要运行代码的地方、访问数据的凭据、**可观测的会话**、随用量伸缩的基础设施。）

**可量化价值（客户案例）**：
- Notion："an early prototype turning roughly **twelve hours of work into twenty minutes**"（约 12 小时的工作 → 20 分钟）
- Alberta 省政府（官方案例）："scanned **466 million lines of code in 20 hours**, remediated security gaps across its systems"（20 小时扫 4.66 亿行代码并修复安全缺口）

**生产级六大问题（官方列举，即"我们替企业解决什么"）**：Hosting and scaling（托管与伸缩）/ Session management（会话管理，可中断恢复、可回溯）/ Filesystem management（文件系统）/ **Execution isolation（执行隔离，出错时的爆炸半径）** / **Credentials（凭据，怎么在不把专有信息暴露给生成代码的前提下授权）** / **Observability（"当 Agent 自主工作一小时做了件出人意料的事，你能重建它走的每一步吗？"）**

### Augment —— "你只能改进你衡量的东西" + 大型代码库

**定位**："Developer first, enterprise ready" / "Built to scale with you. From startups to the Fortune 500, we architected Augment to handle even the largest and most complex codebases."（从初创到财富 500 强，为最大最复杂的代码库而架构）

**价值文案（security 页）**：
> "You can only improve what you measure. Built-in dashboards give a detailed look at usage, trends, and **completion acceptance rate**."
> （你只能改进你衡量的东西。内置仪表盘给出使用、趋势和**补全接受率**的细粒度视图。）

**差异化卖点（第三方评测口径）**：首个 500K 文件上下文的 AI 编码助手；上下文窗口 200,000 token（官方 guide 口径）。

---

## 板块⑤ 工程落地、运行验证与安全可审计

### Sourcegraph —— "Built for Big Code"

**首页企业级卖点（Built for Big Code）**：
> "SOC2 Type II + ISO27001 Compliance. Your code and data stay secure."
> "**Zero data retention.** Your LLM inference is never stored beyond what's required and never shared with third parties."
> （零数据保留：LLM 推理数据不做超必要存储、绝不与第三方共享。）
> "Built to scale. Handles the world's largest monorepos and multi-repo architectures."
> "Enterprise authentication. SSO (SAML, OpenID Connect, OAuth), **SCIM provisioning and lifecycle management**, and **RBAC** for secure, centralized authentication."

**三支柱（产品架构宣传）**：Understanding（Deep Search / MCP Server / Code Search）→ Oversight（Code Insights / Code Monitoring / Living Documentation）→ Evolution（Agentic Batch Changes）—— 理解 → 监督 → 演进。

**人工审批（发布通稿）**：
> "Engineers review and approve **every changeset** before merge."
> "It handles repository specific variation, **reacts to CI signals**, and **iterates on failures before publishing at scale**."
> （工程师在合并前审查并批准每一个变更集；处理仓库间差异、响应 CI 信号、在规模化发布前迭代失败。）

### Anthropic Claude —— 脑手分离、凭据出沙箱、OTel 可观测

**架构叙事（Managed Agents 博客）**：
> "Managed Agents solves these problems by **decoupling the brain from the hands**. The harness that calls Claude runs separately from the sandbox where code executes, and the session–**an append-only log of every model call, tool call, and result**–connects the two. ...a whole run can be reconstructed from its session at any point."
> （把"大脑"与"手"解耦：调用 Claude 的 harness 与执行代码的沙箱分离，会话是每次模型调用、工具调用和结果的**追加式日志**，连接二者——整个运行过程可在任意时刻从会话重建。）

**凭据安全（官方卖点 #1）**：
> "Credentials are kept out of the sandbox. Tokens for tools like MCPs, CLIs, and GitHub repos live in a separate vault, and a proxy fetches them and decrypts them only on demand. ...Vault credentials are protected with **envelope encryption** before storage, and retrieval requires a **signed request token** for verification."
> （凭据不进沙箱。MCP/CLI/GitHub 的 token 放在独立 vault 里，代理按需取用解密；存储前信封加密，取用需签名请求令牌验证。）

**可观测性（官方卖点 #3）**：
> "observability is exportable through **OpenTelemetry** into whatever monitoring stack you already run."
> "the Claude Developer Console offers a **native visual timeline view** of your agent sessions, and a debugging experience that allows you to examine any transcript in-depth."
> （观测数据通过 OpenTelemetry 导出到你现有的监控栈；开发者控制台提供会话可视化时间线，可深度检查任何 transcript。）

**安全与审计（与 Google Cloud 联合技术会，2026）**：
> "how do you enforce what an agent is allowed to do, and how do you **trace and audit what it actually did**."
> "How Anthropic's Agent SDK emits **OpenTelemetry traces and audit events** into Cloud Observability, and how its **hooks and permission layers** let you enforce policy on every tool call."
> （如何强制 Agent 能做什么、如何追踪审计它实际做了什么；Agent SDK 向云观测发送 OTel traces 和审计事件，hooks 与权限层在每次工具调用上执行策略。）

**性能量化（同博客）**：脑手分离使 time-to-first-token 中位数（p50）降约 **60%**、最慢情形（p95）降 **90%+**。

**其他工程能力（官方列举）**：outcomes（Agent 按 rubric 自评结果）、multiagent orchestration、permission policies、webhooks、self-hosted sandboxes（代码执行留在自己 VPC）、MCP tunnels（私有网络 MCP 直连）。

### Augment —— 认证军备竞赛 + 不可提取架构

**首页主标语**："Your keys, your code, your control"（你的密钥、你的代码、你的控制）

**数据保护三件套（security 页）**：
> "We never train on our customer's proprietary data. ...backed by an **indemnification clause** in our terms."
> "Our first-of-its-kind **Proof-of-Possession API** ensures code completions operate only on locally possessed code, eliminating complex authorization management and preventing unauthorized access."
> "**Non-extractable architecture** ... prevents data exfiltration, eliminates cross-tenant leakage, and enforces strict access controls."
> （从不用客户专有数据训练，条款带赔偿条款；首创 Proof-of-Possession API，补全只操作本地持有的代码；不可提取架构防数据外泄、防跨租户泄漏、强制访问控制。）

**认证清单（Certifications & Compliance）**：SOC 2 Type II（Attested）/ **ISO/IEC 42001（Certified——首个获得该 AI 管理系统认证的 AI 编码助手）** / GDPR / CCPA / HIPAA / BAA。

**SOC 2 Type II 博客（2026-04-06）**：
> "SOC 2 Type I ...validates that your security controls are designed correctly. Type II goes further, confirming that those controls truly worked, consistently, over a sustained period. **Ours covered six months.** That's six months of auditors examining whether our access controls, incident response, vendor reviews, change management, and monitoring processes operated the way we said they did – **not just on paper, but in practice.**"
> （Type I 验证控制设计正确；Type II 更进一步，确认控制真实、持续地起作用——我们覆盖了六个月，审计员核查访问控制、事件响应、供应商评审、变更管理、监控流程是否真的按我们说的在运转，不只是纸面上。）

> "You're not just buying software – **you're extending operational trust to us**."
> （你买的不是软件，是把运营信任托付给我们。）

**第三方评测补充能力**：Private Cloud Deployment（私有云部署，代码留在自有基础设施）/ End-to-End Encryption / Fine-Grained Access Controls（RBAC）/ **CMEK（客户管理加密密钥）** / Zero Data Retention；ISO 42001 面向受监管行业（金融、医疗、国防）。

---

## 板块⑦ 落地计划与进展

### Sourcegraph
- **2026-07-03**：Agentic Batch Changes **public beta** 发布（通稿口径："available now in Beta: the frontier agent for code change at scale"）。
- Changelog 持续迭代证据：beta 中"utilizing inner loop coding agents like Claude Code and Codex now stream thinking and [updates]"；Deep Search 新增 agent 文件访问审计日志、source file reader 等。
- 宣传口径强调"演进"三支柱之一：从 Code Search（十年企业信赖）→ MCP → Agentic Batch Changes，逐层扩展。

### Anthropic Claude
- 演进时间线（博客自述）：**2025** 发布 Claude Code → 其后推出 **Claude Agent SDK**（2026 初由 Claude Code SDK 更名）→ **2026-06-10** 推出 **Claude Managed Agents**（生产化托管）。
- 战略理由（官方）：harness 必须随模型共同演进——
> "The harness doesn't evolve alongside model intelligence, the agent breaks down."
> "For most organizations, maintaining a harness is overhead that doesn't differentiate their product."
> （对大多数组织，维护 harness 是纯开销、不构成产品差异化——所以由平台托管演进。）
- 佐证案例："context anxiety"（Sonnet 4.5 需要 context resets，Opus 4.5 不需要）——harness 调优是持续负担。

### Augment
- SOC 2 Type II 博客的"下一步"（roadmap 口径）：
> "SOC 2 is a **floor, not a ceiling**. We're continuing to build up our program – ongoing monitoring, tighter controls as we scale, and an **expanding compliance posture** as customer requirements evolve."
> （SOC 2 是地板不是天花板；持续建设：持续监控、随规模收紧控制、随客户需求扩展合规态势。）
- 里程碑口径：SOC 2 Type I → **SOC 2 Type II（2026-04-06）** → **ISO/IEC 42001（首个 AI 编码平台获得）**；产品线扩展（Cosmos 入口 / 面向供应链的 Augie AI teammate）。

---

## 对我们第 1/5/7 章文案的借鉴点（速查）

| 我们的板块 | 可直接借鉴的表述 | 出自 |
| --- | --- | --- |
| 第1章 痛点 | "工程团队在这里失去控制"的 Agent 盲区叙事；"难的不是决定改什么，而是跨仓库执行不失去可见性" | Sourcegraph |
| 第1章 价值量化 | 单任务成本降 30%、执行速度升 38% 这类**数字对照**；"12 小时→20 分钟"客户证言 | Sourcegraph / Anthropic |
| 第1章 差异化 | 用"普通 Agent 漏 6 处 vs 我们跨 7 层改 12 文件"式**同一任务正反对照** | Sourcegraph |
| 第5章 可运行性 | "基础设施是把原型和生产级分开的分水岭"；生产级六问（托管/会话/文件/隔离/凭据/可观测） | Anthropic |
| 第5章 安全可审计 | "如何强制 Agent 能做什么 + 如何追踪审计它实际做了什么"；"你买的不是软件，是运营信任"；认证是"地板不是天花板" | Anthropic / Augment |
| 第5章 可观测 | "整个运行可从 session 重建"；OTel 导出到自有监控栈；可视化时间线 | Anthropic |
| 第7章 计划 | "harness 与模型共同演进"式**平台演进理由**；"认证是地板不是天花板"的持续合规叙事 | Anthropic / Augment |

**我们的独有优势（三家都没有，可作为差异化强调）**：完整 **Delivery Contract + 独立验证闭环**（不依赖任何一家 Coding Agent，Claude Code / Codex 只是可替换执行器）、**跨仓库 ChangeSet 级回滚恢复**（Sourcegraph 只做到"审查后 merge"，Anthropic 只做到"session 重建"）、**赛题要求的全链路观测**（Higress 网关 1 点覆盖 8 容器 + OTel GenAI 语义，优于单家 SDK 的观测边界）。

---

### 来源链接
- Sourcegraph 首页：https://sourcegraph.com/
- Sourcegraph Agentic Batch Changes 通稿：https://cioinfluence.com/machine-learning/sourcegraph-launches-agentic-batch-changes-in-public-beta-bringing-ai-powered-large-scale-code-change-to-enterprise-engineering-teams/
- Claude Managed Agents 博客：https://claude.com/blog/building-with-claude-managed-agents
- Claude on Google Cloud（监控与安全 Agent）：https://www.anthropic.com/webinars/claude-on-google-cloud-monitoring-and-securing-agents-at-scale
- Augment Security & Privacy：https://www.augmentcode.com/security
- Augment SOC 2 Type II 博客：https://www.goaugment.com/blog/augment-achieves-soc-2-type-ii-certification
