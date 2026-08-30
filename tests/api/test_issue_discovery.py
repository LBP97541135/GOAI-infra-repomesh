"""The discovery chain over HTTP (contract v0.4 §3, §4, §5).

Covers what the panel depends on: the four triggers and their preconditions,
the projection the stepper reads, idempotent replay, downstream invalidation,
the approval gate and its evidence binding, and the one property a 202 is
worthless without — that the blocking model call is off the event loop.

No test here reaches the network. The LLM is a scripted double handed to the
container, and every response is a canned string.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile
from repomesh.persistence.models.platform import AuditEventRecord
from repomesh.settings import get_settings

HEADERS = {"Authorization": "Bearer internal-secret"}

ANALYSIS_OK = json.dumps(
    {
        "sufficient": True,
        "confidence": 0.9,
        "missing_dimensions": [],
        "questions": [],
        "extracted_keywords": ["通知", "邮件"],
    },
    ensure_ascii=False,
)
ANALYSIS_INSUFFICIENT = json.dumps(
    {
        "sufficient": False,
        "confidence": 0.4,
        "missing_dimensions": ["行为描述"],
        "questions": ["希望改成什么行为？", "影响哪些用户？"],
        "extracted_keywords": ["通知"],
    },
    ensure_ascii=False,
)
CANDIDATES = json.dumps(
    [
        {"repository": "ts-notify", "confidence": 0.82, "rationale": "发通知的服务"},
        {"repository": "ts-order", "confidence": 0.51, "rationale": "下单后触发通知"},
    ],
    ensure_ascii=False,
)


def _confirmation(
    status: str,
    *,
    reason: str = "在改动范围内",
    depends_on: tuple[str, ...] = (),
    impacts: tuple[str, ...] = (),
) -> str:
    return json.dumps(
        {
            "status": status,
            "confidence": 0.8,
            "reason": reason,
            "plan_summary": "调整通知模板",
            "changed_apis": ["/api/v1/notify"],
            "changed_modules": ["notify"],
            "depends_on": list(depends_on),
            "impacts": list(impacts),
            "risk": "low",
        },
        ensure_ascii=False,
    )


INTEGRATION = json.dumps(
    {
        "engineering_spec": "统一通知模板",
        "contracts": [
            {
                "producer": "ts-notify",
                "consumer": "ts-order",
                "interface": "POST /api/v1/notify",
                "agreement": "202 表示已入队",
            }
        ],
        "task_dag": [
            {"repository": "ts-notify", "instruction": "改模板", "depends_on": []},
            {
                "repository": "ts-order",
                "instruction": "改调用",
                "depends_on": ["ts-notify"],
            },
        ],
        "execution_batches": [["ts-notify"], ["ts-order"]],
    },
    ensure_ascii=False,
)


class ScriptedLLM:
    """Returns canned responses in order; never opens a socket.

    An exhausted script fails loudly rather than repeating the last answer:
    a test that silently made one more model call than it meant to would be
    asserting against a coincidence.
    """

    def __init__(self, *responses: str | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []
        self.gate: threading.Event | None = None
        self.entered = threading.Event()

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        self.calls.append(messages)
        self.entered.set()
        if self.gate is not None:
            # Blocks the calling thread, exactly as a real HTTP call to a model
            # would. If this runs on the event loop the whole process stops.
            self.gate.wait(timeout=10)
        if not self.responses:
            raise AssertionError(f"unscripted LLM call #{len(self.calls)}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _configure(monkeypatch) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()


def _seed(container: ApplicationContainer) -> tuple[UUID, UUID, UUID, UUID]:
    """One workspace with a leader and a non-leader, plus a second workspace."""

    from repomesh.modules.agent_directory.application import (
        CreateAgent,
        CreateAgentRequest,
    )

    organization_id = uuid4()
    other_organization_id = uuid4()

    async def seed():
        creator = CreateAgent(container.agent_directory)
        leader = await creator.execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="disc-org-leader",
            ),
            idempotency_key="disc-org-leader",
        )
        repo_leader = await creator.execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.REPOSITORY_LEADER,
                agentteams_resource_name="disc-repo-leader",
                leader_agent_id=leader.principal.id,
                repository_id=uuid4(),
                responsibility_paths=("src/**",),
            ),
            idempotency_key="disc-repo-leader",
        )
        stranger = await creator.execute(
            CreateAgentRequest(
                organization_id=other_organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="disc-other-leader",
            ),
            idempotency_key="disc-other-leader",
        )
        for name in ("ts-notify", "ts-order"):
            await container.repository_catalog.add(
                RepositoryProfile(
                    name=name,
                    url=f"https://github.com/acme/{name}",
                    description=f"{name} 服务",
                    topics=("通知",),
                    # Defect A-19: one repository has said how it is verified
                    # and the other has not, because both are real states and
                    # the materialize path has to be honest about each — the
                    # first inherits its commands, the second still dispatches
                    # with none and is refused by delivery later.
                    test_commands=(
                        ("python scripts/run_tests.py",) if name == "ts-notify" else ()
                    ),
                    # Defect A-21: and where that command reads from. Declared
                    # together because a command without its path is the trap
                    # that voided a live run.
                    test_paths=(("tests/**",) if name == "ts-notify" else ()),
                    auto_card=AutoCard(
                        top_dirs=("src",),
                        recent_commits=("fix 通知邮件",),
                        exposed_apis=("/api/v1/notify",),
                    ),
                )
            )
        return leader.principal.id, repo_leader.principal.id, stranger.principal.id

    leader_id, repo_leader_id, stranger_id = asyncio.run(seed())
    return organization_id, leader_id, repo_leader_id, stranger_id


class Chain:
    """Driver for one issue's chain, so the tests read like the panel."""

    def __init__(self, client: TestClient, issue_id: str, leader_id: UUID) -> None:
        self.client = client
        self.issue_id = issue_id
        self.leader = str(leader_id)
        self._key = 0

    def key(self) -> str:
        self._key += 1
        return f"disc-key-{self._key:04d}"

    def post(self, step: str, **body):
        payload = {"created_by_agent_id": self.leader, **body}
        payload.setdefault("idempotency_key", self.key())
        return self.client.post(
            f"/api/v1/issues/{self.issue_id}/discovery/{step}",
            json=payload,
            headers=HEADERS,
        )

    def read(self) -> dict:
        response = self.client.get(
            f"/api/v1/issues/{self.issue_id}/discovery", headers=HEADERS
        )
        assert response.status_code == 200, response.text
        return response.json()

    def poll(self, task_id: str) -> dict:
        response = self.client.get(
            f"/api/v1/issues/{self.issue_id}/discovery/tasks/{task_id}",
            headers=HEADERS,
        )
        assert response.status_code == 200, response.text
        return response.json()

    def await_task(self, response, *, attempts: int = 400) -> dict:
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        body = self.poll(task_id)
        for _ in range(attempts):
            if body["status"] != "running":
                return body
            time.sleep(0.02)
            body = self.poll(task_id)
        raise AssertionError(f"discovery task never finished: {body}")

    def run(self, step: str, **body) -> dict:
        return self.await_task(self.post(step, **body))


