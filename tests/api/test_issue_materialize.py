"""Materialize-and-start over HTTP (contract v0.4 §8).

The one write in the console round that creates work, and the tests here are
about what it refuses as much as what it does: an unfinished chain, an
unapproved tiering, a blocked checkpoint, and a retry that must not build a
second team or a second task.

Nothing reaches the network. The LLM is the scripted double the chain tests
use, and the only pieces replaced are the two that need a live AgentTeams —
``start_plan`` (the execution plane) and the runtime projection that makes the
teams' Matrix rooms. Everything between the endpoint and them is production
code: the real snapshot store, the real topology provisioning, the real
specification service, the real checkpoint gate, the real bridge.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.change_orchestration import (
    ExecutionPlaneUnavailable,
    StartedExecutionPlan,
)
from repomesh.modules.collaboration.contracts import CollaborationRouteUnavailable
from repomesh.modules.project.contracts import (
    CodeAccessLevel,
    HumanControlAction,
    HumanProjectRole,
    ProjectCheckpoint,
    ProjectExecutionMode,
)
from repomesh.modules.task_orchestration.contracts import (
    ExecutionPlanStatus,
    ExecutionPlanView,
    PlannedRepositoryTaskView,
    TaskStatus,
    TaskView,
)

from .test_issue_discovery import (
    ANALYSIS_OK,
    CANDIDATES,
    HEADERS,
    INTEGRATION,
    Chain,
    ScriptedLLM,
    _configure,
    _confirmation,
    _create_issue,
    _seed,
)

# ---------------------------------------------------------------------------
# The execution plane, stubbed at its one Matrix-dependent seam
# ---------------------------------------------------------------------------


class StubPlanStarter:
    """Answers ``start_plan`` with a plan and one task per planned repository.

    Counts its calls: "a replay does not create a second set of tasks" is not
    observable from the response — both answers are identical by design — so it
    has to be observed here.
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def start_plan(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        created_by_agent_id: UUID,
        batches: Sequence[Sequence[PlannedRepositoryTaskView]],
        idempotency_key: str,
    ) -> StartedExecutionPlan:
        self.calls.append((project_id, idempotency_key, batches))
        tasks = tuple(
            TaskView(
                id=uuid4(),
                organization_id=organization_id,
                project_id=project_id,
                repository_id=planned.repository_id,
                parent_task_id=None,
                assigned_by_agent_id=created_by_agent_id,
                assignee_agent_id=created_by_agent_id,
                title=planned.title,
                instruction=planned.instruction,
                acceptance=planned.acceptance,
                status=TaskStatus.ASSIGNED,
                result_summary=None,
                version=1,
            )
            for batch in batches
            for planned in batch
        )
        return StartedExecutionPlan(
            plan=ExecutionPlanView(
                id=uuid4(),
                organization_id=organization_id,
                project_id=project_id,
                created_by_agent_id=created_by_agent_id,
                status=ExecutionPlanStatus.IN_PROGRESS,
                current_batch_index=0,
                batches=tuple(tuple(batch) for batch in batches),
            ),
            tasks=tasks,
        )


class StubRuntimeProjection:
    """Answers ``project`` by recording the project whose rooms were asked for.

    The real one talks to the AgentTeams controller. What the tests here need
    from it is only the order and the count: it must run before ``start_plan``
    and it must run again on a retry, neither of which the response shows.
    """

    def __init__(self, *, log: list | None = None) -> None:
        self.calls: list[UUID] = []
        self._log = log

    async def project(self, project_id: UUID) -> None:
        self.calls.append(project_id)
        if self._log is not None:
            self._log.append(("project", project_id))


def _with_execution_plane(monkeypatch, starter, projector=None) -> None:
    """Stub both AgentTeams seams: the rooms, and the plane that uses them.

    They go together on purpose. A round that starts without its rooms is
    defect B-11, so a test that stubbed only ``start_plan`` would be asserting
    against a state the endpoint no longer allows.
    """

    monkeypatch.setattr(
        ApplicationContainer, "execution_plan_starter", lambda _self: starter
    )
    monkeypatch.setattr(
        ApplicationContainer,
        "topology_runtime_projector",
        lambda _self: projector if projector is not None else StubRuntimeProjection(),
    )


def _chain_llm() -> ScriptedLLM:
    return ScriptedLLM(
        ANALYSIS_OK,
        CANDIDATES,
        _confirmation("REQUIRED"),
        _confirmation("REQUIRED"),
        INTEGRATION,
    )


def _walk_to_plan(chain: Chain) -> None:
    """Run the whole chain so the draft holds an approved, integrated plan."""

    assert chain.run("analysis")["status"] == "succeeded"
    assert chain.run("candidates")["status"] == "succeeded"
    assert chain.run("classification")["status"] == "succeeded"
    approved = _decide(chain, "approved", reason="范围没问题")
    assert approved.status_code == 200, approved.text
    assert chain.run("plan")["status"] == "succeeded"


