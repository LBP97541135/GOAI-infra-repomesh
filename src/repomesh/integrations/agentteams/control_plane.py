import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from repomesh.modules.agent_runtime.ports.agent_team import (
    ManagerProjection,
    ManagerRuntimeRef,
    TeamProjection,
    TeamRole,
    TeamRuntimeRef,
    WorkerProjection,
    WorkerRuntimeRef,
)

AGENTTEAMS_VERSION = "v1.2.0"
AGENTTEAMS_COMMIT = "793db242257a569d911b1aa59c1cd554af78511f"
_RESOURCE_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,251}[a-z0-9])?$")


class AgentTeamsError(RuntimeError):
    pass


class AgentTeamsUnavailable(AgentTeamsError):
    pass


class AgentTeamsConflict(AgentTeamsError):
    pass


class AgentTeamsResponseError(AgentTeamsError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"AgentTeams HTTP {status_code}: {message}")
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class AgentTeamsVersion:
    controller: str
    kube_mode: str


class AgentTeamsControlPlaneClient:
    """Typed client for the AgentTeams v1.2 controller API."""

    #: Worker-document fields confirmed as JSON booleans rather than by
    #: equality. See ``_matches``; the set has one member because
    #: ``containerManaged`` is the one boolean anything turns on.
    _STRICT_BOOLEAN_FIELDS = frozenset({"containerManaged"})

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: float = 10.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        # The control-plane base URL is an in-cluster address (container DNS name
        # or loopback). A host-level HTTP_PROXY/HTTPS_PROXY must never intercept
        # these calls — honoring it silently hijacks the controller-DNS request
        # and surfaces as a spurious "AgentTeams unreachable" 503 (see E-1).
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            response = await self._client.get("/healthz")
        except httpx.HTTPError:
            return False
        return response.status_code == 200 and response.text.strip() == "ok"

    async def version(self) -> AgentTeamsVersion:
        body = await self._json("GET", "/api/v1/version")
        return AgentTeamsVersion(
            controller=str(body.get("controller", "")),
            kube_mode=str(body.get("kubeMode", "")),
        )

    async def ensure_manager(
        self, projection: ManagerProjection, *, idempotency_key: str
    ) -> ManagerRuntimeRef:
        path = self._resource_path("managers", projection.name)
        if existing := await self._get_optional(path):
            self._assert_manager_matches(existing, projection)
            return self._manager_ref(existing)
        payload: dict[str, Any] = {
            "name": projection.name,
            "model": projection.model,
            "runtime": projection.runtime.value,
            "skills": list(projection.skills),
        }
        self._set_optional(payload, "soul", projection.soul)
        self._set_optional(payload, "agents", projection.agents)
        self._set_optional(payload, "image", projection.image)
        body = await self._create_or_reconcile(
            "/api/v1/managers",
            path,
            payload,
            idempotency_key,
            lambda value: self._assert_manager_matches(value, projection),
        )
        return self._manager_ref(body)

    async def ensure_worker(
        self, projection: WorkerProjection, *, idempotency_key: str
    ) -> WorkerRuntimeRef:
        path = self._resource_path("workers", projection.name)
        if existing := await self._get_optional(path):
            self._assert_worker_matches(existing, projection)
            return self._worker_ref(existing)
        payload: dict[str, Any] = {
            "name": projection.name,
            "model": projection.model,
            "runtime": projection.runtime.value,
            "skills": list(projection.skills),
            "state": projection.state.value,
        }
        self._set_optional(payload, "identity", projection.identity)
        self._set_optional(payload, "soul", projection.soul)
        self._set_optional(payload, "agents", projection.agents)
        if not projection.container_managed:
            # Sent only by the explicit external path. The controller defaults
            # the field to true (``resource_handler.go``), so a managed worker's
            # payload stays byte-identical to the one RepoMesh has always sent
            # — two spellings of the same resource is how field-for-field
            # parity turns into a 409.
            payload["containerManaged"] = False
        if projection.mcp_servers:
            payload["mcpServers"] = [
                {
                    "name": server.name,
                    "url": server.url,
                    "transport": server.transport,
                }
                for server in projection.mcp_servers
            ]
        if projection.channel_policy is not None:
            payload["channelPolicy"] = {
                "groupAllowExtra": list(projection.channel_policy.group_allow_extra),
                "groupDenyExtra": list(projection.channel_policy.group_deny_extra),
                "dmAllowExtra": list(projection.channel_policy.dm_allow_extra),
                "dmDenyExtra": list(projection.channel_policy.dm_deny_extra),
            }
        body = await self._create_or_reconcile(
            "/api/v1/workers",
            path,
            payload,
            idempotency_key,
            lambda value: self._assert_worker_matches(value, projection),
        )
        return self._worker_ref(body)

    async def ensure_team(
        self, projection: TeamProjection, *, idempotency_key: str
    ) -> TeamRuntimeRef:
        path = self._resource_path("teams", projection.name)
        if existing := await self._get_optional(path):
            self._assert_team_matches(existing, projection)
            return self._team_ref(existing)
        payload: dict[str, Any] = {
            "name": projection.name,
            # The v1.2.0 controller rejects a Team without a named leader
            # (400 ``leader.name is required``): the leader is a first-class
            # field, not just the first ``workerMembers`` entry.
            "leader": {
                "name": next(
                    member.name
                    for member in projection.members
                    if member.role is TeamRole.LEADER
                )
            },
            "workerMembers": [
                {"name": member.name, "role": member.role.value} for member in projection.members
            ],
        }
        self._set_optional(payload, "description", projection.description)
        self._set_optional(payload, "heartbeatEvery", projection.heartbeat_every)
        body = await self._create_or_reconcile(
            "/api/v1/teams",
            path,
            payload,
            idempotency_key,
            lambda value: self._assert_team_matches(value, projection),
        )
        return self._team_ref(body)

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        body = await self._get_optional(self._resource_path("workers", name))
        return self._worker_ref(body) if body else None

    async def get_manager(self, name: str) -> ManagerRuntimeRef | None:
        body = await self._get_optional(self._resource_path("managers", name))
        return self._manager_ref(body) if body else None

    async def get_team(self, name: str) -> TeamRuntimeRef | None:
        body = await self._get_optional(self._resource_path("teams", name))
        return self._team_ref(body) if body else None

    async def ensure_worker_ready(self, name: str, *, idempotency_key: str) -> WorkerRuntimeRef:
        path = self._resource_path("workers", name) + "/ensure-ready"
        body = await self._json(
            "POST",
            path,
            headers=self._idempotency_headers(idempotency_key),
        )
        return self._worker_ref(body)

    async def sleep_worker(self, name: str, *, idempotency_key: str) -> WorkerRuntimeRef:
        path = self._resource_path("workers", name) + "/sleep"
        body = await self._json(
            "POST",
            path,
            headers=self._idempotency_headers(idempotency_key),
        )
        return self._worker_ref(body)

    async def _create_or_reconcile(
        self,
        collection_path: str,
        item_path: str,
        payload: dict[str, Any],
        idempotency_key: str,
        assert_matches: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        try:
            return await self._json(
                "POST",
                collection_path,
                json=payload,
                headers=self._idempotency_headers(idempotency_key),
                expected_status=201,
            )
        except AgentTeamsResponseError as error:
            if error.status_code != 409:
                raise
            existing = await self._get_optional(item_path)
            if existing is None:
                raise
            assert_matches(existing)
            return existing

    async def _get_optional(self, path: str) -> dict[str, Any] | None:
        try:
            return await self._json("GET", path)
        except AgentTeamsResponseError as error:
            if error.status_code == 404:
                return None
            raise

    async def _json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_status: int = 200,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=json, headers=headers)
        except httpx.HTTPError as error:
            raise AgentTeamsUnavailable(f"AgentTeams request failed: {method} {path}") from error
        if response.status_code != expected_status:
            message = self._error_message(response)
            raise AgentTeamsResponseError(response.status_code, message)
        try:
            body = response.json()
        except ValueError as error:
            raise AgentTeamsResponseError(response.status_code, "invalid JSON response") from error
        if not isinstance(body, dict):
            raise AgentTeamsResponseError(response.status_code, "expected a JSON object")
        return body

    @staticmethod
    def _idempotency_headers(idempotency_key: str) -> dict[str, str]:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required for AgentTeams side effects")
        return {"Idempotency-Key": key}

    @staticmethod
    def _set_optional(payload: dict[str, Any], key: str, value: str | None) -> None:
        if value:
            payload[key] = value

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text.strip()[:500] or "request failed"
        if isinstance(body, dict):
            return str(body.get("error") or body.get("message") or "request failed")[:500]
        return "request failed"

    @staticmethod
    def _resource_path(collection: str, name: str) -> str:
        if not _RESOURCE_NAME.fullmatch(name):
            raise ValueError(f"invalid AgentTeams resource name: {name}")
        return f"/api/v1/{collection}/{quote(name, safe='')}"

    @staticmethod
    def _assert_manager_matches(body: dict[str, Any], expected: ManagerProjection) -> None:
        fields = {"model": expected.model, "runtime": expected.runtime.value}
        # ``image`` is ``omitempty`` on the controller's manager document
        # (types.go), so a manager created before its projection named one
        # answers with the field absent — and that absence is precisely the
        # state the crash-loop bug lived in: the role-blind image fallback
        # hands an imageless manager the worker image, whose entrypoint
        # exits for want of ``AGENTTEAMS_WORKER_NAME``. A projection that
        # names an image therefore refuses over an absent one rather than
        # confirm what it cannot see (the containerManaged precedent), while
        # one that does not is untouched by the field.
        if expected.image is not None:
            if "image" not in body:
                raise AgentTeamsConflict(
                    f"existing AgentTeams manager does not confirm image: {expected.image}"
                )
            fields["image"] = expected.image
        AgentTeamsControlPlaneClient._assert_fields("manager", body, fields)

    @staticmethod
    def _assert_worker_matches(body: dict[str, Any], expected: WorkerProjection) -> None:
        fields: dict[str, Any] = {
            "model": expected.model,
            "runtime": expected.runtime.value,
        }
        # The v1.2.0 controller's GET document omits ``skills`` and
        # ``mcpServers`` (verified live 2026-08-13), so those two can only be
        # compared when the document actually carries them — an absent field
        # must not read as "empty", or every already-provisioned worker looks
        # like a skills conflict.
        if "skills" in body:
            fields["skills"] = list(expected.skills)
        if "mcpServers" in body:
            fields["mcpServers"] = [
                {
                    "name": server.name,
                    "url": server.url,
                    "transport": server.transport,
                }
                for server in expected.mcp_servers
            ]
        # A containerManaged mismatch is the one conflict that must never be
        # reconciled away: adopting a managed worker as external leaves a
        # container running under an identity a local process is serving, and
        # adopting an external one as managed starts a second body for it.
        # Absence is not agreement either — the v1.2.0 worker document always
        # carries the field, so a document without it cannot confirm anything,
        # and only the request that needs a confirmation is refused over it.
        if "containerManaged" in body:
            fields["containerManaged"] = expected.container_managed
        elif not expected.container_managed:
            raise AgentTeamsConflict(
                "existing AgentTeams worker does not confirm containerManaged: false"
            )
        if expected.identity:
            fields["identity"] = expected.identity
        if expected.soul:
            fields["soul"] = expected.soul
        if expected.agents:
            fields["agents"] = expected.agents
        if expected.channel_policy is not None:
            fields["channelPolicy"] = {
                "groupAllowExtra": list(expected.channel_policy.group_allow_extra),
                "groupDenyExtra": list(expected.channel_policy.group_deny_extra),
                "dmAllowExtra": list(expected.channel_policy.dm_allow_extra),
                "dmDenyExtra": list(expected.channel_policy.dm_deny_extra),
            }
        normalized = dict(body)
        if "skills" in body:
            normalized["skills"] = list(body.get("skills") or [])
        if "mcpServers" in body:
            normalized["mcpServers"] = list(body.get("mcpServers") or [])
        AgentTeamsControlPlaneClient._assert_fields("worker", normalized, fields)

    @staticmethod
    def _assert_team_matches(body: dict[str, Any], expected: TeamProjection) -> None:
        # Same omission as the worker document: the v1.2.0 controller's GET
        # response for a Team carries no ``workerMembers`` (the members live
        # on the Worker documents, each naming its Team), so it can only be
        # compared when the document actually includes it.
        fields: dict[str, Any] = {}
        if "workerMembers" in body:
            fields["workerMembers"] = [
                {"name": member.name, "role": member.role.value} for member in expected.members
            ]
        AgentTeamsControlPlaneClient._assert_fields("team", body, fields)

    @staticmethod
    def _matches(key: str, observed: Any, expected: Any) -> bool:
        """Whether the controller's value confirms the one being asked for.

        Equality, except for the fields in ``_STRICT_BOOLEAN_FIELDS``, which are
        compared against the JSON boolean itself. Python's ``0 == False`` is
        true, so a controller answering ``"containerManaged": 0`` used to
        *confirm* an external projection — and that confirmation is the single
        fact the bridge preflight is built on (ADR 0004 decision 5). Identity
        against ``True``/``False`` accepts the JSON literal and nothing that
        merely compares equal to it, which puts ``0``, ``"false"`` and ``null``
        in the same place absence already is: unable to confirm anything.

        Deliberately not applied to the rest. ``model``, ``runtime``,
        ``skills`` and the projections below are strings and lists, where
        equality says exactly what the operator means, and tightening them
        would refuse pairs that agree.
        """

        if key in AgentTeamsControlPlaneClient._STRICT_BOOLEAN_FIELDS:
            return observed is expected
        return observed == expected

    @staticmethod
    def _assert_fields(kind: str, body: dict[str, Any], fields: dict[str, Any]) -> None:
        mismatches = [
            key
            for key, value in fields.items()
            if not AgentTeamsControlPlaneClient._matches(key, body.get(key), value)
        ]
        if mismatches:
            joined = ", ".join(sorted(mismatches))
            raise AgentTeamsConflict(f"existing AgentTeams {kind} differs in: {joined}")

    @staticmethod
    def _manager_ref(body: dict[str, Any]) -> ManagerRuntimeRef:
        return ManagerRuntimeRef(
            name=str(body.get("name", "")),
            phase=str(body.get("phase", "")),
            room_id=body.get("roomID"),
            matrix_user_id=body.get("matrixUserID"),
        )

    @staticmethod
    def _worker_ref(body: dict[str, Any]) -> WorkerRuntimeRef:
        # Anything that is not a JSON boolean reads as "unknown" rather than as
        # a truthy value: the preflight binding turns on this field being
        # exactly False, and a string "false" must not get there.
        observed = body.get("containerManaged")
        return WorkerRuntimeRef(
            name=str(body.get("name", "")),
            phase=str(body.get("phase", "")),
            runtime=body.get("runtime"),
            room_id=body.get("roomID"),
            matrix_user_id=body.get("matrixUserID"),
            message=body.get("message"),
            # Verified against the deployed controller (v1.2.0, read-only GET
            # /api/v1/workers/rm-leader-b-checkout, 2026-08-12): the worker
            # document carries its Team by name. Reading it is what lets the
            # reconcile adopt an existing repository Team instead of walking
            # into the exclusive-membership 400 (A-8).
            team=body.get("team"),
            container_managed=observed if isinstance(observed, bool) else None,
        )

    @staticmethod
    def _team_ref(body: dict[str, Any]) -> TeamRuntimeRef:
        return TeamRuntimeRef(
            name=str(body.get("name", "")),
            phase=str(body.get("phase", "")),
            team_room_id=body.get("teamRoomID"),
            leader_room_id=body.get("leaderDMRoomID"),
            leader_name=str(body.get("leaderName", "")),
            ready_workers=int(body.get("readyWorkers", 0)),
            total_workers=int(body.get("totalWorkers", 0)),
        )
