# Change Orchestration

Owns the cross-module workflow that turns a confirmed integrated change plan into specifications,
batched repository tasks, plan snapshots, and approval handoff documents. It also coordinates
partial replanning after an upstream repository change.

It consumes public contracts from Repository Intelligence, Project, Specification, and Task
Orchestration. It does not scan repositories, implement task state machines, run Coding Agents,
or submit pull requests.

## Internal layout

- `contracts.py`: stable workflow result objects consumed by callers.
- `ports.py`: narrow capabilities required from producing modules.
- `application.py`: materialization and replan sequencing.

The former Repository Intelligence import remains as a compatibility shim. New code must import
from `repomesh.modules.change_orchestration`.
