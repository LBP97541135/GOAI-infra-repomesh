"""Materialize-and-start over HTTP (contract v0.4 §8).

The one write in the console round that creates work, and the tests here are
about what it refuses as much as what it does: an unfinished chain, an
unapproved tiering, a blocked checkpoint, and a retry that must not build a
second team or a second task.

Nothing reaches the network. The LLM is the scripted double the chain tests
use, and the only pieces replaced are the three that need a live AgentTeams —
``start_plan`` (the execution plane), the runtime projection that makes the
teams' Matrix rooms, and the controller read that says whether a member's body
is RepoMesh's to run. Everything between the endpoint and them is production
code: the real snapshot store, the real topology provisioning, the real
specification service, the real checkpoint gate, the real bridge, and the real
external-member readiness gate over the real lease store.
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
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_runtime.application.readiness import (
    ReadinessReportKind,
    ReportExternalMemberReadinessCommand,
)
from repomesh.modules.agent_runtime.contracts import ExternalMemberRole
from repomesh.modules.agent_runtime.ports.agent_team import WorkerRuntimeRef
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
    TaskPublicationUnavailable,
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


class StubMemberControlPlane:
    """The AgentTeams worker documents the readiness gate reads (ADR 0004).

    ``containerManaged`` is the only field it looks at, and it is the whole
    difference between the two fleets this file drives: a managed one, whose
    bodies the controller runs and whose liveness the gate never claims, and an
    external one, where every body is an operator's own CLI and every member has
    to be holding a lease before a round may start.

    Stubbing this rather than the gate itself keeps the production join under
    test everywhere — the directory and the lease store stay real, so a suite
    that passes is a suite where the real gate answered.

    ``container_managed`` is a public attribute rather than a constructor-only
    one because a fleet can change hands: the prefix-lending test below needs
    one round the gate lets through followed by one it blocks, and flipping this
    is the whole of that.
    """

    def __init__(self, *, container_managed: bool) -> None:
        self.container_managed = container_managed

    async def get_worker(self, name: str) -> WorkerRuntimeRef:
        return WorkerRuntimeRef(
            name=name, phase="Running", container_managed=self.container_managed
        )


def _with_execution_plane(monkeypatch, starter, projector=None) -> None:
    """Stub the three AgentTeams seams: the rooms, the plane, the member documents.

    The first two go together on purpose. A round that starts without its rooms
    is defect B-11, so a test that stubbed only ``start_plan`` would be
    asserting against a state the endpoint no longer allows.

    The third makes this suite's fleet a *managed* one, which is what it has
    always been: every member here is a container the controller runs, so the
    readiness gate reports on nobody and the round proceeds (AC-04). The tests
    that drive an external fleet override it with
    :func:`_with_external_members`.
    """

    monkeypatch.setattr(
        ApplicationContainer, "execution_plan_starter", lambda _self: starter
    )
    monkeypatch.setattr(
        ApplicationContainer,
        "topology_runtime_projector",
        lambda _self: projector if projector is not None else StubRuntimeProjection(),
    )
    monkeypatch.setattr(
        ApplicationContainer,
        "external_worker_binding_control_plane",
        lambda _self: StubMemberControlPlane(container_managed=True),
    )


def _with_member_control_plane(monkeypatch, control_plane) -> None:
    """Install one shared controller double, so a test can change its answers."""

    monkeypatch.setattr(
        ApplicationContainer,
        "external_worker_binding_control_plane",
        lambda _self: control_plane,
    )


def _with_external_members(monkeypatch) -> None:
    """Every member of this project's teams is a local CLI RepoMesh does not run.

    Applied after :func:`_with_execution_plane`, which installs the managed
    answer this replaces.
    """

    _with_member_control_plane(monkeypatch, StubMemberControlPlane(container_managed=False))


def _team_members(container: ApplicationContainer, project_id: str) -> tuple[UUID, ...]:
    """Every agent the project's repository teams hold, leaders included."""

    topology = _topology(container, project_id)
    return tuple(
        agent_id
        for team in topology.repository_teams
        for agent_id in (team.leader_agent_id, *team.worker_agent_ids)
    )


