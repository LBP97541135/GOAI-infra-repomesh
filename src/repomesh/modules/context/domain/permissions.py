from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from uuid import UUID

from repomesh.modules.context.contracts import ContextAction, ContextScope


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    action: ContextAction
    scope: ContextScope
    context_object_id: UUID
    repository_id: UUID | None = None
    path: str | None = None
    tool: str | None = None
    command_category: str | None = None
    network_target: str | None = None
    secret_purpose: str | None = None
    requested_at: datetime = field(default_factory=_utc_now)
    use_number: int = 1


@dataclass(frozen=True, slots=True)
class PermissionLayer:
    name: str
    actions: frozenset[ContextAction]
    scopes: frozenset[ContextScope]
    context_object_ids: frozenset[UUID] | None = None
    repository_ids: frozenset[UUID] | None = None
    path_patterns: tuple[str, ...] | None = None
    tools: frozenset[str] | None = None
    command_categories: frozenset[str] | None = None
    network_targets: frozenset[str] | None = None
    secret_purposes: frozenset[str] | None = None
    valid_until: datetime | None = None
    max_uses: int | None = None

    def rejection_reason(self, request: PermissionRequest) -> str | None:
        if request.action not in self.actions:
            return f"{self.name}:action"
        if request.scope not in self.scopes:
            return f"{self.name}:scope"
        if (
            self.context_object_ids is not None
            and request.context_object_id not in self.context_object_ids
        ):
            return f"{self.name}:context_object"
        if (
            request.repository_id is not None
            and self.repository_ids is not None
            and request.repository_id not in self.repository_ids
        ):
            return f"{self.name}:repository"
        if request.path is not None and self.path_patterns is not None:
            normalized_path = request.path.replace("\\", "/")
            if not any(fnmatchcase(normalized_path, pattern) for pattern in self.path_patterns):
                return f"{self.name}:path"
        for value, allowed, dimension in (
            (request.tool, self.tools, "tool"),
            (request.command_category, self.command_categories, "command_category"),
            (request.network_target, self.network_targets, "network_target"),
            (request.secret_purpose, self.secret_purposes, "secret_purpose"),
        ):
            if (
                value is not None
                and allowed is not None
                and value not in allowed
                and "*" not in allowed
            ):
                return f"{self.name}:{dimension}"
        if self.valid_until is not None and request.requested_at >= self.valid_until:
            return f"{self.name}:expired"
        if self.max_uses is not None and request.use_number > self.max_uses:
            return f"{self.name}:usage_limit"
        return None


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    reason: str


def evaluate_permission(
    request: PermissionRequest,
    *,
    layers: tuple[PermissionLayer, ...],
    explicit_denies: tuple[PermissionLayer, ...] = (),
) -> PermissionDecision:
    if not layers:
        return PermissionDecision(False, "no_permission_layers")
    for deny in explicit_denies:
        if deny.rejection_reason(request) is None:
            return PermissionDecision(False, f"explicit_deny:{deny.name}")
    for layer in layers:
        reason = layer.rejection_reason(request)
        if reason is not None:
            return PermissionDecision(False, reason)
    return PermissionDecision(True, "allowed")
