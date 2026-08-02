# Agent Runtime

Owns CodingRun lifecycle, provider-neutral execution requests/results, agent sessions,
interrupt/resume checkpoints, and collected runtime artifacts. Provider CLI behavior belongs in
`repomesh.integrations.coding_agents`; Task Orchestration owns task state.

Public contract: `CodingRunFinished`. The Scenario Mock is the current reference adapter.
