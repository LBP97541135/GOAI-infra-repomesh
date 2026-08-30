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

*The plan's external members are not running.* AC-03. A member whose body is a
CLI on somebody's laptop (ADR 0004) can be provisioned, bound and roomed and
still be a process nobody started, and a round dispatched to one writes its
tasks into rooms nothing is reading — with no button anywhere that delivers
them again. So the last thing checked before work is created is whether the
members exist as processes, and the refusal names each one rather than
summarising them: the remedy is per member, and it is somebody walking to a
machine.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalReader
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
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
    ExternalMemberReadinessGate,
    MemberReadinessFact,
    PlanMaterializer,
    RepositoryCatalog,
    TopologyRuntimeProjector,
)

_logger = logging.getLogger(__name__)


class DiscoveryNotMaterialisable(DiscoveryPreconditionFailed):
    """The chain has not produced something that can be started → 409.

    A subclass so the API layer's existing translation covers it without a new
    branch, and a named type so the reason can be tested for rather than
    matched as a string.
    """


class ExternalMembersNotReady(Exception):
    """The plan's local CLI members are not running → a structured 409 (AC-03).

    Deliberately *not* a :class:`DiscoveryNotMaterialisable`, which is where
    every other refusal on this path lives. Those are sentences: the operator
    reads one and knows what to press. This one is a list — which member, in
    which role, why RepoMesh believes it is absent — and a client has to render
    a row each and offer to start each. Flattening it into a string would be the
    same loss the checkpoint refusal's docstring warns about, one level worse:
    there the operator can at least read the sentence, here they would have to
    parse it.

    The summary is the exception's own message, so the sentence a log line
    carries and the one the refusal body carries cannot drift apart.
    """

    def __init__(self, facts: tuple[MemberReadinessFact, ...]) -> None:
        super().__init__(f"{len(facts)} local CLI members are not ready")
        self.facts = facts


def member_readiness_wire(fact: MemberReadinessFact) -> dict[str, str]:
    """One member of a readiness refusal, in the shape §8 publishes it.

    Shared by the receipt this module writes, the 409 the API layer raises and
    the precheck it serves, so the row an operator reads back and the body they
    were refused with cannot come to disagree. Field by field rather than a
    serialisation of the fact, because a fact is an internal type and this is
    the wire.
    """

    return {
        "agentId": str(fact.agent_id),
        "role": fact.role,
        "status": fact.status,
        "reason": fact.reason,
    }


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


