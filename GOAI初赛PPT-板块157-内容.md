# GOAI 初赛方案 PPT 内容 —— 板块 1 / 5 / 7（第1章、第5章、第7章）

> 依据《GOAI世界人工智能开源大赛 初赛方案 PPT 内容框架模板》撰写
> 项目：RepoMesh —— 面向企业增量需求的 Agentic Delivery Control Plane
> 全部内容基于项目真实素材（产品简报 / 验收报告 / 观测方案），可直接粘贴到模板对应页面
> 项目名称建议（≤20字）：**多Agent可观测交付控制平面 RepoMesh**（备选：跨仓库Agent交付控制台）

---

# 第一章 场景与价值（25%）

> 建议覆盖：目标用户与核心痛点、真实场景、可量化的价值收益、行业可复制性、创新点与差异化优势

## 1.1 目标用户与核心痛点

**目标用户**：企业软件交付负责人（研发负责人 / 产品经理 / 平台工程与SRE）、开源项目维护者、对 AI 编码结果负责的团队。

**核心痛点**（均有行业证据）：

| 痛点 | 证据 |
| --- | --- |
| AI 编码已普及，但交付更不稳定 | DORA 2025：90% 受访者已在工作中使用 AI、超 80% 认为生产力提高；但 AI 在改善交付吞吐量的同时**增加了交付不稳定性** |
| 对 AI 输出缺乏信任 | Stack Overflow 2025：46% 开发者不信任 AI 输出准确性（信任者仅 33%）；41.4% 认为 AI 处理复杂任务表现差 |
| 单仓库、短任务限制 | GitHub Cloud Agent 一次任务限于 1 仓库 / 1 分支 / 1 PR、最长 59 分钟；企业真实需求横跨前后端、数据库、基础设施、文档多仓库 |
| 开发 Agent 自测自证 | Agent 自己声明成功、自己跑测试，无独立验证方 |
| 安全合规要求 | NIST SSDF 要求保护软件组件、保留来源、独立或自动审查、安全测试与漏洞响应 |

**一句话痛点**：企业不缺代码生成工具，缺的是把「生成速度」转化为「稳定、可验证、可追溯交付」的**控制体系**。

## 1.2 真实场景

**场景主线**：产品经理提交 PRD / Issue → 系统自动完成「需求澄清 → 仓库发现 → 计划编排 → 多 Agent 执行 → 独立验证 → 人工审批 → 发布 → 回滚」的完整闭环，人工只处理关键澄清、例外和高风险审批。

**真实场景一（开源验证对象 Saleor）**：外部 App 修改结账商品价格时记录修改原因。该功能真实涉及 **4 个仓库**：后端 PR #19466（数据模型/迁移/GraphQL/17 个测试）、Dashboard PR #6732（类型/查询/管理界面）、Apps PR #2393（示例 App）、Docs PR #1809（文档）——单 Agent 无法完成，必须多仓库编排。

**真实场景二（本项目已实测走通的闭环）**：GUI 建 issue → 真 LLM 需求分析（充分度 0.90）→ 候选仓库评分 → 三档分类审批 → 生成计划 DAG → 物化开工 → 建团队 + 双房间 → 派工 → Runner 备工作区 → 编码 Agent 真写代码 → 真跑测试 exit 0 → 候选分支推真仓 → 开 PR → CI 绿 → 人工授权（绑定不可变快照）→ 自动合并 → **整 change set 回滚（saga 开 revert PR）**。全程 GUI、无一步命令行写入（2026-08-12 独立验收实证）。

## 1.3 可量化的价值收益

**核心价值指标**（产品定义口径）：交付时间 / 首次验收通过率 / 人工介入频次 / 缺陷逃逸率 / 回滚率 / 成本（时间、Token、计算）。

**Saleor 验证评分表**（拟按此衡量并对比人工基线）：

| 指标 | 权重 |
| --- | ---: |
| 隐藏功能测试通过率 | 30% |
| 跨仓库契约一致性 | 20% |
| 原有测试无回归 | 15% |
| 权限与安全边界 | 10% |
| 迁移和回滚能力 | 10% |
| 代码质量与可维护性 | 5% |
| Trace 和交付证据完整度 | 5% |
| 时间 / Token / 计算成本 | 5% |

