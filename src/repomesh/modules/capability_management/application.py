from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalStatus,
    AgentPrincipalView,
)

from .contracts import AgentCapabilityBundle, CapabilityDefinition
from .presets import SKILLS, PresetCapabilityAssembler


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
        if principal.status is not AgentPrincipalStatus.ACTIVE:
            raise AgentCapabilityNotFound(f"agent {agent_id} is disabled")
        return self._assembler.assemble(principal, task_features=task_features)


class RegistryCapabilityAssembler:
    """Wrap the preset assembler with registry-resolved skill versions.

    Fail-closed: a preset skill with no promoted (or canary-for-this-org)
    version in the registry refuses the bundle rather than silently mounting
    an unversioned file. MCP capability definitions pass through unchanged —
    their governance is the runtime policy store, not the version pipeline.
    """

    def __init__(self, registry, *, delegate: PresetCapabilityAssembler | None = None) -> None:
        self._registry = registry
        self._delegate = delegate or PresetCapabilityAssembler()

    async def assemble(
        self,
        principal: AgentPrincipalView,
        *,
        task_features: frozenset[str] = frozenset(),
    ) -> AgentCapabilityBundle:
        bundle = self._delegate.assemble(principal, task_features=task_features)
        organization_id = getattr(principal, "organization_id", None)
        resolved: list[CapabilityDefinition] = []
        for skill in bundle.skills:
            record = await self._registry.resolve_current(skill.id, organization_id)
            if record is None:
                raise AgentCapabilityNotFound(
                    f"skill {skill.id} has no promoted version in the registry"
                )
            resolved.append(
                CapabilityDefinition(
                    id=skill.id,
                    kind=skill.kind,
                    title=skill.title,
                    source=skill.source,
                    access=skill.access,
                    allowed_roles=skill.allowed_roles,
                    allowed_operations=skill.allowed_operations,
                    denied_operations=skill.denied_operations,
                    local_path=skill.local_path,
                    conditional_on=skill.conditional_on,
                    version=record.version,
                )
            )
        return AgentCapabilityBundle(bundle.role, tuple(resolved), bundle.mcp_servers)


async def seed_preset_skills(registry) -> None:
    """Idempotently register the twelve preset skills as promoted 1.0.0.

    Seeds only when the registry has no row for that (skill_id, version): an
    operator who later registers 1.1.0 and rolls it back must not find their
    history overwritten on the next boot.
    """

    for skill_id, definition in SKILLS.items():
        await registry.seed_promoted(
            skill_id=skill_id,
            local_path=definition.local_path or f"capabilities/skills/{skill_id}/SKILL.md",
        )

