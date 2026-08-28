"""OpenAI-compatible embeddings adapter (L3 semantic retrieval).

The endpoint speaks the ``POST /embeddings`` shape shared by OpenAI, local
Ollama (``/v1/embeddings``) and SiliconFlow — the concrete provider is
configuration, not code. The client is deliberately small: one ``embed``
call, no retries and no batching policy. The decision_chain embedding
service owns batch/retry semantics (落地方案 B8: the write path never calls
this — the batch refresher does, and the query vector is produced on read).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    base_url: str
    api_key: str | None
    model: str = "text-embedding-3-small"
    timeout_seconds: float = 30.0


class OpenAICompatibleEmbeddings:
    """Satisfies the ``EmbeddingLookup`` protocol the decision_chain declares."""

    def __init__(self, config: EmbeddingConfig) -> None:
        self._config = config

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Vectorize ``texts``; one embedding per input, input order preserved.

        Responses carry ``data`` entries with an ``index`` — sort by it so the
        caller can zip results back onto its input list without relying on
        provider ordering luck.
        """
        headers = {}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
            response = await client.post(
                f"{self._config.base_url.rstrip('/')}/embeddings",
                headers=headers,
                json={"model": self._config.model, "input": texts},
            )
            response.raise_for_status()
            payload = response.json()
        entries = sorted(payload["data"], key=lambda item: int(item.get("index") or 0))
        return [list(entry["embedding"]) for entry in entries]


def make_embedding_client(
    base_url: str | None,
    *,
    api_key: str | None = None,
    model: str = "text-embedding-3-small",
    timeout_seconds: float = 30.0,
) -> OpenAICompatibleEmbeddings | None:
    """Composition-root factory: no base URL ⇒ semantic retrieval disabled."""
    if not base_url:
        return None
    return OpenAICompatibleEmbeddings(
        EmbeddingConfig(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )
    )
