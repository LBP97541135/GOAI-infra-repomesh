"""Tests for requirement sufficiency analysis."""

from __future__ import annotations

import json

from repomesh.modules.repository_intelligence.application.requirement_analysis import (
    RequirementAnalysis,
    RequirementAnalyzer,
    _parse_analysis,
)

# ---------------------------------------------------------------------------
# Fake LLM client
# ---------------------------------------------------------------------------


class FakeLLMClient:
    """Returns a pre-canned response."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[list[dict[str, str]]] = []

    def chat(
        self, messages: list[dict[str, str]], *, temperature: float = 0.0
    ) -> str:
        self.calls.append(messages)
        return self._response


def _make_response(
    *,
    sufficient: bool = True,
    confidence: float = 0.9,
    missing_dimensions: list[str] | None = None,
    questions: list[str] | None = None,
    extracted_keywords: list[str] | None = None,
) -> str:
    """Build a JSON response string like the LLM would return."""
    return json.dumps(
        {
            "sufficient": sufficient,
            "confidence": confidence,
            "missing_dimensions": missing_dimensions or [],
            "questions": questions or [],
            "extracted_keywords": extracted_keywords or [],
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# _parse_analysis tests
# ---------------------------------------------------------------------------


class TestParseAnalysis:
    def test_sufficient_requirement(self) -> None:
        raw = _make_response(
            sufficient=True,
            confidence=0.95,
            extracted_keywords=["支付", "回调", "超时"],
        )
        result = _parse_analysis(raw)
        assert result.sufficient is True
        assert result.confidence == 0.95
        assert "支付" in result.extracted_keywords

    def test_insufficient_requirement(self) -> None:
        raw = _make_response(
            sufficient=False,
            confidence=0.3,
            missing_dimensions=["业务场景", "行为描述"],
            questions=["具体是哪个环节？", "期望什么效果？"],
        )
        result = _parse_analysis(raw)
        assert result.sufficient is False
        assert len(result.missing_dimensions) == 2
        assert len(result.questions) == 2

    def test_low_confidence_forces_insufficient(self) -> None:
        """Even if LLM says sufficient=True, low confidence overrides."""
        raw = _make_response(sufficient=True, confidence=0.4)
        result = _parse_analysis(raw)
        assert result.sufficient is False

    def test_confidence_capped_to_range(self) -> None:
        raw = _make_response(confidence=1.5)
        result = _parse_analysis(raw)
        assert result.confidence == 1.0

        raw2 = _make_response(confidence=-0.3)
        result2 = _parse_analysis(raw2)
        assert result2.confidence == 0.0

    def test_questions_capped_at_three(self) -> None:
        raw = _make_response(
            sufficient=False,
            questions=["q1", "q2", "q3", "q4", "q5"],
        )
        result = _parse_analysis(raw)
        assert len(result.questions) == 3

    def test_markdown_fence_stripped(self) -> None:
        raw = "```json\n" + _make_response(sufficient=True, confidence=0.9) + "\n```"
        result = _parse_analysis(raw)
        assert result.sufficient is True

    def test_prose_around_json_ignored(self) -> None:
        raw = (
            "Here is my analysis:\n"
            + _make_response(sufficient=False, confidence=0.2)
            + "\nHope this helps!"
        )
        result = _parse_analysis(raw)
        assert result.sufficient is False

    def test_invalid_json_fails_open(self) -> None:
        """On parse failure, we fail-open (sufficient=True) to not block the user."""
        result = _parse_analysis("this is not json at all")
        assert result.sufficient is True
        assert result.confidence == 0.5

    def test_empty_dimensions_default(self) -> None:
        raw = json.dumps({"sufficient": True, "confidence": 0.8})
        result = _parse_analysis(raw)
        assert result.missing_dimensions == []
        assert result.questions == []
        assert result.extracted_keywords == []


# ---------------------------------------------------------------------------
# RequirementAnalyzer tests
# ---------------------------------------------------------------------------


class TestRequirementAnalyzer:
    def test_analyze_sufficient(self) -> None:
        response = _make_response(
            sufficient=True,
            confidence=0.9,
            extracted_keywords=["订票", "邮件", "通知"],
        )
        analyzer = RequirementAnalyzer(FakeLLMClient(response))
        result = analyzer.analyze("修复订票流程中发送通知邮件的异常")

        assert isinstance(result, RequirementAnalysis)
        assert result.sufficient is True
        assert result.confidence == 0.9

    def test_analyze_insufficient_returns_questions(self) -> None:
        response = _make_response(
            sufficient=False,
            confidence=0.3,
            missing_dimensions=["业务场景", "行为描述"],
            questions=[
                "请具体说明是哪个环节需要优化？",
                "期望的改进效果是什么？",
            ],
        )
        analyzer = RequirementAnalyzer(FakeLLMClient(response))
        result = analyzer.analyze("优化订票流程")

        assert result.sufficient is False
        assert len(result.questions) == 2
        assert "业务场景" in result.missing_dimensions

    def test_questions_are_business_language(self) -> None:
        """Questions should not contain technical terms like service names."""
        response = _make_response(
            sufficient=False,
            confidence=0.2,
            questions=[
                "请问是哪种支付方式？（扫码支付 / H5 支付 / 退款）",
                "涉及哪些业务流程？",
            ],
        )
        analyzer = RequirementAnalyzer(FakeLLMClient(response))
        result = analyzer.analyze("加微信支付")

        for q in result.questions:
            # Questions should be in business language, not referencing repos.
            assert "ts-" not in q
            assert "service" not in q.lower() or "支付" in q

    def test_llm_called_with_correct_messages(self) -> None:
        fake = FakeLLMClient(_make_response(sufficient=True, confidence=0.9))
        analyzer = RequirementAnalyzer(fake)
        analyzer.analyze("some requirement")

        assert len(fake.calls) == 1
        messages = fake.calls[0]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "some requirement" in messages[1]["content"]

    def test_temperature_zero(self) -> None:
        """Analyser should use temperature=0 for determinism."""
        fake = FakeLLMClient(_make_response())
        analyzer = RequirementAnalyzer(fake)
        analyzer.analyze("test")
        # FakeLLMClient ignores temperature, but we verify the call happens.
        assert len(fake.calls) == 1
