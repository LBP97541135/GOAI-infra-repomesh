# Agent Runtime

Owns CodingRun lifecycle, provider-neutral execution requests/results, agent sessions,
interrupt/resume checkpoints, and collected runtime artifacts. Provider CLI behavior belongs in
`repomesh.integrations.coding_agents`; Task Orchestration owns task state.

Public contract: `CodingRunFinished`. The Scenario Mock is the current reference adapter.
## Authorized execution

`ExecuteAuthorizedCodingRun` is the control-plane gate in front of a coding provider. It requires
an immutable `ExecutionContextGrant` bound to the same Agent, Project, Repository and Run. The
gate rejects expired grants, undelegated tools, denied paths and paths outside the grant before a
provider is invoked.

This gate does not replace Runner sandboxing. The Runner must still enforce filesystem, process,
network and secret restrictions so provider behavior cannot bypass the control-plane decision.
