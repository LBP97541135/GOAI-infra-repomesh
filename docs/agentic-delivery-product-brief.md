# Agentic Delivery Control Plane：产品方向与验证方案

> 更新时间：2026-07-26  
> 用途：GOAI Agent Infra「新智基座」赛道选题、产品设计与原型验证

## 1. 一句话定义

我们要实现的不是另一个 Coding Agent，而是一个面向企业软件交付的多 Agent 控制平面：

> 将产品需求自动转化为跨仓库、经过独立验证、可观测、可审计、可部署且可回滚的 Release Candidate。

Codex、Claude Code、Hermes 等 Coding Agent 是可替换的代码执行器；系统负责需求契约、任务编排、权限治理、测试、安全审查、发布和回滚。

## 2. 已确认的赛道要求

GOAI Agent Infra 赛道关注企业复杂任务下的多 Agent 基础设施与协同系统，而非单个 Agent 的能力展示。

关键要求：

- 至少设计 3 个不同职能的 Agent，形成端到端任务闭环。
- 必须以 AgentTeams（原 HiClaw）作为多 Agent 协同设计基点。
- Skill 是必选项，需要说明输入输出、调用条件、依赖工具、失败处理、安全边界和复用价值。
- 需要提交 Agent Identity 清单，说明各 Agent 的身份、能力边界和协作关系。
- 系统需体现任务拆解、上下文传递、工具调用、结果验证、执行证据、人工审批、回滚和经验沉淀。
- Agent 记忆、知识库 RAG、共享状态、轨迹可观测四项能力中至少实现两项。
- 初赛以方案为主；复赛需要可执行的 AgentTeams 代码和可运行 Demo。

评审权重：

| 维度 | 权重 |
| --- | ---: |
| 场景价值与行业可复制性 | 25% |
| 多 Agent 协同与自主闭环 | 25% |
| Skill 工程体系与生态复用 | 25% |
| 工程落地、安全与可审计 | 20% |
| 开源贡献 | 5% |

