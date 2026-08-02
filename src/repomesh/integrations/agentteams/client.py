from typing import Any

import httpx


class AgentTeamsError(RuntimeError):
    pass


class AgentTeamsClient:
    """Narrow HTTP boundary; AgentTeams resource details stay out of domain code."""

    def __init__(self, base_url: str, token: str | None = None, timeout: float = 10.0) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            response = await self._client.get("/health")
            return response.is_success
        except httpx.HTTPError:
            return False

    async def create_resource(self, resource: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post("/api/resources", json=resource)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise AgentTeamsError("AgentTeams resource creation failed") from exc