**已实测的质量下限证据**：编码 Agent 读懂并遵守目标仓库 README 关于「假绿」的约定、拒绝写死税率；自识别跨仓舍入口径未决问题并上报；把测试断言由单币种**加强为 EUR/JPY/GBP 三币种**——产线验证的是质量下限，不只是连通性。

## 1.4 行业可复制性

- **Saleor 开源五仓库验证**：saleor（后端）/ saleor-dashboard（前端）/ apps / docs / platform，真实活跃的多仓库场景，无需企业私有代码即可复现验证。
- **TrainTicket 离线评估集**：15 个用例，recall / precision / F1 科学量化。
- **方法论可迁移**：Delivery Contract（机器可检查的交付契约）、Release Evidence Pack（交付证据包）可复制到任意存量 Web 项目 / GitHub、GitLab 生态。
- **适用场景**：增量功能、Bug 修复、简单重构；最大 2-3 仓库起步，逐步扩展到多仓库并发交付。

## 1.5 创新点与差异化

**我们不是**：另一个 Coding Agent（Codex / Claude Code / Hermes 是可替换执行器）；不是 LangChain / LangSmith（应用框架 / 观测工具）。**我们是**位于 Coding Agent 上方的「受控软件交付团队」组织者。

| 差异化优势 | 具体内涵 |
| --- | --- |
| **① Delivery Contract + 独立验证（有牙齿）** | 需求先转成机器可检查的交付契约（验收标准/变更范围/质量门禁/人工审批）；开发 Agent 不能自行宣布成功，测试由独立验证执行；实测：无测试结果的证据被**硬性拒收**、测试真跑真挂如实报失败 |
| **② 全链路可观测、可审计** | OTel GenAI 语义（v1.38）+ 自有 10 属性命名空间；Release Evidence Pack 十项齐全（需求/Agent 身份/任务 DAG/提交 PR/测试/安全扫描/人工审批/预发结果/已知风险） |
| **③ 跨仓库一致性 + 回滚恢复** | 跨仓库任务 DAG、关联 PR、按依赖顺序构建；三阶段回滚（merge 前 withhold → 逆序 revert → 交付后 ChangeSet 递归）+ 数据库迁移补偿，恢复上一跨仓库一致状态 |
| **④ 安全治理内建** | 人工审批必经点、HEAD-BOUND 授权（绑定不可变快照，任一变化授权失效）、最小权限、凭据不进 GUI / 不进 trace |

---

# 第五章 工程落地、运行验证与安全可审计（20%）

> 建议覆盖：可运行性、运行证据、可观测与检索链路、安全治理机制、云产品选型的必要性与边界

## 5.1 可运行性

| 项 | 证据 |
| --- | --- |
| 一键启动（开发档） | `dev-up.ps1` / `dev-up.sh` 一条命令：起 Postgres → 迁移 → 起 API(8100) → 起前端(5280) → 自动开浏览器；幂等可重入，重跑即恢复工作栈 |
| 一键启动（全栈 compose） | `docker compose --profile console up`：宿主机只需 Docker；三容器 healthy、自迁移、8180→200（冷路径首跑即通实证） |
| 质量门槛 | `ruff check .` + `pytest` 全绿；架构/契约/适配器测试齐备；独立验收方法论（测试可信度五形态） |
| 技术栈 | Python/FastAPI + PostgreSQL + Alembic + React/Vite + AgentTeams(Matrix) + MinIO + Higress |

## 5.2 运行证据（独立验收实测，2026-08-12 终态）

**判据①：全流程 GUI 闭环 —— 达成**（验收会话只验收不修改，主链无一步 curl 写入）：

> GUI 建 issue → 真 LLM 需求分析 → 候选评分（5 候选）→ 三档分类 → 改档留痕 → 批准 → 生成计划 v1 → 物化开工 → 建团 + teamRoom/leaderDM 双房间 → 派工点名 → Runner 备工作区（真仓代码+冻结 spec）→ **编码 Agent 真写代码** → **`python scripts/run_tests.py` 真跑 exit 0** → change_set 建立 → 候选分支推真仓 → PR #3 → CI `tests` 绿 → 证据面 → 授权单（IMMUTABLE 快照 + HEAD-BOUND SHA）→ 批准 → **自动 merge** → **整 change set 回滚 → saga 开 revert PR #4**

