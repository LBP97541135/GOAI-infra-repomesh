# Product Components

This directory contains first-party product components that are developed and released from the
RepoMesh monorepo while keeping explicit runtime and language boundaries.

## AgentTeams Runtime Control Plane

`components/agentteams` contains the Go Controller and the Manager/Worker runtime sources imported
with Git subtree. RepoMesh owns the product build and may extend this component through the Runtime
contract. Official upstream provenance and license notices remain preserved.

AgentTeams owns Manager/Worker/Team/Human reconciliation, Matrix collaboration, managed object
storage and gateway integration, and Worker container lifecycle. It does not own RepoMesh project,
task, context, validation, or delivery state.

## RepoMesh Runner Execution Plane

`components/repomesh-runner` describes the Python execution component whose implementation lives in
`src/repomesh_runner`. A Runner executes immutable Runtime v1 coding tasks inside an
AgentTeams-managed Worker and returns ordered events and artifact references.

Both components communicate with the RepoMesh product control plane through
`contracts/runtime/v1`. No component imports another component's internal implementation.
