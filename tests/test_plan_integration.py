"""Tests for PlanIntegrationService."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from repomesh.modules.repository_intelligence.application.confirmation import (  # noqa: E402, I001
    ConfirmationResult,
    ConfirmationSummary,
    RepositoryPlan,
)
from repomesh.modules.repository_intelligence.application.dependency_graph import (  # noqa: E402, I001
    GraphEdge as ScanEdge,
    TopoResult,
)
from repomesh.modules.repository_intelligence.application.plan_integration import (  # noqa: E402
    ContractSpec,
    IntegratedPlan,
    PlanIntegrationService,
    TaskNode,
    _parse_integrated_plan,
    _topological_batches,
    normalize_plan,
    plan_to_graph,
)
from repomesh.modules.repository_intelligence.contracts import (  # noqa: E402, I001
    GraphEdge as PlanGraphEdge,
    integration_method,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class StubLLM:
    """Returns a canned LLM response."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        self.calls.append(messages)
        return self._response


def _make_result(
    repo: str,
    status: str = "REQUIRED",
    *,
    depends_on: tuple[str, ...] = ("ts-common",),
    impacts: tuple[str, ...] = (),
) -> ConfirmationResult:
    return ConfirmationResult(
        repository=repo,
        status=status,
        confidence=0.9,
        reason=f"test reason for {repo}",
        plan_summary=f"change {repo} for the requirement",
        plan=RepositoryPlan(
            changed_apis=("GET /api/v1/endpoint",),
            changed_modules=("src/main",),
            depends_on=depends_on,
            impacts=impacts,
            risk="low",
        ),
    )


