# Cloud And Official Skill Integration

RepoMesh does not score cloud integration by product count. A cloud product or official Skill is
accepted only when it has a clear authority boundary, replaceable contract and end-to-end evidence.

## Current Baseline

The current repository is provider-neutral by default:

- RepoMesh owns project, task, context, validation, delivery and audit state.
- AgentTeams owns Manager, Worker, Team, Matrix, storage, gateway and runtime lifecycle.
- MCP servers are brokered through `capabilities/mcp/servers.json`.
- Credentials are references or broker-injected short-lived values; Agents never receive raw
  secrets in Skill contracts.

This keeps the demo runnable without binding the business modules to a specific cloud SDK.

## Official Cloud Skill Admission Rules

An Alibaba Cloud official Skill, MCP server or cloud product adapter may be added only after the
producer defines the contract first:

| Requirement | Rule |
| --- | --- |
| Necessity | Name the business or operational gap it closes, not just the product name. |
| Interface | Define input schema, output schema, timeout, retry, idempotency and degraded mode. |
| Permissions | Use RAM roles or brokered credentials with least privilege and explicit scope. |
| Replaceability | Keep the application service dependent on a port; wire the cloud adapter only in bootstrap. |
| Audit | Record agent, project, repository, task, run, tool, args hash, result, latency and redacted error. |
| Evidence | Provide a test, replay, trace, screenshot or live report that proves the end-to-end path. |

Business modules must not import cloud SDKs directly. Cloud adapters belong under
`src/repomesh/integrations` or a runtime boundary selected by the composition root.

## Recommended Alibaba Cloud Mapping

These are the preferred integration points when RepoMesh is deployed on Alibaba Cloud. They are not
enabled by default.

| Capability need | Preferred cloud integration | RepoMesh boundary |
| --- | --- | --- |
| Centralized model and MCP traffic governance | Higress or AgentTeams gateway policy | Runtime broker and MCP gateway adapter |
| Secret custody and short-lived credential issuance | KMS or Secrets Manager plus RAM | Identity Access credential-reference port |
| Immutable logs and audit search | Simple Log Service | Observability exporter or audit sink adapter |
| Runtime traces and metrics | Managed Service for OpenTelemetry / ARMS | Observability port |
| Containerized AgentTeams and Runner deployment | ACK plus ACR | Deployment manifests and runtime component packaging |
| Artifact and context bundle storage | OSS-compatible object storage | Context artifact store port |
| Alerts, cost or operational tickets as project intake | CloudMonitor / billing / ticket MCP or API adapter | Project intake source adapter |

## Official Skill Examples

When official cloud Skills are available, RepoMesh should treat them as Skill dependencies rather
than as new sources of truth:

| Skill dependency | Allowed use | Must not do |
| --- | --- | --- |
| Cloud log analysis Skill | Provide incident evidence to `project-intake` or `blocker-reporting`. | Change project scope without confirmation. |
| Cloud cost analysis Skill | Provide cost anomaly context and acceptance criteria. | Grant billing credentials to Workers. |
| Cloud deployment or rollback Skill | Execute an approved delivery or rollback command. | Bypass delivery governance or human approval gates. |
| Cloud security posture Skill | Provide risk findings to `test-review` or `delivery-governance`. | Treat advisory findings as automatic merge approval. |

Each official Skill must still appear in the Skill Set snapshot with version, allowed role,
dependency contract and failure handling.

## Migration Cost Control

Every cloud-facing integration must have a local or mock path for tests. The mock path can return
recorded observations, synthetic metrics or dry-run deployment decisions, but it must preserve the
same contract shape. This allows RepoMesh to move between cloud providers or self-hosted services
without rewriting Agent roles, task state or delivery governance.

