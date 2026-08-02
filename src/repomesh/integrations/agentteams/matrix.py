from urllib.parse import quote

import httpx

from .control_plane import AgentTeamsResponseError, AgentTeamsUnavailable


class AgentTeamsMatrixClient:
    """Sends idempotent tasks into AgentTeams rooms using Matrix client-server API v3."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        timeout: float = 10.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not access_token.strip():
            raise ValueError("Matrix access token is required")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=timeout,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def send_task(
        self,
        room_id: str,
        body: str,
        *,
        transaction_id: str,
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

        path = (
            f"/_matrix/client/v3/rooms/{quote(room, safe='')}/send/"
            f"m.room.message/{quote(transaction, safe='')}"
        )
        try:
            response = await self._client.put(
                path,
                json={"msgtype": "m.text", "body": message},
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
