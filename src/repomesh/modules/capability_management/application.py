from pathlib import Path
from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalStatus,
)

from .contracts import AgentCapabilityBundle
from .presets import PresetCapabilityAssembler
from .registry import PostgresSkillRegistry


class AgentCapabilityNotFound(LookupError):
    pass


class ResolveAgentCapabilities:
    """Resolve presets for a concrete registered agent at runtime."""

    def __init__(
        self,
        directory: AgentPrincipalReader,
        assembler: PresetCapabilityAssembler | None = None,
        registry: PostgresSkillRegistry | None = None,
        capability_root: Path = Path("."),
    ) -> None:
        self._directory = directory
        self._assembler = assembler or PresetCapabilityAssembler()
        self._registry = registry
        self._capability_root = capability_root

    async def execute(
        self,
        agent_id: UUID,
        *,
        task_features: frozenset[str] = frozenset(),
        task_id: UUID | None = None,
        run_id: UUID | None = None,
    ) -> AgentCapabilityBundle:
        principal = await self._directory.get_view(agent_id)
        if principal is None:
            raise AgentCapabilityNotFound(f"agent {agent_id} is not registered")
        if principal.status is not AgentPrincipalStatus.ACTIVE:
            raise AgentCapabilityNotFound(f"agent {agent_id} is disabled")
        bundle = self._assembler.assemble(principal, task_features=task_features)
        if self._registry is None or task_id is None:
            return bundle
        skills = []
        for skill in bundle.skills:
            await self._registry.bootstrap_definition(skill, self._capability_root)
            skills.append(await self._registry.resolve(skill, task_id, run_id=run_id))
        return AgentCapabilityBundle(bundle.role, tuple(skills), bundle.mcp_servers)