def _report_ready(container: ApplicationContainer, member_ids: tuple[UUID, ...]) -> None:
    """Each member's Bridge reports startup, the way a launched CLI does.

    Written through the real store rather than over HTTP because the report
    endpoint authenticates as the member, and what these tests are about is the
    gate downstream of the lease, not the credential upstream of it.
    """

    store = container.external_member_readiness_store()
    principals = {principal.id: principal for principal in _principals(container)}

    async def report() -> None:
        for member_id in member_ids:
            leader = principals[member_id].role is AgentRole.REPOSITORY_LEADER
            await store.startup(
                ReportExternalMemberReadinessCommand(
                    member_agent_id=member_id,
                    instance_id=uuid4(),
                    kind=ReadinessReportKind.STARTUP,
                    role=(
                        ExternalMemberRole.REPOSITORY_LEADER
                        if leader
                        else ExternalMemberRole.WORKER
                    ),
                    leader_lane=leader,
                    governed_lane=not leader,
                    workspace_root=None if leader else ".repomesh-workspaces",
                )
            )

    asyncio.run(report())


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


def _readiness(chain: Chain):
    return chain.client.get(
        f"/api/v1/issues/{chain.issue_id}/discovery/readiness", headers=HEADERS
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
# The verification commands the console never supplied (A-19)
# ---------------------------------------------------------------------------


def test_the_console_round_carries_its_repositories_verification_commands(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Defect A-19: the plan handed to the execution plane must not be untested.

    ``TaskNode.tests`` says the integration LLM does not emit verification
    commands and the caller supplies them when materialising. The script era's
    caller did; this one — the console's only path to work — supplied nothing,
    so every round travelled the whole chain with an empty list and the Runner
    dispatch went out with ``testCommands: []``. The Worker then verified
    nothing and the console showed a green tick over unchecked work.

    Asserted where the plan leaves the module rather than on the service's
    return value: the injection is only worth anything if it survives the
    bridge, and "the caller supplies them" is exactly the kind of claim that
    was true of one caller and false of the other.

    ``ts-order`` declares no commands and gets none. That half is the honest
    fallback and is asserted too, because a fix that invented a default would
    put a command that does not exist into a real dispatch.

    The last assertion guards a trap the first attempt at this fix fell into.
    ``materialize`` writes the plan it was handed into the draft's ``task_dag``,
    and §8 fingerprints that column to decide whether a retry under a *new* key
    may inherit the failed attempt's idempotency prefix. Injecting before that
    write changed the fingerprint between attempts, so the retry forked the
    round instead of repairing it — A-5's failure, reintroduced by A-5's fix.
    The snapshot stays the LLM's; only the execution plan is verified.
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

        # The plan on the draft is what the integration LLM emitted, and it
        # emitted no verification commands at all. This is the defect's start.
        row = _draft_row(container, issue_id)
        assert all(not node.get("tests") for node in row.task_dag)

        assert _materialize(chain).status_code == 200

        # ...and it still is: the round's fingerprint did not move.
        assert all(not node.get("tests") for node in _draft_row(container, issue_id).task_dag)

    assert len(starter.calls) == 1
    batches = starter.calls[0][2]
    by_repository = {
        planned.title: planned.tests for batch in batches for planned in batch
    }
    assert by_repository == {
        "Implement changes for ts-notify": ("python scripts/run_tests.py",),
        "Implement changes for ts-order": (),
    }
    # Defect A-21: and where the command reads from, so the permit downstream
    # can let the agent write the test the command will look for.
    by_paths = {
        planned.title: planned.test_paths for batch in batches for planned in batch
    }
    assert by_paths == {
        "Implement changes for ts-notify": ("tests/**",),
        "Implement changes for ts-order": (),
    }


def test_a_plan_that_states_its_own_tests_is_not_overwritten_by_the_catalog(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The catalog is a default, not an override.

    Nothing writes verification commands into a snapshot today, but ``tests``
    is part of the plan's persisted shape and a plan that states its own must
    outrank a catalog row — otherwise the catalog silently rewrites work
    somebody chose deliberately, which is the same class of bug as supplying
    nothing.
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
        _state_own_tests(container, issue_id, "ts-notify", ("make verify",))
        assert _materialize(chain).status_code == 200

    batches = starter.calls[0][2]
    stated = {
        planned.title: planned.tests for batch in batches for planned in batch
    }["Implement changes for ts-notify"]
    assert stated == ("make verify",)


def _state_own_tests(
    container: ApplicationContainer, issue_id: str, repository: str, tests: tuple[str, ...]
) -> None:
    """Write verification commands onto the draft's own task DAG."""

    store = container.plan_snapshot_store()

    async def write() -> None:
        rows = await store.list_all(UUID(issue_id))
        draft = rows[0]
        task_dag = [
            {**node, "tests": list(tests)} if node.get("repository") == repository else node
            for node in draft.task_dag
        ]
        async with container.database.transaction() as session:
            from repomesh.modules.repository_intelligence.infrastructure.models import (
                PlanSnapshotRecord,
            )

            record = await session.get(PlanSnapshotRecord, draft.id)
            record.task_dag = task_dag

    asyncio.run(write())


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


def test_a_runtime_that_refuses_on_the_merits_answers_409_not_503(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """§8.7.1's second ruling, implemented (A-8).

    The distinction is the only thing the operator can act on. A 503 saying
    "materialize again once AgentTeams answers" is a lie when AgentTeams has
    already answered and said no: the button works, the round stays open, and
    pressing it changes nothing forever — which is exactly what the three
    stuck issues did. The 409 says stop pressing and carries the controller's
    own sentence, which names the resource to go fix.

    What has not changed: nothing was started, so the round is still
    materialisable once the spec is reconciled.
    """

    from repomesh.modules.repository_intelligence.ports import (
        RuntimeProjectionConflict,
    )

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()

    class Refuses:
        async def project(self, project_id: UUID) -> None:
            raise RuntimeProjectionConflict(
                "AgentTeams HTTP 400: Worker repomesh-leader-b-checkout is already "
                "a member of Team repomesh-team-6c503f0227a44e9280b3ab29775c0b76"
            )

    _with_execution_plane(monkeypatch, starter, Refuses())
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        response = _materialize(chain)
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        # The controller's words, verbatim — the actionable half.
        assert "is already a member of Team" in detail
        assert "repomesh-team-6c503f0227a44e9280b3ab29775c0b76" in detail
        # And explicitly *not* the retry advice the 503 gives.
        assert "materialize again once AgentTeams answers" not in detail
        assert "retrying will not help" in detail
        assert starter.calls == []
        assert chain.read()["step"] == 4


def test_the_composition_root_splits_a_conflict_out_of_the_retryable_family(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Where the split is actually made, tested at the seam that makes it.

    ``topology_runtime_projector`` is the only place the AgentTeams taxonomy
    and the port's refusals are allowed to meet, so the mapping is not
    observable from either side alone. Four inputs, because folding any one of
    them the wrong way is a defect someone has already paid for: the rooms-not-
    yet case must stay retryable even though it is an ``AgentTeamsError``, and
    a 5xx from the controller is a bad day rather than a verdict.
    """

    from repomesh.integrations.agentteams import (
        AgentTeamsConflict,
        AgentTeamsResponseError,
        AgentTeamsRoomsPending,
        AgentTeamsUnavailable,
    )
    from repomesh.modules.repository_intelligence.ports import (
        RuntimeProjectionConflict,
        RuntimeProjectionUnavailable,
    )

    conflict = RuntimeProjectionConflict
    unavailable = RuntimeProjectionUnavailable
    cases = [
        (AgentTeamsResponseError(400, "is already a member of Team x"), conflict),
        (AgentTeamsConflict("existing AgentTeams worker differs in: runtime"), conflict),
        (AgentTeamsRoomsPending("rooms are not there yet"), unavailable),
        (AgentTeamsUnavailable("controller unreachable"), unavailable),
        (AgentTeamsResponseError(503, "controller is not ready"), unavailable),
    ]
    # Any non-None control plane will do: the projection itself is stubbed, and
    # the only thing under test is which port refusal the adapter picks.
    container = replace(application_container, agent_team_control_plane=object())

    for raised, expected in cases:

        class _Projection:
            def __init__(self, error=raised) -> None:
                self._error = error

            async def project(self, project_id: UUID) -> None:
                raise self._error

        monkeypatch.setattr(
            "repomesh.integrations.agentteams.ProjectRuntimeProjection",
            lambda *a, **k: _Projection(),
        )
        with pytest.raises(expected) as caught:
            asyncio.run(container.topology_runtime_projector().project(uuid4()))
        # The controller's sentence survives the translation either way.
        assert str(raised) in str(caught.value)


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


# ---------------------------------------------------------------------------
# The store that carries the task package (defect A-10)
# ---------------------------------------------------------------------------

#: The sentence the live acceptance walk got back, verbatim.
LIVE_S3_REFUSAL = (
    "S3 operation failed; code: InvalidAccessKeyId, message: The Access Key Id "
    "you provided does not exist in our records., resource: /agentteams-storage, "
    "bucket_name: agentteams-storage"
)


def test_a_storage_that_refuses_the_task_package_is_a_503_not_a_bare_500(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """A-10: the round got further than any refusal before it and still died bare.

    Found live 2026-08-12. The rooms existed and the identities existed, so
    neither B-11's nor A-6's 503 fired; the plan started, the task rows were
    written, and then the upload of the Worker's task package was refused for
    a credential the operator could fix in a minute. ``S3Error`` was translated
    by nobody and FastAPI answered ``text/plain`` "Internal Server Error".

    Same three assertions as its two siblings: the status, the server's own
    words inside it, and a round still repairable afterwards.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=_chain_llm())

    class RefusedByStorage:
        async def start_plan(self, **_kwargs):
            # What the wrapped publisher now raises out of an upload the store
            # refused; before the fix this arrived here as a raw ``S3Error``.
            raise TaskPublicationUnavailable(LIVE_S3_REFUSAL)

    _with_execution_plane(monkeypatch, RefusedByStorage())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        response = _materialize(chain, key="storage-refused-key")

        assert response.status_code == 503, response.text
        # A JSON body with a detail, not a bare text/plain 500.
        assert response.headers["content-type"].startswith("application/json")
        detail = response.json()["detail"]
        # The store's own words: which code, which bucket. That is the whole
        # actionable content and nothing we could write replaces it.
        assert "InvalidAccessKeyId" in detail
        assert "agentteams-storage" in detail
        assert "materialize again" in detail
        # Unlike B-11's, this 503 must not claim nothing was started.
        assert "nothing was started" not in detail
        # The round is not closed: the draft was never consumed.
        assert chain.read()["step"] == 4


def test_a_round_broken_at_publish_is_finished_by_the_next_press(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The acceptance criterion, over HTTP: press materialize again.

    A-6's failure lands after ``start_plan`` began; this one lands after it has
    also written task rows, which is further than the replay used to reach. The
    receipt is still written failed and the draft still left unconsumed, so the
    same key finishes the round.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=_chain_llm())
    starter = StubPlanStarter()

    class LosesTheBucketOnce:
        """Starts the plan and writes its tasks, then fails the upload."""

        def __init__(self) -> None:
            self.attempts = 0

        async def start_plan(self, **kwargs):
            self.attempts += 1
            if self.attempts > 1:
                return await starter.start_plan(**kwargs)
            # The plan row and its task rows are written before the upload
            # that fails, so the attempt is recorded the way the real one is.
            starter.calls.append(
                (kwargs["project_id"], kwargs["idempotency_key"], kwargs["batches"])
            )
            raise TaskPublicationUnavailable(LIVE_S3_REFUSAL)

    plane = LosesTheBucketOnce()
    _with_execution_plane(monkeypatch, plane)

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        first = _materialize(chain, key="broken-at-publish-key")
        assert first.status_code == 503, first.text
        receipt = _draft_row(container, issue_id).discovery["materialization"]
        assert receipt["status"] == "failed"
        assert "InvalidAccessKeyId" in receipt["error"]
        assert "plan_id" not in receipt
        assert chain.read()["step"] == 4

        # The same key finishes the round the refusal half-started.
        second = _materialize(chain, key="broken-at-publish-key")
        assert second.status_code == 200, second.text

    assert plane.attempts == 2
    # Same plan key both times: the retry re-enters the stranded batch rather
    # than starting a second execution plan beside it.
    assert len(starter.calls) == 2
    assert starter.calls[1][1] == starter.calls[0][1]


def test_a_new_key_also_repairs_a_round_broken_at_publish(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """A reloaded panel, or a second operator, must not fork the round.

    7659c89's other half: a failed materialization receipt lends its prefix to
    the next attempt, so a retry under a *new* key repairs the first attempt
    instead of racing it with a second execution plan. A-10 has to inherit that
    unchanged, because its failure leaves more behind than A-6's did.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=_chain_llm())
    starter = StubPlanStarter()

    class LosesTheBucketOnce:
        def __init__(self) -> None:
            self.attempts = 0

        async def start_plan(self, **kwargs):
            self.attempts += 1
            if self.attempts > 1:
                return await starter.start_plan(**kwargs)
            starter.calls.append(
                (kwargs["project_id"], kwargs["idempotency_key"], kwargs["batches"])
            )
            raise TaskPublicationUnavailable(LIVE_S3_REFUSAL)

    _with_execution_plane(monkeypatch, LosesTheBucketOnce())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        assert _materialize(chain, key="publish-first-key").status_code == 503
        second = _materialize(chain, key="publish-second-key")
        assert second.status_code == 200, second.text

    # The second attempt borrowed the failed receipt's prefix, so the plane saw
    # one plan key and not two.
    assert len(starter.calls) == 2
    assert starter.calls[1][1] == starter.calls[0][1]


# ---------------------------------------------------------------------------
# The external members' readiness gate (AC-03, AC-04)
# ---------------------------------------------------------------------------


def test_a_round_whose_local_cli_members_are_down_is_refused_with_the_names(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The refusal this whole feature exists for, and its actionable half.

    Every member of this project's teams is an external one whose CLI nobody
    launched. Started anyway, the round would write its tasks and dispatch them
    into rooms no process is reading — a green console over work that will never
    move, and no button anywhere that re-delivers it. So the gate refuses
    *before* the materializer, and the body names each member so the operator
    knows which machines to go start.

    Three things make the refusal honest and are asserted as three: nothing was
    started, the receipt records the block, and the draft is unconsumed so the
    round is still there to finish.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    _with_external_members(monkeypatch)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        response = _materialize(chain, key="nobody-is-running-key")
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]

        # Structured, not a sentence: the next action is per member.
        assert detail["code"] == "external_members_not_ready"
        members = _team_members(container, issue_id)
        assert detail["message"] == f"{len(members)} local CLI members are not ready"
        assert {row["agentId"] for row in detail["members"]} == {
            str(member_id) for member_id in members
        }
        assert {row["status"] for row in detail["members"]} == {"offline"}
        # "never launched" rather than "stopped answering": only the first is
        # fixed by starting the process, which is the remedy being offered.
        assert {row["reason"] for row in detail["members"]} == {"no readiness report"}
        # Both roles are named, because a leader's Bridge and a worker's are
        # started differently and the panel has to say which.
        assert {row["role"] for row in detail["members"]} == {
            "repository_leader",
            "worker",
        }

        # Nothing was started, and the round is still open.
        assert starter.calls == []
        receipt = _draft_row(container, issue_id).discovery["materialization"]
        assert receipt["status"] == "blocked"
        assert receipt["by_agent_id"] == str(leader_id)
        assert receipt["at"]
        # The receipt carries no error string at all: nothing failed, and a
        # panel reading `error` would render an empty box under a refusal that
        # has a list to show instead.
        assert "error" not in receipt
        assert {row["agentId"] for row in receipt["blocking_members"]} == {
            str(member_id) for member_id in members
        }
        assert _draft_row(container, issue_id).execution_plan_id is None
        assert chain.read()["step"] == 4


def test_starting_the_missing_members_lets_the_same_key_finish_the_round(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """AC-03's remedy: launch the CLI, press the button again, nothing forks.

    A blocked receipt is deliberately not a failed one, and this is the
    difference in behaviour. ``_replay`` hands back only a ``materialized``
    receipt, so the same key runs the whole path again rather than replaying a
    refusal — and ``_prefix`` lends a prefix only after a ``failed`` receipt, so
    the round is not carrying the first attempt's key namespace for a first
    attempt that wrote nothing.

    The counters are the assertion. Both attempts answer about the same two
    teams, so equality of the response proves nothing; that the topology row is
    the same one, the principals are the same set, and the plane was asked
    exactly once is what says the retry finished the round instead of building a
    second one beside it.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    _with_external_members(monkeypatch)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        blocked = _materialize(chain, key="one-key-for-this-round")
        assert blocked.status_code == 409, blocked.text
        # The teams were built before the gate ran — that is the repair
        # `_ensure_topology` makes on the way in — so the retry has something to
        # be compared against.
        topology_after_block = _topology(container, issue_id)
        principals_after_block = {p.id for p in _principals(container)}

        _report_ready(container, _team_members(container, issue_id))

        retried = _materialize(chain, key="one-key-for-this-round")
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "materialized"
        assert retried.json()["team_count"] == 2

        settled = _topology(container, issue_id)
        assert settled.id == topology_after_block.id
        assert len(settled.repository_teams) == len(topology_after_block.repository_teams)
        assert {p.id for p in _principals(container)} == principals_after_block

    assert len(starter.calls) == 1


def test_one_member_still_down_holds_the_round_and_names_only_that_member(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """A partly-launched fleet is still a refusal, and a precise one.

    The over-reporting failure is the one worth guarding: a gate that answered
    "the fleet is not ready" would send an operator round every machine, and a
    gate that answered on the first member it found would hide the rest.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    _with_external_members(monkeypatch)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        assert _materialize(chain, key="partly-launched-key").status_code == 409
        members = _team_members(container, issue_id)
        _report_ready(container, members[1:])

        response = _materialize(chain, key="partly-launched-key")
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]

    assert detail["message"] == "1 local CLI members are not ready"
    assert [row["agentId"] for row in detail["members"]] == [str(members[0])]
    assert starter.calls == []


def test_the_readiness_precheck_answers_the_panel_and_changes_nothing(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The advisory read the modal shows before offering the button.

    Advisory is the whole of its status: a lease is a claim about *now*, so this
    answer is already a moment old when it is rendered, and the gate inside
    materialize stays the authority. What it must be is free of side effects —
    a precheck that consumed a draft or wrote a receipt would make looking at
    the panel a way to change the round — so the discovery projection is read
    either side of the call and compared.

    The flip from offline to ready is asserted too, because a canned answer
    would satisfy every other assertion here.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter)
    _with_external_members(monkeypatch)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)
        # The blocked attempt is what provisions this project's teams, so the
        # directory holds the members the precheck derives its set from.
        assert _materialize(chain, key="precheck-round-key").status_code == 409
        members = _team_members(container, issue_id)

        before = chain.read()
        down = _readiness(chain)
        assert down.status_code == 200, down.text
        body = down.json()
        assert body["checkedAt"]
        assert {row["agentId"] for row in body["members"]} == {
            str(member_id) for member_id in members
        }
        assert {row["status"] for row in body["members"]} == {"offline"}

        # Read-only: the round is exactly where it was.
        assert chain.read() == before
        assert _draft_row(container, issue_id).execution_plan_id is None
        assert starter.calls == []

        _report_ready(container, members)
        up = _readiness(chain).json()
        assert {row["status"] for row in up["members"]} == {"ready"}


def test_a_block_between_a_failure_and_its_repair_keeps_the_lent_prefix(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """A blocked receipt must carry a lending obligation it inherited, not drop it.

    The hole this closes needs three attempts and is invisible in any two of
    them. Attempt A passes the gate and dies at publish, leaving a *failed*
    receipt whose prefix ``disc-A`` names rows the plane already wrote. Attempt
    B inherits ``disc-A`` — and is then refused by the readiness gate, which
    overwrites the receipt with a *blocked* one. If ``blocked`` did not lend,
    attempt C would compute a fresh prefix, materialise under it, and orphan the
    ``disc-A`` rows: a second execution plan racing the first, which is exactly
    the duplicate ``_prefix`` exists to prevent.

    Lending after a *pure* block is harmless for the reason ``_record_block``
    gives — a block creates nothing keyed on the prefix — so the rule is simply
    that both refusals lend. What is asserted here is the inheritance: one plan
    key across the whole sequence, and it is A's.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    starter = StubPlanStarter()
    # The fleet starts managed, so attempt A reaches the materializer at all.
    control_plane = StubMemberControlPlane(container_managed=True)

    class LosesTheBucketOnce:
        """Writes the plan and its task rows, then fails the upload — A-10's shape."""

        def __init__(self) -> None:
            self.attempts = 0

        async def start_plan(self, **kwargs):
            self.attempts += 1
            if self.attempts > 1:
                return await starter.start_plan(**kwargs)
            starter.calls.append(
                (kwargs["project_id"], kwargs["idempotency_key"], kwargs["batches"])
            )
            raise TaskPublicationUnavailable(LIVE_S3_REFUSAL)

    plane = LosesTheBucketOnce()
    _with_execution_plane(monkeypatch, plane)
    _with_member_control_plane(monkeypatch, control_plane)
    container = replace(application_container, llm_client=_chain_llm())

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        # (A) Through the gate, and broken at publish.
        assert _materialize(chain, key="lending-key-a").status_code == 503
        failed = _draft_row(container, issue_id).discovery["materialization"]
        assert failed["status"] == "failed"
        assert failed["prefix"] == "disc-lending-key-a"

        # The same members are now local CLIs, and nobody has launched one.
        control_plane.container_managed = False

        # (B) A reloaded panel's key, refused by the gate. The receipt it
        # overwrites A's with must still name A's prefix.
        blocked = _materialize(chain, key="lending-key-b")
        assert blocked.status_code == 409, blocked.text
        receipt = _draft_row(container, issue_id).discovery["materialization"]
        assert receipt["status"] == "blocked"
        assert receipt["idempotency_key"] == "lending-key-b"
        assert receipt["prefix"] == "disc-lending-key-a"
        # And it is fingerprinted, so the guard below applies to it as it does
        # to a failed one: a replanned round still gets its own prefix.
        assert receipt["plan_fingerprint"] == failed["plan_fingerprint"]

        # (C) The members come up and a third key arrives.
        _report_ready(container, _team_members(container, issue_id))
        repaired = _materialize(chain, key="lending-key-c")
        assert repaired.status_code == 200, repaired.text
        assert repaired.json()["status"] == "materialized"

        # One round, one execution plan: the repair finished A rather than
        # starting a rival beside it.
        row = _draft_row(container, issue_id)
        assert str(row.execution_plan_id) == repaired.json()["plan_id"]

    assert plane.attempts == 2
    assert len(starter.calls) == 2
    assert starter.calls[0][1] == starter.calls[1][1]
    assert "lending-key-a" in starter.calls[0][1]


def test_the_readiness_precheck_requires_the_action_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Same door as every other route on this router."""

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    _with_execution_plane(monkeypatch, StubPlanStarter())

    with TestClient(create_app(application_container)) as client:
        issue_id = _create_issue(client, leader_id)
        unauthenticated = client.get(f"/api/v1/issues/{issue_id}/discovery/readiness")

    assert unauthenticated.status_code in (401, 403), unauthenticated.text
