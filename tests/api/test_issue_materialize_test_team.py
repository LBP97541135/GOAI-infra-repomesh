"""The cross-repo test team's platform half (spec S-1, acceptance group A).

A repository whose catalog row carries the ``cross-repo-test-team`` capability
profile is the supply-side switch (CONTEXT.md: 档案开关): every project
materialized while it is set gets that repository's team appended to its
topology, staffed and roomed by the same chain that builds the business
teams. The switch has no reach into the past — tearing it off stops future
projects and leaves built teams standing — and it never outranks the guard:
a plan with no catalog repositories of its own is refused before the test
repository is appended, because a project whose only team is the test team
has no business work to assign.

These are the regression tier of the v1 acceptance table (AC-A1 ~ AC-A5);
the live walk (groups B/C/D) happens on a fresh compose stack. The tests
anchor on the module's interfaces — the materialize endpoint and the topology
view — with one deliberate deepening over the sibling file: the runtime
projection is the production one, run against the recording control-plane
double, because "which skills was the controller asked for" (AC-A1's
coverage-table clause) is an input to the controller and observable nowhere
else.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import UUID

from fastapi.testclient import TestClient

from integrations.agentteams.test_runtime_projection import (
    _RUNTIMES,
    MODEL,
    RecordingControlPlane,
)
from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.integrations.agentteams.runtime_projection import (
    ProjectRuntimeProjection,
)
from repomesh.modules.change_orchestration import ExecutionPlaneUnavailable
from repomesh.modules.repository_intelligence.domain import RepositoryProfile

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
from .test_issue_materialize import (
    StubPlanStarter,
    _materialize,
    _topology,
    _walk_to_plan,
    _with_execution_plane,
)

#: One chain's worth of scripted model answers. Spelled as a tuple rather than
#: a ready-made ``ScriptedLLM`` so a test that walks two chains in one
#: container can pass it twice.
_CHAIN_SCRIPT = (
    ANALYSIS_OK,
    CANDIDATES,
    _confirmation("REQUIRED"),
    _confirmation("REQUIRED"),
    INTEGRATION,
)

#: The frozen coverage table (AC-A1): what the controller must be asked to
#: mount on the test team's resources, verbatim from ``team_skills.py``.
_TEST_LEADER_SKILLS = ("cross-repo-test", "worker-management", "reporting")
_TEST_WORKER_SKILLS = ("integration-run", "task-execution")


def _register_test_assets(container: ApplicationContainer) -> UUID:
    """A registered repository carrying the profile — the switch, set."""

    profile = RepositoryProfile(
        name="test-assets",
        url="https://github.com/acme/test-assets",
        description="联调测试资产仓",
        capability_profile="cross-repo-test-team",
    )
    asyncio.run(container.repository_catalog.add(profile))
    return profile.id


class LiveProjection:
    """The production runtime projection over the recording control plane.

    The stub the rest of the family uses answers ``project`` with nothing,
    which is right for tests about ordering and retries and silent about the
    one question AC-A1 asks: which skills each resource was created with.
    That is an *input* to the controller, so it has to be caught on the way
    in — hence the double on the far side of the real projection.
    """

    def __init__(self, container: ApplicationContainer) -> None:
        self.control_plane = RecordingControlPlane()
        self._container = container

    async def project(self, project_id: UUID) -> None:
        await ProjectRuntimeProjection(
            self._container.agent_directory,
            self._container.project_topology_store,
            self.control_plane,  # type: ignore[arg-type]
            model=MODEL,
            **_RUNTIMES,
            repository_catalog=self._container.repository_catalog,
        ).project(project_id)


def _team_for(topology, repository_id: UUID):
    return next(
        team
        for team in topology.repository_teams
        if team.repository_id == repository_id
    )


def _principal(container: ApplicationContainer, agent_id: UUID):
    return asyncio.run(container.agent_directory.get_view(agent_id))


# ---------------------------------------------------------------------------
# AC-A1 / AC-A2 — the switch is on, so the team is built, staffed and skilled
# ---------------------------------------------------------------------------


def test_a_profiled_repository_seats_a_test_team_in_every_new_topology(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """AC-A1/AC-A2: three teams out of a two-repository plan.

    The receipt keeps S-1's accepted wording — ``team_count`` counts every
    team including the test one, ``repositories`` still lists only the plan's
    own — and the skills assertion is exact equality against the coverage
    table, on the test team *and* on a business team, because an overlay that
    leaked onto coders would pass any subset check.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    test_repository_id = _register_test_assets(application_container)
    starter = StubPlanStarter()
    projector = LiveProjection(application_container)
    _with_execution_plane(monkeypatch, starter, projector)
    container = replace(application_container, llm_client=ScriptedLLM(*_CHAIN_SCRIPT))

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        response = _materialize(chain)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["team_count"] == 3
        assert body["repositories"] == ["ts-notify", "ts-order"]

        topology = _topology(container, issue_id)
        assert len(topology.repository_teams) == 3
        team = _team_for(topology, test_repository_id)

        # AC-A2: roomed like any team the same pass built.
        assert team.room_id
        assert team.leader_room_id

        # AC-A1: staffed — the principals exist and answer for the test repo.
        leader = _principal(container, team.leader_agent_id)
        assert leader is not None
        assert leader.repository_id == test_repository_id
        assert team.worker_agent_ids
        worker = _principal(container, team.worker_agent_ids[0])
        assert worker is not None
        assert worker.leader_agent_id == leader.id

        # AC-A1: the controller was asked for exactly the coverage table.
        skills_by_resource = {
            projection.name: projection.skills
            for projection in projector.control_plane.workers
        }
        assert skills_by_resource[leader.agentteams_resource_name] == _TEST_LEADER_SKILLS
        assert skills_by_resource[worker.agentteams_resource_name] == _TEST_WORKER_SKILLS

        # ...and a business team, projected in the same pass, kept the
        # defaults — the overlay replaces the test team's tuples, nobody
        # else's.
        business_team = next(
            candidate
            for candidate in topology.repository_teams
            if candidate.repository_id != test_repository_id
        )
        business_leader = _principal(container, business_team.leader_agent_id)
        assert business_leader is not None
        assert skills_by_resource[business_leader.agentteams_resource_name] == (
            "code-review",
            "planning",
        )


