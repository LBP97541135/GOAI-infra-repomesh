"""M4: trace-event exports feed the validation scorer (--from-trace)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scoring import extract_trace_predictions, main


def _event(name: str = "repository_recommend", status: str = "ok", **payload_extra):
    payload = {"case_id": "C01", **payload_extra}
    return {"name": name, "status": status, "payload": payload}


def test_extract_prefers_explicit_list_over_output_text() -> None:
    events = [
        _event(
            repositories=["a"],
            output="junk that must not win",
        ),
    ]
    assert extract_trace_predictions(events) == {"C01": ["a"]}


def test_extract_skips_non_ok_and_non_dict_events() -> None:
    events = [
        _event(status="error", repositories=["a"]),
        _event(status="skipped", repositories=["a"]),
        "not-a-dict",
        None,
    ]
    assert extract_trace_predictions(events) == {}


def test_extract_output_text_only_for_recommend_tools() -> None:
    events = [
        # A generic tool's output text is NOT trusted.
        {"name": "bash", "status": "ok", "payload": {"case_id": "C01", "output": "a\nb"}},
        # A recommendation tool's output text IS trusted (last resort).
        {
            "name": "repository_recommend",
            "status": "ok",
            "payload": {"case_id": "C02", "output": "x\ny"},
        },
    ]
    assert extract_trace_predictions(events) == {"C02": ["x", "y"]}


def test_extract_skips_events_without_case_id() -> None:
    events = [
        {"name": "repository_recommend", "status": "ok", "payload": {"output": "a"}},
        {"name": "repository_recommend", "status": "ok", "payload": {}},
    ]
    assert extract_trace_predictions(events) == {}


def test_extract_later_ok_wins_for_same_case() -> None:
    events = [
        _event(repositories=["a"]),
        _event(status="error", repositories=["zzz"]),
        _event(repositories=["b"]),
    ]
    assert extract_trace_predictions(events) == {"C01": ["b"]}


def test_extract_reads_alternate_case_id_keys() -> None:
    events = [
        {"name": "recommend_repos", "status": "ok", "payload": {"taskId": "T-9", "repos": ["r1"]}},
    ]
    assert extract_trace_predictions(events) == {"T-9": ["r1"]}


def test_from_trace_end_to_end(tmp_path: Path, capsys) -> None:
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            [
                {
                    "id": "C01",
                    "difficulty": "easy",
                    "ground_truth": {"direct": ["a"], "propagated": [], "context": []},
                },
                {
                    "id": "C02",
                    "difficulty": "medium",
                    "ground_truth": {"direct": ["x"], "propagated": [], "context": []},
                },
            ]
        ),
        encoding="utf-8",
    )
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "name": "repository_recommend",
                        "status": "ok",
                        "payload": {
                            "case_id": "C01",
                            "repositories": ["a"],
                        },
                    }
                ),
                # bash output text is not trusted → C02 scores empty.
                json.dumps(
                    {
                        "name": "bash",
                        "status": "ok",
                        "payload": {"case_id": "C02", "output": "x"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.txt"

    rc = main(
        [
            "--cases",
            str(cases),
            "--from-trace",
            str(trace),
            "--report",
            str(report),
            "--overall-only",
        ]
    )
    assert rc == 0
    text = report.read_text(encoding="utf-8")
    # C01 scores 1.0 from its recommendation trace event; C02 has no trusted
    # prediction → recall 0.4 (direct miss, empty tiers count 1.0) → overall.
    assert "Recall: 0.700" in text
    assert "Precision: 0.500" in text
    assert "F1: 0.500" in text
    # stdout stays clean when --report is used.
    assert "Recall" not in capsys.readouterr().out


def test_from_trace_accepts_json_list_shape(tmp_path: Path) -> None:
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            [{"id": "C01", "ground_truth": {"direct": ["a"], "propagated": [], "context": []}}]
        ),
        encoding="utf-8",
    )
    trace = tmp_path / "trace.json"
    trace.write_text(
        json.dumps(
            [
                {
                    "name": "recommend_repos",
                    "status": "ok",
                    "payload": {"case_id": "C01", "repos": ["a"]},
                }
            ]
        ),
        encoding="utf-8",
    )
    rc = main(["--cases", str(cases), "--from-trace", str(trace), "--overall-only"])
    assert rc == 0


def test_from_trace_conflicts_with_predictions(tmp_path: Path) -> None:
    cases = tmp_path / "cases.json"
    cases.write_text("[]", encoding="utf-8")
    trace = tmp_path / "trace.jsonl"
    trace.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(
            [
                "--cases",
                str(cases),
                "--from-trace",
                str(trace),
                "--predictions",
                str(tmp_path / "p.json"),
            ]
        )
