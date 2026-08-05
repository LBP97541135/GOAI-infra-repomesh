# Preset agent capabilities

RepoMesh ships reviewed local wrappers around selected official projects. Upstream
content is never loaded directly into an agent at runtime.

| Role | Default Skills | Default MCP | Conditional MCP |
| --- | ---: | ---: | ---: |
| Organization Leader | 3 | 1 | 0 |
| Repository Leader | 6 | 2 | 0 |
| Worker | 3 | 1 | 1 (`web_e2e`) |

MCP credentials are supplied by a runtime broker. The catalog contains no tokens.
Every call must carry agent, project, repository, task, and run identifiers and be
checked against the assembled allowlist before reaching an MCP server.

