from dataclasses import dataclass
from uuid import UUID

from repomesh.modules.project.contracts import (
    HumanControlAction,
    ProjectAgentTopologyView,
    ProjectOperationalStatus,
)
from repomesh.modules.project.domain import ProjectTopologyViolation
from repomesh.modules.project.human_control import (
    HumanAuthorizationRequest,
    authorize_human,
)
from repomesh.modules.project.ports import ProjectTopologyStore


@dataclass(frozen=True, slots=True)
class ControlProjectCommand:
    project_id: UUID
    human_principal_id: UUID
    action: HumanControlAction


class ProjectLifecycleService:
    _STATUS_BY_ACTION = {
        HumanControlAction.PAUSE_PROJECT: ProjectOperationalStatus.PAUSED,
        HumanControlAction.RESUME_PROJECT: ProjectOperationalStatus.ACTIVE,
        HumanControlAction.CANCEL_PROJECT: ProjectOperationalStatus.CANCELLED,
    }

    def __init__(self, topologies: ProjectTopologyStore) -> None:
        self._topologies = topologies

    async def control(self, command: ControlProjectCommand) -> ProjectAgentTopologyView:
        status = self._STATUS_BY_ACTION.get(command.action)
        if status is None:
            raise ProjectTopologyViolation("unsupported project lifecycle action")
        topology = await self._topologies.get(command.project_id)
        if topology is None:
            raise ProjectTopologyViolation("project topology does not exist")
        decision = authorize_human(
            topology.to_view(),
            HumanAuthorizationRequest(
                human_principal_id=command.human_principal_id,
                action=command.action,
            ),
        )
        if not decision.allowed:
            raise ProjectTopologyViolation(decision.reason)
        updated = topology.with_operational_status(status)
        await self._topologies.save(updated)
        return updated.to_view()
