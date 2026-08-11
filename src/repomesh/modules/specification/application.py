import json
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalReader, AgentRole
from repomesh.modules.context.contracts import (
    ContextObjectType,
    ContextPublisher,
    ContextScope,
    ContextVersionRef,
    PublishContextRequest,
)
from repomesh.modules.identity_access.contracts import (
    AgentAuthorizationGateway,
    AuthorizationAction,
    AuthorizationRequest,
)
from repomesh.modules.project.checkpoint_fallback import TopologyAwareCheckpointFallback
from repomesh.modules.project.contracts import (
    ProjectCheckpoint,
    ProjectCheckpointGateway,
    ProjectTopologyReader,
)
from repomesh.modules.specification.contracts import (
    ApproveSpecificationCommand,
    CreateSpecificationCommand,
    PublishSpecificationContextCommand,
    ReviseSpecificationCommand,
    SpecificationDiff,
    SpecificationKind,
    SpecificationView,
    SubmitSpecificationCommand,
)
from repomesh.modules.specification.domain import (
    Specification,
    SpecificationBlocked,
    SpecificationConflict,
    SpecificationContent,
    SpecificationDenied,
    SpecificationNotFound,
    SpecificationVersion,
)
from repomesh.modules.specification.ports import SpecificationStore
from repomesh.shared.domain import new_id
from repomesh.shared.idempotency import command_fingerprint

_CONTEXT_MAPPING = {
    SpecificationKind.ENGINEERING: (
        ContextObjectType.ENGINEERING_SPEC,
        ContextScope.PROJECT_SHARED,
    ),
    SpecificationKind.REPOSITORY: (
        ContextObjectType.ENGINEERING_SPEC,
        ContextScope.TEAM_PRIVATE,
    ),
    SpecificationKind.CONTRACT: (
        ContextObjectType.CONTRACT,
        ContextScope.PROJECT_SHARED,
    ),
    SpecificationKind.TASK: (
        ContextObjectType.TASK_SPEC,
        ContextScope.TASK_PRIVATE,
    ),
}


