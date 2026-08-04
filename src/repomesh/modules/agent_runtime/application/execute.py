from datetime import UTC, datetime
from fnmatch import fnmatchcase

from repomesh.modules.agent_directory.contracts import AgentPrincipalReader
from repomesh.modules.agent_runtime.contracts import AuthorizedCodingRunRequest
from repomesh.modules.agent_runtime.ports import CodingAgent, CodingRunRequest, CodingRunResult
from repomesh.modules.identity_access.contracts import (
    AgentAuthorizationGateway,
    AuthorizationAction,
    AuthorizationRequest,
)
from repomesh.modules.project.contracts import ProjectTopologyReader


class CodingRunDenied(Exception):
    pass


class ExecuteCodingRun:
    def __init__(self, agent: CodingAgent) -> None:
        self._agent = agent

    async def execute(self, request: CodingRunRequest) -> CodingRunResult:
        return await self._agent.execute(request)


class ExecuteAuthorizedCodingRun:
    def __init__(
        self,
        agent: CodingAgent,
        directory: AgentPrincipalReader,
        topologies: ProjectTopologyReader,
        authorizer: AgentAuthorizationGateway,
    ) -> None:
        self._agent = agent
        self._directory = directory
        self._topologies = topologies
        self._authorizer = authorizer

    async def execute(self, request: AuthorizedCodingRunRequest) -> CodingRunResult:
        profile = await self._directory.get_view(request.agent_id)
        if profile is None:
            raise CodingRunDenied("agent_not_found")
        topology = await self._topologies.get_view(request.project_id)
        decision = self._authorizer.authorize(
            profile,
            AuthorizationRequest(
                action=AuthorizationAction.CODING_EXECUTE,
                organization_id=request.organization_id,
                project_id=request.project_id,
                repository_id=request.repository_id,
            ),
            topology=topology,
        )
        if not decision.allowed:
            raise CodingRunDenied(decision.reason)
        self._validate_grant(request)
        return await self._agent.execute(request.coding_request)

    @staticmethod
    def _validate_grant(request: AuthorizedCodingRunRequest) -> None:
        grant = request.context_grant
        if (
            grant.project_id != request.project_id
            or grant.repository_id != request.repository_id
            or grant.agent_id != request.agent_id
            or grant.run_id != request.coding_request.run_id
        ):
            raise CodingRunDenied("context_grant_binding_mismatch")
        if datetime.now(UTC) >= grant.expires_at:
            raise CodingRunDenied("context_grant_expired")
        for tool in request.requested_tools:
            if tool not in grant.allowed_tools and "*" not in grant.allowed_tools:
                raise CodingRunDenied(f"tool_denied:{tool}")
        for path in request.requested_paths:
            normalized = path.replace("\\", "/").lstrip("/")
            if any(fnmatchcase(normalized, pattern) for pattern in grant.denied_paths):
                raise CodingRunDenied(f"path_denied:{normalized}")
            if not any(fnmatchcase(normalized, pattern) for pattern in grant.allowed_paths):
                raise CodingRunDenied(f"path_not_allowed:{normalized}")