**关键实测数据点**：
- 候选分支命名 `repomesh/{plan8}/{repo8}`，change_set `18dc502c…`，merge_sha `27126b29f5e8…`，revert 分支由零存储幂等派生（`{change_set8}/{repo8}-{merge_sha12}`）逐字自证
- revert PR 的 CI 红被正确拦截 → 落 `manual_intervention` —— **系统不因「回滚已获批准」免检**
- 质量下限：Agent 拒绝「假绿」（不写死税率、断言加强为三币种、跨仓未决问题如实上报）
- 工程过程：十九次修复落地、九个「一次性事件死角」逐个填平，缺陷按 A（功能坏）/B（闭环缺）/C（优化）分类留痕、活体反证关闭

**判据②：一键打开 —— 过**（compose 冷路径实证；`down -v` 作用域 `--dry-run` 证只删 console 专属卷）。

## 5.3 可观测与检索链路（赛题 5 项必答）

**① 采集方式（三层 + 网关汇聚点）**

| 层 | 位置 | 手段 |
| --- | --- | --- |
| 规划/编排 | RepoMesh API + runner | OTel SDK + `@traced` 业务 span + GenAI span（**约 70% 已写好**，仅差 OTLP 配置） |
| **Agent 容器侧（最大缺口）** | Higress AI 网关 | **Higress OTel wasm 插件：埋 1 点覆盖 8 个 Agent 容器的全部 LLM 调用**（key-auth + ai-proxy 是天然汇聚点）；可选 LoongSuite Pilot 补 Skill/MCP 内部调用 |
| CLI 子进程 | runner 内 claude/codex | `OtelDriverObserver`（DriverEvent → span，按 call_id 配对，已接线） |
| Log / Metrics | 全进程 | JSON 结构化 Log（带 trace_id）+ OTLP Metrics（token/成本/延迟/成功率） |

**② 语义规范**：遵循 **OpenTelemetry GenAI 语义约定 v1.38**（`gen_ai.operation.name` = chat / invoke_agent / execute_tool；`gen_ai.request.model` / `usage.*` / `finish_reasons`）+ 自有 **`repomesh.*` 命名空间（10 个冻结属性，测试兜底防改名）**；凭据序列化禁令：api_key / token 不进任何 span。

**③ 数据类型**：Trace（主打：chat / invoke_agent / execute_tool / retrieval.* / skill.* / mcp.*，覆盖 LLM、Skill、MCP、RAG 四项赛题要求）+ Log + Metrics —— 满足「至少 1-2 类」要求。

**④ 后端存储与检索**：`OTLP/HTTP → Collector → 存储`；短期 AgentScope Studio（零成本、认 GenAI 语义、只收 Trace），中期自建 Collector + ClickHouse（三类数据）；`observability` 模块做 durable projection + 查询 API；按 `task_id / run_id / repository_id / correlation_id` 索引，**单条 trace 可回放**；按 `repomesh.run_id` 聚合 token → 单 issue 成本。

**⑤ 应用场景与效果**（含真实踩坑案例）：

| 场景 | 效果 / 案例 |
| --- | --- |
| 在线监控与告警 | 上游欠费 502 `insufficient_user_quota`（余额 -$0.002446 全部 502）、模型不存在 503 `model_not_found`（任务卡死数小时）、成功率骤降 → Metrics 失败率骤升 → **分钟级告警** |
| 任务卡死定位 | redispatch 前 4 仓库任务长期 assigned → 按 `task_id` 看 last-touch 时间 → 卡死告警；前端 EventTimeline 时间线视图 |
| 成本核算 | 按 issue / 仓库 / agent 聚合 token 与费用（直接支撑买套餐决策） |
| 推理质量回放 | confirmation 输入输出在 span 上可回放，区分判定对错 |
| 离线评估 | trace ↔ `scoring.py`（TrainTicket 15 用例 recall/precision/F1）联动，形成「判定置信度 vs 实际命中」校准曲线 |

## 5.4 安全治理机制