def _create_issue(
    client: TestClient,
    leader_id: UUID,
    key: str = "disc-issue-key",
    requirement: str = "订单完成后没有收到通知邮件",
) -> str:
    created = client.post(
        "/api/v1/issues",
        json={
            "requirement_text": requirement,
            "created_by_agent_id": str(leader_id),
            "idempotency_key": key,
        },
        headers=HEADERS,
    )
    assert created.status_code == 201, created.text
    return created.json()["issue_id"]


def _audit_types(container: ApplicationContainer, event_type: str) -> int:
    async def count():
        async with container.database.transaction() as session:
            result = await session.execute(
                select(func.count())
                .select_from(AuditEventRecord)
                .where(AuditEventRecord.event_type == event_type)
            )
            return result.scalar_one()

    return asyncio.run(count())


# ---------------------------------------------------------------------------
# The happy path, end to end
# ---------------------------------------------------------------------------


def test_the_chain_walks_four_steps_and_lands_on_one_snapshot(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Every step's result is readable, and the stepper moves the way §3.2 says.

    This is the panel's whole contract in one pass: trigger, poll, re-read.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=ScriptedLLM(
        ANALYSIS_OK,
        CANDIDATES,
        _confirmation("REQUIRED"),
        _confirmation("MAYBE", depends_on=("ts-notify",)),
        INTEGRATION,
    ))
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = Chain(client, issue_id, leader_id)

            # Never started: 200 with everything null, not a 404.
            fresh = chain.read()
            assert fresh["step"] == 1
            assert fresh["step_state"] == "idle"
            assert fresh["analysis"] is None
            assert fresh["candidates"] is None
            assert fresh["classification"] is None
            assert fresh["running_task_id"] is None
            assert fresh["requirement_text"] == "订单完成后没有收到通知邮件"

            assert chain.run("analysis")["status"] == "succeeded"
            after_analysis = chain.read()
            assert after_analysis["step"] == 2
            assert after_analysis["analysis"]["sufficient"] is True
            assert after_analysis["analysis"]["error"] is None
            # §4.3: the text sent downstream is the composed one, exposed so
            # the panel can show what was actually analysed.
            assert after_analysis["analyzed_requirement"] == "订单完成后没有收到通知邮件"

            assert chain.run("candidates")["status"] == "succeeded"
            after_candidates = chain.read()
            assert after_candidates["step"] == 3
            names = [c["repository_name"] for c in after_candidates["candidates"]["items"]]
            assert names == ["ts-notify", "ts-order"]
            # Q11: the mechanism reports on itself.
            assert after_candidates["candidates"]["llm_used"] is True
            # Rationale is passed through untouched — no summarising, no cut.
            assert after_candidates["candidates"]["items"][0]["rationale"] == "发通知的服务"

            classification = chain.run("classification")
            assert classification["status"] == "succeeded"
            after_classification = chain.read()
            assert after_classification["step"] == 3  # tiering done, approval pending
            tiers = after_classification["classification"]
            assert [r["repository"] for r in tiers["required"]] == ["ts-notify"]
            assert [r["repository"] for r in tiers["maybe"]] == ["ts-order"]
            assert after_classification["approval"]["state"] == "not_requested"
            assert after_classification["effective_tiers"] == [
                {
                    "repository": "ts-notify",
                    "tier": "required",
                    "adjusted": False,
                    "original_tier": None,
                },
                {
                    "repository": "ts-order",
                    "tier": "maybe",
                    "adjusted": False,
                    "original_tier": None,
                },
            ]

            # A plan before approval is the gate v1 exists for.
            too_early = chain.post("plan")
            assert too_early.status_code == 409
            assert "approv" in too_early.json()["detail"]

            approved = client.post(
                f"/api/v1/issues/{issue_id}/discovery/approval",
                json={
                    "decided_by_agent_id": str(leader_id),
                    "idempotency_key": "disc-approval-01",
                    "decision": "approved",
                    "reason": "范围合理",
                    "evidence_version": after_classification["approval"]["evidence_version"]
                    or after_classification["classification_evidence_version"],
                },
                headers=HEADERS,
            )
            assert approved.status_code == 200, approved.text

            after_approval = chain.read()
            assert after_approval["approval"]["state"] == "approved"
            assert after_approval["step"] == 4

            assert chain.run("plan")["status"] == "succeeded"
            final = chain.read()
            assert final["step"] == 4
            assert final["step_state"] == "done"
            # batch_count comes from the plan-layer graph, which is a merge of
            # scanned facts and model semantics — not from the scan alone.
            # Neither seeded repository declares a dependency on the other, so
            # the scan contributes no edge and would batch them in parallel;
            # but the model states a contract (ts-notify → ts-order) grounded
            # by ts-order's approved plan naming ts-notify in depends_on, so
            # that pair becomes one confirmed ``llm`` edge and the batches are
            # two. Same rule as test_plan_integration.py's
            # test_llm_depends_on_new_edge_enters_batches.
            assert final["integration"] == {
                "task_dag_count": 2,
                "batch_count": 2,
                "contract_count": 1,
            }
            # §2.3/§2.4: one round, one row, still version 1.
            assert final["plan_version"] == 1
    finally:
        get_settings.cache_clear()


