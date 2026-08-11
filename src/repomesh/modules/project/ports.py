from typing import Protocol
from uuid import UUID

from repomesh.modules.project.contracts import (
    CheckpointDecisionKind,
    HumanReviewStatus,
    ProjectCheckpoint,
)
from repomesh.modules.project.domain import (
    HumanReviewRequest,
    ProjectAgentTopology,
    ProjectCheckpointDecision,
)


class ProjectTopologyStore(Protocol):
    async def add(
        self,
        topology: ProjectAgentTopology,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None: ...

    async def get(self, project_id: UUID) -> ProjectAgentTopology | None: ...

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[ProjectAgentTopology, str] | None: ...

    async def save(self, topology: ProjectAgentTopology) -> None: ...


class ProjectCheckpointDecisionStore(Protocol):
    async def add(self, decision: ProjectCheckpointDecision) -> None: ...

    async def latest(
        self,
        project_id: UUID,
        checkpoint: ProjectCheckpoint,
        repository_id: UUID | None,
    ) -> ProjectCheckpointDecision | None: ...


class HumanReviewRequestStore(Protocol):
    async def ensure(self, request: HumanReviewRequest) -> HumanReviewRequest: ...

    async def get_exact(
        self,
        project_id: UUID,
        checkpoint: ProjectCheckpoint,
        repository_id: UUID | None,
        evidence_version: str,
    ) -> HumanReviewRequest | None: ...

    async def get_by_id(self, review_request_id: UUID) -> HumanReviewRequest | None: ...

    async def list_for_human(
        self, human_principal_id: UUID, *, status: HumanReviewStatus | None = None
    ) -> tuple[HumanReviewRequest, ...]: ...

    async def list_all(
        self, *, status: HumanReviewStatus | None = None
    ) -> tuple[HumanReviewRequest, ...]: ...

    async def resolve_pending(
        self,
        review_request_id: UUID,
        decision: CheckpointDecisionKind,
        human_principal_id: UUID,
    ) -> bool: ...
