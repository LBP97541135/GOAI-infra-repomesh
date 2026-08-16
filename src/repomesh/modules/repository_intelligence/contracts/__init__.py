"""Public contracts of the repository_intelligence module.

Cross-module imports may target only ``repomesh.modules.<producer>.contracts``
(see AGENTS.md). This package is that boundary for repository intelligence.
"""

from repomesh.modules.repository_intelligence.contracts.diff import (
    DiffEdge,
    EdgeChangeView,
    PlanDiff,
    diff_plan_graphs,
)
from repomesh.modules.repository_intelligence.contracts.graph import (
    ContractEdgeView,
    EdgeSource,
    EdgeStatus,
    GraphEdge,
    GraphNode,
    PlanGraph,
    TaskDagNodeView,
    derive_edges,
    project_batches,
    project_contracts,
    project_task_dag,
)
from repomesh.modules.repository_intelligence.contracts.integration import (
    ContractSpec,
    IntegratedPlan,
    TaskNode,
    integration_method,
    normalize_plan,
    plan_to_graph,
    tm_order_edges,
)
from repomesh.modules.repository_intelligence.contracts.repository import (
    DISCOVERY_SCHEMA_VERSION,
    GUI_STEP_OF,
    CreateIssueIntake,
    DiscoveryApprovalCommand,
    DiscoveryStepCommand,
    DiscoveryTaskReceipt,
    IssueIntakeCommand,
    IssueIntakeReceipt,
    RepositorySelected,
    classification_fingerprint,
    discovery_step,
    discovery_step_state,
    effective_tiers,
    tier_of,
)

__all__ = [
    "DISCOVERY_SCHEMA_VERSION",
    "GUI_STEP_OF",
    "ContractEdgeView",
    "ContractSpec",
    "CreateIssueIntake",
    "DiffEdge",
    "DiscoveryApprovalCommand",
    "DiscoveryStepCommand",
    "DiscoveryTaskReceipt",
    "EdgeChangeView",
    "EdgeSource",
    "EdgeStatus",
    "GraphEdge",
    "GraphNode",
    "IntegratedPlan",
    "IssueIntakeCommand",
    "IssueIntakeReceipt",
    "PlanDiff",
    "PlanGraph",
    "RepositorySelected",
    "TaskDagNodeView",
    "TaskNode",
    "classification_fingerprint",
    "discovery_step",
    "discovery_step_state",
    "effective_tiers",
    "tier_of",
    "derive_edges",
    "diff_plan_graphs",
    "integration_method",
    "normalize_plan",
    "plan_to_graph",
    "project_batches",
    "project_contracts",
    "project_task_dag",
    "tm_order_edges",
]
