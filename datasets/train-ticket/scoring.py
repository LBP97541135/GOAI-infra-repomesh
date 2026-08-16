#!/usr/bin/env python3
"""RepoMesh validation scoring script for the Train-Ticket dataset.

This script evaluates RepoMesh's repository-discovery output against the
hand-labeled ground truth in ``validation_cases.json``.

A test case defines three tiers of expected repositories:

* ``ground_truth.direct``     — repositories actually modified by the commit.
  Missing these is the most severe error (weight ``0.6`` in Recall).
* ``ground_truth.propagated`` — repositories reachable via the call graph that
  are logically affected but were not directly modified (weight ``0.2``).
* ``ground_truth.context``    — same business domain, may need a follow-up
  change; allowed to be missed without Precision penalty (weight ``0.2``).

Scoring formulas (per case)::

    direct_recall     = |predicted ∩ direct|     / |direct|
    propagated_recall = |predicted ∩ propagated| / |propagated|  (1.0 if empty)
    context_recall    = |predicted ∩ context|    / |context|     (1.0 if empty)

    recall    = 0.6 * direct_recall + 0.2 * propagated_recall + 0.2 * context_recall
    precision = |predicted ∩ all_truth| / |predicted|
    f1        = 2 * precision * recall / (precision + recall)

Usage (CLI)::

    # Score a single prediction file (one repo list per line, or JSON list)
    python datasets/train-ticket/scoring.py \\
        --predictions path/to/output.json \\
        --report report.txt

    # Score a predictions directory (one file per case, named TT-001.json ...)
    python datasets/train-ticket/scoring.py \\
        --predictions-dir path/to/run/ \\
        --report report.txt

    # Quick self-test: feed each case's own ground truth back in
    python datasets/train-ticket/scoring.py --self-test

The predictions payload may be supplied in any of these forms:

* A JSON list of strings ``["ts-order-service", ...]``
* A JSON object ``{"repositories": [...]}`` or ``{"repos": [...]}``
* A plain text file with one repository per line
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- Module location ---------------------------------------------------------

DEFAULT_CASES_PATH = Path(__file__).resolve().parent / "validation_cases.json"

# Weights for the three tiers in the weighted Recall.
WEIGHT_DIRECT = 0.6
WEIGHT_PROPAGATED = 0.2
WEIGHT_CONTEXT = 0.2

# Floating-point tolerance for weight equality checks.
_EPS = 1e-9

# Order in which difficulty groups are reported.
DIFFICULTY_ORDER = ("easy", "medium", "hard")


# --- Data classes ------------------------------------------------------------


@dataclass
class CaseMetrics:
    """Per-case evaluation metrics."""

    case_id: str
    difficulty: str
    anti_bias_type: str
    direct_recall: float
    propagated_recall: float
    context_recall: float
    recall: float
    precision: float
    f1: float
    predicted: list[str] = field(default_factory=list)
    direct: list[str] = field(default_factory=list)
    propagated: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    missed_direct: list[str] = field(default_factory=list)
    missed_propagated: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)


# --- Core scoring logic ------------------------------------------------------


def _safe_ratio(hit: int, total: int) -> float:
    """Return ``hit / total`` with the empty-set convention → 1.0.

    An empty tier means the case does not exercise that tier, so it should not
    penalize Recall. We follow the convention from the validation design doc:
    ``propagated_recall = 1.0 if not propagated``.
    """

    if total == 0:
        return 1.0
    return hit / total


def _f1(precision: float, recall: float) -> float:
    """Harmonic mean of precision and recall (0.0 when both are 0)."""

    denom = precision + recall
    if denom <= _EPS:
        return 0.0
    return 2.0 * precision * recall / denom


def score_case(predicted: list[str], ground_truth: dict[str, Any]) -> CaseMetrics:
    """Score one case.

    ``predicted`` is the list of repository names returned by RepoMesh.
    ``ground_truth`` is the ``ground_truth`` object from the case.
    """

    direct = list(ground_truth.get("direct", []))
    propagated = list(ground_truth.get("propagated", []))
    context = list(ground_truth.get("context", []))

    predicted_set = set(predicted)
    direct_set = set(direct)
    propagated_set = set(propagated)
    context_set = set(context)
    all_truth_set = direct_set | propagated_set | context_set

    direct_recall = _safe_ratio(len(predicted_set & direct_set), len(direct_set))
    propagated_recall = _safe_ratio(
        len(predicted_set & propagated_set), len(propagated_set)
    )
    context_recall = _safe_ratio(len(predicted_set & context_set), len(context_set))

    recall = (
        WEIGHT_DIRECT * direct_recall
        + WEIGHT_PROPAGATED * propagated_recall
        + WEIGHT_CONTEXT * context_recall
    )

    precision = (
        len(predicted_set & all_truth_set) / len(predicted_set) if predicted_set else 0.0
    )

    f1 = _f1(precision, recall)

    return CaseMetrics(
        case_id="",
        difficulty="",
        anti_bias_type="",
        direct_recall=direct_recall,
        propagated_recall=propagated_recall,
        context_recall=context_recall,
        recall=recall,
        precision=precision,
        f1=f1,
        predicted=sorted(predicted_set),
        direct=direct,
        propagated=propagated,
        context=context,
        missed_direct=sorted(direct_set - predicted_set),
        missed_propagated=sorted(propagated_set - predicted_set),
        extra=sorted(predicted_set - all_truth_set),
    )


# --- Predictions loading -----------------------------------------------------


def _extract_repo_list(payload: Any) -> list[str]:
    """Normalize a predictions payload into a flat list of repo names."""

    if payload is None:
        return []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("repositories", "repos", "predicted", "result", "output"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        else:
            # Fall back: dict values that look like strings.
            items = [v for v in payload.values() if isinstance(v, str)]
    else:
        items = [payload]

    repos: list[str] = []
    for item in items:
        if isinstance(item, str):
            repo = item.strip()
            if repo:
                repos.append(repo)
        elif isinstance(item, dict):
            # Accept {"name": "..."} / {"repo": "..."} objects too.
            for key in ("name", "repo", "repository", "id"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    repos.append(value.strip())
                    break
    return repos


def load_predictions_file(path: Path) -> list[str]:
    """Load a single predictions file.

    The file may be JSON (list, or object with a repositories field) or plain
    text with one repository per line.
    """

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    # Try JSON first; fall back to line-delimited text.
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return [line.strip() for line in text.splitlines() if line.strip()]

    return _extract_repo_list(payload)


def load_predictions(
    cases: list[dict[str, Any]],
    predictions_path: Path | None,
    predictions_dir: Path | None,
) -> dict[str, list[str]]:
    """Build a ``{case_id: [repo, ...]}`` map from the supplied predictions.

    If ``predictions_dir`` is given, each case is loaded from
    ``<dir>/<case_id>.json`` (or ``.txt``). Otherwise a single ``predictions_path``
    file is expected to be a JSON object mapping case IDs to repo lists, or a
    JSON list aligned with the case order in ``validation_cases.json``.
    """

    by_id: dict[str, list[str]] = {case["id"]: [] for case in cases}

    if predictions_dir is not None:
        for case in cases:
            case_id = case["id"]
            for candidate in (
                predictions_dir / f"{case_id}.json",
                predictions_dir / f"{case_id}.txt",
                predictions_dir / f"{case_id}",
            ):
                if candidate.exists():
                    by_id[case_id] = load_predictions_file(candidate)
                    break
        return by_id

    if predictions_path is not None:
        text = predictions_path.read_text(encoding="utf-8").strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            # Plain text: treat as the prediction for the first case only.
            repos = [line.strip() for line in text.splitlines() if line.strip()]
            if cases:
                by_id[cases[0]["id"]] = repos
            return by_id

        if isinstance(payload, dict):
            # Either {case_id: [repos]} or a single {"repositories": [...]}.
            if any(key in payload for key in ("repositories", "repos", "predicted")):
                repos = _extract_repo_list(payload)
                if cases:
                    by_id[cases[0]["id"]] = repos
            else:
                for case_id, value in payload.items():
                    if case_id in by_id:
                        by_id[case_id] = _extract_repo_list(value)
        elif isinstance(payload, list):
            # Aligned list of repo lists; one entry per case in order.
            for case, value in zip(cases, payload, strict=False):
                by_id[case["id"]] = _extract_repo_list(value)
        return by_id

    return by_id


# --- Trace-event linkage (M4) ------------------------------------------------


#: Tool names that a repository-recommendation agent turn may invoke; when one
#: of these finished ok, its payload output text is trusted as the repo list.
TRACE_RECOMMEND_TOOLS: frozenset[str] = frozenset(
    {
        "repository_recommend",
        "repository_recommendations",
        "recommend_repositories",
        "recommend_repos",
        "rank_repositories",
        "predict_repositories",
        "repo_recommend",
    }
)


def _trace_case_id(payload: dict[str, Any]) -> str | None:
    for key in ("case_id", "caseId", "id", "task_id", "taskId"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _trace_repo_list(
    payload: dict[str, Any], *, trust_output_text: bool
) -> list[str]:
    """Explicit repositories/repos/predicted lists always win; output text is
    trusted only for known recommendation tools (its stored form is truncated,
    so it is a last resort)."""
    for key in ("repositories", "repos", "predicted"):
        value = payload.get(key)
        if isinstance(value, list):
            repos = _extract_repo_list(value)
            if repos:
                return repos
    if trust_output_text:
        output = payload.get("output")
        if isinstance(output, str) and output.strip():
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            if lines:
                return lines
    return []


def extract_trace_predictions(events: Any) -> dict[str, list[str]]:
    """Map trace events that carried a repository recommendation to predictions.

    Accepts the shape of the ``/observe/trace/events`` API records (or a raw
    dump of ``observability.trace_events``): a sequence of dicts with ``name``,
    ``status`` and ``payload``. An event contributes a prediction when it
    finished ok AND either its tool name is a known recommendation tool or its
    payload carries an explicit ``repositories``/``repos``/``predicted`` list.
    The case id is read from the payload (``case_id``/``caseId``/``id``/
    ``task_id``/``taskId``); events without one are skipped. Later events
    overwrite earlier ones for the same case, mirroring ``load_predictions``.
    """
    predictions: dict[str, list[str]] = {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if event.get("status") not in (None, "ok"):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        name = str(event.get("name") or "")
        trust_output_text = name in TRACE_RECOMMEND_TOOLS or "recommend" in name
        repos = _trace_repo_list(payload, trust_output_text=trust_output_text)
        if not repos:
            continue
        case_id = _trace_case_id(payload)
        if not case_id:
            continue
        predictions[case_id] = repos
    return predictions


def _load_trace_events(path: Path) -> list[dict[str, Any]]:
    """Load trace events from a JSON list, a single event, or JSONL lines."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        inner = data.get("events")
        if isinstance(inner, list):
            return [item for item in inner if isinstance(item, dict)]
        return [data]
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