来源：[GOAI Infra 赛道详情](https://www.goaihz.com/tracks?track=infra)

## 3. AgentTeams 的定位

AgentTeams 既不是 LangChain，也不是 LangSmith。

| 工具 | 主要作用 |
| --- | --- |
| LangChain | 构建 Agent、工具调用和 RAG 等应用逻辑 |
| LangGraph | 编排 Agent 内部或多个 Agent 的状态工作流 |
| LangSmith | LLM Trace、调试、评测和监控 |
| AgentTeams | 部署、组织和治理多个独立 Agent，并支持人类介入 |

AgentTeams 采用 Manager-Workers 架构，本身不实现具体 Agent 推理逻辑，而是组织多个 Agent Runtime。其核心能力包括：

- Manager 统一拆解、分派和跟踪任务；
- Worker 独立运行，可使用 OpenClaw、QwenPaw、Hermes 等 Runtime；
- 基于 Matrix/Element 的透明协作与人工介入；
- MinIO 共享文件；
- Higress 统一管理模型、MCP 和凭据；
- Worker 生命周期、团队和任务 DAG 管理；
- Manager 集中管理并按 Worker 动态分配 Skill。

来源：[AgentTeams 官方仓库](https://github.com/agentscope-ai/AgentTeams)

### Skill 动态管理的边界

- Manager 可以给指定 Worker 添加或移除 Skill，并通知其同步。
- 一个 Agent 形成的能力可以经审核后注册为中心 Skill，再分发给其他 Agent。
- Worker 不能自行修改自身 Skill，从而避免自主扩权。
- 未分配 Skill 可阻止 Worker 加载该能力，但严格的“不可用”还需要同时限制 MCP、网关、凭据和数据权限。
- 人工可以介入、纠正或停止 Worker；外部工具调用能否立即取消或回滚，仍需由 Skill 实现超时、幂等和补偿逻辑。

## 4. 产品定位与边界

### 4.1 产品定位

建议对外定位为：

> 面向企业增量需求的 Agentic Delivery Control Plane。

它位于 Coding Agent 上方，将多个编码、测试、安全和发布 Agent 组织成一支受控的软件交付团队。

### 4.2 不做什么

- 不重新开发 IDE 内代码补全。
- 不和 Cursor 等产品竞争单 Agent 代码编辑体验。
- 不承诺任意需求都能无人监管直接发布生产。
- 不允许开发 Agent 自行降低测试或安全门禁。
- 不让 Coding Agent直接持有生产凭据。

### 4.3 正确的产品承诺

产品经理负责发起需求和确认业务验收标准；系统自动生成经过验证的 Release Candidate；研发、安全或系统责任人处理关键澄清、例外和高风险审批。

## 5. 企业需求证据

现有证据表明，企业不缺代码生成工具，缺的是将生成速度转化为稳定交付的控制体系。

| 证据 | 产品含义 |
| --- | --- |
| DORA 2025：90% 受访者已在工作中使用 AI，超过 80% 认为生产力提高 | AI 编码已经普及，单纯代码生成缺少差异化 |
| DORA 2025：AI 改善交付吞吐量，但仍增加交付不稳定性 | 核心价值应放在验证、门禁、灰度和回滚 |
| Stack Overflow 2025：46% 不信任 AI 输出准确性，信任者为 33% | 必须提供独立验证和可审计证据 |
| Stack Overflow 2025：专业开发者中约 41.4% 认为 AI 处理复杂任务表现差或很差 | 长周期需求需要拆分成小批量、结构化任务 |
| GitHub Cloud Agent 一次任务限于一个仓库、一个分支和一个 PR，最长 59 分钟 | 跨仓库、长周期、多角色交付编排仍有明确空间 |
| NIST SSDF 要求保护软件组件、保留来源、独立或自动审查、安全测试和漏洞响应 | “可交付代码”必须附带测试、安全、来源和发布证据 |

主要来源：

- [DORA 2025 State of AI-assisted Software Development](https://services.google.com/fh/files/misc/2025_state_of_ai_assisted_software_development.pdf)
- [Stack Overflow 2025 AI Survey](https://survey.stackoverflow.co/2025/ai)
- [GitHub Copilot Cloud Agent](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/about-cloud-agent)
- [NIST Secure Software Development Framework](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf)
- [METR 2026 AI Developer Productivity Update](https://metr.org/blog/2026-02-24-uplift-update/)

由此得到的真实企业需求包括：

1. 将模糊 PRD 转化为范围、验收标准、风险和发布规则明确的交付契约。
2. 理解存量仓库、历史决策、内部组件、API、数据库和 CI/CD 上下文。
3. 协调前端、后端、数据库、基础设施和文档等多个仓库。
4. 由独立 Agent 完成测试和安全审查，防止开发 Agent 自测自证。
5. 支持最小权限、预发部署、人工审批、灰度发布和回滚。
6. 用交付时间、首次验收通过率、人工介入、缺陷逃逸、回滚率和成本衡量价值。

## 6. 目标工作流

```text
产品经理提交 PRD / Issue
          ↓
Product Analyst：澄清歧义，生成 Delivery Contract
          ↓
Repository Analyst：分析仓库、依赖、契约和影响面
          ↓
Delivery Planner：生成跨仓库任务 DAG
          ↓
Developer Workers：调用 Hermes / Codex / Claude Code 等执行器
          ↓
QA Guardian：独立生成并执行测试
          ↓ 失败
Repair Loop：诊断、有限修正、重新验证
          ↓
Security Reviewer：权限、依赖、迁移和安全审查
          ↓
Release Guardian：构建、部署预发、冒烟和指标验证
          ↓
人工审批 → 灰度发布 → 验证 → 完成或回滚
```

## 7. 核心能力

### 7.1 Delivery Contract

把产品需求转化为机器可检查的交付契约：

```yaml
requirement:
  goal: ""
  users: []
  acceptance_criteria: []
  non_goals: []

change_scope:
  repositories: []
  allowed_paths: []
  forbidden_paths: []

quality_gates:
  unit_tests: required
  integration_tests: required
  security_scan: required
  staging_smoke_test: required

release:
  human_approval: required
  rollback_condition: ""
```

需求存在关键歧义时必须停止并请求澄清，不能由 Coding Agent自行猜测。

### 7.2 多仓库交付

- 分析一个需求涉及的所有仓库；
- 生成跨仓库任务和依赖 DAG；
- 创建相关联的多个 PR；
- 管理 API、Schema、数据库和版本兼容性；
- 按依赖顺序构建和发布；
- 任何关键仓库失败时，不得把整体交付标记为成功。

### 7.3 Coding Agent Provider

Coding Agent 应作为可替换执行器，通过统一接口接入：

```text
start(task)
followUp(taskId, feedback)
cancel(taskId)
collectPatch(taskId)
collectEvidence(taskId)
```

首版优先使用 AgentTeams 原生 Hermes，再接入一个非交互式 Coding CLI 和一个 Git/PR 通用适配器。

### 7.4 独立验证与修正

- 开发 Agent 不能自行宣布成功；
- 测试 Agent 使用独立上下文和隐藏验收测试；
- 禁止通过删除测试、降低断言或绕过门禁使 CI 通过；
- 修正循环必须记录失败原因、修改范围和验证结果；
- 设置最大重试次数；重复失败、架构冲突、数据迁移和高风险问题升级给人。

### 7.5 可观测与可审计

系统需要记录：

- 需求如何被拆解；
- 各 Agent 的身份、任务和状态；
- 工具、Skill、MCP 和模型调用；
- 文件、提交和 PR 变更；
- 测试、扫描和部署结果；
- 重试、失败、人工介入和审批；
- Token、时间和计算成本。

### 7.6 回滚与恢复

回滚不只等于 `git revert`，还需要覆盖：

- Agent 任务取消与重试；
- 文件、分支和提交回滚；
- 多仓库版本整体回退；
- 数据库迁移补偿；
- 部署版本回滚；
- 失败后恢复到上一个跨仓库一致状态。

## 8. 最终交付物

### 8.1 用户产品

一个面向产品经理和研发负责人的交付控制台：

```text
提交 PRD
→ 回答澄清问题
→ 确认验收标准
→ 查看跨仓库计划
→ 查看 Agent 执行进度
→ 处理异常或审批
→ 获取预发环境
→ 业务验收
→ 批准发布或回滚
```

### 8.2 工程产出

- AgentTeams 团队配置和 Agent Identity；
- PRD 到 Delivery Contract 的解析器；
- 多仓库影响分析和任务 DAG；
- Coding Agent Provider SDK；
- 可复用的开发、测试、安全、发布和回滚 Skills；
- GitHub/GitLab、CI、测试和部署适配器；
- 质量门禁和有限修正循环；
- Trace、审计与证据存储；
- 示例业务仓库和完整 Demo。

### 8.3 Release Evidence Pack

每次交付最终生成：

- 需求与验收条件；
- Agent Identity 与任务 DAG；
- 所有提交和 PR；
- 测试、覆盖率和安全扫描报告；
- 依赖清单或 SBOM；
- 数据库迁移和回滚脚本；
- Agent 执行 Trace；
- 人工审批记录；
- 预发地址和冒烟结果；
- 已知风险与限制。

## 9. 开源验证方案：Saleor

首选 Saleor 作为真实多仓库验证对象：

| 仓库 | 作用 | 技术 |
| --- | --- | --- |
| [saleor/saleor](https://github.com/saleor/saleor) | 核心后端、数据库、GraphQL API | Python |
| [saleor/saleor-dashboard](https://github.com/saleor/saleor-dashboard) | 企业管理后台 | TypeScript/React |
| [saleor/apps](https://github.com/saleor/apps) | 独立业务应用与集成 | TypeScript |
| [saleor/saleor-docs](https://github.com/saleor/saleor-docs) | API 和开发文档 | JavaScript |
| [saleor/saleor-platform](https://github.com/saleor/saleor-platform) | 本地多组件运行环境 | Docker Compose/Shell |

### 9.1 历史任务回放案例

真实需求：外部 App 修改结账商品价格时，允许记录修改原因；将原因保存到订单，通过 GraphQL 暴露，并在管理后台展示，用于调试和审计。

该功能真实涉及四个仓库：

- [后端 PR #19466](https://github.com/saleor/saleor/pull/19466)：数据模型、迁移、GraphQL、权限校验和 17 个测试；
- [Dashboard PR #6732](https://github.com/saleor/saleor-dashboard/pull/6732)：GraphQL 类型、查询和两个管理界面；
- [Apps PR #2393](https://github.com/saleor/apps/pull/2393)：示例支付 App 传递修改原因；
- [Docs PR #1809](https://github.com/saleor/saleor-docs/pull/1809)：文档同步。

### 9.2 验证方法

1. 将各仓库固定在相关 PR 合并前的基线 Commit。
2. 隔离远程 GitHub 上下文，只向 Agent 提供本地仓库。
3. 只提供脱敏后的业务需求和验收条件。
4. 不向开发 Agent 展示原 PR、最终 Diff 和隐藏测试。
5. 让系统自主识别涉及仓库并生成关联变更。
6. 使用原 PR 测试、补充的黑盒测试和人工验收评分。
7. 比较行为与交付质量，不比较代码 Diff 相似度。

### 9.3 建议评分

| 指标 | 权重 |
| --- | ---: |
| 隐藏功能测试通过率 | 30% |
| 跨仓库契约一致性 | 20% |
| 原有测试无回归 | 15% |
| 权限与安全边界 | 10% |
| 迁移和回滚能力 | 10% |
| 代码质量与可维护性 | 5% |
| Trace 和交付证据完整度 | 5% |
| 时间、Token 和计算成本 | 5% |

第二个案例可使用 [Saleor Dashboard Issue #6554](https://github.com/saleor/saleor-dashboard/issues/6554)，验证系统能否发现表面为 Dashboard 500 错误、实际根因位于后端价格计算逻辑的跨边界缺陷。

## 10. MVP 范围

第一版建议严格限定：

- 存量、测试可运行的 Web 项目；
- 一种前端和一种后端技术栈；
- 增量功能、Bug 修复和简单重构；
- 最多 2-3 个代码仓库；
- 自动部署到预发；
- 生产发布必须人工审批；
- 至少演示一次失败、修正和一致性回滚。

第一阶段成功标准：在 Saleor 历史任务回放中，系统能够生成关联 PR，通过隐藏测试，部署预发，生成 Release Evidence Pack，并在故障注入后恢复到上一个一致版本。

## 11. 核心结论

1. 代码生成能力已经商品化，单纯做一个 Coding Agent 缺乏差异化。
2. 企业的关键问题是 AI 加速生成后带来的跨仓库协调、质量、稳定性、安全和责任问题。
3. 产品壁垒是 Delivery Contract、独立验证、权限治理、跨仓库一致性和交付证据，而不是模型本身。
4. AgentTeams 适合作为多 Agent 控制层，Coding Agent 作为可替换执行器，两者是共生关系。
5. “可上线”应定义为经过门禁、可部署并等待授权的 Release Candidate，而不是无人监管直发生产。
6. Saleor 提供了真实、活跃、可复现的多仓库验证环境，无需先获得企业私有代码。
7. 比赛最有说服力的 Demo 不是一次成功生成，而是展示完整闭环：需求、跨仓库修改、独立验证、失败修正、预发部署、审批、证据和回滚。

