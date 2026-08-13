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
    RepositorySelected,
)

__all__ = [
    "ContractEdgeView",
    "ContractSpec",
    "DiffEdge",
    "EdgeChangeView",
    "EdgeSource",
    "EdgeStatus",
    "GraphEdge",
    "GraphNode",
    "IntegratedPlan",
    "PlanDiff",
    "PlanGraph",
    "RepositorySelected",
    "TaskDagNodeView",
    "TaskNode",
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
