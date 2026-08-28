"""LLM-assisted repository discovery with a deterministic fallback."""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, replace
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
        keyword_score_cap: float = 0.99,
    ) -> None:
        self._catalog = catalog
        self._llm = llm_client
        #: Ceiling for the keyword-fallback path, injected from settings at
        #: the composition root. Kept below 1.0 so a model verdict reaching
        #: full confidence stays distinguishable from term-frequency
        #: arithmetic.
        self._keyword_score_cap = keyword_score_cap

    @traced("planning.discovery")
    async def discover(
        self,
        requirement: str,
        *,
        limit: int,
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

    @staticmethod
    def _scannable(profiles: list[RepositoryProfile]) -> list[RepositoryProfile]:
        """Profiles whose scan actually produced a card.

        ``scan_status != "ok"`` means the scan failed or was skipped, so the
        profile carries no trustworthy signal — ranking it would rank noise.
        Registered rows are ``ok`` by construction, so this is a guard for
        in-memory scan pipelines, not a query optimisation.
        """
        return [p for p in profiles if p.scan_status == "ok"]

    @staticmethod
    def _low_signal(profile: RepositoryProfile) -> bool:
        """Whether the profile carries signal a scorer could lean on.

        A scanned profile already carries the scan's own verdict:
        ``auto_card.low_signal`` scores the name, directories, dependencies
        and commits for business signal and flags cards whose confident
        matching would be a guess. Reusing it keeps one definition instead
        of a second, subtly different one.

        Profiles without a card (in-memory catalogs, tests) fall back to the
        facade: with description/topics/languages all empty, whatever scores
        it (model or keyword matcher) is judging a name and nothing else —
        the number is a guess. Flag it so the panel can say so instead of
        presenting the score as a well-supported verdict.
        """
        card = profile.auto_card
        if card is not None:
            return card.low_signal
        return not profile.description and not profile.topics and not profile.languages

    def score(
        self,
        profiles: list[RepositoryProfile],
        requirement: str,
        *,
        limit: int,
        entry_point: str | None = None,
        keywords: list[str] | None = None,
    ) -> DiscoveryOutcome:
        """Rank *profiles* against *requirement*. Synchronous on purpose.

        Split out from :meth:`discover` so the only await — reading the catalog
        — happens on the caller's event loop while this part, which makes a
        blocking ``chat()`` call, can be handed to a worker thread. Running it
        inline on the loop stalls every other request on the process, including
        the polls asking how this one is doing.

        ``limit`` is deliberately required: the top-N cut is product policy,
        so the caller (the composition root, via settings) owns the number.
        This domain layer keeps no default for it.
        """

        by_name = {profile.name: profile for profile in profiles}
        scannable = self._scannable(profiles)
        results = self._discover_with_llm(requirement, scannable) if self._llm else []
        llm_used = bool(results)
        if not results:
            results = self._discover_with_keywords(
                requirement, scannable, keywords or []
            )
        span = trace.get_current_span()
        span.set_attribute("repomesh.discovery.llm_used", llm_used)
        span.set_attribute("repomesh.discovery.candidate_count", len(results))

        evidence_by_name = {
            by_id.name: evidence
            for evidence in results
            if (by_id := next((p for p in profiles if p.id == evidence.repository_id), None))
        }
        entry_evidence = None
        if entry_point and entry_point in by_name:
            profile = by_name[entry_point]
            existing = evidence_by_name.get(entry_point)
            if existing is not None:
                # The entry repo scored like any other; mark it, keep its
                # number and rationale. Forcing 1.0 would turn the ranking
                # into a stage-managed list where the user's pick always
                # "wins", and the rationale swap would hide why the score
                # says what it says.
                entry_evidence = replace(existing, is_entry_point=True)
            else:
                # No scorer named it, but the user did — it lands anyway.
                # The score is not fabricated confidence: presence is
                # guaranteed by instruction, and 0.0 says "nothing
                # measured", which is the truth.
                entry_evidence = DiscoveryEvidence(
                    repository_id=profile.id,
                    matched_terms=(),
                    score=0.0,
                    rationale="User-specified entry point",
                    is_entry_point=True,
                    low_signal=self._low_signal(profile),
                )
            evidence_by_name[entry_point] = entry_evidence

        ranked = sorted(
            evidence_by_name.values(), key=lambda item: item.score, reverse=True
        )
        selected = ranked[:limit]
        if entry_evidence is not None and all(
            item is not entry_evidence for item in selected
        ):
            # The entry point is a floor, not a tie-breaker: it takes the
            # last slot only when the N cut would have dropped it entirely.
            # Displacing the weakest scored candidate is the user's explicit
            # pick overriding a weaker signal, which is the point of naming
            # an entry point at all.
            if len(selected) == limit:
                selected = selected[:-1]
            selected.append(entry_evidence)
            selected.sort(key=lambda item: item.score, reverse=True)
        return DiscoveryOutcome(candidates=selected, llm_used=llm_used)

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
                # 模型幻觉或名字拼写偏差：静默丢弃会让用户看到"候选少了"
                # 却不知道为什么，留一条日志便于排查（可观察性契约）。
                _logger.warning(
                    "LLM discovery named %r, which is not in the catalog; dropped",
                    name,
                )
                continue
            score = max(0.0, min(1.0, float(candidate.get("confidence", 0.0))))
            results.append(
                DiscoveryEvidence(
                    repository_id=profile.id,
                    matched_terms=(),
                    score=score,
                    rationale=str(candidate.get("rationale", "LLM recommendation")),
                    low_signal=self._low_signal(profile),
                )
            )
        return results

    @staticmethod
    def _idf_by_term(
        terms: frozenset[str], profiles: list[RepositoryProfile]
    ) -> dict[str, float]:
        """Inverse document frequency over the corpus, for *terms*.

        A requirement term that every repository carries ("service", "api")
        tells nothing apart; a term that one repository carries is the whole
        reason that repository was matched. Weighting the match by IDF keeps
        the score an honest ratio of how much of the requirement this repo
        actually explains, instead of a raw count where generic vocabulary
        counts as much as the discriminating term.

        Smoothed with +1 on both sides so a term in every document still has
        a finite (1.0) weight and an unseen term the maximum weight, rather
        than a divide-by-zero.
        """

        n = max(1, len(profiles))
        df = Counter()
        for profile in profiles:
            df.update(tokenize(profile.searchable_text) & terms)
        return {term: math.log((n + 1) / (df[term] + 1)) + 1.0 for term in terms}

    def _discover_with_keywords(
        self,
        requirement: str,
        profiles: list[RepositoryProfile],
        extra_keywords: list[str],
    ) -> list[DiscoveryEvidence]:
        terms = tokenize(" ".join((requirement, *extra_keywords)))
        if not terms:
            return []
        idf_by_term = self._idf_by_term(terms, profiles)
        results: list[DiscoveryEvidence] = []
        for profile in profiles:
            matched = tuple(sorted(terms & tokenize(profile.searchable_text)))
            if not matched:
                continue
            # IDF-weighted coverage: the share of the requirement's
            # discriminative weight this profile explains. Capped below 1.0
            # (injected from settings) so a full keyword match stays
            # distinguishable from a model verdict at full confidence.
            score = sum(idf_by_term[term] for term in matched) / sum(
                idf_by_term[term] for term in terms
            )
            score = min(self._keyword_score_cap, score)
            results.append(
                DiscoveryEvidence(
                    repository_id=profile.id,
                    matched_terms=matched,
                    score=round(score, 4),
                    rationale=f"Matched repository signals: {', '.join(matched)}",
                    low_signal=self._low_signal(profile),
                )
            )
        return results


def _build_discovery_prompt(
    requirement: str, profiles: list[RepositoryProfile]
) -> list[dict[str, str]]:
    # Keep the payload light: full searchable_text (deps, commits, dirs) for every
    # profile bloats the prompt and trips provider gateway timeouts at scale.
    repositories = [
        {
            "name": profile.name,
            "description": profile.description,
            "topics": profile.topics,
            "languages": profile.languages,
        }
        for profile in profiles
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
