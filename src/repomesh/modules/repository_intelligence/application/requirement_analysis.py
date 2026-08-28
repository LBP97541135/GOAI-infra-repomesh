"""Requirement sufficiency analysis.

Evaluates whether a natural-language requirement contains enough *business*
information for the repository discovery system to work with.

Design principles
-----------------

* The user is a business person — they do **not** need to know which
  repositories or code files are involved.
* We only check whether the user has described **what** they want and **what
  problem** they are solving, not whether they know the technical details.
* If information is insufficient, we generate follow-up questions in plain
  business language.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from repomesh.modules.repository_intelligence.application.discovery import LLMClient

_logger = logging.getLogger(__name__)

#: Default confidence threshold below which we consider the requirement
#: insufficient. The composition root injects the configured value through
#: ``RequirementAnalyzer``; this stays as the parse-level default so the pure
#: parser keeps its historical behaviour when called directly (tests).
_DEFAULT_SUFFICIENCY_THRESHOLD = 0.7


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RequirementAnalysis:
    """Result of evaluating a requirement's information sufficiency."""

    sufficient: bool
    confidence: float
    missing_dimensions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    extracted_keywords: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Analyser
# ---------------------------------------------------------------------------


class RequirementAnalyzer:
    """Evaluates requirement sufficiency using an LLM.

    Parameters
    ----------
    llm_client:
        Any :class:`LLMClient` implementation (DeepSeek or test double).
    sufficiency_threshold:
        Confidence below which the analysis is treated as insufficient even
        when the model says otherwise (fail-closed). Injected from
        ``REPOMESH_DISCOVERY_ANALYSIS_CONFIDENCE_THRESHOLD`` at the
        composition root; the default matches the historical constant.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        sufficiency_threshold: float = 0.7,
    ) -> None:
        self._llm = llm_client
        self._threshold = sufficiency_threshold

    def analyze(self, requirement: str) -> RequirementAnalysis:
        """Return a :class:`RequirementAnalysis` for *requirement*."""
        messages = _build_analysis_prompt(requirement)
        raw = self._llm.chat(messages, temperature=0.0)
        return _parse_analysis(raw, threshold=self._threshold)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "你是一个需求分析助手。判断一份需求描述是否包含足够的业务信息，"
    "让仓库发现系统能准确地找到需要修改的仓库。\n\n"
    "重要原则：用户是业务人员，不需要知道涉及哪些仓库或代码文件。"
    "你只需要检查用户是否把『要做什么』和『什么问题』说清楚了。\n\n"
    "检查以下维度：\n"
    "1. 业务场景：是否描述了具体的功能场景和问题现象？\n"
    "2. 行为描述：是否说了期望改成什么行为？\n"
    "3. 变更类型：是否能判断是新增功能、修复 bug、还是重构？\n"
    "4. 技术约束：有无性能或兼容性要求？（可选，缺少不算不足）\n\n"
    "返回 ONLY 一个 JSON 对象（不要 markdown fence，不要额外文字）：\n"
    "{\n"
    '  "sufficient": true 或 false,\n'
    '  "confidence": 0.0 到 1.0 的浮点数,\n'
    '  "missing_dimensions": ["缺失的维度名称"],\n'
    '  "questions": ["用业务语言提问的追问，最多3个"],\n'
    '  "extracted_keywords": ["从需求中提取的关键业务词"]\n'
    "}"
)


def _build_analysis_prompt(requirement: str) -> list[dict[str, str]]:
    """Build the chat messages for the sufficiency evaluation LLM call."""

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"## 需求描述\n\n{requirement}"},
    ]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _extract_json_object(text: str) -> str:
    """Extract the outermost ``{...}`` block from *text*.

    Handles markdown fences and surrounding prose.
    """

    # Strip markdown fences.
    fence_pattern = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
    fence_match = fence_pattern.search(text)
    if fence_match:
        text = fence_match.group(1)

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
        raise ValueError("No JSON object found in response")
    return text[first_brace : last_brace + 1]


def _parse_analysis(
    raw: str, *, threshold: float = _DEFAULT_SUFFICIENCY_THRESHOLD
) -> RequirementAnalysis:
    """Parse the LLM response into a :class:`RequirementAnalysis`."""

    try:
        json_text = _extract_json_object(raw)
        data = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        _logger.warning("Failed to parse requirement analysis JSON, using defaults")
        return RequirementAnalysis(
            sufficient=True,  # Fail-open: let discovery proceed.
            confidence=0.5,
        )

    if not isinstance(data, dict):
        return RequirementAnalysis(sufficient=True, confidence=0.5)

    confidence = float(data.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))

    sufficient = bool(data.get("sufficient", True))
    # Cross-check: if confidence is below threshold, force insufficient.
    if confidence < threshold:
        sufficient = False

    missing_dimensions = [
        str(d) for d in data.get("missing_dimensions", []) if isinstance(d, str)
    ]

    questions = [
        str(q) for q in data.get("questions", []) if isinstance(q, str)
    ]

    extracted_keywords = [
        str(k) for k in data.get("extracted_keywords", []) if isinstance(k, str)
    ]

    return RequirementAnalysis(
        sufficient=sufficient,
        confidence=round(confidence, 4),
        missing_dimensions=missing_dimensions,
        questions=questions[:3],  # Cap at 3.
        extracted_keywords=extracted_keywords,
    )
