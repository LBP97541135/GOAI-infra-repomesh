# RepoMesh 文档导航

## 当前阶段（全流程 GUI 闭环）

- [控制台六个面的图示](console-tour.md)
- [全流程闭环总计划（已收官，含批次记录）](development/full-loop-plan-20260812.md)
- [全流程 GUI 设计定稿](development/full-loop-gui-design-20260812.md)
- [交付读模型契约 v0.4（发现链，现行）](contracts/delivery-read-model-v0.4.md)
- [控制台 v2 验收报告（2026-08-11）](development/console-v2-acceptance-report-20260811.md)

## 团队开工

- [团队交接与后续开发总览](development/team-handoff.md)（状态章节已过时，见文首注记）
- [公共契约 v0.1](contracts/public-contracts-v0.1.md)
- [交付读模型契约 v0.1](contracts/delivery-read-model-v0.1.md)（v0.2/v0.3/v0.4 为增量，全部有效）
- [开发流程](development.md)
- [团队开发规则](architecture/team-development.md)

## 架构

- [模块与 Owner](architecture/module-map.md)
- [Agent Identity 清单](architecture/agent-identity-catalog.md)
- [云产品与官方 Skill 接入](architecture/cloud-skill-integration.md)
- [依赖规则](architecture/dependency-rules.md)
- [数据库所有权](architecture/database-ownership.md)
- [上下文管理](architecture/context-management.md)
- [AgentTeams 单仓库集成](architecture/agentteams-monorepo.md)
- [Runtime planes](architecture/runtime-planes.md)
- [独立 RepoMesh Core ADR](adr/0001-independent-repomesh-core.md)
- [First-party AgentTeams Runtime ADR](adr/0002-first-party-agentteams-runtime.md)
- [Runner Protocol Drivers ADR](adr/0003-runner-protocol-drivers.md)
- [Runner Driver 层设计规范](development/runner-driver-spec.md)
- [AgentTeams 资源投影规范](development/agentteams-projection-spec.md)
- [AgentTeams Fork 与 repomesh-runner Runtime 实施计划](development/agentteams-runner-runtime-plan.md)

## 数据与运行

- [数据库基础](database.md)
- [产品方向与验证方案](agentic-delivery-product-brief.md)
- [开源就绪清单](open-source-readiness.md)
- [仓库原生交付 PRD v0.1](repository-native-delivery-prd-v0.1.md)
- [仓库原生交付技术方案 v0.1](development/repository-native-delivery-tech-design-v0.1.md)

文档描述语义，模块 `contracts.py` 是可执行契约。两者不一致时停止联调，由生产模块 Owner
在同一个 PR 中修正，禁止消费方自行兼容未声明字段。
