from dataclasses import dataclass
from fnmatch import fnmatchcase
from uuid import UUID

from repomesh.modules.project.contracts import (
    CodeAccessLevel,
    HumanControlAction,
    ProjectAgentTopologyView,
    ProjectCheckpoint,
    ProjectExecutionMode,
)


@dataclass(frozen=True, slots=True)
class HumanAuthorizationRequest:
    human_principal_id: UUID
    action: HumanControlAction | None = None
    repository_id: UUID | None = None
    path: str | None = None
    code_access: CodeAccessLevel = CodeAccessLevel.NONE


@dataclass(frozen=True, slots=True)
class HumanAuthorizationDecision:
    allowed: bool
    reason: str


def authorize_human(
    topology: ProjectAgentTopologyView,
    request: HumanAuthorizationRequest,
) -> HumanAuthorizationDecision:
    grants = tuple(
        item
        for item in topology.human_grants
        if item.human_principal_id == request.human_principal_id
    )
    if not grants:
        return HumanAuthorizationDecision(False, "human_project_membership_denied")
    scoped = tuple(
        grant
        for grant in grants
        if grant.repository_id is None or grant.repository_id == request.repository_id
    )
    if not scoped:
        return HumanAuthorizationDecision(False, "human_repository_scope_denied")
    normalized = request.path.replace("\\", "/").lstrip("/") if request.path else None
    for grant in scoped:
        if request.action is not None and request.action not in grant.control_actions:
            continue
        if _access_rank(request.code_access) > _access_rank(grant.code_access):
            continue
        if normalized is not None and grant.path_patterns and not any(
            fnmatchcase(normalized, pattern) for pattern in grant.path_patterns
        ):
            continue
        return HumanAuthorizationDecision(True, "allowed")
    return HumanAuthorizationDecision(False, "human_grant_does_not_allow_request")


def requires_human_checkpoint(
    topology: ProjectAgentTopologyView, checkpoint: ProjectCheckpoint
) -> bool:
    if topology.execution_mode is ProjectExecutionMode.AUTO:
        return False
    if checkpoint is ProjectCheckpoint.EXCEPTION_ESCALATION:
        return True
    return checkpoint in topology.required_checkpoints


def _access_rank(level: CodeAccessLevel) -> int:
    return {
        CodeAccessLevel.NONE: 0,
        CodeAccessLevel.READ: 1,
        CodeAccessLevel.WRITE: 2,
    }[level]
