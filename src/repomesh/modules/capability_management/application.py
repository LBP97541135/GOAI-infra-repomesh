from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalReader

from .contracts import AgentCapabilityBundle
from .presets import PresetCapabilityAssembler


class AgentCapabilityNotFound(LookupError):
    pass


class ResolveAgentCapabilities:
    """Resolve presets for a concrete registered agent at runtime."""

    def __init__(
        self,
        directory: AgentPrincipalReader,
        assembler: PresetCapabilityAssembler | None = None,
    ) -> None:
        self._directory = directory
        self._assembler = assembler or PresetCapabilityAssembler()

    async def execute(
        self,
        agent_id: UUID,
        *,
        task_features: frozenset[str] = frozenset(),
    ) -> AgentCapabilityBundle:
        principal = await self._directory.get_view(agent_id)
        if principal is None:
            raise AgentCapabilityNotFound(f"agent {agent_id} is not registered")
        return self._assembler.assemble(principal, task_features=task_features)