# --- Report rendering --------------------------------------------------------


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _build_report(
    cases: list[dict[str, Any]],
    predictions: dict[str, list[str]],
    overall_only: bool = False,
) -> str:
    """Render a human-readable scoring report."""

    metrics: list[tuple[dict[str, Any], CaseMetrics]] = []
    for case in cases:
        case_id = case["id"]
        m = score_case(predictions.get(case_id, []), case["ground_truth"])
        m.case_id = case_id
        m.difficulty = case.get("difficulty", "unknown")
        m.anti_bias_type = case.get("anti_bias_type", "none")
        metrics.append((case, m))

    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("RepoMesh Train-Ticket Validation Report")
    lines.append("=" * 78)
    lines.append("")

    # --- Per-case detail -----------------------------------------------------
    if not overall_only:
        header = (
            f"{'Case':<8} {'Diff':<7} {'AntiBias':<12} "
            f"{'Recall':>7} {'Prec':>7} {'F1':>7}  Notes"
        )
        lines.append(header)
        lines.append("-" * len(header))
        for _case, m in metrics:
            notes: list[str] = []
            if m.missed_direct:
                notes.append(f"miss_direct=[{','.join(m.missed_direct)}]")
            if m.missed_propagated:
                notes.append(f"miss_prop=[{','.join(m.missed_propagated)}]")
            if m.extra:
                notes.append(f"extra=[{','.join(m.extra)}]")
            note_text = " ".join(notes)
            lines.append(
                f"{m.case_id:<8} {m.difficulty:<7} {m.anti_bias_type:<12} "
                f"{m.recall:>7.3f} {m.precision:>7.3f} {m.f1:>7.3f}  {note_text}"
            )
        lines.append("")

    # --- By difficulty group -------------------------------------------------
    lines.append("-" * 78)
    lines.append("By difficulty")
    lines.append("-" * 78)
    group_header = (
        f"{'Group':<10} {'Count':>5} {'Recall':>9} {'Prec':>9} {'F1':>9}"
    )
    lines.append(group_header)
    lines.append("-" * len(group_header))

    for difficulty in DIFFICULTY_ORDER:
        group = [m for _, m in metrics if m.difficulty == difficulty]
        if not group:
            continue
        lines.append(
            f"{difficulty:<10} {len(group):>5} "
            f"{_mean([m.recall for m in group]):>9.3f} "
            f"{_mean([m.precision for m in group]):>9.3f} "
            f"{_mean([m.f1 for m in group]):>9.3f}"
        )

    # --- By anti-bias type ---------------------------------------------------
    lines.append("")
    lines.append("-" * 78)
    lines.append("By anti-bias type")
    lines.append("-" * 78)
    bias_header = f"{'Type':<14} {'Count':>5} {'Recall':>9} {'Prec':>9} {'F1':>9}"
    lines.append(bias_header)
    lines.append("-" * len(bias_header))
    bias_types: list[str] = []
    for _, m in metrics:
        if m.anti_bias_type not in bias_types:
            bias_types.append(m.anti_bias_type)
    for bias_type in bias_types:
        group = [m for _, m in metrics if m.anti_bias_type == bias_type]
        if not group:
            continue
        lines.append(
            f"{bias_type:<14} {len(group):>5} "
            f"{_mean([m.recall for m in group]):>9.3f} "
            f"{_mean([m.precision for m in group]):>9.3f} "
            f"{_mean([m.f1 for m in group]):>9.3f}"
        )

    # --- Overall -------------------------------------------------------------
    lines.append("")
    lines.append("=" * 78)
    lines.append("Overall")
    lines.append("=" * 78)
    all_recall = _mean([m.recall for _, m in metrics])
    all_precision = _mean([m.precision for _, m in metrics])
    all_f1 = _mean([m.f1 for _, m in metrics])
    lines.append(
        f"Cases: {len(metrics)}    "
        f"Recall: {all_recall:.3f}    "
        f"Precision: {all_precision:.3f}    "
        f"F1: {all_f1:.3f}"
    )

    # Coverage of direct tier (binary: did we find every direct repo?).
    direct_full = [m for _, m in metrics if not m.missed_direct]
    if metrics:
        lines.append(
            f"Direct-tier full-hit rate: {len(direct_full)}/{len(metrics)} "
            f"({len(direct_full) / len(metrics):.1%})"
        )
    lines.append("=" * 78)

    return "\n".join(lines) + "\n"


