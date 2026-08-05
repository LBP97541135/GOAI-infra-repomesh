"""Repository Manager second-pass confirmation.

After the total Manager (discovery service) produces a candidate list with
high recall, each candidate is sent to a confirmation pass where an LLM
acts as the Repository Manager for that specific repo and decides:

- REQUIRED: the repo genuinely needs code changes → return a workstream plan
- EXCLUDED: the repo is not affected → return a reason

This module implements the confirmation logic. It reuses the same
:class:`LLMClient` abstraction as the discovery service.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from repomesh.modules.repository_intelligence.domain import (
    AutoCard,
    RepositoryProfile,
)

_logger = logging.getLogger(__name__)


def _format_autocard(card: AutoCard) -> str:
    """Format an AutoCard into a human-readable string for the LLM prompt."""

    lines: list[str] = []

    if card.top_dirs:
        lines.append(f"Top directories: {', '.join(card.top_dirs[:10])}")

    if card.deps:
        lines.append(f"Dependencies: {', '.join(card.deps[:20])}")

    if card.recent_commits:
        lines.append("Recent commits:")
        for c in card.recent_commits[:5]:
            lines.append(f"  - {c}")

    if card.exposed_apis:
        lines.append("Exposed APIs:")
        for api in card.exposed_apis[:10]:
            lines.append(f"  - {api}")

    if not lines:
        return "No information available (low signal)."

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    """Result of a single Repository Manager confirmation."""

    repository: str
    status: str  # "REQUIRED", "MAYBE", or "EXCLUDED"
    confidence: float = 0.0
    reason: str = ""
    plan_summary: str = ""
    missing_dependencies: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ConfirmationSummary:
    """Aggregated result of confirming all candidates."""

    required: list[ConfirmationResult]  # REQUIRED only
    maybe: list[ConfirmationResult]  # MAYBE (kept but low-confidence)
    excluded: list[ConfirmationResult]  # EXCLUDED
    supplemented_repos: list[str]  # repos added via missing_dependencies

    @property
    def final_repos(self) -> list[str]:
        """Names of repos that survived confirmation (REQUIRED + MAYBE)."""
        return [r.repository for r in self.required] + [r.repository for r in self.maybe]


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_confirmation_prompt(
    profile: RepositoryProfile,
    requirement: str,
    all_candidates: list[str],
    *,
    discovery_rationale: str = "",
    discovery_confidence: float = 0.0,
) -> list[dict[str, str]]:
    """Build chat messages for a single Repository Manager confirmation.

    The LLM sees:
    - Its own repo's AutoCard (detailed)
    - The requirement text
    - The full candidate list (so it knows what other repos were flagged)
    - The Project Manager's rationale for flagging this repo (V4 measure 2)
    """

    card_text = _format_autocard(profile.auto_card) if profile.auto_card else "N/A"
    candidates_str = ", ".join(all_candidates)

    system = (
        "You are the Repository Manager for a specific repository.\n"
        "Given your repository's details and a feature requirement, you must "
        "decide whether YOUR repository actually needs code changes.\n\n"
        "IMPORTANT RULES:\n"
        "- The Project Manager has already identified your repository as a "
        "candidate, which means there is initial evidence of relevance.\n"
        "- Default to REQUIRED or MAYBE unless you have CLEAR evidence that "
        "your repository is NOT affected by this requirement.\n"
        "- Use EXCLUDED only when your repository handles a completely "
        "different concern than what the requirement describes.\n\n"
        "STATUS DEFINITIONS:\n"
        "- REQUIRED: Your repository has APIs, dependencies, or code that "
        "directly corresponds to the requirement.\n"
        "- MAYBE: Your repository might be indirectly affected (e.g. depends "
        "on a service that will change) but you are not certain.\n"
        "- EXCLUDED: Your repository is clearly unrelated to the requirement.\n\n"
        "Return ONLY a JSON object (no markdown fences, no extra text):\n"
        "{\n"
        '  "status": "REQUIRED" or "MAYBE" or "EXCLUDED",\n'
        '  "confidence": 0.0 to 1.0,\n'
        '  "reason": "one sentence explanation citing specific evidence",\n'
        '  "plan_summary": "if REQUIRED or MAYBE, brief description of the change",\n'
        '  "missing_dependencies": ["repos you depend on that are NOT in the candidate list"]\n'
        "}"
    )

    # V4 measure 2: include discovery rationale
    pm_context = ""
    if discovery_rationale:
        pm_context = (
            f"\n\n## Project Manager's Assessment of Your Repository\n\n"
            f"The Project Manager flagged your repository with confidence "
            f"{discovery_confidence:.2f}:\n"
            f'"{discovery_rationale}"\n\n'
            f"Please verify whether this assessment is correct. If you cannot "
            f"find evidence to contradict it, lean towards REQUIRED or MAYBE."
        )

    user = (
        f"## Your Repository: {profile.name}\n\n"
        f"{card_text}\n\n"
        f"## Requirement\n\n{requirement}\n\n"
        f"## All Candidates Flagged by Discovery\n\n{candidates_str}\n"
        f"{pm_context}\n\n"
        f"## Task\n\n"
        f"Does YOUR repository ({profile.name}) need code changes for this "
        f"requirement? Return the JSON object now."
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _extract_json_object(text: str) -> str:
    """Extract the outermost ``{...}`` block from *text*."""

    fence = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
    m = fence.search(text)
    if m:
        text = m.group(1).strip()

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("Unterminated JSON object")


def _parse_confirmation(raw: str, repo_name: str) -> ConfirmationResult:
    """Parse the LLM response into a :class:`ConfirmationResult`."""

    try:
        json_text = _extract_json_object(raw)
        data = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        _logger.warning("Failed to parse confirmation for %s, defaulting to REQUIRED", repo_name)
        return ConfirmationResult(
            repository=repo_name,
            status="REQUIRED",
            confidence=0.5,
            reason="Parse error, keeping as safety default",
        )

    status = data.get("status", "REQUIRED").upper()
    if status not in ("REQUIRED", "MAYBE", "EXCLUDED"):
        status = "REQUIRED"

    return ConfirmationResult(
        repository=repo_name,
        status=status,
        confidence=max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
        reason=data.get("reason", ""),
        plan_summary=data.get("plan_summary", ""),
        missing_dependencies=data.get("missing_dependencies", []) if status != "EXCLUDED" else [],
    )


# ---------------------------------------------------------------------------
# Confirmation service
# ---------------------------------------------------------------------------

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# LLM protocol (avoids circular import from discovery)
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    """Minimal protocol for an LLM chat client."""

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str: ...


class ConfirmationService:
    """Orchestrates the second-pass confirmation for all candidates.

    Usage::

        service = ConfirmationService(llm_client, catalog)
        summary = service.confirm(candidates, requirement)
        print(summary.final_repos)  # repos that survived
    """

    def __init__(
        self,
        llm_client: LLMClient,
        profiles_by_name: dict[str, RepositoryProfile],
    ) -> None:
        self._llm = llm_client
        self._profiles = profiles_by_name

    def confirm(
        self,
        candidate_names: list[str],
        requirement: str,
        *,
        discovery_evidence: dict[str, tuple[str, float]] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> ConfirmationSummary:
        """Confirm each candidate repo.

        Args:
            candidate_names: repos to confirm.
            requirement: the feature requirement text.
            discovery_evidence: optional mapping of repo_name → (rationale, confidence)
                from the discovery phase.  When provided, each Repository Manager
                sees *why* the Project Manager flagged it (V4 measure 2).
            on_progress: optional callback ``(index, total, name)``.
        """

        results: list[ConfirmationResult] = []

        for idx, name in enumerate(candidate_names):
            profile = self._profiles.get(name)
            if profile is None:
                _logger.warning("Candidate %s not in catalog, skipping", name)
                continue

            if on_progress:
                on_progress(idx + 1, len(candidate_names), name)

            # V4 measure 2: pass discovery rationale to the Manager
            rationale = ""
            conf = 0.0
            if discovery_evidence and name in discovery_evidence:
                rationale, conf = discovery_evidence[name]

            messages = _build_confirmation_prompt(
                profile,
                requirement,
                candidate_names,
                discovery_rationale=rationale,
                discovery_confidence=conf,
            )
            raw = self._llm.chat(messages, temperature=0.0)
            result = _parse_confirmation(raw, name)
            results.append(result)

            _logger.info(
                "Confirmation %s: %s (confidence=%.2f)",
                name,
                result.status,
                result.confidence,
            )

        required = [r for r in results if r.status == "REQUIRED"]
        maybe = [r for r in results if r.status == "MAYBE"]
        excluded = [r for r in results if r.status == "EXCLUDED"]

        # Collect missing dependencies (one-degree only, from REQUIRED + MAYBE)
        existing = set(candidate_names)
        supplemented: list[str] = []
        for r in required + maybe:
            for dep in r.missing_dependencies:
                if dep not in existing and dep not in supplemented:
                    supplemented.append(dep)

        return ConfirmationSummary(
            required=required,
            maybe=maybe,
            excluded=excluded,
            supplemented_repos=supplemented,
        )
