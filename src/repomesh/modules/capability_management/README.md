# Capability management

This module maps RepoMesh business roles to reviewed local Skill wrappers and
official MCP servers. It is the capability control plane, not an MCP client.

The preset assembler returns only capabilities permitted for the principal's
role. Conditional capabilities, such as Playwright, require an explicit task
feature. Credentials and runtime tool calls remain outside this module.

Reviewed static presets bootstrap version `1.0.0` as the trusted stable baseline. Later versions
are immutable, evaluation-gated, assigned through deterministic stable/canary releases, and frozen
into Runner tasks and context manifests. Failed canary health stops traffic and rolls the version
back without changing assignments already executing.