# --- JSON summary (machine-readable) -----------------------------------------


def build_summary(
    cases: list[dict[str, Any]],
    predictions: dict[str, list[str]],
) -> dict[str, Any]:
    """Build a machine-readable JSON summary alongside the text report."""

    metrics: list[CaseMetrics] = []
    for case in cases:
        m = score_case(predictions.get(case["id"], []), case["ground_truth"])
        m.case_id = case["id"]
        m.difficulty = case.get("difficulty", "unknown")
        m.anti_bias_type = case.get("anti_bias_type", "none")
        metrics.append(m)

    def group(values: list[CaseMetrics]) -> dict[str, float]:
        return {
            "count": len(values),
            "recall": _mean([m.recall for m in values]),
            "precision": _mean([m.precision for m in values]),
            "f1": _mean([m.f1 for m in values]),
        }

    return {
        "overall": group(metrics),
        "by_difficulty": {
            d: group([m for m in metrics if m.difficulty == d])
            for d in DIFFICULTY_ORDER
            if any(m.difficulty == d for m in metrics)
        },
        "by_anti_bias_type": {
            b: group([m for m in metrics if m.anti_bias_type == b])
            for b in sorted({m.anti_bias_type for m in metrics})
        },
        "cases": [
            {
                "id": m.case_id,
                "difficulty": m.difficulty,
                "anti_bias_type": m.anti_bias_type,
                "recall": m.recall,
                "precision": m.precision,
                "f1": m.f1,
                "direct_recall": m.direct_recall,
                "propagated_recall": m.propagated_recall,
                "context_recall": m.context_recall,
                "predicted": m.predicted,
                "missed_direct": m.missed_direct,
                "missed_propagated": m.missed_propagated,
                "extra": m.extra,
            }
            for m in metrics
        ],
    }


