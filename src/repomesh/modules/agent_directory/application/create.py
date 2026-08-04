import hashlib
import json
from dataclasses import asdict, dataclass
from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentCreated,
    AgentRole,
)
from repomesh.modules.agent_directory.domain import (
    ROLE_POLICIES,
    AgentAlreadyExists,
    AgentHierarchyViolation,
    AgentPolicyViolation,
    AgentPrincipal,
)
from repomesh.modules.agent_directory.ports import AgentDirectory
from repomesh.shared.domain import new_id
from repomesh.shared.events import ActorType, EventEnvelope


@dataclass(frozen=True, slots=True)
class CreateAgentRequest:
    organization_id: UUID
    role: AgentRole
    agentteams_resource_name: str
    leader_agent_id: UUID | None = None
    repository_id: UUID | None = None
    responsibility_paths: tuple[str, ...] = ()


class CreateAgent:
    """Register RepoMesh business identity for an existing AgentTeams resource."""

    def __init__(self, directory: AgentDirectory) -> None:
        self._directory = directory

    async def execute(
        self,
        request: CreateAgentRequest,
        *,
        idempotency_key: str,
        actor_type: ActorType = ActorType.SERVICE,
        actor_id: str = "repomesh-api",
    ) -> AgentCreated:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        fingerprint = self._fingerprint(request)
        if existing := await self._directory.get_by_idempotency_key(key):
            principal, existing_fingerprint = existing
            if existing_fingerprint != fingerprint:
                raise AgentAlreadyExists("idempotency key was used for a different agent request")
            return AgentCreated(principal.to_view(), key)

        leader = await self._validate_hierarchy(request)
        self._validate_scope(request, leader)
        principal = AgentPrincipal(
            organization_id=request.organization_id,
            role=request.role,
            leader_agent_id=request.leader_agent_id,
            singleton_key=self._singleton_key(request),
            repository_id=request.repository_id,
            responsibility_paths=request.responsibility_paths,
            agentteams_resource_name=request.agentteams_resource_name.strip(),
        )
        event = EventEnvelope(
            event_type="AgentPrincipalRegistered",
            actor_type=actor_type,
            actor_id=actor_id,
            aggregate_type="AgentPrincipal",
            aggregate_id=principal.id,
            aggregate_version=1,
            correlation_id=new_id(),
            payload={
                "role": principal.role.value,
                "leaderAgentId": (
                    str(principal.leader_agent_id) if principal.leader_agent_id else None
                ),
                "repositoryId": (
                    str(principal.repository_id) if principal.repository_id else None
                ),
                "agentteamsResourceName": principal.agentteams_resource_name,
            },
        )
        await self._directory.add(
            principal,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            events=(event,),
        )
        return AgentCreated(principal.to_view(), key)

    async def _validate_hierarchy(
        self, request: CreateAgentRequest
    ) -> AgentPrincipal | None:
        policy = ROLE_POLICIES[request.role]
        if not policy.allowed_parent_roles:
            if request.leader_agent_id is not None:
                raise AgentHierarchyViolation("organization leader cannot have a leader agent")
            return None
        if request.leader_agent_id is None:
            raise AgentHierarchyViolation(f"{request.role.value} requires a leader agent")
        leader = await self._directory.get(request.leader_agent_id)
        if leader is None:
            raise AgentHierarchyViolation("leader agent does not exist")
        if leader.organization_id != request.organization_id:
            raise AgentHierarchyViolation("leader agent belongs to another organization")
        if leader.role not in policy.allowed_parent_roles:
            raise AgentHierarchyViolation(
                f"{leader.role.value} cannot lead {request.role.value}"
            )
        return leader

    @staticmethod
    def _validate_scope(
        request: CreateAgentRequest, leader: AgentPrincipal | None
    ) -> None:
        policy = ROLE_POLICIES[request.role]
        if policy.requires_repository and request.repository_id is None:
            raise AgentPolicyViolation(f"{request.role.value} requires a repository")
        if policy.requires_repository and not request.responsibility_paths:
            raise AgentPolicyViolation(f"{request.role.value} requires responsibility paths")
        if (
            leader is not None
            and request.role is AgentRole.WORKER
            and leader.repository_id != request.repository_id
        ):
            raise AgentHierarchyViolation(
                "worker repository must match its repository leader"
            )

    @staticmethod
    def _singleton_key(request: CreateAgentRequest) -> str | None:
        if request.role is AgentRole.ORGANIZATION_LEADER:
            return f"organization:{request.organization_id}:leader"
        if request.role is AgentRole.REPOSITORY_LEADER:
            return f"repository:{request.repository_id}:leader"
        return None

    @staticmethod
    def _fingerprint(request: CreateAgentRequest) -> str:
        encoded = json.dumps(
            asdict(request), sort_keys=True, default=str, separators=(",", ":")
        ).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