def _make_summary(*results: ConfirmationResult) -> ConfirmationSummary:
    required = [r for r in results if r.status == "REQUIRED"]
    maybe = [r for r in results if r.status == "MAYBE"]
    return ConfirmationSummary(
        required=required,
        maybe=maybe,
        excluded=[],
        supplemented_repos=[],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTopologicalBatches:
    def test_no_deps_single_batch(self):
        dag = [
            TaskNode(repository="A", instruction="", depends_on=()),
            TaskNode(repository="B", instruction="", depends_on=()),
        ]
        batches = _topological_batches(dag)
        assert len(batches) == 1
        assert set(batches[0]) == {"A", "B"}

    def test_linear_chain(self):
        dag = [
            TaskNode(repository="A", instruction="", depends_on=()),
            TaskNode(repository="B", instruction="", depends_on=("A",)),
            TaskNode(repository="C", instruction="", depends_on=("B",)),
        ]
        batches = _topological_batches(dag)
        assert batches == [["A"], ["B"], ["C"]]

    def test_parallel_branches(self):
        dag = [
            TaskNode(repository="A", instruction="", depends_on=()),
            TaskNode(repository="B", instruction="", depends_on=("A",)),
            TaskNode(repository="C", instruction="", depends_on=("A",)),
            TaskNode(repository="D", instruction="", depends_on=("B", "C")),
        ]
        batches = _topological_batches(dag)
        assert batches[0] == ["A"]
        assert set(batches[1]) == {"B", "C"}
        assert batches[2] == ["D"]

    def test_circular_dependency_breaks(self):
        dag = [
            TaskNode(repository="A", instruction="", depends_on=("B",)),
            TaskNode(repository="B", instruction="", depends_on=("A",)),
        ]
        batches = _topological_batches(dag)
        # Should still produce all repos despite cycle
        all_repos = {r for batch in batches for r in batch}
        assert all_repos == {"A", "B"}

    def test_empty_dag(self):
        assert _topological_batches([]) == []


class TestParseIntegratedPlan:
    def test_parse_full_response(self):
        raw = json.dumps(
            {
                "engineering_spec": "Fix notification system end-to-end",
                "contracts": [
                    {
                        "producer": "ts-notification-service",
                        "consumer": "ts-preserve-service",
                        "interface": "POST /api/v1/notifications/send",
                        "agreement": "Notification API expects {order_id, email, message}",
                    }
                ],
                "task_dag": [
                    {
                        "repository": "ts-notification-service",
                        "instruction": "Fix email parameter parsing",
                        "depends_on": [],
                        "parallelizable_with": [],
                    },
                    {
                        "repository": "ts-preserve-service",
                        "instruction": "Update notification call to match new API",
                        "depends_on": ["ts-notification-service"],
                        "parallelizable_with": [],
                    },
                ],
            }
        )

        plan = _parse_integrated_plan(raw, ["ts-notification-service", "ts-preserve-service"])

        assert "notification" in plan.engineering_spec.lower()
        assert len(plan.contracts) == 1
        assert plan.contracts[0].producer == "ts-notification-service"
        assert plan.contracts[0].consumer == "ts-preserve-service"
        assert len(plan.task_dag) == 2
        assert plan.execution_batches == [["ts-notification-service"], ["ts-preserve-service"]]

    def test_parse_with_markdown_fence(self):
        raw = "```json\n" + json.dumps(
            {
                "engineering_spec": "Test spec",
                "contracts": [],
                "task_dag": [
                    {"repository": "A", "instruction": "do A", "depends_on": []},
                ],
            }
        ) + "\n```"

        plan = _parse_integrated_plan(raw, ["A"])
        assert plan.engineering_spec == "Test spec"
        assert len(plan.task_dag) == 1

    def test_parse_missing_repos_added(self):
        raw = json.dumps(
            {
                "engineering_spec": "Spec",
                "contracts": [],
                "task_dag": [
                    {"repository": "A", "instruction": "do A", "depends_on": []},
                ],
            }
        )

        plan = _parse_integrated_plan(raw, ["A", "B", "C"])
        repos_in_dag = {t.repository for t in plan.task_dag}
        assert repos_in_dag == {"A", "B", "C"}

    def test_parse_invalid_json_fallback(self):
        plan = _parse_integrated_plan("not json at all", ["A", "B"])
        assert plan.engineering_spec == "Failed to generate engineering spec."
        assert len(plan.task_dag) == 2
        assert plan.execution_batches == [["A", "B"]]

    def test_parse_contract_missing_fields_skipped(self):
        raw = json.dumps(
            {
                "engineering_spec": "Spec",
                "contracts": [
                    {"producer": "", "consumer": "B"},  # missing producer
                    {"producer": "A", "consumer": "B", "interface": "API", "agreement": "ok"},
                ],
                "task_dag": [],
            }
        )
        plan = _parse_integrated_plan(raw, ["A", "B"])
        assert len(plan.contracts) == 1

    def test_parse_contract_filtered_to_confirmed_repos(self):
        """Contracts referencing repos not in the confirmed list must be dropped."""
        raw = json.dumps(
            {
                "engineering_spec": "Spec",
                "contracts": [
                    # valid: both in confirmed list
                    {"producer": "A", "consumer": "B",
                     "interface": "API1", "agreement": "ok"},
                    # invalid: producer not in confirmed list
                    {"producer": "ts-ghost-service", "consumer": "B",
                     "interface": "API2", "agreement": "bad"},
                    # invalid: consumer not in confirmed list
                    {"producer": "A", "consumer": "ts-phantom-service",
                     "interface": "API3", "agreement": "bad"},
                    # invalid: both not in confirmed list
                    {"producer": "ts-ghost", "consumer": "ts-phantom",
                     "interface": "API4", "agreement": "bad"},
                ],
                "task_dag": [],
            }
        )
        plan = _parse_integrated_plan(raw, ["A", "B"])
        assert len(plan.contracts) == 1
        assert plan.contracts[0].producer == "A"
        assert plan.contracts[0].consumer == "B"
        assert plan.contracts[0].interface == "API1"


class TestPlanIntegrationService:
    def test_integrate_success(self):
        llm_response = json.dumps(
            {
                "engineering_spec": "Complete plan",
                "contracts": [
                    {"producer": "A", "consumer": "B", "interface": "API-X", "agreement": "ok"}
                ],
                "task_dag": [
                    {"repository": "A", "instruction": "change A", "depends_on": []},
                    {"repository": "B", "instruction": "change B", "depends_on": ["A"]},
                ],
            }
        )
        llm = StubLLM(llm_response)
        service = PlanIntegrationService(llm)

        summary = _make_summary(_make_result("A"), _make_result("B"))
        plan = service.integrate("test requirement", summary)

        assert isinstance(plan, IntegratedPlan)
        assert plan.engineering_spec == "Complete plan"
        assert len(plan.contracts) == 1
        assert plan.execution_batches == [["A"], ["B"]]

    def test_integrate_no_repos(self):
        llm = StubLLM("{}")
        service = PlanIntegrationService(llm)

        summary = ConfirmationSummary(required=[], maybe=[], excluded=[], supplemented_repos=[])
        plan = service.integrate("test", summary)

        assert plan.engineering_spec == "No repositories confirmed."
        assert plan.contracts == []
        assert plan.task_dag == []

    def test_integrate_llm_called_with_correct_messages(self):
        llm_response = json.dumps(
            {"engineering_spec": "S", "contracts": [], "task_dag": []}
        )
        llm = StubLLM(llm_response)
        service = PlanIntegrationService(llm)

        summary = _make_summary(_make_result("ts-order-service"))
        service.integrate("fix order bug", summary)

        assert len(llm.calls) == 1
        messages = llm.calls[0]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "fix order bug" in messages[1]["content"]
        assert "ts-order-service" in messages[1]["content"]

    def test_integrate_includes_plan_details(self):
        llm_response = json.dumps(
            {"engineering_spec": "S", "contracts": [], "task_dag": []}
        )
        llm = StubLLM(llm_response)
        service = PlanIntegrationService(llm)

        result = ConfirmationResult(
            repository="ts-payment-service",
            status="REQUIRED",
            confidence=0.95,
            reason="payment related",
            plan_summary="fix payment callback",
            plan=RepositoryPlan(
                changed_apis=("POST /api/v1/pay",),
                changed_modules=("service/payment",),
                depends_on=("ts-order-service",),
                impacts=("ts-notification-service",),
                risk="high",
            ),
        )
        summary = _make_summary(result)
        service.integrate("fix payment", summary)

        user_msg = llm.calls[0][1]["content"]
        assert "POST /api/v1/pay" in user_msg
        assert "ts-order-service" in user_msg
        assert "ts-notification-service" in user_msg
        assert "high" in user_msg

    def test_to_dict_roundtrip(self):
        plan = IntegratedPlan(
            engineering_spec="Test",
            contracts=[ContractSpec(producer="A", consumer="B", interface="API", agreement="ok")],
            task_dag=[
                TaskNode(repository="A", instruction="do A"),
                TaskNode(repository="B", instruction="do B", depends_on=("A",)),
            ],
            execution_batches=[["A"], ["B"]],
        )
        d = plan.to_dict()
        assert d["engineering_spec"] == "Test"
        assert len(d["contracts"]) == 1
        assert len(d["task_dag"]) == 2
        assert d["execution_batches"] == [["A"], ["B"]]

        # Ensure JSON serialisable
        json.dumps(d)


class TestPlanToGraphBackfill:
    """plan_to_graph must backfill a plan-layer graph whose projections
    reproduce the plan fields — including manually approved batch order."""

    @staticmethod
    def _plan(
        repos: list[str],
        *,
        depends_on: dict[str, tuple[str, ...]] | None = None,
        batches: list[list[str]] | None = None,
        contracts: list[ContractSpec] | None = None,
    ) -> IntegratedPlan:
        depends_on = depends_on or {}
        dag = [
            TaskNode(
                repository=r,
                instruction=f"change {r}",
                depends_on=depends_on.get(r, ()),
            )
            for r in repos
        ]
        return IntegratedPlan(
            engineering_spec="Test",
            contracts=contracts or [],
            task_dag=dag,
            execution_batches=batches if batches is not None else [repos],
        )

    def test_tm_edges_preserve_explicit_batch_order(self):
        """Approved batch order without dependency facts must survive the
        graph round-trip via source=tm edges."""
        plan = self._plan(["A", "B"], batches=[["A"], ["B"]])

        graph = plan_to_graph(plan)
        tm_edges = [e for e in graph.edges if e.source == "tm"]
        assert tm_edges == [
            PlanGraphEdge(from_="A", to="B", status="confirmed", source="tm")
        ]

        normalized = normalize_plan(plan, graph)
        assert normalized.execution_batches == [["A"], ["B"]]
        assert normalized.task_dag[1].depends_on == ("A",)

    def test_three_batch_chain_reproduced(self):
        plan = self._plan(["A", "B", "C"], batches=[["A"], ["B"], ["C"]])

        normalized = normalize_plan(plan, plan_to_graph(plan))
        assert normalized.execution_batches == [["A"], ["B"], ["C"]]
        sources = {e.source for e in normalized.graph.edges}
        assert sources == {"tm"}

    def test_single_batch_adds_no_tm_edges(self):
        plan = self._plan(["A", "B"], batches=[["A", "B"]])
        graph = plan_to_graph(plan)
        assert graph.edges == []
        assert normalize_plan(plan, graph).execution_batches == [["A", "B"]]

    def test_dependency_facts_win_over_conflicting_batches(self):
        """A manual order contradicting a real dependency must not override
        the dependency edge."""
        plan = self._plan(
            ["A", "B"],
            depends_on={"A": ("B",)},  # B must finish before A
            batches=[["A"], ["B"]],  # contradictory manual order
        )

        normalized = normalize_plan(plan, plan_to_graph(plan))
        # Dependency fact kept, manual order dropped.
        assert normalized.task_dag[0].depends_on == ("B",)
        assert normalized.execution_batches == [["B"], ["A"]]

    def test_contract_upgrades_dependency_edge_instead_of_duplicating(self):
        plan = self._plan(
            ["A", "B"],
            depends_on={"B": ("A",)},
            batches=[["A"], ["B"]],
            contracts=[
                ContractSpec(
                    producer="A",
                    consumer="B",
                    interface="API",
                    agreement="same key",
                )
            ],
        )

        graph = plan_to_graph(plan)
        assert len(graph.edges) == 1
        edge = graph.edges[0]
        assert edge.from_ == "A" and edge.to == "B"
        assert edge.status == "confirmed"
        assert edge.interface == "API"
        assert edge.agreement == "same key"
        # Contracts projection picks up the upgraded edge.
        assert [(c.producer, c.consumer, c.interface) for c in graph.contracts] == [
            ("A", "B", "API")
        ]

    def test_contract_pair_not_in_dag_becomes_edge(self):
        plan = self._plan(
            ["A", "B"],
            batches=[["A", "B"]],
            contracts=[
                ContractSpec(
                    producer="A", consumer="B", interface="API", agreement="ok"
                )
            ],
        )
        graph = plan_to_graph(plan)
        assert len(graph.edges) == 1
        assert graph.edges[0].from_ == "A" and graph.edges[0].to == "B"


class TestNormalizePlan:
    def test_idempotent_when_already_consistent(self):
        plan = IntegratedPlan(
            engineering_spec="Spec",
            contracts=[
                ContractSpec(
                    producer="A", consumer="B", interface="API", agreement="ok"
                )
            ],
            task_dag=[
                TaskNode(repository="A", instruction="do A"),
                TaskNode(repository="B", instruction="do B", depends_on=("A",)),
            ],
            execution_batches=[["A"], ["B"]],
        )
        graph = plan_to_graph(plan)

        normalized = normalize_plan(plan, graph)
        assert normalized.execution_batches == [["A"], ["B"]]
        assert normalized.contracts == plan.contracts
        assert normalized.task_dag[1].depends_on == ("A",)
        assert normalized.graph is graph

    def test_rebuilds_projection_columns_from_graph(self):
        """Graph wins over inconsistent plan fields."""
        graph = plan_to_graph(
            IntegratedPlan(
                engineering_spec="Spec",
                contracts=[],
                task_dag=[
                    TaskNode(repository="A", instruction=""),
                    TaskNode(repository="B", instruction="", depends_on=("A",)),
                    TaskNode(repository="C", instruction="", depends_on=("B",)),
                ],
                execution_batches=[["A"], ["B"], ["C"]],
            )
        )
        plan = IntegratedPlan(
            engineering_spec="Spec",
            contracts=[],
            task_dag=[
                TaskNode(repository="A", instruction=""),
                TaskNode(repository="B", instruction=""),
                TaskNode(repository="C", instruction=""),
            ],
            execution_batches=[["A", "B", "C"]],  # wrong vs graph
        )

        normalized = normalize_plan(plan, graph)
        assert normalized.execution_batches == [["A"], ["B"], ["C"]]
        assert normalized.task_dag[2].depends_on == ("B",)

    def test_carries_llm_metadata_keyed_by_repository(self):
        plan = IntegratedPlan(
            engineering_spec="Spec",
            contracts=[],
            task_dag=[
                TaskNode(
                    repository="A",
                    instruction="do A",
                    parallelizable_with=("C",),
                    tests=("pytest",),
                ),
                TaskNode(repository="B", instruction="do B", depends_on=("A",)),
            ],
            execution_batches=[["A"], ["B"]],
        )

        normalized = normalize_plan(plan, plan_to_graph(plan))
        node_a = next(t for t in normalized.task_dag if t.repository == "A")
        assert node_a.instruction == "do A"
        assert node_a.parallelizable_with == ("C",)
        assert node_a.tests == ("pytest",)


class StubGraphService:
    """Duck-typed DependencyGraphService for graph-assisted integration."""

    def __init__(
        self,
        edges: list[ScanEdge],
        batches: list[list[str]],
        cyclic_repos: list[str] | None = None,
    ) -> None:
        self._edges = edges
        self._batches = batches
        self._cyclic = cyclic_repos or []

    def edges_in(self, repos: list[str]) -> list[ScanEdge]:
        return self._edges

    def topological_batches(self, repos: list[str]) -> TopoResult:
        return TopoResult(batches=self._batches, cyclic_repos=self._cyclic)


def _llm_response(*, task_dag: list[dict], contracts: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "engineering_spec": "Graph-assisted plan",
            "contracts": contracts or [],
            "task_dag": task_dag,
        }
    )


