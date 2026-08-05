"""LLM-assisted repository discovery with a deterministic fallback."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from repomesh.modules.repository_intelligence.domain import (
    DiscoveryEvidence,
    RepositoryProfile,
    tokenize,
)
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog

_logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str: ...


@dataclass(frozen=True, slots=True)
class DeepSeekConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    timeout_seconds: float = 60.0


class DeepSeekClient:
    def __init__(self, config: DeepSeekConfig) -> None:
        self._config = config

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        response = httpx.post(
            f"{self._config.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {self._config.api_key}"},
            json={
                "model": self._config.model,
                "messages": messages,
                "temperature": temperature,
            },
            timeout=self._config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return str(payload["choices"][0]["message"]["content"])


def make_llm_client(
    api_key: str | None,
    *,
    base_url: str = "https://api.deepseek.com/v1",
    model: str = "deepseek-chat",
) -> LLMClient | None:
    if not api_key:
        return None
    return DeepSeekClient(DeepSeekConfig(api_key=api_key, base_url=base_url, model=model))


class RepositoryDiscoveryService:
    def __init__(
        self,
        catalog: RepositoryCatalog,
        *,
        llm_client: LLMClient | None = None,
    ) -> None:
        self._catalog = catalog
        self._llm = llm_client

    async def discover(
        self,
        requirement: str,
        *,
        limit: int = 5,
        entry_point: str | None = None,
        keywords: list[str] | None = None,
    ) -> list[DiscoveryEvidence]:
        profiles = await self._catalog.list()
        by_name = {profile.name: profile for profile in profiles}
        results = self._discover_with_llm(requirement, profiles) if self._llm else []
        if not results:
            results = self._discover_with_keywords(requirement, profiles, keywords or [])

        evidence_by_name = {
            by_id.name: evidence
            for evidence in results
            if (by_id := next((p for p in profiles if p.id == evidence.repository_id), None))
        }
        if entry_point and entry_point in by_name:
            profile = by_name[entry_point]
            evidence_by_name[entry_point] = DiscoveryEvidence(
                repository_id=profile.id,
                matched_terms=(),
                score=1.0,
                rationale="User-specified entry point",
                is_entry_point=True,
            )

        return sorted(evidence_by_name.values(), key=lambda item: item.score, reverse=True)[:limit]

    def _discover_with_llm(
        self, requirement: str, profiles: list[RepositoryProfile]
    ) -> list[DiscoveryEvidence]:
        assert self._llm is not None
        messages = _build_discovery_prompt(requirement, profiles)
        try:
            raw = self._llm.chat(messages, temperature=0.0)
            candidates = _parse_candidates(raw)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            _logger.exception("LLM repository discovery failed; using keyword fallback")
            return []

        by_name = {profile.name: profile for profile in profiles}
        results: list[DiscoveryEvidence] = []
        for candidate in candidates:
            name = str(candidate.get("repository", ""))
            profile = by_name.get(name)
            if profile is None:
                continue
            score = max(0.0, min(1.0, float(candidate.get("confidence", 0.0))))
            results.append(
                DiscoveryEvidence(
                    repository_id=profile.id,
                    matched_terms=(),
                    score=score,
                    rationale=str(candidate.get("rationale", "LLM recommendation")),
                )
            )
        return results

    @staticmethod
    def _discover_with_keywords(
        requirement: str,
        profiles: list[RepositoryProfile],
        extra_keywords: list[str],
    ) -> list[DiscoveryEvidence]:
        terms = tokenize(" ".join((requirement, *extra_keywords)))
        results: list[DiscoveryEvidence] = []
        for profile in profiles:
            matched = tuple(sorted(terms & tokenize(profile.searchable_text)))
            if not matched:
                continue
            score = min(0.99, len(matched) / max(1, len(terms)))
            results.append(
                DiscoveryEvidence(
                    repository_id=profile.id,
                    matched_terms=matched,
                    score=score,
                    rationale=f"Matched repository signals: {', '.join(matched)}",
                )
            )
        return results


def _build_discovery_prompt(
    requirement: str, profiles: list[RepositoryProfile]
) -> list[dict[str, str]]:
    repositories = [
        {"name": profile.name, "signals": profile.searchable_text} for profile in profiles
    ]
    return [
        {
            "role": "system",
            "content": (
                "Select repositories that may require code changes. Return only a JSON array "
                "of objects with repository, confidence, and rationale fields. Prefer recall."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"requirement": requirement, "repositories": repositories},
                ensure_ascii=False,
            ),
        },
    ]


def _parse_candidates(raw: str) -> list[dict[str, object]]:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if fenced:
        raw = fenced.group(1)
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end < start:
        raise ValueError("No JSON candidate array found")
    value = json.loads(raw[start : end + 1])
    if not isinstance(value, list):
        raise ValueError("Candidate response must be a list")
    return [item for item in value if isinstance(item, dict)]
