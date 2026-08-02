# RepoMesh 文档导航

## 团队开工

- [团队交接与后续开发总览](development/team-handoff.md)
- [并行开发任务分工](development/parallel-work-plan.md)
- [公共契约 v0.1](contracts/public-contracts-v0.1.md)
- [开发流程](development.md)
- [团队开发规则](architecture/team-development.md)

## 架构

- [模块与 Owner](architecture/module-map.md)
- [依赖规则](architecture/dependency-rules.md)
- [数据库所有权](architecture/database-ownership.md)
- [上下文管理](architecture/context-management.md)
- [AgentTeams 单仓库集成](architecture/agentteams-monorepo.md)
- [Runtime planes](architecture/runtime-planes.md)
- [独立 RepoMesh Core ADR](adr/0001-independent-repomesh-core.md)
- [First-party AgentTeams Runtime ADR](adr/0002-first-party-agentteams-runtime.md)

## 数据与运行

- [数据库基础](database.md)
- [产品方向与验证方案](agentic-delivery-product-brief.md)

文档描述语义，模块 `contracts.py` 是可执行契约。两者不一致时停止联调，由生产模块 Owner
在同一个 PR 中修正，禁止消费方自行兼容未声明字段。
