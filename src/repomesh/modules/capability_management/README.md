# Capability management

This module maps RepoMesh business roles to reviewed local Skill wrappers and
official MCP servers. It is the capability control plane, not an MCP client.

The preset assembler returns only capabilities permitted for the principal's
role. Conditional capabilities, such as Playwright, require an explicit task
feature. Credentials and runtime tool calls remain outside this module.

