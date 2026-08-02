from collections.abc import Iterable

from repomesh.modules.agent_runtime.ports.agent_adapter import AdapterManifest, AgentAdapter

from .base import AdapterError
from .catalog import create_adapters


class AdapterNotFound(AdapterError):
    pass


class DuplicateAdapter(AdapterError):
    pass


class AdapterRegistry:
    def __init__(self, adapters: Iterable[AgentAdapter]) -> None:
        self._adapters: dict[str, AgentAdapter] = {}
        for adapter in adapters:
            adapter_id = adapter.manifest.id.strip()
            if not adapter_id:
                raise DuplicateAdapter("adapter id cannot be empty")
            if adapter_id in self._adapters:
                raise DuplicateAdapter(f"duplicate adapter id: {adapter_id}")
            self._adapters[adapter_id] = adapter

    def list_manifests(self) -> tuple[AdapterManifest, ...]:
        return tuple(self._adapters[adapter_id].manifest for adapter_id in sorted(self._adapters))

    def resolve(self, adapter_id: str) -> AgentAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError as error:
            raise AdapterNotFound(f"unknown adapter: {adapter_id}") from error


def build_default_registry() -> AdapterRegistry:
    return AdapterRegistry(create_adapters())
