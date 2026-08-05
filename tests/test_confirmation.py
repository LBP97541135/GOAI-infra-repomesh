"""Tests for the Repository Manager confirmation module."""

from __future__ import annotations

import json

from repomesh.modules.repository_intelligence.application.confirmation import (
    ConfirmationService,
    _build_confirmation_prompt,
    _parse_confirmation,
)
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
                "status": "MAYBE",
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

    def test_collects_missing_dependencies(self) -> None:
        """REQUIRED repo reports a missing dependency → supplemented."""
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
        service = ConfirmationService(llm, {"ts-preserve-service": profile})
        summary = service.confirm(["ts-preserve-service"], "fix notification")
        assert "ts-config-service" in summary.supplemented_repos

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
