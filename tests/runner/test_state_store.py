"""The local at-most-once ledger."""

import json
from pathlib import Path

from repomesh_runner.state_store import TaskLedger


def test_a_fresh_ledger_has_seen_nothing(tmp_path: Path) -> None:
    ledger = TaskLedger(tmp_path)

    assert ledger.seen("run-1") is False


def test_a_recorded_key_is_seen(tmp_path: Path) -> None:
    ledger = TaskLedger(tmp_path)

    ledger.record("run-1", "succeeded")

    assert ledger.seen("run-1") is True
    assert ledger.seen("run-2") is False


def test_records_survive_a_restart(tmp_path: Path) -> None:
    TaskLedger(tmp_path).record("run-1", "failed")

    assert TaskLedger(tmp_path).seen("run-1") is True


def test_the_state_directory_is_created_on_demand(tmp_path: Path) -> None:
    ledger = TaskLedger(tmp_path / "nested" / "state")

    ledger.record("run-1", "succeeded")

    assert ledger.path.is_file()


def test_the_stored_document_carries_the_terminal_status(tmp_path: Path) -> None:
    ledger = TaskLedger(tmp_path)

    ledger.record("run-1", "interrupted")

    document = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert document["tasks"] == {"run-1": "interrupted"}
    assert document["version"] == 1


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    ledger = TaskLedger(tmp_path)

    ledger.record("run-1", "succeeded")
    ledger.record("run-2", "failed")

    assert sorted(path.name for path in tmp_path.iterdir()) == ["task-ledger.json"]


def test_a_corrupt_file_starts_empty_rather_than_crashing(tmp_path: Path) -> None:
    (tmp_path / "task-ledger.json").write_text("{not json", encoding="utf-8")

    ledger = TaskLedger(tmp_path)

    assert ledger.seen("run-1") is False


def test_a_file_with_the_wrong_shape_starts_empty(tmp_path: Path) -> None:
    (tmp_path / "task-ledger.json").write_text(json.dumps({"tasks": ["run-1"]}), encoding="utf-8")

    ledger = TaskLedger(tmp_path)

    assert ledger.seen("run-1") is False


def test_recovery_rewrites_a_usable_ledger(tmp_path: Path) -> None:
    (tmp_path / "task-ledger.json").write_text("garbage", encoding="utf-8")
    ledger = TaskLedger(tmp_path)

    ledger.record("run-1", "succeeded")

    assert TaskLedger(tmp_path).seen("run-1") is True


def test_a_missing_state_directory_is_not_an_error_until_a_write(tmp_path: Path) -> None:
    ledger = TaskLedger(tmp_path / "absent")

    assert ledger.seen("run-1") is False
    assert not (tmp_path / "absent").exists()
