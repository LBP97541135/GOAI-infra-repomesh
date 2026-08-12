"""The discovery chain as a server-side fact (contract v0.4 §2 and §4).

The five pipeline endpoints are stateless: each returns its result and keeps
nothing, and ``scripts/run_pipeline.py`` gets away with that because it holds
every intermediate result in a local variable while it runs. A browser cannot.
Refresh and the candidates are gone; open a second tab and two people are
looking at different classifications; leave the page during a ten-candidate
confirmation and there is nothing to come back to.

So the chain runs here instead, against one row: the issue's current draft
snapshot, in its ``discovery`` column. Each step reads the block, does its
work, and writes the block back. The endpoints hold no state, the browser
holds no state, and "where did this get to" has exactly one answer.

Three rules the shape of this module comes from:

*Nothing pretends.* A step that failed records the server's own message under
its own key and leaves the later steps null. There is no progress that isn't
progress, and no empty object standing in for a step that never ran.

*Re-running a step voids what came after it.* Otherwise the panel shows the
new analysis next to the old candidates, both labelled complete, and the user
has no way to know the second one answers a question nobody asked any more.

*The blocking work goes to a thread.* Every LLM call in this pipeline is a
synchronous ``chat()``. Awaiting one from a coroutine stops the event loop for
the whole process — the 202 would be honest about the request and a lie about
the server, and the polls asking after the task would be queued behind it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalStatus,
    AgentRole,
)
from repomesh.modules.repository_intelligence.application.confirmation import (
    ConfirmationResult,
    ConfirmationService,
    ConfirmationSummary,
    RepositoryPlan,
)
from repomesh.modules.repository_intelligence.application.dependency_graph import (
    DependencyGraphService,
)
from repomesh.modules.repository_intelligence.application.discovery import (
    LLMClient,
    RepositoryDiscoveryService,
)
from repomesh.modules.repository_intelligence.application.plan_integration import (
    PlanIntegrationService,
)
from repomesh.modules.repository_intelligence.application.requirement_analysis import (
    RequirementAnalyzer,
)
from repomesh.modules.repository_intelligence.contracts import (
    DISCOVERY_SCHEMA_VERSION,
    GUI_STEP_OF,
    DiscoveryApprovalCommand,
    DiscoveryStepCommand,
    classification_fingerprint,
    effective_tiers,
    tier_of,
)
from repomesh.modules.repository_intelligence.infrastructure.plan_snapshot_store import (
    PlanSnapshotStore,
)
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog
from repomesh.shared.domain import DomainError, new_id
from repomesh.shared.events import ActorType, EventEnvelope

_logger = logging.getLogger(__name__)

#: Server-side error text kept short enough to render, long enough to act on.
_ERROR_LIMIT = 500


class DiscoveryIssueNotFound(DomainError):
    """No snapshot evidences this issue → 404."""


class DiscoveryActorNotFound(DomainError):
    """The acting agent id is not in the directory → 404."""


class DiscoveryDenied(DomainError):
    """The actor is not an active organization leader, or is in another
    workspace → 403."""


class DiscoveryPreconditionFailed(DomainError):
    """An earlier step has not produced what this one consumes → 409."""


class DiscoveryEvidenceStale(DomainError):
    """The approval names a classification that has since been replaced → 409."""


class DiscoveryLLMUnavailable(DomainError):
    """No LLM is configured, so this step cannot run at all → 503."""


class DiscoveryAuditLog(Protocol):
    async def append(self, event: EventEnvelope) -> None: ...


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _summarise_error(error: BaseException) -> dict[str, str]:
    """The server's own words, capped — never a fabricated user-facing phrase.

    §0.1 rule 4: an LLM failure shows the real message. A generic "something
    went wrong" here would leave an operator with a red step and no lead, and
    this text is produced by our own pipeline rather than echoed from an
    outbound request, so there is nothing to disclose by showing it.
    """

    text = str(error).strip() or error.__class__.__name__
    return {"message": text[:_ERROR_LIMIT], "at": _now()}


@dataclass(frozen=True, slots=True)
class DiscoveryTarget:
    """The draft snapshot a chain write is going to land on."""

    snapshot_id: UUID
    project_id: UUID
    plan_version: int
    requirement_text: str
    discovery: dict[str, Any]


class DiscoveryPipeline:
    """The four LLM steps, each moved off the event loop.

    Deliberately thin: it owns no chain state and makes no decisions about
    ordering or preconditions. It exists so that the one thing every step here
    has in common — a blocking call that must not run on the loop — is handled
    once instead of four times.
    """

    def __init__(
        self,
        catalog: RepositoryCatalog,
        llm_client: LLMClient | None,
        analyzer: RequirementAnalyzer | None = None,
    ) -> None:
        self._catalog = catalog
        self._llm = llm_client
        self._analyzer = analyzer

    def _require_llm(self) -> LLMClient:
        if self._llm is None:
            raise DiscoveryLLMUnavailable(
                "LLM not configured — the discovery chain cannot run"
            )
        return self._llm

    def require_available(self, step: str) -> None:
        """Refuse before accepting, when this step cannot possibly run.

        Called from the trigger path so an unconfigured deployment answers 503
        to the request instead of 202 followed by a task that was doomed when
        it was created. A 202 means "accepted and started"; handing one back
        for work that cannot start is a lie the caller then has to poll to
        discover.

        Step 1 is deliberately absent: candidate scoring has a keyword
        fallback and runs without a model, reporting ``llm_used: false`` for
        what it produced. Refusing it would deny a real, honest capability;
        the panel is told the scores are arithmetic, not judgement.
        """

        if step == "analysis" and self._analyzer is None:
            raise DiscoveryLLMUnavailable(
                "LLM not configured — requirement analysis unavailable"
            )
        if step in ("classification", "plan") and self._llm is None:
            raise DiscoveryLLMUnavailable(
                f"LLM not configured — discovery step '{step}' unavailable"
            )

    async def analyze(self, requirement: str) -> dict[str, Any]:
        analyzer = self._analyzer
        if analyzer is None:
            raise DiscoveryLLMUnavailable(
                "LLM not configured — requirement analysis unavailable"
            )
        result = await asyncio.to_thread(analyzer.analyze, requirement)
        return {
            "sufficient": result.sufficient,
            "confidence": result.confidence,
            "missing_dimensions": list(result.missing_dimensions),
            "questions": list(result.questions),
            "extracted_keywords": list(result.extracted_keywords),
        }

    async def score_candidates(
        self, requirement: str, *, limit: int, entry_point: str | None
    ) -> tuple[list[dict[str, Any]], bool]:
        # The catalog read is the only await; the scoring, which may call the
        # model, goes to a thread.
        profiles = await self._catalog.list()
        service = RepositoryDiscoveryService(self._catalog, llm_client=self._llm)
        outcome = await asyncio.to_thread(
            service.score,
            profiles,
            requirement,
            limit=limit,
            entry_point=entry_point,
        )
        by_id = {profile.id: profile for profile in profiles}
        items = [
            {
                "repository_id": str(item.repository_id),
                "repository_name": by_id[item.repository_id].name,
                "score": item.score,
                "matched_terms": list(item.matched_terms),
                "rationale": item.rationale,
                "is_entry_point": item.is_entry_point,
            }
            for item in outcome.candidates
            if item.repository_id in by_id
        ]
        return items, outcome.llm_used

    async def classify(
        self,
        requirement: str,
        candidates: list[dict[str, Any]],
        *,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> ConfirmationSummary:
        llm = self._require_llm()
        profiles = await self._catalog.list()
        by_name = {profile.name: profile for profile in profiles}
        graph = DependencyGraphService(profiles) if profiles else None
        service = ConfirmationService(llm, by_name, graph=graph)

        names = [
            str(item["repository_name"])
            for item in candidates
            if str(item.get("repository_name", "")) in by_name
        ]
        if not names:
            # The user's candidates are all gone from the catalog. That is a
            # request that cannot be served, not a server fault.
            raise DiscoveryPreconditionFailed(
                "none of the scored candidates are in the repository catalog"
            )
        evidence = {
            str(item["repository_name"]): (
                str(item.get("rationale", "")),
                float(item.get("score", 0.0)),
            )
            for item in candidates
            if str(item.get("repository_name", "")) in by_name
        }
        return await asyncio.to_thread(
            lambda: service.confirm(
                names,
                requirement,
                discovery_evidence=evidence,
                on_progress=on_progress,
            )
        )

    async def integrate(self, requirement: str, summary: ConfirmationSummary):  # noqa: ANN201
        llm = self._require_llm()
        profiles = await self._catalog.list()
        graph = DependencyGraphService(profiles) if profiles else None
        service = PlanIntegrationService(llm, graph=graph)
        return await asyncio.to_thread(service.integrate, requirement, summary)


def _result_to_dict(result: ConfirmationResult) -> dict[str, Any]:
    """The ConfirmationResultView shape, produced once.

    Same field names the ``/confirmation`` endpoint already publishes: the read
    model projects this block straight through, so writing a second spelling
    here would give the panel two vocabularies for one fact.
    """

    return {
        "repository": result.repository,
        "status": result.status,
        "confidence": result.confidence,
        "reason": result.reason,
        "plan_summary": result.plan_summary,
        "plan": (
            None
            if result.plan is None
            else {
                "changed_apis": list(result.plan.changed_apis),
                "changed_modules": list(result.plan.changed_modules),
                "depends_on": list(result.plan.depends_on),
                "impacts": list(result.plan.impacts),
                "risk": result.plan.risk,
            }
        ),
        "missing_dependencies": list(result.missing_dependencies),
    }


def _result_from_dict(payload: dict[str, Any]) -> ConfirmationResult:
    plan_payload = payload.get("plan")
    plan = (
        RepositoryPlan(
            changed_apis=tuple(plan_payload.get("changed_apis") or ()),
            changed_modules=tuple(plan_payload.get("changed_modules") or ()),
            depends_on=tuple(plan_payload.get("depends_on") or ()),
            impacts=tuple(plan_payload.get("impacts") or ()),
            risk=str(plan_payload.get("risk", "medium")),
        )
        if isinstance(plan_payload, dict)
        else None
    )
    return ConfirmationResult(
        repository=str(payload.get("repository", "")),
        status=str(payload.get("status", "REQUIRED")),
        confidence=float(payload.get("confidence", 0.0)),
        reason=str(payload.get("reason", "")),
        plan_summary=str(payload.get("plan_summary", "")),
        plan=plan,
        missing_dependencies=list(payload.get("missing_dependencies") or ()),
    )


def summary_in_force(classification: dict[str, Any]) -> ConfirmationSummary:
    """Rebuild the confirmation summary the *approver released*, not the model's.

    §4.3: integration is fed ``effective_tiers``. The distinction is the whole
    point of keeping adjustments separate — if Step 3 re-read the model's
    original buckets, a leader who moved a repository from maybe to required
    would watch the plan come back without it.
    """

    by_name = {
        str(item.get("repository", "")): _result_from_dict(item)
        for tier in ("required", "maybe", "excluded")
        for item in classification.get(tier) or ()
    }
    buckets: dict[str, list[ConfirmationResult]] = {
        "required": [],
        "maybe": [],
        "excluded": [],
    }
    for row in effective_tiers(classification):
        name = row["repository"]
        result = by_name.get(name)
        if result is None:
            # Added by the approver alone: it carries no model plan, and
            # saying so beats inventing a reason it was never given.
            result = ConfirmationResult(
                repository=name,
                status=row["tier"].upper(),
                confidence=0.0,
                reason="Added by the approver during classification review",
            )
        elif result.status != row["tier"].upper():
            # Rewrite ``status`` to the tier in force, not just the bucket.
            # Integration reads the field, not the list it arrived in
            # (``repo_names`` filters on ``status != "EXCLUDED"``), so a
            # repository the approver promoted out of *excluded* was being
            # dropped from the contracts and the batching while still
            # appearing in the task DAG — promoted on screen, absent from half
            # the plan.
            result = replace(result, status=row["tier"].upper())
        buckets[row["tier"]].append(result)
    return ConfirmationSummary(
        required=buckets["required"],
        maybe=buckets["maybe"],
        excluded=buckets["excluded"],
        supplemented_repos=list(classification.get("supplemented_repos") or ()),
    )


class DiscoveryChainService:
    """Owns the discovery block: who may write it, in what order, and what a
    re-run costs downstream."""

    def __init__(
        self,
        snapshots: PlanSnapshotStore,
        directory: AgentPrincipalReader,
        audit: DiscoveryAuditLog,
        pipeline: DiscoveryPipeline,
    ) -> None:
        self._snapshots = snapshots
        self._directory = directory
        self._audit = audit
        self._pipeline = pipeline

    # -------------------------------------------------- subject and target

    async def _actor(self, agent_id: UUID, project_id: UUID):  # noqa: ANN202
        """Contract §4.1: an active ORGANIZATION_LEADER of the issue's workspace.

        Same rule and the same lookup as issue intake (v0.3 §1.2) — the roster
        derivation the console already does for every other write. The
        workspace of record is the issue creator's, never anything the request
        body says.
        """

        actor = await self._directory.get_view(agent_id)
        if actor is None:
            raise DiscoveryActorNotFound(f"agent principal not found: {agent_id}")
        if (
            actor.status is not AgentPrincipalStatus.ACTIVE
            or actor.role is not AgentRole.ORGANIZATION_LEADER
        ):
            raise DiscoveryDenied(
                "the discovery chain requires an active organization leader"
            )
        owner = await self._issue_organization(project_id)
        if owner is not None and owner != actor.organization_id:
            raise DiscoveryDenied("the issue belongs to a different organization")
        return actor

    async def _issue_organization(self, project_id: UUID) -> UUID | None:
        snapshots = await self._snapshots.list_all(project_id)
        if not snapshots:
            return None
        creator_id = snapshots[-1].created_by_agent_id
        if creator_id is None:
            return None
        creator = await self._directory.get_view(creator_id)
        return creator.organization_id if creator is not None else None

    async def target(self, issue_id: UUID) -> DiscoveryTarget:
        """The draft this issue's chain writes to (§2.3).

        A draft that does not exist yet is not created here. Every issue is
        born as one (v0.3 §1.1), and an issue whose draft was consumed is a
        round that has already shipped — starting a fresh chain on it is the
        next round, which is not a decision a step trigger gets to make on its
        own.
        """

        draft = await self._snapshots.current_draft(issue_id)
        if draft is None:
            if not await self._snapshots.list_all(issue_id):
                raise DiscoveryIssueNotFound(f"issue not found: {issue_id}")
            raise DiscoveryPreconditionFailed(
                "this issue's plan has already been materialised; its discovery "
                "chain is closed"
            )
        return DiscoveryTarget(
            snapshot_id=draft.id,
            project_id=draft.project_id,
            plan_version=draft.plan_version,
            requirement_text=draft.requirement_text or "",
            discovery=dict(draft.discovery or {}),
        )

    # ------------------------------------------------------------ writing

    async def _commit(self, target: DiscoveryTarget, block: dict[str, Any]) -> None:
        block["schema_version"] = DISCOVERY_SCHEMA_VERSION
        await self._snapshots.set_discovery(target.snapshot_id, block)

    @staticmethod
    def _downstream_of(step: str) -> tuple[str, ...]:
        """What a re-run of *step* invalidates (§4.4 / Q8).

        Everything after it, including the approval: a leader released one
        specific classification, and a new run of any upstream step means the
        thing they released no longer exists.
        """

        order = ("analysis", "candidates", "classification", "approval", "plan")
        return order[order.index(step) + 1 :]

    async def _void_downstream(
        self, block: dict[str, Any], step: str, *, actor_id: UUID, project_id: UUID
    ) -> None:
        voided = [name for name in self._downstream_of(step) if block.get(name)]
        for name in voided:
            block.pop(name, None)
        if not voided:
            return
        await self._audit_event(
            "DiscoveryDownstreamVoided",
            actor_id=actor_id,
            project_id=project_id,
            payload={"rerunStep": step, "voidedSteps": voided},
        )

    async def _audit_event(
        self,
        event_type: str,
        *,
        actor_id: UUID,
        project_id: UUID,
        payload: dict[str, Any],
    ) -> None:
        await self._audit.append(
            EventEnvelope(
                event_type=event_type,
                actor_type=ActorType.AGENT,
                actor_id=str(actor_id),
                aggregate_type="Project",
                aggregate_id=project_id,
                aggregate_version=1,
                correlation_id=new_id(),
                project_id=project_id,
                payload=payload,
            )
        )

    def _replayed(self, block: dict[str, Any], step: str, key: str) -> bool:
        """Same key on the same step → the earlier result stands (§4.4).

        Re-running is not free and not deterministic: a retried request that
        called the model again would bill twice and could produce a second,
        contradicting answer for one logical action.
        """

        return bool((block.get(step) or {}).get("idempotency_key") == key)

    # ------------------------------------------------------- the four steps

    async def analysis(
        self, command: DiscoveryStepCommand
    ) -> tuple[DiscoveryTarget, bool]:
        """Step 0. Returns the target and whether work is actually needed."""

        target = await self.target(command.issue_id)
        actor = await self._actor(command.created_by_agent_id, target.project_id)
        block = target.discovery

        if command.force_continue:
            # No model involved, so an unconfigured deployment can still
            # override questions it already has on file.
            # §4.6: not a re-run. The user is saying "I have read the
            # questions and I am going anyway", which is a fact about them,
            # not a new opinion from the model.
            analysis = block.get("analysis")
            if not analysis:
                raise DiscoveryPreconditionFailed(
                    "there is no requirement analysis to continue past"
                )
            if analysis.get("sufficient"):
                raise DiscoveryPreconditionFailed(
                    "the requirement analysis already passed; nothing to override"
                )
            ignored = len(analysis.get("questions") or ())
            analysis["forced_continue"] = {
                "ignored_question_count": ignored,
                "by_agent_id": str(actor.id),
                "at": _now(),
            }
            await self._void_downstream(
                block, "analysis", actor_id=actor.id, project_id=target.project_id
            )
            await self._commit(target, block)
            await self._audit_event(
                "DiscoveryClarifyOverridden",
                actor_id=actor.id,
                project_id=target.project_id,
                payload={"ignoredQuestionCount": ignored},
            )
            return target, False

        if self._replayed(block, "analysis", command.idempotency_key):
            return target, False
        self._pipeline.require_available("analysis")
        return target, True

    async def run_analysis(
        self, target: DiscoveryTarget, command: DiscoveryStepCommand
    ) -> None:
        block = dict(target.discovery)
        answers = [
            {"question": question, "answer": answer}
            for question, answer in command.answers
        ]
        requirement = _compose_requirement(target.requirement_text, answers)
        record: dict[str, Any] = {
            "answers": answers,
            "analyzed_requirement": requirement,
            "idempotency_key": command.idempotency_key,
            "ran_at": _now(),
            "by_agent_id": str(command.created_by_agent_id),
            "forced_continue": None,
            "error": None,
        }
        try:
            record.update(await self._pipeline.analyze(requirement))
        except Exception as error:  # noqa: BLE001 - recorded, then surfaced
            record["error"] = _summarise_error(error)
            record.setdefault("sufficient", False)
            record.setdefault("confidence", 0.0)
            record.setdefault("questions", [])
            record.setdefault("missing_dimensions", [])
            record.setdefault("extracted_keywords", [])
            block["analysis"] = record
            await self._commit(target, block)
            raise
        block["analysis"] = record
        await self._void_downstream(
            block,
            "analysis",
            actor_id=command.created_by_agent_id,
            project_id=target.project_id,
        )
        await self._commit(target, block)

    async def candidates(
        self, command: DiscoveryStepCommand
    ) -> tuple[DiscoveryTarget, bool]:
        target = await self.target(command.issue_id)
        await self._actor(command.created_by_agent_id, target.project_id)
        self._pipeline.require_available("candidates")
        block = target.discovery
        analysis = block.get("analysis")
        if not analysis:
            raise DiscoveryPreconditionFailed(
                "requirement analysis has not run for this issue"
            )
        if not analysis.get("sufficient") and not analysis.get("forced_continue"):
            raise DiscoveryPreconditionFailed(
                "requirement analysis did not pass and was not overridden; answer "
                "the follow-up questions or continue explicitly"
            )
        if self._replayed(block, "candidates", command.idempotency_key):
            return target, False
        return target, True

    async def run_candidates(
        self, target: DiscoveryTarget, command: DiscoveryStepCommand
    ) -> None:
        block = dict(target.discovery)
        requirement = _requirement_in_force(block, target.requirement_text)
        record: dict[str, Any] = {
            "items": [],
            "llm_used": False,
            "limit": command.limit,
            "entry_point": command.entry_point,
            "idempotency_key": command.idempotency_key,
            "ran_at": _now(),
            "by_agent_id": str(command.created_by_agent_id),
            "error": None,
        }
        try:
            items, llm_used = await self._pipeline.score_candidates(
                requirement, limit=command.limit, entry_point=command.entry_point
            )
        except Exception as error:  # noqa: BLE001 - recorded, then surfaced
            record["error"] = _summarise_error(error)
            block["candidates"] = record
            await self._commit(target, block)
            raise
        record["items"] = items
        record["llm_used"] = llm_used
        block["candidates"] = record
        await self._void_downstream(
            block,
            "candidates",
            actor_id=command.created_by_agent_id,
            project_id=target.project_id,
        )
        await self._commit(target, block)

    async def classification(
        self, command: DiscoveryStepCommand
    ) -> tuple[DiscoveryTarget, bool]:
        target = await self.target(command.issue_id)
        await self._actor(command.created_by_agent_id, target.project_id)
        self._pipeline.require_available("classification")
        block = target.discovery
        candidates = block.get("candidates") or {}
        if not candidates.get("items"):
            raise DiscoveryPreconditionFailed(
                "no scored candidates for this issue"
            )
        if self._replayed(block, "classification", command.idempotency_key):
            return target, False
        return target, True

    async def run_classification(
        self,
        target: DiscoveryTarget,
        command: DiscoveryStepCommand,
        *,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        block = dict(target.discovery)
        requirement = _requirement_in_force(block, target.requirement_text)
        items = list((block.get("candidates") or {}).get("items") or ())
        record: dict[str, Any] = {
            "required": [],
            "maybe": [],
            "excluded": [],
            "supplemented_repos": [],
            "adjustments": [],
            "idempotency_key": command.idempotency_key,
            "ran_at": _now(),
            "by_agent_id": str(command.created_by_agent_id),
            "error": None,
        }
        try:
            summary = await self._pipeline.classify(
                requirement, items, on_progress=on_progress
            )
        except Exception as error:  # noqa: BLE001 - recorded, then surfaced
            record["error"] = _summarise_error(error)
            block["classification"] = record
            await self._commit(target, block)
            raise
        record["required"] = [_result_to_dict(r) for r in summary.required]
        record["maybe"] = [_result_to_dict(r) for r in summary.maybe]
        record["excluded"] = [_result_to_dict(r) for r in summary.excluded]
        record["supplemented_repos"] = list(summary.supplemented_repos)
        block["classification"] = record
        await self._void_downstream(
            block,
            "classification",
            actor_id=command.created_by_agent_id,
            project_id=target.project_id,
        )
        await self._commit(target, block)

    async def plan(self, command: DiscoveryStepCommand) -> tuple[DiscoveryTarget, bool]:
        target = await self.target(command.issue_id)
        await self._actor(command.created_by_agent_id, target.project_id)
        self._pipeline.require_available("plan")
        block = target.discovery
        if not block.get("classification"):
            raise DiscoveryPreconditionFailed("no classification for this issue")
        if (block.get("approval") or {}).get("state") != "approved":
            # The contract's one hard gate (§0.1 rule 1): approval is not
            # advisory, and a plan built from an unapproved classification is
            # a plan nobody agreed to.
            raise DiscoveryPreconditionFailed(
                "the classification has not been approved; a plan cannot be "
                "generated before the approval is recorded"
            )
        if self._replayed(block, "plan", command.idempotency_key):
            return target, False
        return target, True

    async def run_plan(
        self, target: DiscoveryTarget, command: DiscoveryStepCommand
    ) -> None:
        block = dict(target.discovery)
        requirement = _requirement_in_force(block, target.requirement_text)
        classification = block.get("classification") or {}
        record: dict[str, Any] = {
            "idempotency_key": command.idempotency_key,
            "ran_at": _now(),
            "by_agent_id": str(command.created_by_agent_id),
            "error": None,
        }
        try:
            integrated = await self._pipeline.integrate(
                requirement, summary_in_force(classification)
            )
        except Exception as error:  # noqa: BLE001 - recorded, then surfaced
            record["error"] = _summarise_error(error)
            block["plan"] = record
            await self._commit(target, block)
            raise

        # §4.3: the products land on the same draft, without a version bump.
        # The plan belongs to the round the draft already represents.
        await self._snapshots.set_integration(
            target.snapshot_id,
            engineering_spec=integrated.engineering_spec,
            contracts=[c.to_dict() for c in integrated.contracts],
            task_dag=[t.to_dict() for t in integrated.task_dag],
            execution_batches=[list(batch) for batch in integrated.execution_batches],
            integration_method="llm_only",
        )
        record["task_dag_count"] = len(integrated.task_dag)
        record["batch_count"] = len(integrated.execution_batches)
        record["contract_count"] = len(integrated.contracts)
        block["plan"] = record
        await self._commit(target, block)

    # ------------------------------------------------------------ approval

    async def approve(
        self, command: DiscoveryApprovalCommand
    ) -> tuple[DiscoveryTarget, bool]:
        """§5.2: record the leader's decision, tier edits included.

        Synchronous — nothing here calls a model — so it answers 200 rather
        than handing back a task to poll for a decision that is already made.

        Returns ``(target, replayed)``. A replay must be distinguishable: the
        adjustments would otherwise be appended a second time and the tiering
        would grow a duplicate edit every time a client retried.
        """

        target = await self.target(command.issue_id)
        actor = await self._actor(command.decided_by_agent_id, target.project_id)
        block = target.discovery
        classification = block.get("classification")
        if not classification:
            raise DiscoveryPreconditionFailed(
                "there is no classification to approve"
            )
        if command.decision not in ("approved", "changes_requested"):
            raise ValueError(f"unknown decision: {command.decision}")

        existing = block.get("approval") or {}
        if existing.get("idempotency_key") == command.idempotency_key:
            return target, True

        current = classification_fingerprint(classification)
        if command.evidence_version != current:
            # §5.3: an approval is bound to the evidence its author saw. The
            # alternative — release whatever the latest classification is —
            # approves a tiering nobody read.
            raise DiscoveryEvidenceStale(
                "the classification has changed since it was reviewed; reload the "
                "panel and approve the current one"
            )

        adjustments = list(classification.get("adjustments") or ())
        original = {
            str(item.get("repository", "")): tier_of(str(item.get("status", "")))
            for tier in ("required", "maybe", "excluded")
            for item in classification.get(tier) or ()
        }
        for repository, tier in command.adjustments:
            adjustments.append(
                {
                    "repository": repository,
                    "from": (original.get(repository) or "").upper() or None,
                    "to": tier_of(tier).upper(),
                    "by_agent_id": str(actor.id),
                    "at": _now(),
                }
            )
        classification["adjustments"] = adjustments
        block["classification"] = classification
        block["approval"] = {
            "state": command.decision,
            "evidence_version": current,
            "decided_by_agent_id": str(actor.id),
            "reason": command.reason,
            "decided_at": _now(),
            "idempotency_key": command.idempotency_key,
        }
        await self._commit(target, block)
        await self._audit_event(
            "DiscoveryClassificationDecided",
            actor_id=actor.id,
            project_id=target.project_id,
            payload={
                "decision": command.decision,
                "evidenceVersion": current,
                "adjustmentCount": len(command.adjustments),
                "reason": command.reason,
            },
        )
        return target, False


def _compose_requirement(
    requirement_text: str, answers: list[dict[str, str]]
) -> str:
    """Fold the follow-up answers into the text the model will see (§4.3/Q16).

    Server-side because the alternative is the browser composing it, which
    makes the browser a source of truth for what was analysed. The issue's
    ``requirement_text`` is deliberately left alone: it is where the issue
    title comes from, and rewriting it would make the title change under the
    user as they answer questions.
    """

    if not answers:
        return requirement_text
    lines = [requirement_text, "", "## 补充说明"]
    lines.extend(
        f"- {answer['question']}：{answer['answer']}" for answer in answers
    )
    return "\n".join(lines)


def _requirement_in_force(block: dict[str, Any], fallback: str) -> str:
    """What downstream steps analyse: the composed text, not the issue title.

    Spelled out because getting it wrong is silent — the pipeline accepts
    either string and only the quality of the result differs.
    """

    analyzed = (block.get("analysis") or {}).get("analyzed_requirement")
    return str(analyzed) if analyzed else fallback


#: Exported for the API layer's step numbering; the mapping itself is in
#: contracts so the read model and the writers cannot disagree about it.
__all__ = [
    "GUI_STEP_OF",
    "DiscoveryActorNotFound",
    "DiscoveryApprovalCommand",
    "DiscoveryChainService",
    "DiscoveryDenied",
    "DiscoveryEvidenceStale",
    "DiscoveryIssueNotFound",
    "DiscoveryLLMUnavailable",
    "DiscoveryPipeline",
    "DiscoveryPreconditionFailed",
    "DiscoveryStepCommand",
    "DiscoveryTarget",
    "summary_in_force",
]