class TestGraphAssistedIntegration:
    def test_scan_edges_and_batches_are_authoritative(self):
        graph_service = StubGraphService(
            edges=[
                ScanEdge(
                    producer="A",
                    consumer="B",
                    confidence="confirmed",
                    match_reason="exact",
                )
            ],
            batches=[["A"], ["B"]],
        )
        llm = StubLLM(
            _llm_response(
                task_dag=[
                    {"repository": "A", "instruction": "do A", "depends_on": []},
                    # LLM claims no dependency; the graph supplies it.
                    {"repository": "B", "instruction": "do B", "depends_on": []},
                ]
            )
        )
        service = PlanIntegrationService(llm, graph=graph_service)

        plan = service.integrate(
            "requirement",
            _make_summary(_make_result("A"), _make_result("B")),
        )

        assert plan.execution_batches == [["A"], ["B"]]
        assert plan.graph is not None
        scan_edges = [e for e in plan.graph.edges if e.source == "scan"]
        assert scan_edges == [
            PlanGraphEdge(from_="A", to="B", status="confirmed", source="scan")
        ]
        assert plan.task_dag[1].depends_on == ("A",)
        assert integration_method(plan.graph) == "graph_assisted"

    def test_possible_edges_never_enter_batches(self):
        graph_service = StubGraphService(
            edges=[
                ScanEdge(
                    producer="A",
                    consumer="B",
                    confidence="confirmed",
                    match_reason="exact",
                ),
                ScanEdge(
                    producer="B",
                    consumer="C",
                    confidence="possible",
                    match_reason="substring",
                ),
            ],
            # World-layer topo includes the possible edge; the plan must not.
            batches=[["A"], ["B"], ["C"]],
        )
        llm = StubLLM(
            _llm_response(
                task_dag=[
                    {"repository": "A", "instruction": "do A", "depends_on": []},
                    {"repository": "B", "instruction": "do B", "depends_on": []},
                    {"repository": "C", "instruction": "do C", "depends_on": []},
                ]
            )
        )
        service = PlanIntegrationService(llm, graph=graph_service)

        plan = service.integrate(
            "requirement",
            _make_summary(_make_result("A"), _make_result("B"), _make_result("C")),
        )

        # C is not serialised after B: the possible edge is candidate-only.
        assert plan.execution_batches == [["A", "C"], ["B"]]
        candidate = [e for e in plan.graph.edges if e.status == "candidate"]
        assert candidate == [
            PlanGraphEdge(from_="B", to="C", status="candidate", source="scan")
        ]
        node_c = next(t for t in plan.task_dag if t.repository == "C")
        assert node_c.depends_on == ()

    def test_contract_promotes_possible_edge_and_adds_interface(self):
        graph_service = StubGraphService(
            edges=[
                ScanEdge(
                    producer="B",
                    consumer="C",
                    confidence="possible",
                    match_reason="substring",
                )
            ],
            batches=[["B", "C"]],
        )
        llm = StubLLM(
            _llm_response(
                task_dag=[
                    {"repository": "B", "instruction": "do B", "depends_on": []},
                    {"repository": "C", "instruction": "do C", "depends_on": []},
                ],
                contracts=[
                    {
                        "producer": "B",
                        "consumer": "C",
                        "interface": "API",
                        "agreement": "both sides",
                    }
                ],
            )
        )
        service = PlanIntegrationService(llm, graph=graph_service)

        plan = service.integrate(
            "requirement",
            _make_summary(_make_result("B"), _make_result("C")),
        )

        edge = [e for e in plan.graph.edges if e.from_ == "B" and e.to == "C"][0]
        assert edge.status == "confirmed"
        assert edge.source == "scan"  # promoted, provenance preserved
        assert edge.interface == "API"
        assert plan.execution_batches == [["B"], ["C"]]
        assert [(c.producer, c.consumer) for c in plan.contracts] == [("B", "C")]
        assert integration_method(plan.graph) == "graph_assisted"

    def test_llm_depends_on_new_edge_enters_batches(self):
        graph_service = StubGraphService(edges=[], batches=[["A", "B"]])
        llm = StubLLM(
            _llm_response(
                task_dag=[
                    {"repository": "A", "instruction": "do A", "depends_on": []},
                    {"repository": "B", "instruction": "do B", "depends_on": ["A"]},
                ]
            )
        )
        service = PlanIntegrationService(llm, graph=graph_service)

        plan = service.integrate(
            "requirement",
            _make_summary(
                _make_result("A"),
                _make_result("B", depends_on=("A",)),
            ),
        )

        # The LLM-discovered dependency is a confirmed llm edge and therefore
        # enters the execution batches even though the scan graph was empty.
        assert plan.execution_batches == [["A"], ["B"]]
        assert [e.source for e in plan.graph.edges] == ["llm"]
        assert integration_method(plan.graph) == "llm_only"

    def test_ungrounded_llm_edge_does_not_serialize_parallel_consumers(self):
        pricing = "repomesh-e2e-pricing-core"
        billing = "repomesh-e2e-billing"
        checkout = "repomesh-e2e-checkout"
        graph_service = StubGraphService(
            edges=[],
            batches=[[pricing, billing, checkout]],
        )
        llm = StubLLM(
            _llm_response(
                task_dag=[
                    {
                        "repository": pricing,
                        "instruction": "extend pricing contract",
                        "depends_on": [],
                    },
                    {
                        "repository": billing,
                        "instruction": "render invoice precision",
                        "depends_on": [checkout, pricing],
                    },
                    {
                        "repository": checkout,
                        "instruction": "pass currency through checkout",
                        "depends_on": [pricing],
                    },
                ],
                contracts=[
                    {
                        "producer": checkout,
                        "consumer": billing,
                        "interface": "POST /checkout",
                        "agreement": "invented cross-consumer flow",
                    },
                    {
                        "producer": pricing,
                        "consumer": billing,
                        "interface": "GET /pricing/rounding-rules",
                        "agreement": "billing consumes pricing precision",
                    },
                    {
                        "producer": pricing,
                        "consumer": checkout,
                        "interface": "POST /quote",
                        "agreement": "checkout consumes pricing quote",
                    },
                ],
            )
        )
        service = PlanIntegrationService(llm, graph=graph_service)

        plan = service.integrate(
            "multi-currency precision",
            _make_summary(
                _make_result(
                    pricing,
                    depends_on=(),
                    impacts=(billing, checkout),
                ),
                _make_result(billing, depends_on=(pricing,)),
                # Reproduce the weak aliases returned by the R2 confirmation
                # model.  They are not exact approved repository identities.
                _make_result(checkout, depends_on=("pricing-core", "billing")),
            ),
        )

        assert plan.execution_batches == [[pricing], [billing, checkout]]
        assert {(e.from_, e.to) for e in plan.graph.edges} == {
            (pricing, billing),
            (pricing, checkout),
        }
        assert {(c.producer, c.consumer) for c in plan.contracts} == {
            (pricing, billing),
            (pricing, checkout),
        }
