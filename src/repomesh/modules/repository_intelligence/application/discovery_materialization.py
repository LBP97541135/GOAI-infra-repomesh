"""Materialise an issue's plan and start the work (contract v0.4 §8).

The last move of a console round, and the one the browser must not be trusted
with. ``scripts/run_pipeline.py`` holds the whole plan in local variables and
POSTs it to ``/bridge/materialize``; a browser doing the same would be handing
the server back a plan the server itself produced, with a round trip in the
middle where it could be edited. So nothing about the plan is accepted here.
The request carries a subject and an idempotency key; every fact that becomes a
task is read from the draft snapshot.

Three refusals, all of them honest about what is missing:

*The chain has not finished.* §3.2's stepper must be standing on cell 4 with a
plan on the draft. Anything earlier answers 409 saying which of the two things
is absent — an unapproved classification and an unrun Step 3 are different
problems with different next actions, and one message for both would send the
operator to the wrong button.

*There is no topology.* Not a refusal, a repair: the console creates a
workspace leader and catalog rows and nothing else, so the first round of every
project arrives here with no teams. One is built from the plan's own repository
set before the bridge is called. This is the only write that happens before the
gate, and it is the reason it is safe to: a topology with no execution plan
assigns nobody anything.

*The scope checkpoint has not been cleared.* The bridge's own gate, and its
reason is passed through verbatim rather than reworded. A panel that says
"blocked" where the server said which checkpoint and which evidence version has
thrown away the only part the operator can act on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalReader
from repomesh.modules.project.contracts import (
    ProjectTopologyProvisioner,
    ProjectTopologyReader,
)
from repomesh.modules.repository_intelligence.application.discovery_chain import (
    DiscoveryIssueNotFound,
    DiscoveryPreconditionFailed,
    require_organization_leader,
)
from repomesh.modules.repository_intelligence.application.plan_integration import (
    ContractSpec,
    IntegratedPlan,
    TaskNode,
)
from repomesh.modules.repository_intelligence.contracts import discovery_step
from repomesh.modules.repository_intelligence.infrastructure.plan_snapshot_store import (
    PlanSnapshotStore,
)
from repomesh.modules.repository_intelligence.ports import (
    PlanMaterializer,
    RepositoryCatalog,
)

_logger = logging.getLogger(__name__)


class DiscoveryNotMaterialisable(DiscoveryPreconditionFailed):
    """The chain has not produced something that can be started → 409.

    A subclass so the API layer's existing translation covers it without a new
    branch, and a named type so the reason can be tested for rather than
    matched as a string.
    """


@dataclass(frozen=True, slots=True)
class MaterializeIssueCommand:
    issue_id: UUID
    created_by_agent_id: UUID
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class IssueMaterialization:
    """§8's response, produced identically for a fresh run and a replay."""

    plan_id: UUID | None
    task_ids: tuple[UUID, ...]
    team_count: int
    repositories: tuple[str, ...]
    replayed: bool

    @property
    def status(self) -> str:
        return "replayed" if self.replayed else "materialized"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _plan_from_snapshot(record: Any) -> IntegratedPlan:
    """Rebuild the integrated plan from the columns Step 3 wrote.

    Reading it back rather than keeping it in memory is the point of the whole
    feature: the round survives a refresh, a second tab and a server restart
    because this row, not a browser, is where the plan lives.
    """

    return IntegratedPlan(
        engineering_spec=record.engineering_spec or "",
        contracts=[
            ContractSpec(
                producer=str(item.get("producer", "")),
                consumer=str(item.get("consumer", "")),
                interface=str(item.get("interface", "")),
                agreement=str(item.get("agreement", "")),
            )
            for item in (record.contracts or ())
        ],
        task_dag=[
            TaskNode(
                repository=str(item.get("repository", "")),
                instruction=str(item.get("instruction", "")),
                depends_on=tuple(item.get("depends_on") or ()),
                parallelizable_with=tuple(item.get("parallelizable_with") or ()),
                tests=tuple(item.get("tests") or ()),
            )
            for item in (record.task_dag or ())
        ],
        execution_batches=[list(batch) for batch in (record.execution_batches or ())],
    )