# --- Self-test ---------------------------------------------------------------


def _assert_close(actual: float, expected: float, label: str) -> None:
    if not math.isclose(actual, expected, abs_tol=1e-9):
        raise AssertionError(
            f"self-test failed for {label}: expected {expected:.4f}, got {actual:.4f}"
        )


def run_self_test() -> None:
    """Validate the scoring formulas against hand-computed expectations.

    Feeding the union of all three tiers back as the prediction should give a
    perfect Recall and Precision of 1.0. Feeding an empty prediction should
    give Recall/Precision/F1 of 0.0 (except for the empty-tier convention
    which doesn't apply here because direct is always non-empty in our set).
    """

    # Synthetic case 1: full prediction.
    gt = {
        "direct": ["a", "b"],
        "propagated": ["c"],
        "context": ["d"],
    }
    m = score_case(["a", "b", "c", "d"], gt)
    _assert_close(m.direct_recall, 1.0, "synthetic-1 direct_recall")
    _assert_close(m.propagated_recall, 1.0, "synthetic-1 propagated_recall")
    _assert_close(m.context_recall, 1.0, "synthetic-1 context_recall")
    _assert_close(m.recall, 1.0, "synthetic-1 recall")
    _assert_close(m.precision, 1.0, "synthetic-1 precision")
    _assert_close(m.f1, 1.0, "synthetic-1 f1")

    # Synthetic case 2: empty prediction.
    m = score_case([], gt)
    _assert_close(m.direct_recall, 0.0, "synthetic-2 direct_recall")
    _assert_close(m.recall, 0.0, "synthetic-2 recall")
    _assert_close(m.precision, 0.0, "synthetic-2 precision")
    _assert_close(m.f1, 0.0, "synthetic-2 f1")

    # Synthetic case 3: hit direct + context, miss propagated, one extra.
    # direct_recall = 1.0, propagated_recall = 0.0, context_recall = 1.0
    # recall = 0.6*1 + 0.2*0 + 0.2*1 = 0.8
    # all_truth = {a,b,c,d}; predicted = {a,b,d,z} → precision = 3/4 = 0.75
    # f1 = 2*0.75*0.8/(0.75+0.8) = 1.2/1.55 ≈ 0.7741935
    m = score_case(["a", "b", "d", "z"], gt)
    _assert_close(m.direct_recall, 1.0, "synthetic-3 direct_recall")
    _assert_close(m.propagated_recall, 0.0, "synthetic-3 propagated_recall")
    _assert_close(m.context_recall, 1.0, "synthetic-3 context_recall")
    _assert_close(m.recall, 0.8, "synthetic-3 recall")
    _assert_close(m.precision, 0.75, "synthetic-3 precision")
    _assert_close(m.f1, 2 * 0.75 * 0.8 / (0.75 + 0.8), "synthetic-3 f1")

    # Synthetic case 4: empty propagated/context tiers → Recall ignores them.
    gt_sparse = {"direct": ["a"], "propagated": [], "context": []}
    m = score_case(["a"], gt_sparse)
    _assert_close(m.recall, 1.0, "synthetic-4 recall")
    _assert_close(m.precision, 1.0, "synthetic-4 precision")

    print("self-test passed")