def test_the_whole_chain_lives_on_a_single_draft_snapshot(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Four steps must not become four versions (§2.3).

    If any step bumped the version, the newest snapshot would be one with an
    empty ``execution_batches`` — and the plan endpoint reads the newest, so
    the DAG panel would go blank for a plan that exists.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=ScriptedLLM(ANALYSIS_OK, CANDIDATES))
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = Chain(client, issue_id, leader_id)
            chain.run("analysis")
            chain.run("candidates")

            versions = client.get(
                f"/api/v1/plans/{issue_id}/versions", headers=HEADERS
            ).json()
    finally:
        get_settings.cache_clear()

    assert [row["plan_version"] for row in versions] == [1]
    assert versions[0]["execution_plan_id"] is None


# ---------------------------------------------------------------------------
# Authorization and the acting subject
# ---------------------------------------------------------------------------


def test_every_discovery_route_requires_the_action_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    try:
        with TestClient(create_app(application_container)) as client:
            issue_id = _create_issue(client, leader_id)
            writes = {
                step: client.post(
                    f"/api/v1/issues/{issue_id}/discovery/{step}", json={}
                ).status_code
                for step in ("analysis", "candidates", "classification", "plan", "approval")
            }
            poll = client.get(
                f"/api/v1/issues/{issue_id}/discovery/tasks/{uuid4()}"
            ).status_code
            projection = client.get(f"/api/v1/issues/{issue_id}/discovery").status_code
    finally:
        get_settings.cache_clear()

    assert set(writes.values()) == {401}, writes
    assert poll == 401
    assert projection == 401