| 机制 | 内涵（实测点） |
| --- | --- |
| **人工审批必经点** | Delivery Contract 强制 `release.human_approval: required`；发现审批 v1 必经；授权单绑定**不可变快照 + HEAD-BOUND SHA**，任一 SHA/契约/门禁变化授权立即失效；决策历史 append-only 可审计 |
| **六检查点治理** | repository_scope / specification / execution / validation / delivery / exception_escalation；`manual_controlled` 模式缺一即拒（不全则静默降级）；审批仅对精确 evidence_version 有效，变更即失效并 409 防重放 |
| **权限模型** | human grants：角色（组织/项目/仓库主管）× code_access（none/read/write）与 control_actions **独立评估**——可「只读审交付、批准发布」，批准人无法改仓库；授权须单个 grant 完全覆盖动作+仓库+路径+访问级别 |
| **凭据安全** | token 不进 GUI（服务端 env 配置）；api_key/token 不进 span；Coding Agent 不持有生产凭据；身份用 scrypt 加盐哈希 + HttpOnly cookie（仅存 SHA-256）；审批端点不接受 body 中的身份字段（防冒名） |
| **执行安全边界** | 沙箱拒绝任意 shell（设计内安全姿态）、allowed_paths / forbidden_paths（`.git/**`、`.github/workflows/**` 拒绝）、工具守卫词边界匹配、Context 权限交集 + 4h 过期 |
| **回滚与恢复** | 三阶段：merge 前 withhold → 部分 merge 逆序 revert → 交付后「回滚即 ChangeSet」递归；RecoverySagaExecutor + 数据库迁移补偿；回滚也必过自己的 CI 与门禁 |
| **合规对齐** | NIST SSDF：保护软件组件、保留来源、独立或自动审查、安全测试、漏洞响应 |

## 5.5 云产品选型的必要性与边界

- **AgentTeams**（选型基点）：Manager-Workers 架构，本身不实现 Agent 推理，只组织多 Agent Runtime；Matrix 透明协作 + MinIO 共享 + Higress 统一管理模型/MCP/凭据 —— 满足赛题「必须以 AgentTeams 为多 Agent 协同基点」。
- **Higress**：AI 网关天然汇聚所有 Agent 的 LLM 调用，OTel 插件一处覆盖 8 容器，是「纯 AgentScope 方案」之外的差异化加分项。
- **LoongSuite**：采集端（Agent 侧 hook/插件），与展示端 AgentScope Studio 解耦（OTLP 接口），埋点代码不变、只改 endpoint 即可切换。
- **边界与降级**：Studio 短期零成本展示、中期自建 Collector；前端诚实降级（未接入 ≠ 未探测，三态分开）；部署前须处理 Studio 监听 0.0.0.0 含完整 prompt 的防火墙隔离、`intercept.js` 密钥脱敏、LoongSuite 安装脚本来源审阅。

---

# 第七章 落地计划与进展（对应「当前进展」与整体可行性）

> 建议覆盖：当前进展、里程碑与落地计划、风险控制

## 7.1 当前进展（已完成 · 有可运行成果）

| 时间 | 里程碑 | 证据 |
| --- | --- | --- |
| 2026-07 下旬 | 选题与产品定义、AgentTeams 集成调研、Saleor 验证方案设计 | product-brief 定稿 |
| 2026-08 上旬 | 控制台 v2 八批次施工全部完成并逐批验收合并（仓库池 / 发现链 / 物化+DAG / 回滚实装 / GUI 回滚 / 一键启动） | 施工收官记录（头 9290061） |
| 2026-08-11 | 交付控制台 v2 终验：**8/8 步全过、A 类缺陷 0** | console-v2-acceptance-report |
| 2026-08-12 | **全流程闭环终态实机验收：主链达成**（需求→合并→可回滚，全程 GUI）；19 次修复、9 个死角填平 | full-loop-acceptance-report |
| 2026-08-14 | 可观测性赛题对照与实现方案定稿：**埋点约 70% 已写好、0% 导出**，缺口定位精确到文件/函数 | 可观测性-赛题对照与实现方案 |

**可运行成果**：`dev-up` 一键启动 / compose 全栈一条命令；控制台 GUI 全流程闭环（已实证）；AgentTeams 团队 + 双房间 + Runner 执行面真实运转。

## 7.2 里程碑与落地计划

**阶段一 · 核心闭环（已完成）**：GUI 全流程闭环（发现 → 计划 → 物化 → 执行 → 验证 → 审批 → 合并 → 回滚），独立验收实证。

