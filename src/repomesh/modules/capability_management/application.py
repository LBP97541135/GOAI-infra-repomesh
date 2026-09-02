import inspect
from collections.abc import Awaitable
from typing import Protocol
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


class CapabilityAssembler(Protocol):
    """What :class:`ResolveAgentCapabilities` needs from an assembler.

    Two implementations exist: the preset assembler answers synchronously, the
    registry-backed one has to await the version store. The resolver accepts
    either, so the container can swap them without the callers noticing.
    """

    def assemble(
        self,
        principal: AgentPrincipalView,
        *,
        task_features: frozenset[str] = ...,
        profile: str | None = ...,
    ) -> AgentCapabilityBundle | Awaitable[AgentCapabilityBundle]: ...


class ResolveAgentCapabilities:
    """Resolve presets for a concrete registered agent at runtime."""

    def __init__(
        self,
        directory: AgentPrincipalReader,
        assembler: CapabilityAssembler | None = None,
    ) -> None:
        self._directory = directory
        self._assembler: CapabilityAssembler = assembler or PresetCapabilityAssembler()

    async def execute(
        self,
        agent_id: UUID,
        *,
        task_features: frozenset[str] = frozenset(),
        profile: str | None = None,
    ) -> AgentCapabilityBundle:
        principal = await self._directory.get_view(agent_id)
        if principal is None:
            raise AgentCapabilityNotFound(f"agent {agent_id} is not registered")
        if principal.status is not AgentPrincipalStatus.ACTIVE:
            raise AgentCapabilityNotFound(f"agent {agent_id} is disabled")
        bundle = self._assembler.assemble(principal, task_features=task_features, profile=profile)
        if inspect.isawaitable(bundle):
            bundle = await bundle
        return bundle


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
        profile: str | None = None,
    ) -> AgentCapabilityBundle:
        # The repository's capability profile (档案开关) decides which extra
        # skills the presets carry, so it has to reach the delegate; the
        # registry lookup below then versions whatever the presets answered.
        bundle = self._delegate.assemble(principal, task_features=task_features, profile=profile)
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

