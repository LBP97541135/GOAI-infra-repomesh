from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .control_plane import AgentTeamsResponseError, AgentTeamsUnavailable


@dataclass(frozen=True, slots=True)
class MatrixRoomMessage:
    event_id: str
    room_id: str
    sender: str
    body: str


@dataclass(frozen=True, slots=True)
class MatrixSyncBatch:
    next_batch: str
    messages: tuple[MatrixRoomMessage, ...]


class AgentTeamsMatrixClient:
    """Sends idempotent tasks into AgentTeams rooms using Matrix client-server API v3."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        timeout: float = 10.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        control_plane=None,
    ) -> None:
        if not access_token.strip():
            raise ValueError("Matrix access token is required")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=timeout,
            transport=transport,
        )
        self._control_plane = control_plane

    async def close(self) -> None:
        await self._client.aclose()

    async def whoami(self) -> str:
        try:
            response = await self._client.get("/_matrix/client/v3/account/whoami")
        except httpx.HTTPError as error:
            raise AgentTeamsUnavailable("Matrix whoami failed") from error
        if response.status_code != 200:
            raise AgentTeamsResponseError(response.status_code, "Matrix whoami failed")
        payload = response.json()
        user_id = payload.get("user_id") if isinstance(payload, dict) else None
        if not isinstance(user_id, str) or not user_id:
            raise AgentTeamsResponseError(200, "Matrix whoami response missing user_id")
        return user_id

    async def sync_once(
        self,
        *,
        since: str | None = None,
        timeout_ms: int = 0,
    ) -> MatrixSyncBatch:
        params: dict[str, str | int] = {"timeout": max(0, timeout_ms)}
        if since:
            params["since"] = since
        # Use a timeout slightly longer than the long-poll window so
        # httpx doesn't abort before Matrix responds.
        sync_timeout = max(10.0, (timeout_ms / 1000.0) + 5.0)
        try:
            response = await self._client.get(
                "/_matrix/client/v3/sync",
                params=params,
                timeout=sync_timeout,
            )
        except httpx.HTTPError as error:
            raise AgentTeamsUnavailable("Matrix sync failed") from error
        if response.status_code != 200:
            raise AgentTeamsResponseError(response.status_code, "Matrix sync failed")
        try:
            payload = response.json()
        except ValueError as error:
            raise AgentTeamsResponseError(200, "invalid Matrix sync JSON") from error
        next_batch = payload.get("next_batch") if isinstance(payload, dict) else None
        if not isinstance(next_batch, str) or not next_batch:
            raise AgentTeamsResponseError(200, "Matrix sync response missing next_batch")
        messages = []
        joined = payload.get("rooms", {}).get("join", {})
        if isinstance(joined, dict):
            for room_id, room in joined.items():
                events = (
                    room.get("timeline", {}).get("events", [])
                    if isinstance(room, dict)
                    else []
                )
                for event in events:
                    content = event.get("content", {}) if isinstance(event, dict) else {}
                    if event.get("type") != "m.room.message" or content.get("msgtype") != "m.text":
                        continue
                    event_id = event.get("event_id")
                    sender = event.get("sender")
                    body = content.get("body")
                    if all(isinstance(value, str) and value for value in (event_id, sender, body)):
                        messages.append(MatrixRoomMessage(event_id, room_id, sender, body))
        return MatrixSyncBatch(next_batch, tuple(messages))

    async def send_task(
        self,
        room_id: str,
        body: str,
        *,
        transaction_id: str,
        recipient_resource_name: str | None = None,
    ) -> str:
        room = room_id.strip()
        message = body.strip()
        transaction = transaction_id.strip()
        if not room:
            raise ValueError("AgentTeams room_id is required")
        if not message:
            raise ValueError("AgentTeams task body is required")
        if not transaction:
            raise ValueError("transaction_id is required for idempotent Matrix delivery")

        recipient_matrix_id = None
        if recipient_resource_name and self._control_plane is not None:
            worker = await self._control_plane.get_worker(recipient_resource_name)
            recipient_matrix_id = worker.matrix_user_id if worker else None
            if not recipient_matrix_id:
                raise AgentTeamsUnavailable("AgentTeams recipient Matrix identity is unavailable")
            message = f"{recipient_matrix_id} {message}"
        path = (
            f"/_matrix/client/v3/rooms/{quote(room, safe='')}/send/"
            f"m.room.message/{quote(transaction, safe='')}"
        )
        try:
            response = await self._client.put(
                path,
                json={
                    "msgtype": "m.text",
                    "body": message,
                    **(
                        {"m.mentions": {"user_ids": [recipient_matrix_id]}}
                        if recipient_matrix_id
                        else {}
                    ),
                },
            )
        except httpx.HTTPError as error:
            raise AgentTeamsUnavailable("AgentTeams Matrix task delivery failed") from error
        if response.status_code != 200:
            raise AgentTeamsResponseError(response.status_code, "Matrix task delivery failed")
        try:
            payload = response.json()
        except ValueError as error:
            raise AgentTeamsResponseError(200, "invalid Matrix JSON response") from error
        event_id = payload.get("event_id") if isinstance(payload, dict) else None
        if not isinstance(event_id, str) or not event_id:
            raise AgentTeamsResponseError(200, "Matrix response missing event_id")
        return event_id