# ---------------------------------------------------------------------------
# AC-A3 — replays and retries converge on the same team, id for id
# ---------------------------------------------------------------------------


def test_a_same_key_replay_returns_the_same_test_team(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """AC-A3, replay half: identity is asserted by id and name, never counted.

    Counting teams would pass even if the replay had torn the first team down
    and built an identical-looking second one — the historical trap the AC's
    wording exists to forbid.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    test_repository_id = _register_test_assets(application_container)
    _with_execution_plane(
        monkeypatch, StubPlanStarter(), LiveProjection(application_container)
    )
    container = replace(application_container, llm_client=ScriptedLLM(*_CHAIN_SCRIPT))

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        assert _materialize(chain, key="test-team-replay").status_code == 200
        team = _team_for(_topology(container, issue_id), test_repository_id)

        replayed = _materialize(chain, key="test-team-replay")
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["status"] == "replayed"

        after = _team_for(_topology(container, issue_id), test_repository_id)
        assert after.id == team.id
        assert after.agentteams_team_name == team.agentteams_team_name
        assert after.leader_agent_id == team.leader_agent_id


def test_a_fresh_key_retry_keeps_the_topology_field_for_field(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """AC-A3 retry half + AC-A5: the early exit hands back the built topology.

    The first attempt fails *after* the topology is built and roomed — the
    execution plane goes away mid-flight — so the round stays open and the
    retry under a fresh key re-enters ``_ensure_topology`` with a topology
    already on record. That is the early-exit path, and the whole view must
    come back unchanged, field for field: same test team id, same rooms, same
    workers, nothing rebuilt beside it.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    test_repository_id = _register_test_assets(application_container)
    inner = StubPlanStarter()

    class _FailsOnce:
        calls = 0

        async def start_plan(self, **kwargs):
            _FailsOnce.calls += 1
            if _FailsOnce.calls == 1:
                raise ExecutionPlaneUnavailable("plane went away mid-flight")
            return await inner.start_plan(**kwargs)

    _with_execution_plane(
        monkeypatch, _FailsOnce(), LiveProjection(application_container)
    )
    container = replace(application_container, llm_client=ScriptedLLM(*_CHAIN_SCRIPT))

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)

        doomed = _materialize(chain, key="first-attempt-fails")
        assert doomed.status_code == 503, doomed.text
        before = _topology(container, issue_id)
        assert before is not None
        team_before = _team_for(before, test_repository_id)

        retried = _materialize(chain, key="a-fresh-retry-key")
        assert retried.status_code == 200, retried.text

        after = _topology(container, issue_id)
        assert after == before
        team_after = _team_for(after, test_repository_id)
        assert team_after.id == team_before.id
        assert team_after.agentteams_team_name == team_before.agentteams_team_name


# ---------------------------------------------------------------------------
# AC-A4 — the switch is supply-side: no reach into the past
# ---------------------------------------------------------------------------