def _plan_fingerprint(record: Any) -> str:
    """Identify the plan a materialization attempt was made against.

    Taken from the snapshot's own columns rather than the rebuilt
    :class:`IntegratedPlan`, because those columns are what a re-run of step 3
    rewrites — this is the number that has to change when the work changes.
    """

    payload = json.dumps(
        {
            "engineering_spec": record.engineering_spec or "",
            "contracts": record.contracts or [],
            "task_dag": record.task_dag or [],
            "execution_batches": record.execution_batches or [],
        },
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


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
        runtime: TopologyRuntimeProjector,
        readiness_gate: ExternalMemberReadinessGate,
    ) -> None:
        self._snapshots = snapshots
        self._directory = directory
        self._catalog = catalog
        self._topologies = topologies
        self._provisioner = provisioner
        self._materializer = materializer
        self._runtime = runtime
        self._readiness_gate = readiness_gate

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
        fingerprint = _plan_fingerprint(draft)
        prefix = self._prefix(block, command.idempotency_key, fingerprint)
        topology = await self._ensure_topology(
            project_id=draft.project_id,
            organization_id=actor.organization_id,
            organization_leader_id=actor.id,
            repositories=repositories,
            prefix=prefix,
        )

        # The rooms, before the round. A topology is rows; the Matrix rooms its
        # teams are dispatched over are the controller's, and nothing on this
        # path used to ask for them — so every console round started with
        # `room_id` NULL and died on its first dispatch (defect B-11). Placed
        # here rather than inside `_ensure_topology` on purpose: a project that
        # already has a topology skips that repair and still needs this, and a
        # topology made by an earlier round whose runtime has since been rebuilt
        # needs it again.
        try:
            await self._runtime.project(draft.project_id)
        except Exception as error:
            await self._record_failure(draft, block, command, prefix, fingerprint, error)
            raise

        # The processes, before the work. Placed after the projection because
        # that is what makes the members' AgentTeams documents exist to be asked
        # about, and before the materializer because a task assigned to a Bridge
        # nobody started is a task that never moves and that no button
        # re-delivers (AC-03).
        blocking = await self._blocking_members(topology, repositories)
        if blocking:
            await self._record_block(draft, block, command, prefix, fingerprint, blocking)
            raise ExternalMembersNotReady(blocking)

        try:
            result = await self._materializer.materialize(
                plan=plan,
                requirement=self._requirement(block, draft),
                project_id=draft.project_id,
                leader_agent_id=actor.id,
                idempotency_prefix=prefix,
            )
        except Exception as error:
            await self._record_failure(draft, block, command, prefix, fingerprint, error)
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
            "prefix": prefix,
            "plan_fingerprint": fingerprint,
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

    # ------------------------------------------------- external member gate

    async def _blocking_members(
        self, topology: ProjectAgentTopologyView, repositories: tuple[str, ...]
    ) -> tuple[MemberReadinessFact, ...]:
        """This round's external members that are not running, as facts.

        Scoped to the teams the plan actually assigns to. A project may hold
        teams for repositories this round never names — an earlier round's, or
        an operator's — and refusing because somebody else's CLI is down would
        be a gate nobody could clear by doing the work in front of them.

        The Organization Leader is excluded without a rule saying so: it belongs
        to no repository team. Which is the right answer for D-11's reason — it
        stays on the AgentTeams Manager and runs no Bridge — and it is worth
        naming here because a future member-set built from the directory instead
        would have to exclude it deliberately.
        """

        profiles = await self._catalog.list()
        planned = {profile.id for profile in profiles if profile.name in repositories}
        members = tuple(
            agent_id
            for team in topology.repository_teams
            if team.repository_id in planned
            for agent_id in (team.leader_agent_id, *team.worker_agent_ids)
        )
        facts = await self._readiness_gate.check(members)
        return tuple(fact for fact in facts if fact.status != "ready")

    async def _record_block(
        self,
        draft: Any,
        block: dict[str, Any],
        command: MaterializeIssueCommand,
        prefix: str,
        fingerprint: str,
        blocking: tuple[MemberReadinessFact, ...],
    ) -> None:
        """``blocked``, and deliberately not ``failed``.

        The two look alike from outside — both are refusals that leave the round
        open — and the difference is entirely in what the other two halves of
        §8's idempotency do with them.

        :meth:`_prefix` lends a refused receipt's prefix to a retry under a new
        key, because a failed attempt may have left half-written rows keyed on
        it for the retry to land on and finish. A gate block leaves nothing of
        the sort *of its own*: it happens before the materializer is called at
        all, and the only stage that did run — the runtime projection — keys its
        writes per agent and per team, never on this prefix.

        Which is why the ``prefix`` written here is carried rather than minted.
        A block can land on a round that is already carrying a failed attempt's
        debt, and this receipt is what the next attempt reads: dropping the
        prefix at that point would orphan the rows the failure left. Lending
        after a pure block, meanwhile, costs nothing for the reason just given.
        The two together are why :meth:`_prefix` treats ``blocked`` exactly as
        it treats ``failed``.

        :meth:`_replay` hands back only a ``materialized`` receipt, so a retry
        under the *same* key runs the whole path again rather than replaying a
        refusal — which is exactly AC-03's remedy, start the missing member and
        press the button again, with no machinery added for it.

        The members are copied out through :func:`member_readiness_wire`, the
        same function the refusal body uses, so the row read back later and the
        body the operator was refused with say the same thing. ``at`` is when
        the gate answered, and it is the field that makes the list mean
        anything: a lease is a claim about *now*, so a receipt without a time is
        a list of members with no statement about when they were absent.
        """

        block["materialization"] = {
            "idempotency_key": command.idempotency_key,
            "prefix": prefix,
            "plan_fingerprint": fingerprint,
            "status": "blocked",
            "by_agent_id": str(command.created_by_agent_id),
            "at": _now(),
            "blocking_members": [member_readiness_wire(fact) for fact in blocking],
        }
        await self._snapshots.set_discovery(draft.id, block)

    # ------------------------------------------------------------ failures

    async def _record_failure(
        self,
        draft: Any,
        block: dict[str, Any],
        command: MaterializeIssueCommand,
        prefix: str,
        fingerprint: str,
        error: Exception,
    ) -> None:
        """Recorded, then surfaced — the same bargain the four steps make.

        The receipt is deliberately left incomplete so the same key can be
        retried once the operator has cleared whatever blocked it. ``prefix``
        and ``plan_fingerprint`` are what let a retry under a *different* key
        land on the same rows; see :meth:`_prefix`.

        Both failing stages write the same receipt, and that is safe for the
        runtime one for a reason worth naming: nothing the projection does is
        keyed on ``prefix`` (its controller keys are per agent and per team,
        the reconcile's are per repository), so lending the prefix to a later
        key costs nothing there and still buys the materializer what it was
        introduced for.
        """

        block["materialization"] = {
            "idempotency_key": command.idempotency_key,
            "prefix": prefix,
            "plan_fingerprint": fingerprint,
            "status": "failed",
            "by_agent_id": str(command.created_by_agent_id),
            "at": _now(),
            "error": str(error)[:500] or error.__class__.__name__,
        }
        await self._snapshots.set_discovery(draft.id, block)

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

    # ------------------------------------------------------------- prefix

    @staticmethod
    def _prefix(block: dict[str, Any], key: str, fingerprint: str) -> str:
        """The idempotency prefix every write of this materialization shares.

        Normally the caller's key, and normally that is the whole story: the
        console keeps one key per round and re-sends it, so a retry after a
        failure lands on the same prefix and the writes that already succeeded
        are found instead of repeated.

        The exception is a retry that arrives under a *new* key — a reloaded
        panel, a second operator, a script. Left alone it would materialise the
        same plan a second time under a fresh prefix: a duplicate engineering
        spec, and a second execution plan racing the orphan the failure left
        behind. So a failed receipt lends its prefix to whoever comes next,
        which turns "press it again" into a repair of the first attempt rather
        than a rival of it.

        Only while the plan is unchanged. Re-running step 3 makes different
        work, and different work under the first attempt's keys would collide
        with rows describing the plan that was replaced — a conflict the
        operator cannot act on. The fingerprint is the guard, and a plan that
        has moved on simply gets its own prefix.

        A ``blocked`` receipt lends on the same terms, and it is an *inherited*
        obligation rather than one of its own. A block creates nothing keyed on
        the prefix — :meth:`_record_block` says why — so lending after a pure
        block costs nothing. But a block can land on a round that was already
        carrying somebody else's debt: a failed attempt writes ``disc-A``, the
        retry under key B inherits it and is then refused by the readiness gate,
        which overwrites the receipt. Reading only ``failed`` here would drop
        ``disc-A`` at that point, and the next attempt would materialise under a
        fresh prefix while A's rows sat orphaned — a second execution plan
        racing the first, which is the exact duplicate this method exists to
        prevent. So both refusals lend, and the fingerprint guards both.
        """

        receipt = block.get("materialization") or {}
        if receipt.get("status") not in ("failed", "blocked"):
            return f"disc-{key}"
        if receipt.get("plan_fingerprint") != fingerprint:
            return f"disc-{key}"
        previous = receipt.get("prefix") or (
            f"disc-{receipt['idempotency_key']}" if receipt.get("idempotency_key") else None
        )
        return str(previous) if previous else f"disc-{key}"

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
        prefix: str,
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
            idempotency_key=prefix,
        )


__all__ = [
    "DiscoveryMaterializationService",
    "DiscoveryNotMaterialisable",
    "ExternalMembersNotReady",
    "IssueMaterialization",
    "MaterializeIssueCommand",
    "member_readiness_wire",
]