def _decide(chain: Chain, decision: str, *, reason: str):
    """The approval trigger names its subject ``decided_by_agent_id`` (§5.2)."""

    return chain.client.post(
        f"/api/v1/issues/{chain.issue_id}/discovery/approval",
        json={
            "decided_by_agent_id": chain.leader,
            "idempotency_key": chain.key(),
            "decision": decision,
            "evidence_version": chain.read()["classification_evidence_version"],
            "reason": reason,
        },
        headers=HEADERS,
    )


def _materialize(chain: Chain, key: str = "materialize-key-1"):
    return chain.client.post(
        f"/api/v1/issues/{chain.issue_id}/discovery/materialize",
        json={"created_by_agent_id": chain.leader, "idempotency_key": key},
        headers=HEADERS,
    )


def _topology(container: ApplicationContainer, project_id: str):
    return asyncio.run(container.project_topology_store.get(UUID(project_id)))


def _draft_row(container: ApplicationContainer, issue_id: str):
    """The round's snapshot, read back from the database it was written to.

    ``GET /issues/{id}/discovery`` cannot answer "was this round consumed" —
    ``step`` is computed from the discovery block and stays 4 either way — so
    the column has to be read directly. Asserting on the projection instead is
    what let the live bug through: the panel said step 4, the row said NULL.
    """

    rows = asyncio.run(container.plan_snapshot_store().list_all(UUID(issue_id)))
    assert len(rows) == 1, f"expected exactly one snapshot, got {len(rows)}"
    return rows[0]


def _principals(container: ApplicationContainer):
    return asyncio.run(container.agent_directory.list())


