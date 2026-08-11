# 交付控制台落地工作项（2026-08-11）

- 文档状态：可分派
- 契约基线：`docs/contracts/delivery-read-model-v0.1.md`（dbd2b1a）
- 前端基线：`frontend/`（Variant D 正式实现，mock 驱动，32d0272）
- 编号沿用 `parallel-work-plan.md` 体例，前缀 `CONS-`（Console）
- 规则：一个 PR 一个 Owner 模块；跨模块只读聚合放 `api` 层；先契约后实现

## 1. 依赖主线

```text
CONS-01 specification forbidden_paths   CONS-02 治理写端点+归档     （可立即并行）
        \                                      |
         v                                     v
CONS-03 读模型聚合服务（列表+全貌+状态映射） <---+
         |                \
         v                 v
CONS-04 events/messages   CONS-05 decisions 派生
         |                 |
         +--------+--------+
                  v
CONS-10 前端 fetch 层 → CONS-11 接入三视图 → CONS-12 决策夹写回路
                  |
                  v
CONS-13 回放模式（Demo）

旁路（不阻塞主线）：CONS-20 Runner diffstat · CONS-21 成本采集
```

前端 CONS-10 只依赖契约文本，可与后端并行开工（先打 mock server / 契约夹具）。

## 2. 后端工作项

| ID | 任务 | Owner 组 / 模块 | 依赖 | 交付物 | 完成标准 | 规模 |
| --- | --- | --- | --- | --- | --- | --- |
| CONS-01 | Specification 增加 `forbidden_paths` 可选字段 | 项目规格组 / `specification` | 无 | contracts 字段 + 迁移 + Runner 投影透传 | 旧数据返回空集不报错；CodingAgentPackage 带出该字段；契约测试更新 | S |
| CONS-02 | 治理决策写端点 + 交付归档端点 | 质量交付组 / `delivery` | 无 | `POST /deliveries/{id}/governance-decisions`（包装既有 Command，head-bound、幂等、写审计）+ `POST /deliveries/{id}/archive`（终态校验、幂等、409 语义） | E2E 脚本改走 API 不再直调应用服务；SHA 漂移 409 有测试；活跃交付拒绝归档 | M |
| CONS-03 | 交付读模型聚合服务 | 平台组 / `api`（新 `api/read_models/`） | 无（01/02 合入后补字段） | `GET /deliveries`（分组列表 + phase 推导）、`GET /deliveries/{id}` 全貌聚合；§5 状态映射与 attempt/rework 推导的唯一实现 | phase 推导单测覆盖契约 §2 全部分支；6 态映射单测；只经各模块 contracts 读取，架构测试通过；对 8-10 验收留存数据可正确聚合 | L |
| CONS-04 | 事件与消息端点 | 平台组 / `api` + 编排组 / `collaboration` | CONS-03 | `GET .../events`（runner/matrix/gate/plan 合并时间线，游标分页）+ `GET .../messages`（CollaborationMessageView 投影） | 时间线排序稳定、游标可续读；不产出 `deny` kind；messages 单向限制在响应中可辨识 | M |
| CONS-05 | 决策夹派生端点 | 质量交付组 / `delivery` + `api` | CONS-03 | `GET .../decisions`：approve（gate 全绿缺 READY）+ watch（未终态 recovery/rework） | 纯派生无新表；对 8-10 验收数据能再现「两仓待审批」场景；决策消化后条目消失 | S |
| CONS-20 | Runner 采集 diffstat | Runner 组 / `repomesh_runner` | 无（旁路） | 变更采集时附 `git diff --numstat`，进 runner.completed payload 与 `diffs[].diffstat` | 仅允许路径内文件；payload schema 版本兼容（可选字段） | S |
| CONS-21 | Token/成本采集 | Runner 组 + 平台组（observability） | 无（旁路） | CLI 适配器回传 token 用量，聚合进 `cost` 字段 | 至少覆盖 claude-code 适配器；无数据时保持 `null` | M |

## 3. 前端工作项

| ID | 任务 | 依赖 | 交付物 | 完成标准 | 规模 |
| --- | --- | --- | --- | --- | --- |
| CONS-10 | fetch 层与契约类型对齐 | 契约文本 | `src/api/` 客户端（typed，对齐契约 JSON）、数据源开关（`live | replay`）、mock 数据重构为 replay 夹具 | 契约每个 nullable 字段有降级渲染路径；无后端时 replay 模式完整可用 | M |
| CONS-11 | 三视图接入真实数据 | CONS-03/04 + CONS-10 | 侧栏列表（分组+phase 徽标）、对话流/计划纸面/环境窗改 live 数据；轮询或 SSE 待定 | 对 8-10 验收数据（repomesh-e2e-* 双仓）完整展示一次真实交付；blocked/虚拟草稿交付可见 | L |
| CONS-12 | 决策夹写回路 | CONS-02/05 + CONS-10 | 决策夹接 `GET decisions`；审批弹窗提交 `POST governance-decisions`（head_sha 绑定、幂等键、409 冲突提示） | 真实批准后 merge gate 放行可在界面观察到；SHA 漂移时弹窗内失效提示 | M |
| CONS-13 | 回放模式（Demo 叙事） | CONS-10 | 场景状态机（参考原型 4 状态）驱动 replay 夹具推进，含 clarify 决策演示 | 一键回放完整闭环：需求→DAG→失败修复→治理拦截→审批→合并；clarify 仅存在于回放 | M |

## 4. 明确不在本批（引用既有缺口任务）

- deny 治理拦截入审计、Worker→Leader 回报摄取、统一 trace_id：审计线
  （closed-loop-gap-analysis §4.2），另行排期；契约已按 nullable 降级。
- clarify 决策实体（ChangeRequest 回路）：等 team-handoff §5.4 机制设计。
- non_goals / release_rules 字段：无执行面消费方，需求进来再补。

## 5. 建议分派与顺序

第一批并行开工：CONS-01（S，项目规格组）、CONS-02（M，质量交付组）、
CONS-03（L，平台组）、CONS-10（M，前端）。
第二批：CONS-04、CONS-05 随 03 合入即接；前端 11→12→13 串行。
旁路 CONS-20 可随任意 Runner 改动搭车。

关键路径是 **CONS-03 → CONS-11**：读模型聚合是唯一 L 规模项，建议最先认领。
