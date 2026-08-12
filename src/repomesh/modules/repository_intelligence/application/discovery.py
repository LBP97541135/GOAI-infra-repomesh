"""LLM-assisted repository discovery with a deterministic fallback."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

import httpx
from opentelemetry import trace

from repomesh.modules.repository_intelligence.domain import (
    DiscoveryEvidence,
    RepositoryProfile,
    tokenize,
)
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog
from repomesh.telemetry import traced

_logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str: ...


@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    """Candidates plus the one thing their shape cannot tell you.

    The LLM path and the keyword fallback produce byte-identical
    ``DiscoveryEvidence``: same fields, same ranges. A consumer holding a 0.62
    cannot tell a model's judgement from a term-frequency ratio, and a panel
    that renders both as "score" is presenting arithmetic as an opinion.

    ``llm_used`` is the producing mechanism reporting on itself. Do not try to
    infer it from the data — "matched_terms is empty" is a signal that happens
    to correlate today, and correlated signals are not causes.
    """

    candidates: list[DiscoveryEvidence]
    llm_used: bool


class RepositoryDiscoveryService:
    def __init__(
        self,
        catalog: RepositoryCatalog,
        *,
        llm_client: LLMClient | None = None,
    ) -> None:
        self._catalog = catalog
        self._llm = llm_client

    @traced("planning.discovery")
    async def discover(
        self,
        requirement: str,
        *,
        limit: int = 5,
        entry_point: str | None = None,
        keywords: list[str] | None = None,
    ) -> list[DiscoveryEvidence]:
        profiles = await self._catalog.list()
        return self.score(
            profiles,
            requirement,
            limit=limit,
            entry_point=entry_point,
            keywords=keywords,
        ).candidates

    def score(
        self,
        profiles: list[RepositoryProfile],
        requirement: str,
        *,
        limit: int = 5,
        entry_point: str | None = None,
        keywords: list[str] | None = None,
    ) -> DiscoveryOutcome:
        """Rank *profiles* against *requirement*. Synchronous on purpose.

        Split out from :meth:`discover` so the only await — reading the catalog
        — happens on the caller's event loop while this part, which makes a
        blocking ``chat()`` call, can be handed to a worker thread. Running it
        inline on the loop stalls every other request on the process, including
        the polls asking how this one is doing.
        """

        by_name = {profile.name: profile for profile in profiles}
        results = self._discover_with_llm(requirement, profiles) if self._llm else []
        llm_used = bool(results)
        if not results:
            results = self._discover_with_keywords(requirement, profiles, keywords or [])
        span = trace.get_current_span()
        span.set_attribute("repomesh.discovery.llm_used", llm_used)
        span.set_attribute("repomesh.discovery.candidate_count", len(results))

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

        ranked = sorted(
            evidence_by_name.values(), key=lambda item: item.score, reverse=True
        )[:limit]
        return DiscoveryOutcome(candidates=ranked, llm_used=llm_used)

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