def test_tearing_the_profile_off_stops_new_projects_and_keeps_built_teams(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """AC-A4, both halves written separately as the spec demands.

    First: a project materialized after the profile is cleared gets business
    teams only. Second: the project materialized before keeps its test team
    untouched — the switch governs what gets *built*, never what exists.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    test_repository_id = _register_test_assets(application_container)
    _with_execution_plane(
        monkeypatch, StubPlanStarter(), LiveProjection(application_container)
    )
    container = replace(
        application_container,
        llm_client=ScriptedLLM(*_CHAIN_SCRIPT, *_CHAIN_SCRIPT),
    )

    with TestClient(create_app(container)) as client:
        first_issue = _create_issue(client, leader_id, key="profiled-issue-key")
        first_chain = Chain(client, first_issue, leader_id)
        _walk_to_plan(first_chain)
        assert _materialize(first_chain, key="profiled-round-key").status_code == 200
        team_before = _team_for(_topology(container, first_issue), test_repository_id)

        # Tear the profile off through the same PATCH the console uses;
        # ``null`` is the one spelling of "default" the API accepts.
        cleared = client.patch(
            f"/api/v1/repositories/{test_repository_id}/capability-profile",
            json={"capability_profile": None},
            headers=HEADERS,
        )
        assert cleared.status_code == 200, cleared.text

        second_issue = _create_issue(client, leader_id, key="torn-issue-key")
        second_chain = Chain(client, second_issue, leader_id)
        _walk_to_plan(second_chain)
        assert _materialize(second_chain, key="torn-round-key").status_code == 200

        # Half one: the new project seats no team on the test repository.
        second_topology = _topology(container, second_issue)
        assert test_repository_id not in {
            team.repository_id for team in second_topology.repository_teams
        }

        # Half two: the earlier project's test team stands exactly as built.
        team_after = _team_for(_topology(container, first_issue), test_repository_id)
        assert team_after == team_before


# ---------------------------------------------------------------------------
# S-1's frozen guard order and dedup rule
# ---------------------------------------------------------------------------


def test_the_guard_judges_the_plan_before_the_test_repository_is_appended(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """S-1 rule 1, the blast-radius rule: guard first, append second.

    Every repository the plan names has left the catalog, while the profiled
    test repository is very much in it. If the append ran first the id set
    would be non-empty and the round would start a project whose only team is
    the test team — an org chart with no business work. The refusal must win,
    and nothing may be built.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    _register_test_assets(application_container)
    starter = StubPlanStarter()
    _with_execution_plane(monkeypatch, starter, LiveProjection(application_container))
    container = replace(application_container, llm_client=ScriptedLLM(*_CHAIN_SCRIPT))

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)
        # The plan's own repositories vanish from under it: rewrite the
        # snapshot's DAG onto names the catalog has never held, the same
        # direct-write manoeuvre ``_state_own_tests`` established.
        _rename_dag_repositories(container, issue_id, "ghost-repo")

        response = _materialize(chain)

        assert response.status_code == 409, response.text
        assert "none of the plan's repositories are in the catalog" in (
            response.json()["detail"]
        )
        assert starter.calls == []
        assert _topology(container, issue_id) is None


def test_a_plan_that_names_the_test_repository_gets_one_team_on_it(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """S-1 rule 3: ``not in`` dedup — a plan node on the test repo is no
    double-booking.

    The plan itself names ``test-assets`` (someone scoped work onto the test
    asset repository), so the append must recognise it is already seated and
    add nothing. One repository, one team, asserted by the id set.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    test_repository_id = _register_test_assets(application_container)
    _with_execution_plane(
        monkeypatch, StubPlanStarter(), LiveProjection(application_container)
    )
    container = replace(application_container, llm_client=ScriptedLLM(*_CHAIN_SCRIPT))

    with TestClient(create_app(container)) as client:
        issue_id = _create_issue(client, leader_id)
        chain = Chain(client, issue_id, leader_id)
        _walk_to_plan(chain)
        # The plan's DAG lands on the test repository itself.
        _rename_dag_repositories(container, issue_id, "test-assets")

        response = _materialize(chain)
        assert response.status_code == 200, response.text

        topology = _topology(container, issue_id)
        seated = [
            team.id
            for team in topology.repository_teams
            if team.repository_id == test_repository_id
        ]
        assert len(seated) == 1
        assert {team.repository_id for team in topology.repository_teams} == {
            test_repository_id
        }


def _rename_dag_repositories(
    container: ApplicationContainer, issue_id: str, repository: str
) -> None:
    """Point every node of the draft's task DAG at ``repository``.

    The scripted chain cannot produce a DAG over unregistered names — the
    classification step filters candidates against the catalog and the plan
    integrator backfills every confirmed repository into the DAG — so the
    states S-1's guard and dedup rules exist for are written directly onto
    the snapshot, the way ``_state_own_tests`` already writes ``tests``.
    """

    store = container.plan_snapshot_store()

    async def write() -> None:
        rows = await store.list_all(UUID(issue_id))
        draft = rows[0]
        seen: set[str] = set()
        task_dag = []
        for node in draft.task_dag:
            renamed = {**node, "repository": repository, "depends_on": []}
            if repository in seen:
                continue
            seen.add(repository)
            task_dag.append(renamed)
        async with container.database.transaction() as session:
            from repomesh.modules.repository_intelligence.infrastructure.models import (
                PlanSnapshotRecord,
            )

            record = await session.get(PlanSnapshotRecord, draft.id)
            record.task_dag = task_dag

    asyncio.run(write())
