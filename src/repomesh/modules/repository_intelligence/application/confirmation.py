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
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import Context, copy_context
from dataclasses import dataclass, field, replace
from typing import Protocol

from opentelemetry import trace

from repomesh.modules.repository_intelligence.domain import (
    AutoCard,
    RepositoryProfile,
)
from repomesh.telemetry import traced

from .dependency_graph import DependencyGraphService, GraphEdge

_logger = logging.getLogger(__name__)

_tracer = trace.get_tracer("repomesh.planning")


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
class RepositoryPlan:
    """Structured change plan produced by the Team Manager."""

    changed_apis: tuple[str, ...] = ()
    changed_modules: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    impacts: tuple[str, ...] = ()
    risk: str = "medium"  # low / medium / high


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    """Result of a single Repository Manager confirmation."""

    repository: str
    status: str  # "REQUIRED", "MAYBE", or "EXCLUDED"
    confidence: float = 0.0
    reason: str = ""
    plan_summary: str = ""
    plan: RepositoryPlan | None = None
    missing_dependencies: list[str] = field(default_factory=list)
    #: Brought in by the Project Manager's graph pre-supplement (not part of
    #: the original candidate list). The confirmation is otherwise identical.
    is_supplemented: bool = False
    #: The graph found a confirmed dependency edge from this repo to a
    #: REQUIRED/MAYBE repo while the model judged it EXCLUDED — a review
    #: suggestion for the approver, never a verdict.
    graph_conflict: bool = False


@dataclass(frozen=True, slots=True)
class SupplementEvidence:
    """One graph edge's worth of "why this repo was added" (PM → RM).

    Produced deterministically by the discovery graph pre-supplement: the
    repo is a first-degree neighbour of a candidate, so it joins the
    confirmation list with the evidence that brought it in. The same record
    feeds the RM's prompt (``## Why You Were Added``) and the block audit
    trail — never a bare name.
    """

    repository: str
    via: str  # the candidate whose edge pulled this repo in
    confidence: str  # "confirmed" | "declared"
    mechanism: str
    match_reason: str


@dataclass(frozen=True, slots=True)
class GraphConflict:
    """A graph-vs-model disagreement, surfaced for the approver.

    The model judged the repo EXCLUDED, but a *confirmed* dependency edge
    connects it to a REQUIRED/MAYBE repo. The repo's EXCLUDED verdict was
    reached without seeing what the kept repo changes — so the conflict is a
    review suggestion, never an error report.
    """

    repository: str
    status: str  # the model's verdict (EXCLUDED in practice)
    via: tuple[str, ...]  # the kept repos the confirmed edges point to
    edges: tuple[GraphEdge, ...]


@dataclass(frozen=True, slots=True)
class SupplementObservation:
    """A name the model reported (missing_dependencies / impacts) that is
    not in the confirmation list and has no graph edge behind it.

    Shown to the approver as a low-trust observation — the model's word only.
    The approver may manually tier it (the adjustments mechanism already
    supports adding repos nobody confirmed).
    """

    repository: str
    via: str  # which confirmed repo reported it


