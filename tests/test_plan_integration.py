"""Tests for PlanIntegrationService."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from repomesh.modules.repository_intelligence.application.confirmation import (  # noqa: E402
    ConfirmationResult,
    ConfirmationSummary,
    RepositoryPlan,
)
from repomesh.modules.repository_intelligence.application.plan_integration import (  # noqa: E402
    ContractSpec,
    IntegratedPlan,
    PlanIntegrationService,
    TaskNode,
    _parse_integrated_plan,
    _topological_batches,
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


def _make_result(repo: str, status: str = "REQUIRED") -> ConfirmationResult:
    return ConfirmationResult(
        repository=repo,
        status=status,
        confidence=0.9,
        reason=f"test reason for {repo}",
        plan_summary=f"change {repo} for the requirement",
        plan=RepositoryPlan(
            changed_apis=("GET /api/v1/endpoint",),
            changed_modules=("src/main",),
            depends_on=("ts-common",),
            impacts=(),
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
