"""Fail-closed checkpoint fallback for legacy constructor call sites."""

from uuid import UUID

from repomesh.modules.project.contracts import (
    CheckpointGateDecision,
    ProjectCheckpoint,
    ProjectExecutionMode,
    ProjectOperationalStatus,
    ProjectTopologyReader,
)


class TopologyAwareCheckpointFallback:
    """Allow only active automatic projects when the real gateway is absent."""

    def __init__(self, topologies: ProjectTopologyReader) -> None:
        self._topologies = topologies

    async def operational_gate(self, project_id: UUID) -> CheckpointGateDecision:
        topology = await self._topologies.get_view(project_id)
        if topology is None:
            return CheckpointGateDecision(False, "project_topology_missing")
        if topology.operational_status is ProjectOperationalStatus.PAUSED:
            return CheckpointGateDecision(False, "project_paused")
        if topology.operational_status is ProjectOperationalStatus.CANCELLED:
            return CheckpointGateDecision(False, "project_cancelled")
        if topology.execution_mode is not ProjectExecutionMode.AUTO:
            return CheckpointGateDecision(False, "checkpoint_gateway_not_configured")
        return CheckpointGateDecision(True, "automatic_project")

    async def evaluate(
        self,
        project_id: UUID,
        checkpoint: ProjectCheckpoint,
        evidence_version: str,
        *,
        repository_id: UUID | None = None,
        requested_by_agent_id: UUID | None = None,
        title: str | None = None,
        summary: str | None = None,
    ) -> CheckpointGateDecision:
        del checkpoint, evidence_version, repository_id, requested_by_agent_id, title, summary
        return await self.operational_gate(project_id)