@dataclass(frozen=True, slots=True)
class ConfirmationSummary:
    """Aggregated result of confirming all candidates."""

    required: list[ConfirmationResult]  # REQUIRED only
    maybe: list[ConfirmationResult]  # MAYBE (kept but low-confidence)
    excluded: list[ConfirmationResult]  # EXCLUDED
    supplements: list[SupplementEvidence] = field(default_factory=list)
    conflicts: list[GraphConflict] = field(default_factory=list)
    observations: list[SupplementObservation] = field(default_factory=list)

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
    supplement_context: str | None = None,
    history_context: str | None = None,
) -> list[dict[str, str]]:
    """Build chat messages for a single Repository Manager confirmation.

    The LLM sees:
    - Its own repo's AutoCard (detailed)
    - The requirement text
    - The full candidate list (so it knows what other repos were flagged)
    - The Project Manager's rationale for flagging this repo (V4 measure 2)
    - Why a graph-supplemented repo was added (deterministic edge evidence)
    - Similar historical decision chains (Phase 4b) — reference evidence only:
      precedent to calibrate the verdict, never a substitute for this
      requirement's own evidence

    Graph structure is deliberately *not* in the prompt: the graph's decision
    role (who joins the list, which EXCLUDED verdicts deserve review) lives in
    the deterministic pre-supplement and conflict detection, not in the model's
    context window.
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
        '  "changed_apis": ["API endpoints that will be modified or added"],\n'
        '  "changed_modules": ["modules/packages/directories that will be modified"],\n'
        '  "depends_on": ["other services/repos whose APIs this repo calls"],\n'
        '  "impacts": ["other services/repos that call this repo APIs and may break"],\n'
        '  "risk": "low" or "medium" or "high",\n'
        '  "missing_dependencies": ["repos you depend on that are NOT in the candidate list"]\n'
        "}"
    )

    # Include discovery rationale
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

    # Include supplement evidence (graph pre-supplement)
    supplement_text = ""
    if supplement_context:
        supplement_text = (
            "\n\n## Why You Were Added\n"
            "You were not in the original candidate list. The discovery "
            "graph pulled you in because of a structural dependency:\n"
            f"- {supplement_context}\n"
            "Confirm whether your repository really needs changes. Your "
            "direct evidence may be thinner than the original candidates' — "
            "when you cannot find solid evidence, answer MAYBE, not REQUIRED."
        )

    # Include similar historical decision chains (Phase 4b) — reference only.
    history_text = ""
    if history_context:
        history_text = f"\n\n{history_context}\n"

    user = (
        f"## Your Repository: {profile.name}\n\n"
        f"{card_text}\n\n"
        f"## Requirement\n\n{requirement}\n\n"
        f"## All Candidates Flagged by Discovery\n\n{candidates_str}\n"
        f"{pm_context}"
        f"{supplement_text}"
        f"{history_text}\n\n"
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

    status = str(data.get("status", "REQUIRED")).upper()
    if status not in ("REQUIRED", "MAYBE", "EXCLUDED"):
        status = "REQUIRED"

    # Parse structured plan (only for non-excluded repos)
    plan: RepositoryPlan | None = None
    if status != "EXCLUDED":
        plan = RepositoryPlan(
            changed_apis=_string_tuple(data.get("changed_apis")),
            changed_modules=_string_tuple(data.get("changed_modules")),
            depends_on=_string_tuple(data.get("depends_on")),
            impacts=_string_tuple(data.get("impacts")),
            risk=(
                risk
                if (risk := str(data.get("risk", "medium")).lower())
                in {"low", "medium", "high"}
                else "medium"
            ),
        )

    return ConfirmationResult(
        repository=repo_name,
        status=status,
        confidence=_confidence(data.get("confidence")),
        reason=str(data.get("reason") or ""),
        plan_summary=str(data.get("plan_summary") or ""),
        plan=plan,
        missing_dependencies=(
            list(_string_tuple(data.get("missing_dependencies")))
            if status != "EXCLUDED"
            else []
        ),
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value if value is not None else 0.5)))
    except (TypeError, ValueError):
        return 0.5


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
        graph: DependencyGraphService | None = None,
    ) -> None:
        self._llm = llm_client
        self._profiles = profiles_by_name
        self._graph = graph

    @traced("planning.confirmation")
    def confirm(
        self,
        candidate_names: list[str],
        requirement: str,
        *,
        discovery_evidence: dict[str, tuple[str, float]] | None = None,
        supplement_evidence: dict[str, SupplementEvidence] | None = None,
        history_context: str | None = None,
        concurrency: int = 1,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> ConfirmationSummary:
        """Confirm each repo in the (already graph-supplemented) list.

        Args:
            candidate_names: repos to confirm — the original candidates plus
                any graph pre-supplemented repos, in deterministic order.
            requirement: the feature requirement text.
            discovery_evidence: optional mapping of repo_name → (rationale,
                confidence) from the discovery phase.  When provided, each
                Repository Manager sees *why* the Project Manager flagged it
                (V4 measure 2).
            supplement_evidence: optional mapping of repo_name → evidence for
                repos the PM's graph pre-supplement added (they were not in
                the original candidate list).
            history_context: optional rendered section of similar historical
                decision chains (Phase 4b). Reference evidence for calibrating
                each verdict — never instructions, and never a substitute for
                this requirement's own evidence.
            concurrency: bounded parallelism for the LLM calls (a global
                rate-limit approximation; keep well under the provider's
                RPM/TPM). ``1`` reproduces the serial behaviour.
            on_progress: optional callback ``(completed, total, name)``
                invoked from the submitting thread as each confirmation
                finishes, in completion order.
        """

        if concurrency > 1 and len(candidate_names) > 1:
            results = self._confirm_parallel(
                candidate_names,
                requirement,
                discovery_evidence=discovery_evidence,
                supplement_evidence=supplement_evidence,
                history_context=history_context,
                concurrency=concurrency,
                on_progress=on_progress,
            )
        else:
            results = self._confirm_serial(
                candidate_names,
                requirement,
                discovery_evidence=discovery_evidence,
                supplement_evidence=supplement_evidence,
                history_context=history_context,
                on_progress=on_progress,
            )

        required = [r for r in results if r.status == "REQUIRED"]
        maybe = [r for r in results if r.status == "MAYBE"]
        excluded = [r for r in results if r.status == "EXCLUDED"]

        # Flag every confirmed result that came in via the graph
        # pre-supplement — the panel marks these (deterministic evidence)
        # distinctly from the model's own judgement. Applied to all three
        # tiers so the marker survives a repo being bucketed anywhere.
        supplement_names = set(supplement_evidence) if supplement_evidence else set()
        if supplement_names:
            required = [
                replace(r, is_supplemented=r.repository in supplement_names)
                for r in required
            ]
            maybe = [
                replace(r, is_supplemented=r.repository in supplement_names)
                for r in maybe
            ]
            excluded = [
                replace(r, is_supplemented=r.repository in supplement_names)
                for r in excluded
            ]

        # Graph-vs-model conflict detection: a confirmed edge from an
        # EXCLUDED repo to a kept repo means the EXCLUDED verdict deserves a
        # second look. Deterministic, no LLM call.
        conflicts = self._detect_conflicts(required, maybe, excluded)
        if conflicts:
            conflict_names = {c.repository for c in conflicts}
            excluded = [
                replace(r, graph_conflict=(r.repository in conflict_names))
                for r in excluded
            ]

        # Low-trust observations: names the model reported that are neither
        # in the confirmation list nor backed by a graph edge. Approver's
        # word, surfaced for manual tiering — never auto-confirmed.
        observations = self._collect_observations(required, maybe, candidate_names)

        return ConfirmationSummary(
            required=required,
            maybe=maybe,
            excluded=excluded,
            supplements=list(supplement_evidence.values()) if supplement_evidence else [],
            conflicts=conflicts,
            observations=observations,
        )

    def _confirm_one(
        self,
        idx: int,
        name: str,
        requirement: str,
        candidate_names: list[str],
        *,
        discovery_evidence: dict[str, tuple[str, float]] | None,
        supplement_evidence: dict[str, SupplementEvidence] | None,
        history_context: str | None = None,
    ) -> tuple[int, ConfirmationResult | None]:
        """Confirm a single repo. ``None`` when the repo is not in the catalog.

        Runs on a worker thread during parallel confirmation; the OpenTelemetry
        context is propagated by the caller via ``copy_context``.
        """

        profile = self._profiles.get(name)
        if profile is None:
            _logger.warning("Candidate %s not in catalog, skipping", name)
            return idx, None

        # V4 measure 2: pass discovery rationale to the Manager
        rationale = ""
        conf = 0.0
        if discovery_evidence and name in discovery_evidence:
            rationale, conf = discovery_evidence[name]

        # Graph pre-supplement: tell the Manager why it was called in.
        supplement_context = None
        if supplement_evidence and name in supplement_evidence:
            ev = supplement_evidence[name]
            supplement_context = (
                f"'{ev.match_reason}' — pulled in via {ev.via} "
                f"({ev.confidence} edge, mechanism {ev.mechanism})"
            )

        messages = _build_confirmation_prompt(
            profile,
            requirement,
            candidate_names,
            discovery_rationale=rationale,
            discovery_confidence=conf,
            supplement_context=supplement_context,
            history_context=history_context,
        )
        with _tracer.start_as_current_span(
            f"confirm {name}",
            attributes={"repomesh.repository_name": name},
        ) as repo_span:
            raw = self._llm.chat(messages, temperature=0.0)
            result = _parse_confirmation(raw, name)
            repo_span.set_attribute("repomesh.confirmation.status", result.status)
            repo_span.set_attribute("repomesh.confirmation.confidence", result.confidence)
        _logger.info(
            "Confirmation %s: %s (confidence=%.2f)",
            name,
            result.status,
            result.confidence,
        )
        return idx, result

    def _confirm_parallel(
        self,
        names: list[str],
        requirement: str,
        *,
        discovery_evidence: dict[str, tuple[str, float]] | None,
        supplement_evidence: dict[str, SupplementEvidence] | None,
        history_context: str | None = None,
        concurrency: int,
        on_progress: Callable[[int, int, str], None] | None,
    ) -> list[ConfirmationResult]:
        """Confirm with a bounded thread pool.

        Results come back in candidate order regardless of completion order
        (each future carries its index). ``on_progress`` fires on the
        submitting thread inside the ``as_completed`` loop, so the callback
        never races across workers. Every task gets its *own* copy of the
        capturing thread's context — a single ``Context`` instance cannot be
        entered concurrently by two workers, so sharing one would raise
        ``RuntimeError: cannot enter context ... already entered``.

        ``history_context`` is an immutable rendered string, safe to pass by
        value into every worker (never shared mutable state).
        """

        def run(task_ctx: Context, idx: int, name: str) -> tuple[int, ConfirmationResult | None]:
            return task_ctx.run(
                self._confirm_one,
                idx,
                name,
                requirement,
                names,
                discovery_evidence=discovery_evidence,
                supplement_evidence=supplement_evidence,
                history_context=history_context,
            )

        results: dict[int, ConfirmationResult] = {}
        completed = 0
        with ThreadPoolExecutor(max_workers=min(concurrency, len(names))) as executor:
            futures = {}
            for idx, name in enumerate(names):
                # Captured on the submitting thread so tracing spans keep
                # their parent link; copied per task so concurrent workers
                # never share one live Context.
                futures[executor.submit(run, copy_context(), idx, name)] = idx
            for future in as_completed(futures):
                idx, result = future.result()
                if result is None:
                    continue
                results[idx] = result
                completed += 1
                if on_progress:
                    on_progress(completed, len(names), result.repository)
        return [results[i] for i in sorted(results)]

    def _confirm_serial(
        self,
        names: list[str],
        requirement: str,
        *,
        discovery_evidence: dict[str, tuple[str, float]] | None,
        supplement_evidence: dict[str, SupplementEvidence] | None,
        history_context: str | None = None,
        on_progress: Callable[[int, int, str], None] | None,
    ) -> list[ConfirmationResult]:
        results: list[ConfirmationResult] = []
        completed = 0
        for idx, name in enumerate(names):
            _, result = self._confirm_one(
                idx,
                name,
                requirement,
                names,
                discovery_evidence=discovery_evidence,
                supplement_evidence=supplement_evidence,
                history_context=history_context,
            )
            if result is None:
                continue
            results.append(result)
            completed += 1
            if on_progress:
                on_progress(completed, len(names), result.repository)
        return results

    def _detect_conflicts(
        self,
        required: list[ConfirmationResult],
        maybe: list[ConfirmationResult],
        excluded: list[ConfirmationResult],
    ) -> list[GraphConflict]:
        """Confirmed edges from an EXCLUDED repo into the kept set.

        Direction matters: only ``consumer depends on producer`` edges where
        the EXCLUDED repo is the consumer count — the producer's API is
        changing, so the consumer's EXCLUDED verdict deserves review. The
        reverse direction (the EXCLUDED repo is *depended on*) means its own
        API is untouched and the verdict stands.
        """

        if self._graph is None or not excluded:
            return []
        kept = {r.repository for r in required} | {r.repository for r in maybe}
        conflicts: list[GraphConflict] = []
        for r in excluded:
            edges = [
                e
                for e in self._graph.forward_dependencies(r.repository)
                if e.confidence == "confirmed" and e.producer in kept
            ]
            if edges:
                conflicts.append(
                    GraphConflict(
                        repository=r.repository,
                        status=r.status,
                        via=tuple(sorted({e.producer for e in edges})),
                        edges=tuple(edges),
                    )
                )
        return conflicts

    def _collect_observations(
        self,
        required: list[ConfirmationResult],
        maybe: list[ConfirmationResult],
        names: list[str],
    ) -> list[SupplementObservation]:
        """Model-reported names outside the confirmation list, in the catalog.

        These have no graph edge behind them — the model's word only. Only
        catalog members are surfaced (a hallucinated name cannot be tiered
        anyway); the approver decides whether to manually add one.
        """

        if not (required or maybe):
            return []
        known = set(names)
        observations: list[SupplementObservation] = []
        seen: set[str] = set()
        for r in required + maybe:
            reported = list(r.missing_dependencies)
            if r.plan is not None:
                reported += list(r.plan.impacts)
            for dep in reported:
                if dep in known or dep in seen or dep not in self._profiles:
                    continue
                seen.add(dep)
                observations.append(
                    SupplementObservation(repository=dep, via=r.repository)
                )
        return observations