**阶段二 · 可观测性落地（进行中，5 步）**

| 步骤 | 内容 | 工作量 | 收益 |
| --- | --- | --- | --- |
| ① | `.env` 配 OTLP endpoint + compose 起 Studio/Collector | 半天 | 让已写好的 70% 埋点立刻通、出第一条完整 trace |
| ② | Higress 挂 OTel 插件 | 1 天 | 覆盖全部 Agent 容器 LLM（最大缺口、最大收益） |
| ③ | 补 `retrieval.*` / `skill.*` / `mcp.*` span + Log 结构化 + Metrics + 告警 | 1-2 天 | 满足赛题全部覆盖项 |
| ④ | `observability` 模块落库 + 查询 API + 前端时间线 | 2 天 | 在线监控、成本核算 |
| ⑤ | trace ↔ `scoring.py` 离线评估联动 | 1 天 | 「科学量化」落地 |

**阶段三 · 规模化验证（规划）**：Saleor 五仓库历史任务回放（固定基线 → 隔离远程 → 脱敏需求 → 自主识别仓库 → 隐藏测试 + 黑盒评分）；TrainTicket 第二验证集；MVP 从 2-3 仓扩展到多仓并发交付。

**复赛准备**：可执行 AgentTeams 代码 + 可运行 Demo。Demo 重点不是「一次成功生成」，而是**完整闭环**：需求 → 跨仓库修改 → 独立验证 → 失败修正 → 预发部署 → 人工审批 → 证据 → 回滚。

## 7.3 风险控制

| 风险 | 案例 / 根因 | 控制措施 |
| --- | --- | --- |
| 上游 LLM 不可用 | 欠费 502 `insufficient_user_quota`；模型 503 `model_not_found`（任务卡死数小时） | 失败率骤升分钟级告警；可替换 provider 适配器（23 个 CLI 适配器） |
| 任务无推进 | redispatch 前 4 仓库任务长期 assigned；一次性事件死角（派工点名/任务包/开 PR「界面绿物理世界停」） | 幂等可重放 + 收敛巡检（9 例死角已填平）；last-touch 卡死告警；重派工入口 |
| 假绿 / 自测自证 | Agent 宣称成功但未执行；测试命令未注入 | 独立验证、Runner 代跑测试、无测试结果证据硬性拒收、testResults 溯源（executedBy 谁跑的） |
| 凭据泄露 | token 进 GUI / 进 span | 服务端 env 配置、凭据序列化禁令、沙箱拒绝任意 shell、生产凭据不交给 Coding Agent |
| 配置漂移 | 部署形态变化 → 路径映射失配，Runner 静默放单 | 对账哨兵（派单在租 + 事件流水为空 = 有人租了活没干） |
| 工具误伤 | 子串匹配误拦 `rm-` 资源名 → agent 停在无人可批的审批 | 安全规则词边界匹配；命名空间与安全词表一起设计 |
| 数据迁移风险 | 多仓库整体回退 | 三阶段回滚 + 数据库迁移补偿 + 恢复上一跨仓库一致状态 |
| 观测后端安全 | Studio 监听 0.0.0.0 且含完整 prompt；LoongSuite 脚本来自外网 | 防火墙拦外部入站；脚本来源审阅；`intercept.js` 密钥脱敏后再接生产 |

---

## 附：P0 一页纸速览（供全队统一口径）

- **项目名称**：多 Agent 可观测交付控制平面 RepoMesh
- **问题与场景**：企业 AI 编码已普及但交付不稳定、不透明、难审计；跨仓库长周期需求单 Agent 无法完成
- **核心解决方案**：用 AgentTeams 组织多个职能 Agent，将需求自动转化为「经过独立验证、可观测、可审计、可回滚的 Release Candidate」
- **创新点与差异化**：Delivery Contract + 独立验证（有牙齿）；全链路 OTel 可观测 + Release Evidence Pack
- **开放 / 复用价值**：Saleor 五仓库开源验证 + TrainTicket 评估集 + 可复用 Skills / 适配器 / 契约
- **当前进展**：全流程 GUI 闭环已实证（2026-08-12）；可观测埋点 70% 就绪、Higress 网关方案定稿
