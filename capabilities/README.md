# Preset agent capabilities

RepoMesh ships reviewed local wrappers around selected official projects. Upstream
content is never loaded directly into an agent at runtime.

- [Skill engineering lifecycle](skills/README.md)
- [MCP server catalog](mcp/servers.json)

| Role | Default Skills | Default MCP | Conditional MCP |
| --- | ---: | ---: | ---: |
| Organization Leader | 3 | 1 | 0 |
| Repository Leader | 6 | 2 | 0 |
| Worker | 4 | 1 | 1 (`web_e2e`) |

Repository teams can also assemble under a **team capability profile** — an additive
skill overlay named on the repository catalog row. The `cross-repo-test-team` profile
adds `cross-repo-test` to the team leader and `integration-run` to its Workers; it is
set (or cleared) through `PATCH /repositories/{id}/capability-profile`, before the
team is onboarded, because the AgentTeams-side skill lists are chosen at creation.

MCP credentials are supplied by a runtime broker. The catalog contains no tokens.
Every call must carry agent, project, repository, task, and run identifiers and be
checked against the assembled allowlist before reaching an MCP server.

