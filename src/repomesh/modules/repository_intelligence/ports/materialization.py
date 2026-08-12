"""The materialization capability, as this module needs to see it.

Contract v0.4 §8. The discovery chain ends with a plan on a draft snapshot;
turning that plan into specs and tasks belongs to ``change_orchestration``,
which owns "plan materialization" and already imports this module's
:class:`IntegratedPlan`.

Declaring the dependency as a port here, rather than importing
``PlanExecutionBridge``, is not tidiness — it is the only way round. The import
already runs ``change_orchestration → repository_intelligence``; adding the
reverse edge would close a cycle between two production modules. The bridge
satisfies this protocol structurally, and the composition root hands it over.

The result is described the same way: only the four facts §8's response is
built from, so this module does not need ``MaterializationResult`` and the
module that owns it does not need to know who reads it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from uuid import UUID

from repomesh.modules.repository_intelligence.application.plan_integration import (
    IntegratedPlan,
)


class MaterializedTask(Protocol):
    @property
    def id(self) -> UUID: ...


class MaterializedPlan(Protocol):
    """What materializing a plan produced, narrowed to §8's response."""

    @property
    def plan_id(self) -> UUID | None: ...

    @property
    def tasks(self) -> Sequence[MaterializedTask]: ...

    @property
    def skipped_repos(self) -> Sequence[str]: ...


class PlanMaterializer(Protocol):
    """Create specs and tasks from an integrated plan.

    Raises, and the caller passes both through verbatim rather than rewording
    them (§8):

    - ``WorkflowBlocked`` when the REPOSITORY_SCOPE checkpoint has not been
      cleared — the reason names the gate and the operator acts on it;
    - ``ExecutionPlaneUnavailable`` when nothing can assign the tasks, raised
      before any side effect.
    """

    async def materialize(
        self,
        plan: IntegratedPlan,
        requirement: str,
        project_id: UUID,
        leader_agent_id: UUID,
        *,
        idempotency_prefix: str,
        repo_details: Mapping[str, Mapping[str, Any]] | None = ...,
    ) -> MaterializedPlan: ...


__all__ = ["MaterializedPlan", "MaterializedTask", "PlanMaterializer"]
