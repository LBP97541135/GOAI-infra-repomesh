# Third-Party Notices

This file records third-party components that RepoMesh vendors or embeds directly. Dependency
packages resolved by `uv.lock` keep their own package metadata and licenses.

RepoMesh itself is licensed under the Apache License, Version 2.0. See `LICENSE`.

## Embedded Components

| Component | Source | Version or commit | License | Location |
| --- | --- | --- | --- | --- |
| AgentTeams | `https://github.com/agentscope-ai/AgentTeams.git` | `v1.2.0` / `793db242257a569d911b1aa59c1cd554af78511f` | Apache-2.0 | `components/agentteams` |

The AgentTeams upstream license and notices are preserved under `components/agentteams`. Do not
remove or replace upstream notices when updating the subtree.

## Referenced External Projects

RepoMesh also records reviewed integration sources that are not vendored into the root package:

| Project | Source | Use |
| --- | --- | --- |
| GitHub MCP Server | `https://github.com/github/github-mcp-server` | Brokered SCM MCP integration. |
| Context7 | `https://github.com/upstash/context7` | Brokered documentation lookup integration. |
| Playwright MCP | `https://github.com/microsoft/playwright-mcp` | Conditional web E2E evidence collection. |

Exact reviewed pins for MCP projects live in `capabilities/mcp/servers.json`.
