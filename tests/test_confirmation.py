"""Tests for the Repository Manager confirmation module."""

from __future__ import annotations

import json
import re
import threading
import time

from repomesh.modules.repository_intelligence.application.confirmation import (
    ConfirmationService,
    SupplementEvidence,
    SupplementObservation,
    _build_confirmation_prompt,
    _parse_confirmation,
)
from repomesh.modules.repository_intelligence.application.dependency_graph import GraphEdge
from repomesh.modules.repository_intelligence.domain import (
    AutoCard,
    RepositoryProfile,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_profile(
    name: str = "ts-notification-service",
    *,
    deps: tuple[str, ...] = ("spring-boot", "lombok"),
    commits: tuple[str, ...] = ("fix email sending", "add SMS support"),
    apis: tuple[str, ...] = ("/api/v1/notifyservice/sendEmail",),
) -> RepositoryProfile:
    return RepositoryProfile(
        id=name,
        name=name,
        url=f"https://github.com/org/{name}",
        auto_card=AutoCard(
            top_dirs=("src/main/java",),
            deps=deps,
            recent_commits=commits,
            exposed_apis=apis,
            low_signal=False,
        ),
    )


class _FakeLLM:
    """Fake LLM that returns a pre-configured response."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.last_messages: list[dict[str, str]] = []

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        self.last_messages = messages
        return self._response


class _ByRepoLLM:
    """Fake LLM returning a per-repository response, order- and thread-safe.

    The user message carries ``## Your Repository: <name>``; extracting the
    name lets the same fake serve parallel confirmation where call order is
    not deterministic.
    """

    def __init__(self, by_name: dict[str, str]) -> None:
        self._by_name = by_name
        self.last_messages: list[dict[str, str]] = []

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        self.last_messages = messages
        match = re.search(r"## Your Repository: (\S+)", messages[1]["content"])
        name = match.group(1) if match else "?"
        return self._by_name.get(name, _REQUIRED)


class _StubGraph:
    """Minimal graph double exposing the two queries the service uses."""

    def __init__(
        self,
        forward: dict[str, list[GraphEdge]] | None = None,
        reverse: dict[str, list[GraphEdge]] | None = None,
    ) -> None:
        self._forward = forward or {}
        self._reverse = reverse or {}

    def forward_dependencies(self, repo_name: str) -> list[GraphEdge]:
        return list(self._forward.get(repo_name, ()))

    def reverse_dependencies(self, repo_name: str) -> list[GraphEdge]:
        return list(self._reverse.get(repo_name, ()))


def _result_json(status: str = "REQUIRED", **extra) -> str:
    payload = {
        "status": status,
        "confidence": 0.9,
        "reason": "r",
        "plan_summary": "p",
        "missing_dependencies": [],
    }
    payload.update(extra)
    return json.dumps(payload)


_REQUIRED = _result_json("REQUIRED")


# ---------------------------------------------------------------------------
# _parse_confirmation tests
# ---------------------------------------------------------------------------


class TestParseConfirmation:
    def test_required(self) -> None:
        raw = json.dumps(
            {
                "status": "REQUIRED",
                "confidence": 0.9,
                "reason": "handles email sending",
                "plan_summary": "fix email params",
                "missing_dependencies": [],
            }
        )
        result = _parse_confirmation(raw, "ts-notification-service")
        assert result.status == "REQUIRED"
        assert result.confidence == 0.9
        assert result.reason == "handles email sending"
        assert result.plan_summary == "fix email params"
        assert result.missing_dependencies == []

    def test_excluded(self) -> None:
        raw = json.dumps(
            {
                "status": "EXCLUDED",
                "confidence": 0.95,
                "reason": "handles avatar uploads",
                "plan_summary": "",
                "missing_dependencies": [],
            }
        )
        result = _parse_confirmation(raw, "ts-avatar-service")
        assert result.status == "EXCLUDED"
        assert result.confidence == 0.95
        assert result.reason == "handles avatar uploads"

    def test_missing_dependencies(self) -> None:
        raw = json.dumps(
            {
                "status": "REQUIRED",
                "confidence": 0.8,
                "reason": "calls notification API",
                "plan_summary": "update API call params",
                "missing_dependencies": ["ts-notification-service"],
            }
        )
        result = _parse_confirmation(raw, "ts-preserve-service")
        assert result.missing_dependencies == ["ts-notification-service"]

    def test_excluded_has_no_missing_deps(self) -> None:
        raw = json.dumps(
            {
                "status": "EXCLUDED",
                "confidence": 0.9,
                "reason": "unrelated",
                "plan_summary": "",
                "missing_dependencies": ["should-be-ignored"],
            }
        )
        result = _parse_confirmation(raw, "ts-avatar-service")
        assert result.status == "EXCLUDED"
        assert result.missing_dependencies == []  # always empty for EXCLUDED

    def test_markdown_fence(self) -> None:
        raw = (
            "```json\n"
            + json.dumps(
                {
                    "status": "REQUIRED",
                    "confidence": 0.7,
                    "reason": "test",
                    "plan_summary": "test",
                    "missing_dependencies": [],
                }
            )
            + "\n```"
        )
        result = _parse_confirmation(raw, "ts-test-service")
        assert result.status == "REQUIRED"

    def test_prose_wrapped(self) -> None:
        raw = (
            "Here is my decision:\n"
            + json.dumps(
                {
                    "status": "EXCLUDED",
                    "confidence": 0.9,
                    "reason": "not related",
                    "plan_summary": "",
                    "missing_dependencies": [],
                }
            )
            + "\nThat's all."
        )
        result = _parse_confirmation(raw, "ts-test-service")
        assert result.status == "EXCLUDED"

    def test_invalid_json_defaults_to_required(self) -> None:
        result = _parse_confirmation("not json at all", "ts-test-service")
        assert result.status == "REQUIRED"
        assert result.confidence == 0.5

    def test_invalid_status_defaults_to_required(self) -> None:
        raw = json.dumps(
            {
                "status": "UNKNOWN",
                "confidence": 0.5,
                "reason": "unsure",
            }
        )
        result = _parse_confirmation(raw, "ts-test-service")
        assert result.status == "REQUIRED"

    def test_confidence_clamped(self) -> None:
        raw = json.dumps(
            {
                "status": "REQUIRED",
                "confidence": 1.5,
                "reason": "test",
            }
        )
        result = _parse_confirmation(raw, "ts-test-service")
        assert result.confidence == 1.0

        raw_low = json.dumps(
            {
                "status": "REQUIRED",
                "confidence": -0.5,
                "reason": "test",
            }
        )
        result_low = _parse_confirmation(raw_low, "ts-test-service")
        assert result_low.confidence == 0.0

    def test_valid_json_with_invalid_field_types_uses_safe_defaults(self) -> None:
        raw = json.dumps(
            {
                "status": None,
                "confidence": "high",
                "changed_apis": None,
                "changed_modules": "pricing",
                "depends_on": ["orders", 42, ""],
                "risk": "critical",
                "missing_dependencies": "billing",
            }
        )

        result = _parse_confirmation(raw, "ts-pricing-service")

        assert result.status == "REQUIRED"
        assert result.confidence == 0.5
        assert result.plan is not None
        assert result.plan.changed_apis == ()
        assert result.plan.changed_modules == ()
        assert result.plan.depends_on == ("orders",)
        assert result.plan.risk == "medium"
        assert result.missing_dependencies == []

    def test_maybe_status(self) -> None:
        raw = json.dumps(
            {
                "status": "MAYBE",
                "confidence": 0.6,
                "reason": "might be indirectly affected",
                "plan_summary": "check API compatibility",
                "missing_dependencies": [],
            }
        )
        result = _parse_confirmation(raw, "ts-order-service")
        assert result.status == "MAYBE"
        assert result.confidence == 0.6

    def test_maybe_has_missing_deps(self) -> None:
        raw = json.dumps(
            {
                "status": "MAYBE",
                "confidence": 0.5,
                "reason": "depends on changing service",
                "plan_summary": "monitor",
                "missing_dependencies": ["ts-config-service"],
            }
        )
        result = _parse_confirmation(raw, "ts-order-service")
        assert result.missing_dependencies == ["ts-config-service"]


# ---------------------------------------------------------------------------
# Prompt builder tests
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_includes_repo_name(self) -> None:
        profile = _make_profile("ts-notification-service")
        messages = _build_confirmation_prompt(profile, "fix email", ["ts-notification-service"])
        assert "ts-notification-service" in messages[1]["content"]

    def test_includes_requirement(self) -> None:
        profile = _make_profile()
        messages = _build_confirmation_prompt(profile, "修复邮件通知异常", ["repo1"])
        assert "修复邮件通知异常" in messages[1]["content"]

    def test_includes_candidate_list(self) -> None:
        profile = _make_profile()
        messages = _build_confirmation_prompt(
            profile,
            "test",
            ["repo-a", "repo-b", "repo-c"],
        )
        assert "repo-a" in messages[1]["content"]
        assert "repo-c" in messages[1]["content"]

    def test_history_context_rendered_as_reference_section(self) -> None:
        """Phase 4b: the rendered history lands in the user message, clearly
        separated from the task instruction — reference evidence, not a
        directive."""
        profile = _make_profile("ts-notification-service")
        history = (
            "## Similar Historical Decisions\n"
            "1. confirmation / confirmed · 2026-08-01 · "
            "repos: ts-core-service (REQUIRED)"
        )
        messages = _build_confirmation_prompt(
            profile,
            "fix email",
            ["ts-notification-service"],
            history_context=history,
        )
        user = messages[1]["content"]
        assert "## Similar Historical Decisions" in user
        assert "1. confirmation / confirmed · 2026-08-01" in user
        assert user.index("## Similar Historical Decisions") < user.index("## Task")

    def test_without_history_context_no_history_section(self) -> None:
        """No history → byte-identical to the pre-Phase-4b prompt (no empty
        section, no stray header)."""
        profile = _make_profile()
        messages = _build_confirmation_prompt(profile, "fix email", ["repo1"])
        assert "## Similar Historical Decisions" not in messages[1]["content"]


# ---------------------------------------------------------------------------
# ConfirmationService tests
# ---------------------------------------------------------------------------


class TestConfirmationService:
    def test_excludes_unrelated(self) -> None:
        """Repository Manager says EXCLUDED → not in final list."""
        llm = _FakeLLM(
            json.dumps(
                {
                    "status": "EXCLUDED",
                    "confidence": 0.95,
                    "reason": "avatar uploads unrelated to email",
                    "plan_summary": "",
                    "missing_dependencies": [],
                }
            )
        )
        profile = _make_profile("ts-avatar-service")
        service = ConfirmationService(llm, {"ts-avatar-service": profile})
        summary = service.confirm(["ts-avatar-service"], "fix email")
        assert "ts-avatar-service" not in summary.final_repos
        assert len(summary.excluded) == 1
        assert len(summary.maybe) == 0

    def test_keeps_required(self) -> None:
        """Repository Manager says REQUIRED → in final list."""
        llm = _FakeLLM(
            json.dumps(
                {
                    "status": "REQUIRED",
                    "confidence": 0.9,
                    "reason": "handles email sending directly",
                    "plan_summary": "fix email params",
                    "missing_dependencies": [],
                }
            )
        )
        profile = _make_profile("ts-notification-service")
        service = ConfirmationService(llm, {"ts-notification-service": profile})
        summary = service.confirm(["ts-notification-service"], "fix email")
        assert "ts-notification-service" in summary.final_repos
        assert len(summary.required) == 1
        assert summary.required[0].plan_summary == "fix email params"

    def test_maybe_kept_in_final(self) -> None:
        """Repository Manager says MAYBE → still in final list."""
        llm = _FakeLLM(
            json.dumps(
                {
                    "status": "MAYBE",
                    "confidence": 0.6,
                    "reason": "might be indirectly affected",
                    "plan_summary": "check API compat",
                    "missing_dependencies": [],
                }
            )
        )
        profile = _make_profile("ts-order-service")
        service = ConfirmationService(llm, {"ts-order-service": profile})
        summary = service.confirm(["ts-order-service"], "fix email")
        assert "ts-order-service" in summary.final_repos
        assert len(summary.maybe) == 1
        assert len(summary.required) == 0

    def test_missing_dependency_reported_as_observation(self) -> None:
        """A REQUIRED repo reports a missing dependency outside the list →
        the name surfaces in the low-trust observation list (model's word
        only). It must NOT land in ``supplements`` — that list is reserved
        for the PM's deterministic graph pre-supplement."""
        llm = _FakeLLM(
            json.dumps(
                {
                    "status": "REQUIRED",
                    "confidence": 0.8,
                    "reason": "calls notification",
                    "plan_summary": "update API call",
                    "missing_dependencies": ["ts-config-service"],
                }
            )
        )
        profile = _make_profile("ts-preserve-service")
        config_profile = _make_profile("ts-config-service")
        service = ConfirmationService(
            llm,
            {
                "ts-preserve-service": profile,
                "ts-config-service": config_profile,
            },
        )
        summary = service.confirm(["ts-preserve-service"], "fix notification")
        assert summary.supplements == []
        assert summary.observations == [
            SupplementObservation(repository="ts-config-service", via="ts-preserve-service")
        ]

    def test_mixed_results(self) -> None:
        """Some REQUIRED, some EXCLUDED."""
        responses = [
            json.dumps(
                {
                    "status": "REQUIRED",
                    "confidence": 0.9,
                    "reason": "r1",
                    "plan_summary": "p1",
                    "missing_dependencies": [],
                }
            ),
            json.dumps(
                {
                    "status": "EXCLUDED",
                    "confidence": 0.95,
                    "reason": "r2",
                    "plan_summary": "",
                    "missing_dependencies": [],
                }
            ),
        ]
        call_count = [0]

        class _MultiLLM:
            def chat(self, messages, *, temperature=0.0):
                r = responses[call_count[0]]
                call_count[0] += 1
                return r

        p1 = _make_profile("ts-a-service")
        p2 = _make_profile("ts-b-service")
        service = ConfirmationService(
            _MultiLLM(),
            {
                "ts-a-service": p1,
                "ts-b-service": p2,
            },
        )
        summary = service.confirm(["ts-a-service", "ts-b-service"], "test")
        assert "ts-a-service" in summary.final_repos
        assert "ts-b-service" not in summary.final_repos

    def test_progress_callback(self) -> None:
        llm = _FakeLLM(
            json.dumps(
                {
                    "status": "REQUIRED",
                    "confidence": 0.9,
                    "reason": "r",
                    "plan_summary": "p",
                    "missing_dependencies": [],
                }
            )
        )
        profile = _make_profile("ts-a-service")
        service = ConfirmationService(llm, {"ts-a-service": profile})

        calls = []
        service.confirm(
            ["ts-a-service"],
            "test",
            on_progress=lambda i, total, name: calls.append((i, total, name)),
        )
        assert calls == [(1, 1, "ts-a-service")]

    # ------------------------------------------------ graph pre-supplement

    def test_graph_supplement_is_marked_and_carried(self) -> None:
        """A repo the PM's graph pre-supplement pulled in is flagged on its
        result, listed in supplements and carries its edge evidence — the
        same record feeds the prompt and the audit trail."""
        llm = _FakeLLM(_REQUIRED)
        profile = _make_profile("ts-a-service")
        service = ConfirmationService(llm, {"ts-a-service": profile})
        evidence = {
            "ts-a-service": SupplementEvidence(
                repository="ts-a-service",
                via="ts-core-service",
                confidence="confirmed",
                mechanism="reverse_dependencies",
                match_reason="core 的结账 API 变更会波及导出",
            )
        }
        summary = service.confirm(
            ["ts-a-service"],
            "fix email",
            supplement_evidence=evidence,
        )
        assert [s.repository for s in summary.supplements] == ["ts-a-service"]
        assert summary.supplements[0].via == "ts-core-service"
        assert summary.required[0].is_supplemented is True

    def test_supplement_reason_reaches_the_prompt(self) -> None:
        """The RM sees why it was called in (## Why You Were Added) — the
        graph evidence is the reason, not a bare name."""
        llm = _FakeLLM(_REQUIRED)
        profile = _make_profile("ts-a-service")
        service = ConfirmationService(llm, {"ts-a-service": profile})
        evidence = {
            "ts-a-service": SupplementEvidence(
                repository="ts-a-service",
                via="ts-core-service",
                confidence="confirmed",
                mechanism="reverse_dependencies",
                match_reason="结账 API 变更会波及导出",
            )
        }
        service.confirm(
            ["ts-a-service"],
            "fix email",
            supplement_evidence=evidence,
        )
        user = llm.last_messages[1]["content"]
        assert "## Why You Were Added" in user
        assert "结账 API 变更会波及导出" in user
        assert "ts-core-service" in user

    def test_without_supplement_evidence_nothing_is_marked(self) -> None:
        """No graph pre-supplement → the old behaviour: nothing supplemented,
        nothing flagged, no observation noise."""
        llm = _FakeLLM(_REQUIRED)
        profile = _make_profile("ts-a-service")
        service = ConfirmationService(llm, {"ts-a-service": profile})
        summary = service.confirm(["ts-a-service"], "fix email")
        assert summary.supplements == []
        assert summary.observations == []
        assert summary.required[0].is_supplemented is False
        assert "## Why You Were Added" not in llm.last_messages[1]["content"]

    # ------------------------------------------------ history injection (4b)

    def test_history_context_reaches_the_rm_prompt(self) -> None:
        """Phase 4b: the rendered history section reaches the Repository
        Manager's user message (serial path)."""
        llm = _FakeLLM(_REQUIRED)
        profile = _make_profile("ts-a-service")
        service = ConfirmationService(llm, {"ts-a-service": profile})
        history = (
            "## Similar Historical Decisions\n"
            "1. confirmation / confirmed · repos: ts-core-service (REQUIRED)"
        )
        service.confirm(
            ["ts-a-service"],
            "fix email",
            history_context=history,
        )
        user = llm.last_messages[1]["content"]
        assert "## Similar Historical Decisions" in user
        assert "ts-core-service (REQUIRED)" in user

    def test_history_context_reaches_every_parallel_worker(self) -> None:
        """The same immutable history string is handed to every worker — passed
        by value through ``copy_context``, never shared mutable state."""
        names = [f"ts-{i}-service" for i in range(3)]
        history = (
            "## Similar Historical Decisions\n"
            "1. confirmation / confirmed · repos: ts-core-service (REQUIRED)"
        )

        class _RecordingLLM:
            def __init__(self) -> None:
                self.recorded: list[str] = []

            def chat(
                self, messages: list[dict[str, str]], *, temperature: float = 0.0
            ) -> str:
                self.recorded.append(messages[1]["content"])
                return _REQUIRED

        llm = _RecordingLLM()
        service = ConfirmationService(
            llm, {n: _make_profile(n) for n in names}
        )
        service.confirm(names, "test", concurrency=3, history_context=history)
        assert len(llm.recorded) == len(names)
        for user in llm.recorded:
            assert "## Similar Historical Decisions" in user
            assert "ts-core-service (REQUIRED)" in user

    # ------------------------------------------------ parallel confirmation

    def test_parallel_preserves_candidate_order(self) -> None:
        """Results come back in candidate order even though the LLM calls
        finish out of order — order stability is part of the contract."""
        names = [f"ts-{i}-service" for i in range(6)]
        responses = {n: _result_json("REQUIRED", reason=f"reason-{n}") for n in names}
        service = ConfirmationService(
            _ByRepoLLM(responses),
            {n: _make_profile(n) for n in names},
        )
        summary = service.confirm(names, "test", concurrency=4)
        assert [r.repository for r in summary.required] == names
        assert [r.reason for r in summary.required] == [f"reason-{n}" for n in names]

    def test_parallel_respects_concurrency_cap(self) -> None:
        """Never more than ``concurrency`` LLM calls in flight, even with far
        more candidates than slots."""
        names = [f"ts-{i}-service" for i in range(8)]
        in_flight = 0
        peak = 0
        lock = threading.Lock()

        class _SlowLLM:
            def chat(self, messages, *, temperature=0.0):
                nonlocal in_flight, peak
                with lock:
                    in_flight += 1
                    peak = max(peak, in_flight)
                time.sleep(0.02)
                with lock:
                    in_flight -= 1
                return _REQUIRED

        service = ConfirmationService(
            _SlowLLM(),
            {n: _make_profile(n) for n in names},
        )
        service.confirm(names, "test", concurrency=3)
        assert peak <= 3
        assert peak > 1  # and it actually went parallel

    def test_parallel_progress_counts_finished_confirmations(self) -> None:
        """on_progress fires once per finished confirmation, completed
        counting 1..N and total staying at N (progress semantics are about
        finished calls, not the serial iteration index)."""
        names = [f"ts-{i}-service" for i in range(4)]
        service = ConfirmationService(
            _ByRepoLLM({n: _REQUIRED for n in names}),
            {n: _make_profile(n) for n in names},
        )
        calls = []
        service.confirm(
            names,
            "test",
            concurrency=4,
            on_progress=lambda i, total, name: calls.append((i, total, name)),
        )
        assert [i for i, _, _ in calls] == [1, 2, 3, 4]
        assert {total for _, total, _ in calls} == {4}
        assert {name for _, _, name in calls} == set(names)

    def test_concurrency_one_falls_back_to_serial(self) -> None:
        """concurrency=1 reproduces the pre-parallel path exactly — a single
        explicit concurrency must not behave differently from the default."""
        names = ["ts-a-service", "ts-b-service"]
        responses = {
            "ts-a-service": _result_json("REQUIRED"),
            "ts-b-service": _result_json("EXCLUDED"),
        }
        service = ConfirmationService(
            _ByRepoLLM(responses),
            {n: _make_profile(n) for n in names},
        )
        summary = service.confirm(names, "test", concurrency=1)
        assert summary.final_repos == ["ts-a-service"]
        assert len(summary.excluded) == 1

    # ------------------------------------------------ graph-vs-model conflicts

    def test_conflict_marks_excluded_consumer_with_confirmed_edge(self) -> None:
        """An EXCLUDED repo that *consumes* a kept repo's API via a confirmed
        edge contradicts the verdict: the producer's API is changing, so the
        consumer's exclusion deserves review."""
        graph = _StubGraph(
            forward={
                "ts-docs-service": [
                    GraphEdge(
                        producer="ts-core-service",
                        consumer="ts-docs-service",
                        confidence="confirmed",
                        mechanism="SOURCE",
                        match_reason="文档仓引用核心 API schema",
                    )
                ]
            }
        )
        responses = {
            "ts-core-service": _result_json("REQUIRED"),
            "ts-docs-service": _result_json("EXCLUDED"),
        }
        service = ConfirmationService(
            _ByRepoLLM(responses),
            {n: _make_profile(n) for n in responses},
            graph=graph,
        )
        summary = service.confirm(list(responses), "test")
        assert len(summary.conflicts) == 1
        conflict = summary.conflicts[0]
        assert conflict.repository == "ts-docs-service"
        assert conflict.status == "EXCLUDED"
        assert conflict.via == ("ts-core-service",)
        assert conflict.edges[0].producer == "ts-core-service"
        assert summary.excluded[0].graph_conflict is True

    def test_no_conflict_when_excluded_repo_is_depended_upon(self) -> None:
        """The reverse direction — the EXCLUDED repo's own API is what others
        consume — does not contradict its verdict: it is not changing."""
        graph = _StubGraph(
            reverse={
                "ts-config-service": [
                    GraphEdge(
                        producer="ts-config-service",
                        consumer="ts-order-service",
                        confidence="confirmed",
                        mechanism="SOURCE",
                        match_reason="订单仓调用配置 API",
                    )
                ]
            }
        )
        responses = {
            "ts-order-service": _result_json("REQUIRED"),
            "ts-config-service": _result_json("EXCLUDED"),
        }
        service = ConfirmationService(
            _ByRepoLLM(responses),
            {n: _make_profile(n) for n in responses},
            graph=graph,
        )
        summary = service.confirm(list(responses), "test")
        assert summary.conflicts == []
        assert summary.excluded[0].graph_conflict is False

    def test_declared_edges_do_not_raise_conflict(self) -> None:
        """Only hard (confirmed) edges can question a verdict; a declared edge
        is a discovery hint and carries no contradiction."""
        graph = _StubGraph(
            forward={
                "ts-docs-service": [
                    GraphEdge(
                        producer="ts-core-service",
                        consumer="ts-docs-service",
                        confidence="declared",
                        mechanism="DECLARED_DEPS",
                        match_reason="配置里声明过",
                    )
                ]
            }
        )
        responses = {
            "ts-core-service": _result_json("REQUIRED"),
            "ts-docs-service": _result_json("EXCLUDED"),
        }
        service = ConfirmationService(
            _ByRepoLLM(responses),
            {n: _make_profile(n) for n in responses},
            graph=graph,
        )
        summary = service.confirm(list(responses), "test")
        assert summary.conflicts == []
        assert summary.excluded[0].graph_conflict is False

    # ------------------------------------------------ low-trust observations

    def test_observations_skip_names_not_in_catalog(self) -> None:
        """A hallucinated dependency (not in the catalog) is filtered out —
        there is nothing the approver could tier, and surfacing it would just
        be the model talking to itself."""
        llm = _FakeLLM(
            _result_json(
                "REQUIRED",
                missing_dependencies=["ghost-service"],
            )
        )
        profile = _make_profile("ts-a-service")
        service = ConfirmationService(llm, {"ts-a-service": profile})
        summary = service.confirm(["ts-a-service"], "test")
        assert summary.observations == []

    def test_observations_skip_names_already_confirmed(self) -> None:
        """Names already in the confirmation list are not observations — they
        were confirmed for real, not merely mentioned."""
        responses = {
            "ts-a-service": _result_json(
                "REQUIRED",
                missing_dependencies=["ts-b-service"],
            ),
            "ts-b-service": _result_json("REQUIRED"),
        }
        service = ConfirmationService(
            _ByRepoLLM(responses),
            {n: _make_profile(n) for n in responses},
        )
        summary = service.confirm(list(responses), "test")
        assert summary.observations == []

    def test_observations_deduplicate_across_reporters(self) -> None:
        """Two reporters naming the same dependency produce one observation."""
        responses = {
            "ts-a-service": _result_json(
                "REQUIRED",
                missing_dependencies=["ts-config-service"],
            ),
            "ts-b-service": _result_json(
                "MAYBE",
                missing_dependencies=["ts-config-service"],
            ),
        }
        profiles = {
            n: _make_profile(n)
            for n in [*responses, "ts-config-service"]
        }
        service = ConfirmationService(_ByRepoLLM(responses), profiles)
        summary = service.confirm(list(responses), "test")
        assert len(summary.observations) == 1
        assert summary.observations[0].repository == "ts-config-service"
        # First reporter wins the `via` (deterministic, list-order).
        assert summary.observations[0].via == "ts-a-service"

    def test_observations_collect_plan_impacts(self) -> None:
        """Names from plan.impacts are observed too, not just
        missing_dependencies."""
        llm = _FakeLLM(
            json.dumps(
                {
                    "status": "REQUIRED",
                    "confidence": 0.8,
                    "reason": "r",
                    "plan_summary": "p",
                    "changed_apis": [],
                    "changed_modules": [],
                    "depends_on": [],
                    "impacts": ["ts-consumer-service"],
                    "risk": "medium",
                    "missing_dependencies": [],
                }
            )
        )
        profiles = {
            "ts-a-service": _make_profile("ts-a-service"),
            "ts-consumer-service": _make_profile("ts-consumer-service"),
        }
        service = ConfirmationService(llm, profiles)
        summary = service.confirm(["ts-a-service"], "test")
        assert summary.observations == [
            SupplementObservation(repository="ts-consumer-service", via="ts-a-service")
        ]
