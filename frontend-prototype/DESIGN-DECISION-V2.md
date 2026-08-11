# 控制台重设计 v2 定稿（2026-08-11）

> 状态：**已定稿**（用户确认）。取代 v1（DESIGN-DECISION.md）的信息架构部分；
> Variant D 视觉令牌与组件体系**继续有效**，仅信息架构（导航/顶级对象/页面组织）重构。
> 可点击原型：`frontend-prototype/redesign-issue-centric.html`（自包含单文件，
> `python -m http.server 8788 --directory frontend-prototype` 后访问）。

## 1. 决策链（为什么重构）

1. **顶级对象讨论**：v1 沿用 Codex「项目 → 会话」树。但本系统中交付是**有终态的事件**
   （delivered/failed/archived），不是可回访的会话；把事件钉进侧栏树是类比失配。
2. 真正长期稳定的实体是仓库网格与常设 agent 组织（团队按 issue×仓库自动组建、
   房间双设、无拆除路径）——这是与 Codex 类产品的本质差异（组织层不解散）。
3. **看板被否决**：8 相 phase 是严格线性流水线且系统推导（不可拖拽），列分布极度
   偏斜；看板形式承载非线性人工分类，不适配。
4. **最终采纳 GitHub 式 issue 列表**（用户裁决）：扁平列表 + Open/Closed 二元 +
   行内徽标承载状态；流程感只存在于徽标不存在于结构。

## 2. 最终信息架构

```
侧栏：工作区切换器（≈组织）
      + 新建 issue（弹窗：处理者=Org Leader，需求文本，范围默认 Org Leader 提议）
      issue（导航，计数）→ 主屏 GitHub 式列表（Open/Closed + 筛选）
      仓库（导航）→ 主屏：每仓 · 驻扎团队数 · 团队状态
      团队（导航）→ 主屏：rm-team-* · 归属仓库 · 所属 issue · 成员及状态
      智能体（导航）→ 主屏：花名册表格（状态/归属/运行时/时长）
      设置（底部）→ 主屏：Agent runtime 适配器 + 连接健康（首版只读）
      用户身份（底部）

issue 详情页：标题+元数据+Open 徽标 → 原始需求卡（含冻结契约 hash）
      → 关联仓库·团队芯片 → 决策夹（迁移现有 VARIADEX 组件 + 审批弹窗）
      → 房间区（每仓一块：teamRoom + leaderDM 上下排，Element 式房间行）

活体房间视图：房间头（成员 + LIVE 呼吸灯 + 刷新机制标注）
      → 双视图切换：房间聊天 ↔ 每仓 DAG·PLAN·SPEC（奶油纸面）
      → 右侧悬浮环境窗（单仓作用域：状态/变更/CHANGESET 本仓位置/环境）
```

语义等式（全部经代码核实，零新实体）：
- **issue = Project**（1 需求 = 1 项目；交付 = issue 内轮次；范围 = 项目拓扑）
- **工作区 = Organization**（全实体原生按 organization_id 隔离）
- **房间 = AgentTeams 真实 Matrix 房间**（团队按 issue×仓库建，每团队
  teamRoom + leaderDM；leader↔leader、worker↔worker 无横向边）
- **Open/Closed 与 issue 当前 phase = 读模型派生**（前端禁止映射，红线不变）

设计原则延续：诚实数据（无源字段显「未接入」、空房间不装满、LIVE 由 in_progress
任务派生而非假 presence）；状态映射唯一实现在读模型；chat 是传输层不是事实源。

## 3. 分支与代码基线裁决（2026-08-11）

- v2 施工在 **`feat/console-v2`**（自 origin/main=80ba960 新建，merge UI_Design）。
  后端侦察结论 B 级：读模型代码零改动直带；适配三项——迁移重编号
  0016/0017→0019/0020、六处文本冲突机械解（container.py 最重）、合并后冒烟。
- UI_Design 冻结存档。
- **main 的 `web/` 保留不动**（队友的人工审核台：ReviewRequest + 建项目 + 账户）。
  它带来两份直接采用的资产：**身份系统**（本地账户/会话/角色，销掉「身份未接入」
  缺口）与 **SSE 推送模式**（房间 LIVE 的升级位）。ReviewRequest 与治理决策为两套
  并行审批机制（不同表）；「决策夹统一呈现两者」列为产品级整合待议项。

## 4. v2 工作项（第二批，编号续用 CONS-）

| ID | 侧 | 任务 | 规模 | 依赖 |
| --- | --- | --- | --- | --- |
| CONS-30 | 后端 | feat/console-v2 建分支 + merge UI_Design + B1/B2/B3 适配 | M | 无 |
| CONS-31 | 后端 | issue 读模型：GET /issues（open/closed 派生、issue 粒度 phase、轮次数、pending_decision_count 复用）| S | 30 |
| CONS-32 | 后端 | 网格/团队/花名册读模型：GET /repositories、/teams、/agents（拓扑+任务派生；运行时字段走 AgentTeams Controller 实时代理，不可达 null）| M | 30 |
| CONS-33 | 后端 | 房间读模型：messages 透出 room_id；房间清单（并 32 拓扑）；治理决策投影 leaderDM 流；每仓 spec 投影；DAG 显式依赖边确认（问询项）| M | 30 |
| CONS-34 | 后端 | 契约文本 v0.2 起草（31-33 新端点），主脑裁决后实现 | — | 前置于 31-33 |
| CONS-40 | 前端 | 侧栏与导航改造（工作区切换器/四导航/设置底锚/main auth 接入）| M | 30 |
| CONS-41 | 前端 | issue 列表页 + 新建 issue 弹窗（先 replay 夹具，31 落地接 live）| M | 40 |
| CONS-42 | 前端 | issue 详情页（需求卡/关联芯片/决策夹迁移/房间列表）| L | 41, 33 |
| CONS-43 | 前端 | 活体房间视图（聊天流/LIVE/轮询→SSE/双视图/单仓环境窗）| L | 42 |
| CONS-44 | 前端 | 仓库/团队/智能体/设置四页 | M | 32 |

流程不变：先契约后实现；worktree 施工主脑验收合并；定向测试+功能验收；
横向协调与升级规则沿用。

## 5. 已知边界（诚实清单）

- 房间内容当前仅结构化信封（指派/返工），Worker 回报待审计线；6-kind 词汇表
  （assignment/report/question/answer/progress/decision）schema 已就绪。
- 房间输入框已移除（b6b04b2），clarify/ChangeRequest 回路落地时恢复为其入口。
- 设置页 runtime「配置/接入」写路径为二期（适配器注册表 API 未立项）。
- 团队/房间无拆除路径（运维遗留，另行立项）。
- Manager 多项目上下文：架构上间歇式无状态决策，会话层隔离未验证；
  近期靠一项目一 Org Leader 分片规避。