# --- CLI ---------------------------------------------------------------------


def _load_cases(cases_path: Path) -> list[dict[str, Any]]:
    with cases_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"{cases_path} must contain a JSON list of cases")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score RepoMesh output against the Train-Ticket validation set.",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="Path to validation_cases.json (default: alongside this script).",
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        help=(
            "Single predictions file. JSON object {case_id: [repos]}, JSON list "
            "of repo lists aligned with the cases, or plain text for one case."
        ),
    )
    parser.add_argument(
        "--predictions-dir",
        type=Path,
        help=(
            "Directory with one predictions file per case "
            "(named <case_id>.json or <case_id>.txt)."
        ),
    )
    parser.add_argument(
        "--from-trace",
        type=Path,
        help=(
            "Score predictions extracted from an observability trace-event "
            "export (JSON list, single event, or JSONL of the "
            "/observe/trace/events records). Cannot be combined with "
            "--predictions/--predictions-dir."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Write the text report to this path (default: stdout).",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        help="Also write a machine-readable JSON summary to this path.",
    )
    parser.add_argument(
        "--overall-only",
        action="store_true",
        help="Omit the per-case detail table (still print group + overall).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run built-in self-tests and exit (no scoring).",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        run_self_test()
        return 0

    cases = _load_cases(args.cases)
    if args.from_trace is not None:
        if args.predictions is not None or args.predictions_dir is not None:
            parser.error(
                "--from-trace 不能与 --predictions/--predictions-dir 同时使用"
            )
        trace_events = _load_trace_events(args.from_trace)
        predictions = extract_trace_predictions(trace_events)
    else:
        predictions = load_predictions(cases, args.predictions, args.predictions_dir)
    report = _build_report(cases, predictions, overall_only=args.overall_only)

    if args.report is not None:
        args.report.write_text(report, encoding="utf-8")
        print(f"Report written to {args.report}")
    else:
        sys.stdout.write(report)

    if args.summary is not None:
        summary = build_summary(cases, predictions)
        args.summary.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Summary written to {args.summary}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