class DiscoveryMaterializationService:
    """§8: turn the draft's plan into an execution plan, once."""

    def __init__(
        self,
        snapshots: PlanSnapshotStore,
        directory: AgentPrincipalReader,
        catalog: RepositoryCatalog,
        topologies: ProjectTopologyReader,
        provisioner: ProjectTopologyProvisioner,
        materializer: PlanMaterializer,
    ) -> None:
        self._snapshots = snapshots
        self._directory = directory
        self._catalog = catalog
        self._topologies = topologies
        self._provisioner = provisioner
        self._materializer = materializer

    async def materialize(
        self, command: MaterializeIssueCommand
    ) -> IssueMaterialization:
        replayed = await self._replay(command)
        if replayed is not None:
            return replayed

        draft = await self._snapshots.current_draft(command.issue_id)
        if draft is None:
            if not await self._snapshots.list_all(command.issue_id):
                raise DiscoveryIssueNotFound(f"issue not found: {command.issue_id}")
            # A consumed draft with no receipt for this key: an earlier
            # materialize with a *different* key already started this round.
            raise DiscoveryNotMaterialisable(
                "this issue's plan has already been materialised; its discovery "
                "chain is closed"
            )

        actor = await require_organization_leader(
            self._directory, self._snapshots, command.created_by_agent_id, draft.project_id
        )
        block = dict(draft.discovery or {})
        plan = _plan_from_snapshot(draft)
        self._assert_ready(block, plan)

        repositories = tuple(sorted({node.repository for node in plan.task_dag}))
        topology = await self._ensure_topology(
            project_id=draft.project_id,
            organization_id=actor.organization_id,
            organization_leader_id=actor.id,
            repositories=repositories,
            idempotency_key=command.idempotency_key,
        )

        try:
            result = await self._materializer.materialize(
                plan=plan,
                requirement=self._requirement(block, draft),
                project_id=draft.project_id,
                leader_agent_id=actor.id,
                idempotency_prefix=f"disc-{command.idempotency_key}",
            )
        except Exception as error:
            # Recorded, then surfaced — the same bargain the four steps make.
            # The receipt is deliberately left incomplete so the same key can
            # be retried once the operator has cleared whatever blocked it.
            block["materialization"] = {
                "idempotency_key": command.idempotency_key,
                "status": "failed",
                "by_agent_id": str(command.created_by_agent_id),
                "at": _now(),
                "error": str(error)[:500] or error.__class__.__name__,
            }
            await self._snapshots.set_discovery(draft.id, block)
            raise

        outcome = IssueMaterialization(
            plan_id=result.plan_id,
            task_ids=tuple(task.id for task in result.tasks),
            team_count=len(topology.repository_teams),
            repositories=repositories,
            replayed=False,
        )
        block["materialization"] = {
            "idempotency_key": command.idempotency_key,
            "status": "materialized",
            "by_agent_id": str(command.created_by_agent_id),
            "at": _now(),
            "error": None,
            "plan_id": str(outcome.plan_id) if outcome.plan_id else None,
            "task_ids": [str(task_id) for task_id in outcome.task_ids],
            "team_count": outcome.team_count,
            "repositories": list(outcome.repositories),
            "skipped_repos": list(result.skipped_repos),
        }
        await self._snapshots.set_discovery(draft.id, block)
        _logger.info(
            "Materialised issue %s into plan %s (%d task(s), %d team(s))",
            command.issue_id, outcome.plan_id, len(outcome.task_ids), outcome.team_count,
        )
        return outcome

    # ------------------------------------------------------------- replay

    async def _replay(
        self, command: MaterializeIssueCommand
    ) -> IssueMaterialization | None:
        """The receipt this key already wrote, if it completed.

        Scanned across every snapshot of the issue rather than the draft,
        because a successful materialize consumes the draft — the row holding
        the receipt is no longer the current one by the time the retry arrives.

        Only a completed receipt replays. A failed one is a record of what went
        wrong, not a result to hand back, and the key stays usable.
        """

        for record in await self._snapshots.list_all(command.issue_id):
            receipt = (record.discovery or {}).get("materialization") or {}
            if receipt.get("idempotency_key") != command.idempotency_key:
                continue
            if receipt.get("status") != "materialized":
                return None
            plan_id = receipt.get("plan_id")
            return IssueMaterialization(
                plan_id=UUID(str(plan_id)) if plan_id else None,
                task_ids=tuple(UUID(str(t)) for t in receipt.get("task_ids") or ()),
                team_count=int(receipt.get("team_count") or 0),
                repositories=tuple(receipt.get("repositories") or ()),
                replayed=True,
            )
        return None

    # ---------------------------------------------------------- readiness

    @staticmethod
    def _assert_ready(block: dict[str, Any], plan: IntegratedPlan) -> None:
        """§3.2 must read *cell 4, done* — and say which half is missing.

        ``discovery_step`` is the read model's own function, not a second
        opinion: the button the operator pressed was enabled by that number, so
        refusing on a different rule would make the panel and the server
        disagree about the same screen.
        """

        step = discovery_step(block)
        if step < 4:
            if (block.get("approval") or {}).get("state") == "changes_requested":
                raise DiscoveryNotMaterialisable(
                    "the classification was returned for changes; re-run the "
                    "affected steps and have the tiering approved before starting "
                    "work"
                )
            if block.get("classification") and not block.get("approval"):
                raise DiscoveryNotMaterialisable(
                    "the classification has not been approved; work cannot start "
                    "on a repository set nobody released"
                )
            raise DiscoveryNotMaterialisable(
                f"the discovery chain is still on step {step} of 4; there is no "
                "plan to materialise yet"
            )
        if (block.get("plan") or {}).get("error"):
            raise DiscoveryNotMaterialisable(
                "the last plan generation failed; re-run it before starting work"
            )
        if not plan.task_dag:
            raise DiscoveryNotMaterialisable(
                "the classification is approved but no plan has been generated; "
                "run the plan step first"
            )

    @staticmethod
    def _requirement(block: dict[str, Any], record: Any) -> str:
        analyzed = (block.get("analysis") or {}).get("analyzed_requirement")
        return str(analyzed) if analyzed else (record.requirement_text or "")

    # ----------------------------------------------------------- topology

    async def _ensure_topology(
        self,
        *,
        project_id: UUID,
        organization_id: UUID,
        organization_leader_id: UUID,
        repositories: tuple[str, ...],
        idempotency_key: str,
    ):  # noqa: ANN202 - ProjectAgentTopologyView, named by the contract
        existing = await self._topologies.get_view(project_id)
        if existing is not None:
            return existing

        profiles = await self._catalog.list()
        by_name = {profile.name: profile.id for profile in profiles}
        repository_ids = tuple(
            by_name[name] for name in repositories if name in by_name
        )
        if not repository_ids:
            # Every repository the plan names has left the catalog. The bridge
            # would report them all as skipped and start nothing; saying so
            # here beats a 200 with an empty task list.
            raise DiscoveryNotMaterialisable(
                "none of the plan's repositories are in the catalog "
                f"({', '.join(repositories)}); nothing can be assigned"
            )
        return await self._provisioner.ensure(
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=organization_leader_id,
            repository_ids=repository_ids,
            idempotency_key=f"disc-{idempotency_key}",
        )


__all__ = [
    "DiscoveryMaterializationService",
    "DiscoveryNotMaterialisable",
    "IssueMaterialization",
    "MaterializeIssueCommand",
]