class SpecificationService:
    def __init__(
        self,
        directory: AgentPrincipalReader,
        topologies: ProjectTopologyReader,
        specifications: SpecificationStore,
        contexts: ContextPublisher,
        authorizer: AgentAuthorizationGateway,
        checkpoints: ProjectCheckpointGateway | None = None,
    ) -> None:
        self._directory = directory
        self._topologies = topologies
        self._specifications = specifications
        self._contexts = contexts
        self._authorizer = authorizer
        self._checkpoints = checkpoints or TopologyAwareCheckpointFallback(topologies)

    async def create(
        self, command: CreateSpecificationCommand, *, idempotency_key: str
    ) -> SpecificationView:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        fingerprint = command_fingerprint(command)
        if existing := await self._specifications.get_by_idempotency_key(key):
            specification, previous_fingerprint = existing
            if fingerprint != previous_fingerprint:
                raise SpecificationConflict(
                    "idempotency key was used for another specification"
                )
            return specification.to_view()
        actor, topology = await self._actor_and_topology(
            command.created_by_agent_id, command.project_id
        )
        object_type, scope = _CONTEXT_MAPPING[command.kind]
        self._authorize(
            actor,
            topology,
            AuthorizationAction.CONTEXT_PUBLISH,
            command.organization_id,
            command.project_id,
            command.repository_id,
            object_type,
            scope,
        )
        if command.kind in {SpecificationKind.ENGINEERING, SpecificationKind.CONTRACT}:
            if actor.role is not AgentRole.ORGANIZATION_LEADER:
                raise SpecificationDenied("project specifications require organization leader")
        elif actor.role is not AgentRole.REPOSITORY_LEADER:
            raise SpecificationDenied("repository specifications require repository leader")
        specification_id = new_id()
        version = SpecificationVersion(
            specification_id=specification_id,
            version=1,
            content=self._content(command),
            created_by_agent_id=actor.id,
        )
        specification = Specification(
            id=specification_id,
            organization_id=command.organization_id,
            project_id=command.project_id,
            repository_id=command.repository_id,
            task_id=command.task_id,
            kind=command.kind,
            title=command.title.strip(),
            owner_agent_id=actor.id,
            current_version=version,
        )
        await self._specifications.add(
            specification,
            idempotency_key=key,
            request_fingerprint=fingerprint,
        )
        return specification.to_view()

    async def revise(self, command: ReviseSpecificationCommand) -> SpecificationView:
        specification = await self._required(command.specification_id)
        if specification.revision != command.expected_revision:
            raise SpecificationConflict("specification revision changed")
        if specification.owner_agent_id != command.actor_agent_id:
            raise SpecificationDenied("only the specification owner can revise it")
        await self._authorize_owner_write(specification, command.actor_agent_id)
        version = SpecificationVersion(
            specification_id=specification.id,
            version=specification.current_version.version + 1,
            supersedes_version_id=specification.current_version.id,
            content=self._content(command),
            created_by_agent_id=command.actor_agent_id,
        )
        updated = specification.revise(version)
        await self._specifications.update(
            updated, expected_revision=specification.revision
        )
        return updated.to_view()

    async def submit(self, command: SubmitSpecificationCommand) -> SpecificationView:
        specification = await self._required(command.specification_id)
        if specification.revision != command.expected_revision:
            raise SpecificationConflict("specification revision changed")
        if specification.owner_agent_id != command.actor_agent_id:
            raise SpecificationDenied("only the specification owner can submit it")
        await self._authorize_owner_write(specification, command.actor_agent_id)
        updated = specification.submit()
        await self._specifications.update(
            updated, expected_revision=specification.revision
        )
        return updated.to_view()

    async def approve(self, command: ApproveSpecificationCommand) -> SpecificationView:
        specification = await self._required(command.specification_id)
        if specification.revision != command.expected_revision:
            raise SpecificationConflict("specification revision changed")
        actor, topology = await self._actor_and_topology(
            command.actor_agent_id, specification.project_id
        )
        object_type, scope = _CONTEXT_MAPPING[specification.kind]
        self._authorize(
            actor,
            topology,
            AuthorizationAction.CONTEXT_APPROVE,
            specification.organization_id,
            specification.project_id,
            specification.repository_id,
            object_type,
            scope,
        )
        updated = specification.approve(freeze=command.freeze)
        await self._specifications.update(
            updated, expected_revision=specification.revision
        )
        return updated.to_view()

    async def publish_to_context(
        self, command: PublishSpecificationContextCommand
    ) -> ContextVersionRef:
        specification = await self._required(command.specification_id)
        if specification.status.value not in {"approved", "frozen"}:
            raise SpecificationConflict("only approved specifications can be published")
        if command.actor_agent_id != specification.owner_agent_id:
            raise SpecificationDenied("only the specification owner can publish it")
        await self._authorize_owner_write(specification, command.actor_agent_id)
        if specification.kind is not SpecificationKind.TASK:
            gate = await self._checkpoints.evaluate(
                specification.project_id,
                ProjectCheckpoint.SPECIFICATION,
                specification.current_version.content_hash,
                repository_id=specification.repository_id,
            )
            if not gate.allowed:
                raise SpecificationBlocked(gate.reason)
        object_type, scope = _CONTEXT_MAPPING[specification.kind]
        spec_version = specification.current_version
        return await self._contexts.publish(
            PublishContextRequest(
            organization_id=specification.organization_id,
            project_id=specification.project_id,
            object_type=object_type,
            scope=scope,
            owner_subject=str(specification.owner_agent_id),
            title=specification.title,
            content_uri=(
                f"specification://{specification.id}/versions/{spec_version.version}"
            ),
            content_hash=spec_version.content_hash,
            mime_type="application/vnd.repomesh.specification+json",
            size_bytes=len(
                json.dumps(spec_version.content.canonical_payload()).encode()
            ),
            created_by=str(command.actor_agent_id),
            )
        )

    async def diff(
        self, specification_id: UUID, from_version: int, to_version: int
    ) -> SpecificationDiff:
        before = await self._specifications.get_version(specification_id, from_version)
        after = await self._specifications.get_version(specification_id, to_version)
        if before is None or after is None:
            raise SpecificationNotFound("specification version not found")
        before_payload = before.content.canonical_payload()
        after_payload = after.content.canonical_payload()
        return SpecificationDiff(
            specification_id=specification_id,
            from_version=from_version,
            to_version=to_version,
            changed_fields=tuple(
                key
                for key in sorted(before_payload.keys() | after_payload.keys())
                if before_payload.get(key) != after_payload.get(key)
            ),
        )

    async def _required(self, specification_id: UUID) -> Specification:
        specification = await self._specifications.get(specification_id)
        if specification is None:
            raise SpecificationNotFound(f"specification not found: {specification_id}")
        return specification

    async def _actor_and_topology(self, agent_id: UUID, project_id: UUID):
        actor = await self._directory.get_view(agent_id)
        if actor is None:
            raise SpecificationDenied("actor does not exist")
        topology = await self._topologies.get_view(project_id)
        if topology is None:
            raise SpecificationDenied("project topology does not exist")
        return actor, topology

    async def _authorize_owner_write(
        self, specification: Specification, actor_agent_id: UUID
    ) -> None:
        actor, topology = await self._actor_and_topology(
            actor_agent_id, specification.project_id
        )
        object_type, scope = _CONTEXT_MAPPING[specification.kind]
        self._authorize(
            actor,
            topology,
            AuthorizationAction.CONTEXT_PUBLISH,
            specification.organization_id,
            specification.project_id,
            specification.repository_id,
            object_type,
            scope,
        )

    def _authorize(
        self,
        actor,
        topology,
        action,
        organization_id,
        project_id,
        repository_id,
        object_type,
        scope,
    ) -> None:
        decision = self._authorizer.authorize(
            actor,
            AuthorizationRequest(
                action=action,
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repository_id,
                context_object_type=object_type,
                context_scope=scope,
            ),
            topology=topology,
        )
        if not decision.allowed:
            raise SpecificationDenied(decision.reason)

    @staticmethod
    def _content(command) -> SpecificationContent:
        return SpecificationContent(
            goal=command.goal.strip(),
            acceptance=tuple(item.strip() for item in command.acceptance),
            scope=tuple(item.strip() for item in command.scope),
            constraints=tuple(item.strip() for item in command.constraints),
            tests=tuple(item.strip() for item in command.tests),
            dependencies=tuple(item.strip() for item in command.dependencies),
            allowed_paths=tuple(item.strip() for item in command.allowed_paths),
            forbidden_paths=tuple(item.strip() for item in command.forbidden_paths),
            interface_changes=tuple(item.strip() for item in command.interface_changes),
        )