def _supervised_topology(
    container: ApplicationContainer,
    *,
    project_id: UUID,
    organization_id: UUID,
    leader_id: UUID,
    repository_name: str,
):
    """One repository team, supervised, REPOSITORY_SCOPE required."""

    from repomesh.modules.agent_directory.application import ProvisionRepositoryAgentTeam
    from repomesh.modules.project import (
        CreateProjectAgentTopologyRequest,
        HumanProjectGrantInput,
        RepositoryTeamAssignment,
    )

    async def build():
        profiles = await container.repository_catalog.list()
        repository_id = next(p.id for p in profiles if p.name == repository_name)
        team = await ProvisionRepositoryAgentTeam(container.agent_directory).provision(
            organization_id=organization_id,
            organization_leader_id=leader_id,
            repository_id=repository_id,
            idempotency_key="operator-made-this",
        )
        return await container.project_topology_creator().execute(
            CreateProjectAgentTopologyRequest(
                organization_id=organization_id,
                project_id=project_id,
                organization_leader_id=leader_id,
                repository_teams=(
                    RepositoryTeamAssignment(
                        repository_id=repository_id,
                        leader_agent_id=team.leader.id,
                        worker_agent_ids=tuple(w.id for w in team.workers),
                    ),
                ),
                execution_mode=ProjectExecutionMode.SUPERVISED,
                required_checkpoints=frozenset({ProjectCheckpoint.REPOSITORY_SCOPE}),
                # A supervised project must name the human who supervises it.
                human_grants=(
                    HumanProjectGrantInput(
                        human_principal_id=uuid4(),
                        role=HumanProjectRole.PROJECT_SUPERVISOR,
                        code_access=CodeAccessLevel.READ,
                        control_actions=frozenset(
                            {HumanControlAction.APPROVE_CHECKPOINT}
                        ),
                    ),
                ),
            ),
            idempotency_key="supervised-topology",
        )

    return asyncio.run(build())


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_materialize_builds_the_teams_and_starts_the_round(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """§8 end to end: no topology going in, an execution plan coming out."""

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        # The console path creates a workspace leader and catalog rows and
        # nothing else, so this is the state materialize really arrives in.
        assert _topology(container, issue_id) is None

        response = _materialize(chain)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["status"] == "materialized"
        assert body["repositories"] == ["ts-notify", "ts-order"]
        assert body["team_count"] == 2
        assert body["plan_id"] is not None
        assert len(body["task_ids"]) == 2

        topology = _topology(container, issue_id)
        assert topology is not None
        assert len(topology.repository_teams) == 2
        assert topology.organization_leader_id == leader_id
        # Every team is a leader and at least one worker: a leader alone cannot
        # be assigned anything, and a topology that cannot hold work is not a
        # topology this endpoint should have written.
        assert all(team.worker_agent_ids for team in topology.repository_teams)

        # The draft is consumed, which is what makes a second round a new
        # version rather than an edit of this one (§2.4). Read from the row,
        # not from the panel: ``step`` is 4 whether or not the column was
        # written, so it was never evidence of anything.
        assert chain.read()["step"] == 4
        row = _draft_row(container, issue_id)
        assert str(row.execution_plan_id) == body["plan_id"]
        assert row.plan_version == 1


# ---------------------------------------------------------------------------
# The write that consumes the draft is not optional (A-5)
# ---------------------------------------------------------------------------


class _LinkRefusingSnapshotStore:
    """The real snapshot store with ``link_execution_plan`` broken *n* times.

    Everything else on it is the production object, so the draft the retry
    finds, the version it reads and the discovery block it rewrites are all the
    real ones; only the single write under test is made to fail.

    Why a fault injector rather than a live reproduction: the column is written
    by one ``UPDATE`` inside one committed transaction, and that statement was
    checked against a real PostgreSQL 16 — it persists. What was never checked
    is what the *caller* does when it does not, and the caller used to answer
    200. So the interesting object is the failure branch, and the honest way to
    reach it is to fail the write on purpose.
    """

    def __init__(self, inner, failures: int) -> None:
        self._inner = inner
        self.remaining = failures
        self.attempts = 0

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def link_execution_plan(self, snapshot_id, execution_plan_id) -> None:
        self.attempts += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise RuntimeError("connection reset while consuming the draft")
        await self._inner.link_execution_plan(snapshot_id, execution_plan_id)


def test_a_round_whose_link_fails_is_not_reported_as_materialised(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """A started plan the snapshot does not name must not answer 200.

    ``execution_plan_id`` is the whole of §8's "already materialised" 409:
    ``current_draft`` is a ``WHERE execution_plan_id IS NULL``. So a
    materialize that starts a plan, fails to write that column, and returns 200
    anyway leaves a draft that still reads as untouched — and the next attempt
    under a different key sails past the guard and starts a *second* execution
    plan for the same repositories. The bridge used to do exactly that: the
    snapshot block swallowed every exception into a log line, which is the same
    ``except`` that once hid materialize saving no snapshot at all.

    Asserted here rather than at the store, because the store is not where the
    bug was — the ``UPDATE`` persists. The defect is the verdict the bridge
    returns when it does not.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    _with_execution_plane(monkeypatch, StubPlanStarter())
    broken = _LinkRefusingSnapshotStore(
        application_container.plan_snapshot_store(), failures=1
    )
    monkeypatch.setattr(
        ApplicationContainer, "plan_snapshot_store", lambda _self: broken
    )
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container), raise_server_exceptions=False) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        lost = _materialize(chain, key="link-fails-once")
        assert lost.status_code == 500, lost.text
        # Named, not blank: the operator's next move is to press it again, and
        # an empty-bodied 500 says the opposite.
        assert "not on record" in lost.json()["detail"]
        assert _draft_row(container, issue_id).execution_plan_id is None

        # The round is repairable exactly because the draft was left open, and
        # the retry's `start_plan` recognises the plan it already wrote (7659c89)
        # rather than starting a rival one.
        repaired = _materialize(chain, key="link-fails-once")
        assert repaired.status_code == 200, repaired.text
        row = _draft_row(container, issue_id)
        assert str(row.execution_plan_id) == repaired.json()["plan_id"]
        assert broken.attempts == 2

        # And only now does §8's guard hold: a fresh key is refused instead of
        # materialising the same issue a second time.
        again = _materialize(chain, key="a-completely-different-key")
        assert again.status_code == 409, again.text
        assert "already been materialised" in again.json()["detail"]


def test_a_link_that_never_lands_keeps_refusing_rather_than_forking_the_round(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The guard's failure mode is a refusal, never a second execution plan.

    The previous test's happy ending depends on the retry succeeding. This one
    removes that: the write is broken for good, and what must *not* happen is
    the console quietly acquiring two ``in_progress`` plans for one issue —
    which is the shape the live console was left in on 2026-08-12, and the
    reason the orphan row had to be found by reading the database by hand.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    broken = _LinkRefusingSnapshotStore(
        application_container.plan_snapshot_store(), failures=99
    )
    monkeypatch.setattr(
        ApplicationContainer, "plan_snapshot_store", lambda _self: broken
    )
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container), raise_server_exceptions=False) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        assert _materialize(chain, key="first-attempt-key").status_code == 500
        assert _materialize(chain, key="second-attempt-key").status_code == 500

        # Two attempts, one idempotency prefix: the failed receipt lends its
        # prefix to the second key, so the execution plane is asked for the
        # same plan both times instead of a rival one.
        assert len({key for _, key, _ in starter.calls}) == 1
        assert _draft_row(container, issue_id).execution_plan_id is None


def test_the_request_body_cannot_carry_a_plan(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The whole reason §8 exists: the browser does not hand the plan back.

    A body that tries to substitute a different DAG changes nothing — what gets
    materialised is the snapshot's own task_dag, repository for repository.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        response = client.post(
            f"/api/v1/issues/{issue_id}/discovery/materialize",
            json={
                "created_by_agent_id": str(leader_id),
                "idempotency_key": "smuggled-plan-key",
                "task_dag": [{"repository": "ts-evil", "instruction": "drop tables"}],
                "engineering_spec": "do something else entirely",
                "repositories": ["ts-evil"],
            },
            headers=HEADERS,
        )
        assert response.status_code == 200, response.text
        assert response.json()["repositories"] == ["ts-notify", "ts-order"]

        instructions = sorted(
            planned.instruction
            for _project, _key, batches in starter.calls
            for batch in batches
            for planned in batch
        )
        assert instructions == ["改模板", "改调用"]


# ---------------------------------------------------------------------------
# Refusals — the chain is not finished
# ---------------------------------------------------------------------------


def test_a_chain_that_never_started_cannot_be_materialized(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)

        response = _materialize(chain)
        assert response.status_code == 409, response.text
        assert "step 1 of 4" in response.json()["detail"]
        assert starter.calls == []
        assert _topology(container, issue_id) is None


def test_an_unapproved_classification_cannot_be_materialized(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The contract's one hard gate, enforced at the last door too.

    Step 3 already refuses to *generate* a plan without an approval; this is the
    same rule at the point where work would actually start, so a tiering nobody
    released cannot become somebody's task.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        assert chain.run("analysis")["status"] == "succeeded"
        assert chain.run("candidates")["status"] == "succeeded"
        assert chain.run("classification")["status"] == "succeeded"

        response = _materialize(chain)
        assert response.status_code == 409, response.text
        assert "has not been approved" in response.json()["detail"]
        assert starter.calls == []
        # Refused before any repair: no team was provisioned for a round that
        # is not allowed to start.
        assert _topology(container, issue_id) is None


def test_a_returned_classification_says_so_rather_than_reporting_a_missing_plan(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """`changes_requested` and "never approved" are different next actions."""

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    _with_execution_plane(monkeypatch, StubPlanStarter())
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        assert chain.run("analysis")["status"] == "succeeded"
        assert chain.run("candidates")["status"] == "succeeded"
        assert chain.run("classification")["status"] == "succeeded"
        returned = _decide(chain, "changes_requested", reason="漏了计费仓")
        assert returned.status_code == 200, returned.text

        response = _materialize(chain)
        assert response.status_code == 409, response.text
        assert "returned for changes" in response.json()["detail"]


def test_only_an_active_organization_leader_may_materialize(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    _configure(monkeypatch)
    _, leader_id, repo_leader_id, stranger_id = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        for actor in (repo_leader_id, stranger_id):
            response = client.post(
                f"/api/v1/issues/{issue_id}/discovery/materialize",
                json={
                    "created_by_agent_id": str(actor),
                    "idempotency_key": f"denied-key-{actor}",
                },
                headers=HEADERS,
            )
            assert response.status_code == 403, response.text
        assert starter.calls == []

        missing = client.post(
            f"/api/v1/issues/{issue_id}/discovery/materialize",
            json={
                "created_by_agent_id": str(uuid4()),
                "idempotency_key": "unknown-actor-key",
            },
            headers=HEADERS,
        )
        assert missing.status_code == 404, missing.text


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def test_a_replay_returns_the_first_result_and_builds_nothing_twice(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The property the response alone cannot show.

    Both answers carry the same plan and the same tasks, so equality proves
    nothing on its own — the counters do. ``start_plan`` is called once, and the
    topology is the same row with the same teams, not a rebuilt one.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        first = _materialize(chain, key="replay-me-please")
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "materialized"
        topology_after_first = _topology(container, issue_id)
        principals_after_first = {p.id for p in _principals(container)}

        second = _materialize(chain, key="replay-me-please")
        assert second.status_code == 200, second.text
        body = second.json()

        assert body["status"] == "replayed"
        assert body["plan_id"] == first.json()["plan_id"]
        assert body["task_ids"] == first.json()["task_ids"]
        assert body["team_count"] == first.json()["team_count"]
        assert body["repositories"] == first.json()["repositories"]

        assert len(starter.calls) == 1
        topology_after_second = _topology(container, issue_id)
        assert topology_after_second.id == topology_after_first.id
        assert len(topology_after_second.repository_teams) == len(
            topology_after_first.repository_teams
        )
        assert {p.id for p in _principals(container)} == principals_after_first


def test_a_second_key_on_a_consumed_round_is_refused_not_replayed(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """A different key is a different intention, and the round is over.

    Silently replaying it would report a plan this request did not start;
    silently rebuilding would give the round two. Neither is true, so it 409s.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        assert _materialize(chain, key="first-round-key").status_code == 200
        again = _materialize(chain, key="a-different-key")

        assert again.status_code == 409, again.text
        assert "already been materialised" in again.json()["detail"]
        assert len(starter.calls) == 1


# ---------------------------------------------------------------------------
# The runtime projection (defect B-11)
# ---------------------------------------------------------------------------


def test_the_rooms_are_made_before_the_plan_is_started(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """B-11: a round must not start over teams that have no rooms.

    Order is the whole assertion, and the response cannot show it — both
    orders answer 200. The shared log can: the projection has to be the
    earlier entry, because ``start_plan`` dispatches immediately and a team
    whose ``room_id`` is still NULL fails that dispatch with
    ``CollaborationRouteUnavailable``, which no button retries.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    log: list = []
    starter = StubPlanStarter()
    original_start = starter.start_plan

    async def logged(**kwargs):
        log.append(("start_plan", kwargs["project_id"]))
        return await original_start(**kwargs)

    starter.start_plan = logged  # type: ignore[method-assign]
    projector = StubRuntimeProjection(log=log)
    _with_execution_plane(monkeypatch, starter, projector)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        assert _materialize(chain).status_code == 200

    assert [stage for stage, _ in log] == ["project", "start_plan"]
    # And it is *this* project's rooms, not some other topology's.
    assert projector.calls == [UUID(issue_id)]


def test_a_runtime_without_rooms_answers_503_and_starts_nothing(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The controller cannot give the teams rooms: refuse, do not start.

    Before B-11 was fixed this round started anyway and died on its first
    dispatch. The three assertions are the three halves of "honest 503": the
    status, that ``start_plan`` was never reached, and that the draft is still
    a draft so the same key can finish the round later.
    """

    from repomesh.modules.repository_intelligence.ports import (
        RuntimeProjectionUnavailable,
    )

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()

    class Unreachable:
        calls = 0

        async def project(self, project_id: UUID) -> None:
            Unreachable.calls += 1
            raise RuntimeProjectionUnavailable(
                "AgentTeams HTTP 503: controller is not ready"
            )

    _with_execution_plane(monkeypatch, starter, Unreachable())
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        response = _materialize(chain)
        assert response.status_code == 503, response.text
        assert "no rooms for this project's teams" in response.json()["detail"]
        assert "controller is not ready" in response.json()["detail"]
        assert starter.calls == []
        # Unconsumed: the round is still materialisable.
        assert chain.read()["step"] == 4

        # The same key retries the whole projection rather than replaying a
        # result nobody produced.
        assert _materialize(chain).status_code == 503
        assert Unreachable.calls == 2


def test_a_retry_after_a_runtime_failure_finishes_the_round(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Re-entrancy across the new stage, under a *different* key.

    The A-3 rule is that a retry completes the half-executed round instead of
    racing it. A projection failure leaves the topology built and nothing
    started, so the second attempt must project again, start once, and hand
    back one plan — not a second one alongside an orphan.
    """

    from repomesh.modules.repository_intelligence.ports import (
        RuntimeProjectionUnavailable,
    )

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()

    class FlakyRuntime:
        def __init__(self) -> None:
            self.calls = 0

        async def project(self, project_id: UUID) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeProjectionUnavailable("rooms are not there yet")

    runtime = FlakyRuntime()
    _with_execution_plane(monkeypatch, starter, runtime)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        first = _materialize(chain, key="rooms-came-late")
        assert first.status_code == 503, first.text
        topology_after_failure = _topology(container, issue_id)
        assert topology_after_failure is not None
        principals_after_failure = {p.id for p in _principals(container)}

        second = _materialize(chain, key="a-fresh-panel-key")
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "materialized"

    assert runtime.calls == 2
    assert len(starter.calls) == 1
    # The repair reused what the first attempt built rather than rivalling it.
    assert _topology(container, issue_id).id == topology_after_failure.id
    assert {p.id for p in _principals(container)} == principals_after_failure


def test_an_unconfigured_control_plane_refuses_rather_than_skipping(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The real projector, with the container the way defect B-11 found it.

    No stub for the projection here — this is the composition root's own
    adapter, and the point is that a container without an AgentTeams control
    plane answers 503 instead of quietly starting a roomless round, which is
    precisely how the defect stayed invisible.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    monkeypatch.setattr(
        ApplicationContainer, "execution_plan_starter", lambda _self: starter
    )
    container = replace(application_container, llm_client=_chain_llm())
    assert container.agent_team_control_plane is None

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        response = _materialize(chain)
        assert response.status_code == 503, response.text
        assert "control plane is not configured" in response.json()["detail"]
        assert starter.calls == []
        assert chain.read()["step"] == 4


# ---------------------------------------------------------------------------
# The gate and the missing execution plane
# ---------------------------------------------------------------------------


def test_a_blocked_scope_checkpoint_is_passed_through_verbatim(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The bridge's reason reaches the panel unedited, and nothing is rebuilt.

    A project that already has a topology keeps it — including the supervision
    policy an operator chose — so ``ensure`` must not overwrite one to get a
    permissive gate.
    """

    _configure(monkeypatch)
    organization_id, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        # A supervised topology, created the way an operator creates one — and
        # covering only one of the plan's two repositories, so "was it rebuilt"
        # is answerable by counting teams.
        supervised = _supervised_topology(
            container,
            project_id=UUID(issue_id),
            organization_id=organization_id,
            leader_id=leader_id,
            repository_name="ts-notify",
        )

        response = _materialize(chain)
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "human_checkpoint_pending"
        assert starter.calls == []

        kept = _topology(container, issue_id)
        assert kept.id == supervised.id
        assert kept.execution_mode is ProjectExecutionMode.SUPERVISED
        assert len(kept.repository_teams) == 1


def test_no_execution_plane_answers_503_and_leaves_the_round_open(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """AgentTeams is not configured: refuse before any spec, and say why.

    The existing degradation, unchanged — ``POST /bridge/materialize`` reads it
    the same way, and ``run_pipeline.py`` treats a 503 as "the plan is valid,
    nothing was scheduled". The round stays materialisable once the plane is
    there, which is only true because the bridge fails closed before writing.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    _with_execution_plane(monkeypatch, None)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        response = _materialize(chain)
        assert response.status_code == 503, response.text
        assert "task orchestration plane is not configured" in response.json()["detail"]

        # Still a draft: nothing consumed it, so the round can be started again
        # once the plane exists.
        assert chain.read()["step"] == 4

    # And it can: same issue, same key, with a plane configured. A fresh
    # container because ``plan_execution_bridge`` is cached per container, and
    # the cached one was built without a plane.
    _with_execution_plane(monkeypatch, StubPlanStarter())
    revived = replace(application_container, llm_client=_chain_llm())
    with TestClient(create_app(revived)) as client:
        chain = Chain(client, issue_id, leader_id)
        retried = _materialize(chain)
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "materialized"


# ---------------------------------------------------------------------------
# The service's own units
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["", "short"])
def test_the_idempotency_key_has_a_floor(
    application_container: ApplicationContainer, monkeypatch, key: str
) -> None:
    """Eight characters, the same floor every other §4 trigger uses."""

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    _with_execution_plane(monkeypatch, StubPlanStarter())
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        response = client.post(
            f"/api/v1/issues/{issue_id}/discovery/materialize",
            json={"created_by_agent_id": str(leader_id), "idempotency_key": key},
            headers=HEADERS,
        )
        assert response.status_code == 422, response.text


def test_an_unknown_issue_is_a_404(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    _with_execution_plane(monkeypatch, StubPlanStarter())
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        response = client.post(
            f"/api/v1/issues/{uuid4()}/discovery/materialize",
            json={
                "created_by_agent_id": str(leader_id),
                "idempotency_key": "no-such-issue-key",
            },
            headers=HEADERS,
        )
        assert response.status_code == 404, response.text


def test_the_action_token_is_required(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    _with_execution_plane(monkeypatch, StubPlanStarter())
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        response = client.post(
            f"/api/v1/issues/{uuid4()}/discovery/materialize",
            json={
                "created_by_agent_id": str(leader_id),
                "idempotency_key": "unauthenticated-key",
            },
        )
        assert response.status_code in (401, 403), response.text


def test_a_failed_materialize_does_not_become_a_replayable_receipt(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """A refusal is a record of what went wrong, not a result to hand back.

    Without this the second attempt with the same key would answer 200 and
    report a plan that was never started.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=_chain_llm())

    class Exploding:
        calls = 0

        async def start_plan(self, **_kwargs):
            Exploding.calls += 1
            raise ExecutionPlaneUnavailable("plane went away mid-flight")

    _with_execution_plane(monkeypatch, Exploding())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        first = _materialize(chain, key="doomed-attempt-key")
        assert first.status_code == 503, first.text

        second = _materialize(chain, key="doomed-attempt-key")
        assert second.status_code == 503, second.text
        assert Exploding.calls == 2


def test_the_discovery_read_carries_the_materialization_receipt(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Defect B-12: the receipt the panel needs to know a retry is warranted.

    Materialize has been re-entrant since 7659c89, but §8.3's receipt never
    reached ``GET …/discovery`` — so a round that half-executed looked, to the
    GUI, exactly like a round that had never been tried, and the console had no
    honest reason to offer the retry the server was already prepared to serve.

    Three states in one walk, because it is the *transition* that matters: no
    receipt before an attempt, a failed one after a refusal, and a materialized
    one once the retry lands. The failed leg also pins the two things that make
    the projection honest — the error is the server's own words, verbatim, and
    the replay bookkeeping stays server-side.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=_chain_llm())
    starter = StubPlanStarter()

    class Flaky:
        """Refuses once, then behaves — the half-executed round B-12 is about."""

        calls = 0

        async def start_plan(self, **kwargs):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise ExecutionPlaneUnavailable("plane went away mid-flight")
            return await starter.start_plan(**kwargs)

    _with_execution_plane(monkeypatch, Flaky())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        # (1) Never attempted: null, not an empty object posing as an attempt.
        assert chain.read()["materialization"] is None

        assert _materialize(chain, key="doomed-key").status_code == 503

        # (2) Refused. This is the whole defect: the panel can now see it.
        receipt = chain.read()["materialization"]
        assert receipt is not None
        assert receipt["status"] == "failed"
        assert receipt["by_agent_id"] == str(leader_id)
        assert receipt["at"]
        # Verbatim — the server's words, not a category we invented for them.
        assert "plane went away mid-flight" in receipt["error"]
        # Nothing was started, so there is no plan to point at.
        assert receipt["plan_id"] is None
        # Replay bookkeeping stays server-side: a client that can read the key
        # namespace is a client that can collide with it (§8.3).
        assert set(receipt) == {"status", "at", "by_agent_id", "error", "plan_id"}
        # And no derived judgment: the reader decides from `status` alone.
        assert "stuck" not in receipt and "retryable" not in receipt

        # (3) The retry the receipt justifies, under a fresh key as the modal
        # mints one, finishes the round the refusal half-started.
        retry = _materialize(chain, key="retry-key")
        assert retry.status_code == 200, retry.text

        settled = chain.read()["materialization"]
        assert settled["status"] == "materialized"
        assert settled["error"] is None
        assert settled["plan_id"] == retry.json()["plan_id"]


def test_a_repository_staffed_by_another_organization_is_a_409_not_a_500(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The provisioner's cross-organization refusal must reach the caller.

    A repository's leader singleton is global, so a repository already staffed
    by another organization cannot be converged on. That is an honest business
    rejection — and it used to leak out of this endpoint as a 500, found live
    by the final acceptance walk (2026-08-12): a fresh workspace's issue
    covering a seed-staffed repository blew up instead of being refused.
    """

    from repomesh.modules.agent_directory.application import (
        CreateAgent,
        CreateAgentRequest,
        ProvisionRepositoryAgentTeam,
    )
    from repomesh.modules.agent_directory.contracts import AgentRole

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    container = replace(application_container, llm_client=_chain_llm())

    async def staff_elsewhere() -> None:
        rival_org = uuid4()
        rival = await CreateAgent(container.agent_directory).execute(
            CreateAgentRequest(
                organization_id=rival_org,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="rival-org-leader",
            ),
            idempotency_key="rival-org-leader",
        )
        profiles = await container.repository_catalog.list()
        repository_id = next(p.id for p in profiles if p.name == "ts-notify")
        await ProvisionRepositoryAgentTeam(container.agent_directory).provision(
            organization_id=rival_org,
            organization_leader_id=rival.principal.id,
            repository_id=repository_id,
            idempotency_key="rival-staffs-ts-notify",
        )

    asyncio.run(staff_elsewhere())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        response = _materialize(chain)

        assert response.status_code == 409, response.text
        assert "another organization" in response.json()["detail"]
        # Refused before any side effect: no topology row, no execution plan.
        assert _topology(container, issue_id) is None
        assert starter.calls == []


def test_a_team_without_a_room_is_a_503_not_a_500(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The execution plane's "no room yet" is a retry, not a server fault.

    Found live by the final acceptance walk (2026-08-12): materialize answered
    500 with a stack trace because the repository teams it had just
    provisioned had no AgentTeams rooms. Nothing about the request was wrong
    and nothing about the server was broken — the runtime had not caught up
    with the topology — so the panel needs the same reading it gets when the
    plane is missing entirely.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=_chain_llm())

    class Roomless:
        async def start_plan(self, **_kwargs):
            raise CollaborationRouteUnavailable("AgentTeams room is not ready")

    _with_execution_plane(monkeypatch, Roomless())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        response = _materialize(chain, key="roomless-attempt-key")

        assert response.status_code == 503, response.text
        detail = response.json()["detail"]
        assert "AgentTeams room is not ready" in detail
        assert "materialize again" in detail


def test_a_retry_under_a_new_key_repairs_the_first_attempt(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """A reloaded panel must finish the half-started round, not race it.

    The console keeps one idempotency key per round, so the ordinary retry
    reuses it — but a reload mints a new one, and under a new key every write
    of the materialization would be made a second time: a duplicate
    engineering spec, and a second execution plan alongside the one the failed
    attempt stranded. The failed receipt lends its prefix to the retry, so the
    second press repairs the first rather than competing with it.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=_chain_llm())
    starter = StubPlanStarter()

    class RefusesOnce:
        """Refuses the first plan, the way a team whose rooms arrive late does."""

        def __init__(self) -> None:
            self.refused = False

        async def start_plan(self, **kwargs):
            if self.refused:
                return await starter.start_plan(**kwargs)
            self.refused = True
            starter.calls.append(
                (kwargs["project_id"], kwargs["idempotency_key"], kwargs["batches"])
            )
            raise CollaborationRouteUnavailable("AgentTeams room is not ready")

    _with_execution_plane(monkeypatch, RefusesOnce())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        first = _materialize(chain, key="first-attempt-key")
        assert first.status_code == 503, first.text

        second = _materialize(chain, key="a-reloaded-panel-key")
        assert second.status_code == 200, second.text

    assert len(starter.calls) == 2
    assert "first-attempt-key" in starter.calls[0][1]
    # Same plan key both times: the retry lands on the plan the refusal
    # stranded and finishes it instead of writing a second one.
    assert starter.calls[1][1] == starter.calls[0][1]


def test_a_retry_after_the_plan_changed_gets_its_own_keys(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Repairing the first attempt only makes sense while the work is the same.

    Re-running step 3 produces a different plan, and writing it under the
    failed attempt's keys would collide with rows describing the plan it
    replaced — a conflict with no operator move. A plan that has moved on gets
    a fresh prefix.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    replanned = INTEGRATION.replace("统一通知模板", "统一通知模板（返工后）")
    container = replace(
        application_container,
        llm_client=ScriptedLLM(
            ANALYSIS_OK,
            CANDIDATES,
            _confirmation("REQUIRED"),
            _confirmation("REQUIRED"),
            INTEGRATION,
            replanned,
        ),
    )
    starter = StubPlanStarter()

    class RefusesOnce:
        def __init__(self) -> None:
            self.refused = False

        async def start_plan(self, **kwargs):
            if self.refused:
                return await starter.start_plan(**kwargs)
            self.refused = True
            starter.calls.append(
                (kwargs["project_id"], kwargs["idempotency_key"], kwargs["batches"])
            )
            raise CollaborationRouteUnavailable("AgentTeams room is not ready")

    _with_execution_plane(monkeypatch, RefusesOnce())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        assert _materialize(chain, key="stale-attempt-key").status_code == 503
        # Step 3 again: a new integration overwrites the plan on the draft.
        assert chain.run("plan")["status"] == "succeeded"
        assert _materialize(chain, key="after-replan-key").status_code == 200

    assert len(starter.calls) == 2
    assert starter.calls[1][1] != starter.calls[0][1]
    assert "after-replan-key" in starter.calls[1][1]


# ---------------------------------------------------------------------------
# The recipient's Matrix identity (defect A-6)
# ---------------------------------------------------------------------------


def test_a_recipient_without_a_matrix_identity_is_a_503_not_a_bare_500(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """A-6: the dispatch found the room but not the worker behind it.

    Found live by the acceptance walk (2026-08-12). The rooms existed — the
    runtime projection had just made them — so B-11's 503 did not fire; the
    round started, and then the *first dispatch* asked the controller for the
    recipient's Matrix user id and got nothing, because every worker container
    had died on boot. ``AgentTeamsUnavailable`` escaped untranslated and
    FastAPI answered ``text/plain`` "Internal Server Error" with no body at
    all: no detail, nothing to read, nothing to press.

    This is the same reading as the room refusal above, and it must arrive by
    the same route, so it asserts the same three things: the status, the
    server's own words inside it, and a round still repairable afterwards.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=_chain_llm())

    class NoIdentity:
        async def start_plan(self, **_kwargs):
            # What the wrapped messenger now raises out of a dispatch the
            # gateway refused at ``matrix.py``; before the fix this arrived
            # here as ``AgentTeamsUnavailable`` and was translated by nobody.
            raise CollaborationRouteUnavailable(
                "AgentTeams recipient Matrix identity is unavailable"
            )

    _with_execution_plane(monkeypatch, NoIdentity())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        response = _materialize(chain, key="no-identity-key")

        assert response.status_code == 503, response.text
        # A JSON body with a detail, not a bare text/plain 500.
        assert response.headers["content-type"].startswith("application/json")
        detail = response.json()["detail"]
        assert "Matrix identity is unavailable" in detail
        assert "materialize again" in detail
        # The round is not closed: the draft was never consumed.
        assert chain.read()["step"] == 4


def test_a_round_broken_mid_dispatch_is_finished_by_the_next_press(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The A-6 failure lands *after* ``start_plan`` began, and still replays.

    This is the part the room refusal (B-11) does not cover. There the plane
    was refused before it wrote anything; here the plan row exists and its
    first batch is half-assigned, which is exactly the state 7659c89 taught
    ``AdvanceExecutionPlan.start`` to re-enter.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=_chain_llm())
    starter = StubPlanStarter()

    class LosesTheWorkerOnce:
        """Starts the plan, then fails the dispatch — the live A-6 shape."""

        def __init__(self) -> None:
            self.attempts = 0

        async def start_plan(self, **kwargs):
            self.attempts += 1
            if self.attempts > 1:
                return await starter.start_plan(**kwargs)
            # The plan row and its batch are written before the dispatch that
            # fails, so the attempt is recorded the way the real one is.
            starter.calls.append(
                (kwargs["project_id"], kwargs["idempotency_key"], kwargs["batches"])
            )
            raise CollaborationRouteUnavailable(
                "AgentTeams recipient Matrix identity is unavailable"
            )

    plane = LosesTheWorkerOnce()
    _with_execution_plane(monkeypatch, plane)

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        first = _materialize(chain, key="broken-mid-dispatch-key")
        assert first.status_code == 503, first.text
        # The receipt is written failed, and the draft is left unconsumed.
        receipt = _draft_row(container, issue_id).discovery["materialization"]
        assert receipt["status"] == "failed"
        # A failed receipt is a record of what went wrong, not a result: it
        # carries the refusal's words and no plan, so nothing replays it.
        assert "Matrix identity is unavailable" in receipt["error"]
        assert "plan_id" not in receipt
        assert chain.read()["step"] == 4

        # The same key finishes the round the refusal half-started.
        second = _materialize(chain, key="broken-mid-dispatch-key")
        assert second.status_code == 200, second.text

    assert plane.attempts == 2
    # Same plan key both times: the retry re-enters the stranded batch rather
    # than starting a second execution plan beside it.
    assert len(starter.calls) == 2
    assert starter.calls[1][1] == starter.calls[0][1]
