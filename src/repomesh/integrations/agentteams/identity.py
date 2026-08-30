from typing import Protocol
from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.ports.agent_team import AgentTeamControlPlane


class RecipientMatrixIdentityResolver(Protocol):
    async def resolve(self, role: AgentRole, resource_name: str) -> str | None: ...


async def _runtime_for_role(
    control_plane: AgentTeamControlPlane,
    role: AgentRole,
    resource_name: str,
):
    if role is AgentRole.ORGANIZATION_LEADER:
        return await control_plane.get_manager(resource_name)
    if role in {AgentRole.REPOSITORY_LEADER, AgentRole.WORKER}:
        return await control_plane.get_worker(resource_name)
    raise ValueError(f"unsupported AgentTeams recipient role: {role!r}")


class AgentTeamsRecipientMatrixIdentityResolver:
    """Resolve a known recipient through its role's AgentTeams collection."""

    def __init__(self, control_plane: AgentTeamControlPlane) -> None:
        self._control_plane = control_plane

    async def resolve(self, role: AgentRole, resource_name: str) -> str | None:
        runtime = await _runtime_for_role(self._control_plane, role, resource_name)
        return runtime.matrix_user_id if runtime is not None else None


class AgentTeamsMatrixIdentityVerifier:
    def __init__(self, control_plane: AgentTeamControlPlane) -> None:
        self._control_plane = control_plane

    async def verify(self, profile: AgentPrincipalView, matrix_user_id: str) -> bool:
        runtime = await _runtime_for_role(
            self._control_plane,
            profile.role,
            profile.agentteams_resource_name,
        )
        return runtime is not None and runtime.matrix_user_id == matrix_user_id


class AgentTeamsMatrixIdentityResolver:
    """Matrix user id -> the principal behind it, or None.

    The verifier above answers the same question in the direction the
    controller supports natively: given a principal, ask the control plane for
    its runtime and compare. Nothing in AgentTeams answers the reverse — there
    is no "who owns this Matrix user" endpoint — so a resolver has to build the
    reverse map itself, which is why this is a separate object rather than a
    second method on the verifier. Keeping them apart also keeps the verifier
    what it is: a proof used on the path that decides whether to trust a
    report, never a guess.

    The map is cached because the alternative is one control-plane round trip
    per principal per inbound message, on a poller that sees every message in
    every room. It is rebuilt on a miss, which is the only case where a
    membership change can matter: a Matrix user we already know keeps
    resolving, and one we have never seen triggers exactly one refresh before
    being reported unknown. A principal the controller does not know, or one
    with no Matrix identity yet, simply does not enter the map — its messages
    resolve to None and are recorded under their raw Matrix handle (D-4).

    A control plane that is *unreachable* raises rather than resolving to
    None, and that is the safer failure: the poller retries the batch, so the
    message is recorded once the controller answers. Degrading to None instead
    would write "unknown sender" for a transient outage — and because a
    recorded event is never re-resolved on replay, that wrong attribution
    would be permanent.
    """

    def __init__(
        self,
        directory: AgentPrincipalReader,
        control_plane: AgentTeamControlPlane,
    ) -> None:
        self._directory = directory
        self._control_plane = control_plane
        self._cache: dict[str, UUID] = {}

    async def resolve(self, matrix_user_id: str) -> UUID | None:
        user_id = matrix_user_id.strip()
        if not user_id:
            return None
        if (known := self._cache.get(user_id)) is not None:
            return known
        await self._refresh()
        return self._cache.get(user_id)

    async def _refresh(self) -> None:
        rebuilt: dict[str, UUID] = {}
        for profile in await self._directory.list_views():
            runtime = await self._runtime(profile)
            if runtime is None or not runtime.matrix_user_id:
                continue
            # First writer wins. Two principals claiming one Matrix user is a
            # provisioning fault, and picking the later one at random would
            # make the attribution of a message depend on registry order.
            rebuilt.setdefault(runtime.matrix_user_id, profile.id)
        self._cache = rebuilt

    async def _runtime(self, profile: AgentPrincipalView):
        return await _runtime_for_role(
            self._control_plane,
            profile.role,
            profile.agentteams_resource_name,
        )