def test_the_actor_must_be_an_active_leader_of_this_issues_workspace(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Same rule as issue intake, and it is checked against the issue, not the body.

    The cross-workspace case is the one that matters: a valid leader of another
    organization is a real, active principal, so only comparing the issue's
    owning workspace catches it.
    """

    _configure(monkeypatch)
    _, leader_id, repo_leader_id, stranger_id = _seed(application_container)
    container = replace(application_container, llm_client=ScriptedLLM(ANALYSIS_OK))
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)

            def analysis_as(agent_id: str, key: str):
                return client.post(
                    f"/api/v1/issues/{issue_id}/discovery/analysis",
                    json={"created_by_agent_id": agent_id, "idempotency_key": key},
                    headers=HEADERS,
                )

            unknown = analysis_as(str(uuid4()), "disc-unknown-key")
            non_leader = analysis_as(str(repo_leader_id), "disc-nonleader-key")
            other_workspace = analysis_as(str(stranger_id), "disc-stranger-key")
            missing_issue = client.post(
                f"/api/v1/issues/{uuid4()}/discovery/analysis",
                json={
                    "created_by_agent_id": str(leader_id),
                    "idempotency_key": "disc-missing-key",
                },
                headers=HEADERS,
            )
    finally:
        get_settings.cache_clear()

    assert unknown.status_code == 404
    assert non_leader.status_code == 403
    assert other_workspace.status_code == 403
    assert "different organization" in other_workspace.json()["detail"]
    assert missing_issue.status_code == 404


# ---------------------------------------------------------------------------
# Ordering, idempotency, invalidation
# ---------------------------------------------------------------------------


def test_steps_refuse_to_run_before_what_they_consume(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """409 with a reason, rather than a step that runs on nothing."""

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=ScriptedLLM(ANALYSIS_INSUFFICIENT))
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = Chain(client, issue_id, leader_id)

            no_analysis = chain.post("candidates")
            no_candidates = chain.post("classification")

            chain.run("analysis")
            # Analysis raised questions and nobody answered or overrode them.
            blocked = chain.post("candidates")
            stopped = chain.read()
    finally:
        get_settings.cache_clear()

    assert no_analysis.status_code == 409
    assert no_candidates.status_code == 409
    assert blocked.status_code == 409
    assert "did not pass" in blocked.json()["detail"]
    # §3.2 rule 2: an analysis that did not pass leaves the stepper in cell 1.
    assert stopped["step"] == 1
    assert stopped["analysis"]["questions"] == ["希望改成什么行为？", "影响哪些用户？"]


def test_replaying_a_key_returns_the_existing_result_without_calling_the_model(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """§4.4: a retried request must not buy a second, contradicting answer.

    The scripted LLM has exactly one response, so a second call would raise
    rather than quietly return the same string — the assertion on ``calls``
    would pass either way, and that is not enough on its own.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    llm = ScriptedLLM(ANALYSIS_OK)
    container = replace(application_container, llm_client=llm)
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = Chain(client, issue_id, leader_id)

            first = chain.post("analysis", idempotency_key="disc-same-key-01")
            chain.await_task(first)
            calls_after_first = len(llm.calls)
            replay = chain.post("analysis", idempotency_key="disc-same-key-01")
    finally:
        get_settings.cache_clear()

    assert first.status_code == 202
    assert replay.status_code == 200
    assert replay.json() == {"task_id": None, "step": 1, "status": "replayed"}
    assert len(llm.calls) == calls_after_first == 1


def test_rerunning_a_step_voids_everything_downstream_and_says_so(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """§4.4 / Q8: the alternative is two generations on screen, both "complete".

    Without this the panel shows the new analysis beside candidates scored
    from the old one, with nothing marking which question they answer.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=ScriptedLLM(
        ANALYSIS_OK,
        CANDIDATES,
        _confirmation("REQUIRED"),
        _confirmation("REQUIRED"),
        ANALYSIS_OK,
    ))
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = Chain(client, issue_id, leader_id)
            chain.run("analysis")
            chain.run("candidates")
            chain.run("classification")

            before = chain.read()
            assert before["candidates"] is not None
            assert before["classification"] is not None

            chain.run("analysis")  # fresh key → a real re-run
            after = chain.read()
            voided = _audit_types(application_container, "DiscoveryDownstreamVoided")
    finally:
        get_settings.cache_clear()

    assert after["analysis"] is not None
    assert after["candidates"] is None
    assert after["classification"] is None
    assert after["step"] == 2
    assert voided == 1


def test_a_second_step_while_one_is_running_is_refused(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Two people pressing the same button must not race one JSON column."""

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    llm = ScriptedLLM(ANALYSIS_OK)
    llm.gate = threading.Event()
    container = replace(application_container, llm_client=llm)
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = Chain(client, issue_id, leader_id)

            first = chain.post("analysis")
            assert first.status_code == 202
            assert llm.entered.wait(timeout=10)

            second = chain.post("analysis")
            running = chain.read()

            llm.gate.set()
            chain.await_task(first)
    finally:
        llm.gate.set()
        get_settings.cache_clear()

    assert second.status_code == 409
    assert first.json()["task_id"] in second.json()["detail"]
    # The projection says which task is holding it, so the panel can wait
    # rather than offering a button that is going to 409.
    assert running["running_task_id"] == first.json()["task_id"]
    assert running["step_state"] == "running"


# ---------------------------------------------------------------------------
# The property a 202 is worthless without
# ---------------------------------------------------------------------------


def test_a_blocking_model_call_does_not_stall_the_event_loop(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Q6, the hard requirement: 202 plus a thread, not 202 alone.

    Every ``chat()`` in this pipeline blocks. Run one from the coroutine and
    the loop stops for the whole process — the browser would poll an endpoint
    that cannot answer until the call it is asking about has finished, which
    is a worse experience than the synchronous request it replaced, and it
    takes the rest of the API down with it.

    The scripted LLM blocks inside ``chat()`` and is released only after the
    assertions below. That every one of them is served while it blocks is the
    evidence: served requests during a blocked model call means the call is
    not on the loop.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    llm = ScriptedLLM(ANALYSIS_OK)
    llm.gate = threading.Event()
    container = replace(application_container, llm_client=llm)
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = Chain(client, issue_id, leader_id)

            accepted = chain.post("analysis")
            assert accepted.status_code == 202
            assert llm.entered.wait(timeout=10), "the model call never started"

            # The loop is serving requests while chat() sits blocked.
            polled = chain.poll(accepted.json()["task_id"])
            projection = chain.read()
            unrelated = client.get("/api/v1/repositories")

            llm.gate.set()
            finished = chain.await_task(accepted)
    finally:
        llm.gate.set()
        get_settings.cache_clear()

    assert polled["status"] == "running"
    assert projection["step_state"] == "running"
    assert unrelated.status_code == 200
    assert finished["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Honest failure and honest provenance
# ---------------------------------------------------------------------------


def test_a_failed_step_shows_the_server_message_and_leaves_later_steps_null(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """§0.1 rule 4: the real error, and no invented progress after it."""

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=ScriptedLLM(
        RuntimeError("deepseek returned 429 rate limited")
    ))
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = Chain(client, issue_id, leader_id)

            failed = chain.run("analysis")
            projection = chain.read()
    finally:
        get_settings.cache_clear()

    assert failed["status"] == "failed"
    assert "429 rate limited" in failed["error"]
    assert "429 rate limited" in projection["analysis"]["error"]["message"]
    assert projection["step"] == 1
    assert projection["step_state"] == "failed"
    # No hollow objects standing in for steps that never ran.
    assert projection["candidates"] is None
    assert projection["classification"] is None


def test_candidates_report_the_keyword_fallback_rather_than_posing_as_scores(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Q11. The two paths produce identical shapes, so only self-report works.

    A term-frequency ratio rendered as "0.5 confidence" reads as a model's
    judgement. ``matched_terms`` being non-empty correlates with the fallback
    today, but correlation is not the mechanism — this asserts the flag the
    producer sets.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=ScriptedLLM(
        ANALYSIS_OK, "not json at all — the model wandered off"
    ))
    try:
        with TestClient(create_app(container)) as client:
            # Names the repository outright so the keyword path has a term to
            # match on: the point here is the provenance flag, and a fallback
            # that scored nothing would not exercise it.
            issue_id = _create_issue(
                client, leader_id, requirement="ts-notify 的邮件没发出去"
            )
            chain = Chain(client, issue_id, leader_id)
            chain.run("analysis")
            assert chain.run("candidates")["status"] == "succeeded"
            projection = chain.read()
    finally:
        get_settings.cache_clear()

    assert projection["candidates"]["llm_used"] is False
    assert projection["candidates"]["error"] is None  # a fallback is not a failure
    assert projection["candidates"]["items"], "the keyword path should still score"


def test_an_unconfigured_llm_is_503_on_every_step_that_needs_one(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Q12: a deployment with no model is not a server fault."""

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=None)
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = Chain(client, issue_id, leader_id)
            analysis = chain.post("analysis")
            confirmation = client.post(
                "/api/v1/confirmation",
                json={"requirement": "改通知", "candidate_repos": ["ts-notify"]},
                headers=HEADERS,
            )
            integration = client.post(
                "/api/v1/integration",
                json={
                    "requirement": "改通知",
                    "confirmation": {
                        "required": [],
                        "maybe": [],
                        "excluded": [],
                        "supplemented_repos": [],
                        "final_repos": [],
                    },
                },
                headers=HEADERS,
            )
    finally:
        get_settings.cache_clear()

    assert analysis.status_code == 503
    assert confirmation.status_code == 503
    assert integration.status_code == 503


def test_candidates_outside_the_catalog_are_a_422_not_a_crash(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Q13: a bare ValueError used to escape as a 500.

    "None of these repositories are registered" is an ordinary thing for a
    caller to get wrong; reporting it as a server crash sends them hunting for
    a bug that is not there.
    """

    _configure(monkeypatch)
    _seed(application_container)
    container = replace(application_container, llm_client=ScriptedLLM())
    try:
        with TestClient(create_app(container)) as client:
            response = client.post(
                "/api/v1/confirmation",
                json={"requirement": "改通知", "candidate_repos": ["never-registered"]},
                headers=HEADERS,
            )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 422
    assert "catalog" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Clarify override
# ---------------------------------------------------------------------------


def test_forcing_past_the_questions_is_recorded_without_a_second_opinion(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """§4.6 / Q15: the override is a fact about the user, not a new analysis.

    It is written twice on purpose — into the snapshot, where the panel shows
    it for as long as the issue exists, and into the audit log, where it can
    be reviewed. There is no decision-record entity during discovery to put it
    in, so those are the two places that exist.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    llm = ScriptedLLM(ANALYSIS_INSUFFICIENT, CANDIDATES)
    container = replace(application_container, llm_client=llm)
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = Chain(client, issue_id, leader_id)
            chain.run("analysis")
            calls_before = len(llm.calls)

            forced = chain.post("analysis", force_continue=True)
            # Snapshot before anything else runs: the candidates step below
            # legitimately calls the model, and reading the counter after it
            # would compare against the wrong number and pass either way.
            calls_after_override = len(llm.calls)
            after = chain.read()
            # Overriding unblocks Step 1 without changing the analysis.
            assert chain.run("candidates")["status"] == "succeeded"
            overrides = _audit_types(application_container, "DiscoveryClarifyOverridden")
    finally:
        get_settings.cache_clear()

    assert forced.status_code == 200
    assert calls_after_override == calls_before, "the override must not re-run the model"
    assert after["analysis"]["forced_continue"]["ignored_question_count"] == 2
    assert after["analysis"]["forced_continue"]["by_agent_id"] == str(leader_id)
    assert after["analysis"]["sufficient"] is False  # the model's view is untouched
    assert after["step"] == 2
    assert overrides == 1


def test_answers_are_folded_in_server_side_and_the_title_does_not_move(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Q16: answers go into the discovery block, never into requirement_text.

    ``requirement_text`` is where the issue title comes from. Rewriting it
    would make the title change under the user while they answer questions.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    llm = ScriptedLLM(ANALYSIS_INSUFFICIENT, ANALYSIS_OK)
    container = replace(application_container, llm_client=llm)
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = Chain(client, issue_id, leader_id)
            chain.run("analysis")
            chain.run(
                "analysis",
                answers=[{"question": "希望改成什么行为？", "answer": "下单后 1 分钟内发邮件"}],
            )
            projection = chain.read()
            issue = client.get(f"/api/v1/issues/{issue_id}", headers=HEADERS).json()
    finally:
        get_settings.cache_clear()

    assert projection["requirement_text"] == "订单完成后没有收到通知邮件"
    assert issue["requirement_text"] == "订单完成后没有收到通知邮件"
    assert "下单后 1 分钟内发邮件" in projection["analyzed_requirement"]
    assert projection["analysis"]["answers"] == [
        {"question": "希望改成什么行为？", "answer": "下单后 1 分钟内发邮件"}
    ]
    # The composed text is what the model actually saw.
    assert "下单后 1 分钟内发邮件" in json.dumps(llm.calls[-1], ensure_ascii=False)


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


def _walk_to_classification(client: TestClient, leader_id: UUID, issue_id: str) -> Chain:
    chain = Chain(client, issue_id, leader_id)
    chain.run("analysis")
    chain.run("candidates")
    chain.run("classification")
    return chain


def test_an_approval_bound_to_a_replaced_classification_is_refused(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """§5.3 / Q18: approve what you read, not whatever is newest.

    Releasing against the current tiering regardless would mean approving a
    classification nobody looked at.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=ScriptedLLM(
        ANALYSIS_OK,
        CANDIDATES,
        _confirmation("REQUIRED"),
        _confirmation("MAYBE"),
        _confirmation("EXCLUDED"),
        _confirmation("EXCLUDED"),
    ))
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = _walk_to_classification(client, leader_id, issue_id)
            stale_version = chain.read()["classification_evidence_version"]

            # Someone re-runs the tiering while the approver has the dialog open.
            chain.run("classification")
            fresh_version = chain.read()["classification_evidence_version"]

            def approve(version: str, key: str):
                return client.post(
                    f"/api/v1/issues/{issue_id}/discovery/approval",
                    json={
                        "decided_by_agent_id": str(leader_id),
                        "idempotency_key": key,
                        "decision": "approved",
                        "reason": "ok",
                        "evidence_version": version,
                    },
                    headers=HEADERS,
                )

            stale = approve(stale_version, "disc-approve-stale")
            current = approve(fresh_version, "disc-approve-fresh")
    finally:
        get_settings.cache_clear()

    assert stale_version != fresh_version
    assert stale.status_code == 409
    assert "reload" in stale.json()["detail"]
    assert current.status_code == 200


def test_an_approvers_retier_survives_as_evidence_and_reaches_the_plan(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """§2.2 and §4.3: keep both opinions, and integrate the one in force.

    The model's buckets stay exactly as produced and the edit is recorded
    beside them — overwriting would delete the only record of what the model
    said. ``effective_tiers`` is the single derivation of the result, and Step
    3 builds the plan from it, so a leader who promotes a repository sees it
    in the plan.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=ScriptedLLM(
        ANALYSIS_OK,
        CANDIDATES,
        # The excluded repository's plan is discarded by design, so the edge
        # evidence must live on the producer's side: ts-notify names ts-order
        # in ``impacts``, which is what grounds the LLM edge after the
        # approver promotes ts-order back into scope.
        _confirmation("REQUIRED", impacts=("ts-order",)),
        _confirmation("EXCLUDED", reason="看起来无关"),
        INTEGRATION,
    ))
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = _walk_to_classification(client, leader_id, issue_id)
            before = chain.read()

            approved = client.post(
                f"/api/v1/issues/{issue_id}/discovery/approval",
                json={
                    "decided_by_agent_id": str(leader_id),
                    "idempotency_key": "disc-approve-retier",
                    "decision": "approved",
                    "reason": "ts-order 也要改",
                    "adjustments": [{"repository": "ts-order", "tier": "required"}],
                    "evidence_version": before["classification_evidence_version"],
                },
                headers=HEADERS,
            )
            assert approved.status_code == 200, approved.text
            after = chain.read()
            chain.run("plan")
            plan_snapshot = client.get(
                f"/api/v1/plans/{issue_id}/latest", headers=HEADERS
            ).json()
    finally:
        get_settings.cache_clear()

    # The model's own tiering is untouched.
    assert [r["repository"] for r in after["classification"]["excluded"]] == ["ts-order"]
    assert after["classification"]["excluded"][0]["reason"] == "看起来无关"
    # The edit is recorded beside it, with who and when.
    adjustment = after["classification"]["adjustments"][0]
    assert (adjustment["repository"], adjustment["from"], adjustment["to"]) == (
        "ts-order",
        "EXCLUDED",
        "REQUIRED",
    )
    assert adjustment["by_agent_id"] == str(leader_id)
    # And the derived answer is what the panel and Step 3 both use.
    assert {row["repository"]: row["tier"] for row in after["effective_tiers"]} == {
        "ts-notify": "required",
        "ts-order": "required",
    }
    assert [row for row in after["effective_tiers"] if row["adjusted"]][0][
        "original_tier"
    ] == "excluded"
    # The promoted repository is in the plan that was generated — and not only
    # in the task DAG. Integration filters contracts and batching on each
    # result's ``status`` field rather than on the bucket it arrived in, so a
    # promotion that moved the bucket without rewriting the field produced a
    # plan where ts-order had a task but no contract and no place in the
    # batches: promoted on screen, half-absent from the plan.
    assert {node["repository"] for node in plan_snapshot["task_dag"]} == {
        "ts-notify",
        "ts-order",
    }
    assert [
        (c["producer"], c["consumer"]) for c in plan_snapshot["contracts"]
    ] == [("ts-notify", "ts-order")]
    assert [name for batch in plan_snapshot["execution_batches"] for name in batch] == [
        "ts-notify",
        "ts-order",
    ]


def test_changes_requested_holds_the_gate_without_deleting_the_tiering(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """§5.2: the leader wants changes; the model's judgement is still a fact."""

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=ScriptedLLM(
        ANALYSIS_OK, CANDIDATES, _confirmation("REQUIRED"), _confirmation("MAYBE")
    ))
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = _walk_to_classification(client, leader_id, issue_id)
            response = client.post(
                f"/api/v1/issues/{issue_id}/discovery/approval",
                json={
                    "decided_by_agent_id": str(leader_id),
                    "idempotency_key": "disc-approve-changes",
                    "decision": "changes_requested",
                    "reason": "缺少支付仓库",
                    "evidence_version": chain.read()["classification_evidence_version"],
                },
                headers=HEADERS,
            )
            after = chain.read()
            blocked = chain.post("plan")
            decided = _audit_types(
                application_container, "DiscoveryClassificationDecided"
            )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert after["approval"]["state"] == "changes_requested"
    assert after["approval"]["reason"] == "缺少支付仓库"
    assert after["classification"] is not None
    assert after["step"] == 3
    assert blocked.status_code == 409
    assert decided == 1


# ---------------------------------------------------------------------------
# Task registry honesty
# ---------------------------------------------------------------------------


def test_an_unknown_task_says_to_read_the_projection_rather_than_re_run(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The 404 must not read as "bad id", and must not advise a re-run.

    A lost scan has to be re-run. A lost discovery task does not: the result
    was written to the snapshot before the task was marked done, so the
    projection answers "did it land" for free.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    try:
        with TestClient(create_app(application_container)) as client:
            issue_id = _create_issue(client, leader_id)
            missing = client.get(
                f"/api/v1/issues/{issue_id}/discovery/tasks/{uuid4()}", headers=HEADERS
            )
    finally:
        get_settings.cache_clear()

    assert missing.status_code == 404
    detail = missing.json()["detail"]
    assert "restart" in detail
    assert "/discovery" in detail


def test_the_task_view_never_carries_the_step_result(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """One serialisation per fact: the task reports progress, not candidates."""

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    container = replace(application_container, llm_client=ScriptedLLM(ANALYSIS_OK, CANDIDATES))
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = Chain(client, issue_id, leader_id)
            chain.run("analysis")
            finished = chain.run("candidates")
    finally:
        get_settings.cache_clear()

    assert set(finished) == {
        "task_id",
        "issue_id",
        "step",
        "status",
        "progress",
        "error",
        "started_at",
        "finished_at",
    }
    assert "ts-notify" not in json.dumps(finished)


def test_classification_reports_progress_per_candidate(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Q17: N model calls without progress is minutes of silence.

    The stub blocks on the second candidate, so the poll below is a real
    observation of a partly-finished run rather than a finished one caught
    early.
    """

    _configure(monkeypatch)
    _, leader_id, _, _ = _seed(application_container)
    llm = ScriptedLLM(
        ANALYSIS_OK, CANDIDATES, _confirmation("REQUIRED"), _confirmation("MAYBE")
    )
    container = replace(application_container, llm_client=llm)
    try:
        with TestClient(create_app(container)) as client:
            issue_id = _create_issue(client, leader_id)
            chain = Chain(client, issue_id, leader_id)
            chain.run("analysis")
            chain.run("candidates")

            llm.entered.clear()
            llm.gate = threading.Event()
            accepted = chain.post("classification")
            assert llm.entered.wait(timeout=10)
            mid = chain.poll(accepted.json()["task_id"])

            llm.gate.set()
            finished = chain.await_task(accepted)
    finally:
        if llm.gate is not None:
            llm.gate.set()
        get_settings.cache_clear()

    assert mid["status"] == "running"
    assert mid["progress"]["total"] == 2
    assert mid["progress"]["done"] == 1
    assert mid["progress"]["label"] == "ts-notify"
    assert finished["status"] == "succeeded"
