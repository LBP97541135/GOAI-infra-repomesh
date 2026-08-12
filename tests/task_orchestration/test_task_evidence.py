"""TaskView.evidence: the producer's own reading of its free-text summary.

``result_summary`` is declared as ``str | None`` and carries three unrelated
shapes — a Runner JSON document, ``SUPERSEDED: ...`` and plain agent prose.
The read model used to json.loads() it, so correctness rested on a
JSONDecodeError handler rather than on any promise. These tests pin the
declared alternative: task orchestration decides what counts as evidence and
publishes a typed view, and everything else is honestly ``None``.
"""

import json
from uuid import UUID, uuid4

from repomesh.modules.task_orchestration.contracts import TaskStatus
from repomesh.modules.task_orchestration.domain import Task

RUN_ID = uuid4()
HEAD = "a" * 40
BASE = "9" * 40


def _task(**overrides) -> Task:
    return Task(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=uuid4(),
        title="Implement the approved scope",
        instruction="Implement it.",
        acceptance=("Tests pass",),
        **overrides,
    )


def _runner_document(**overrides) -> str:
    document = {
        "summary": "runner.completed",
        "changedFiles": ["src/pricing.py", "tests/test_pricing.py"],
        "testResults": [{"command": "pytest", "exitCode": 0}],
        "commitSha": HEAD,
        "runId": str(RUN_ID),
        "workspacePath": "C:/ws",
        "baseSha": BASE,
    }
    document.update(overrides)
    return json.dumps(document, sort_keys=True)


def test_runner_evidence_is_published_as_a_typed_view() -> None:
    view = _task(status=TaskStatus.SUCCEEDED, result_summary=_runner_document()).to_view()

    assert view.evidence is not None
    assert view.evidence.commit_sha == HEAD
    # A run id is a UUID in the contract, not whatever string was written.
    assert view.evidence.run_id == RUN_ID
    assert isinstance(view.evidence.run_id, UUID)
    assert view.evidence.changed_files == ("src/pricing.py", "tests/test_pricing.py")
    assert view.evidence.base_sha == BASE
    # The free-text column is untouched: this is a new bypass, not a rewrite.
    assert view.result_summary == _runner_document()


def test_superseded_and_prose_summaries_carry_no_evidence() -> None:
    superseded = _task(status=TaskStatus.ASSIGNED).supersede(reason="replaced by plan v2")
    assert superseded.result_summary == "SUPERSEDED: replaced by plan v2"
    assert superseded.to_view().evidence is None

    prose = _task(status=TaskStatus.ASSIGNED).report(
        TaskStatus.SUCCEEDED, "Implemented pricing and all tests pass."
    )
    assert prose.to_view().evidence is None

    assert _task().to_view().evidence is None


def test_a_document_without_a_commit_sha_is_not_evidence() -> None:
    """commit_sha is the premise of the view existing, so it is never empty."""

    for summary in (
        _runner_document(commitSha=None),
        _runner_document(commitSha=""),
        json.dumps({"summary": "runner.completed", "runId": str(RUN_ID)}),
    ):
        view = _task(status=TaskStatus.SUCCEEDED, result_summary=summary).to_view()
        assert view.evidence is None, summary


def test_valid_json_that_is_not_an_object_is_not_evidence() -> None:
    for summary in ("[1, 2, 3]", '"just a quoted string"', "42", "null"):
        view = _task(status=TaskStatus.SUCCEEDED, result_summary=summary).to_view()
        assert view.evidence is None, summary


def test_an_unparseable_run_id_nulls_the_run_id_but_keeps_the_evidence() -> None:
    """A run id that is not a UUID is not a run id we can hand out typed.

    Losing the commit sha and changed files too would throw away the evidence
    the contract is actually about, so only the unusable field goes null.
    """

    view = _task(
        status=TaskStatus.SUCCEEDED,
        result_summary=_runner_document(runId="not-a-uuid"),
    ).to_view()

    assert view.evidence is not None
    assert view.evidence.run_id is None
    assert view.evidence.commit_sha == HEAD


def test_a_missing_run_id_is_null_rather_than_invented() -> None:
    view = _task(
        status=TaskStatus.SUCCEEDED,
        result_summary=_runner_document(runId=None),
    ).to_view()

    assert view.evidence is not None
    assert view.evidence.run_id is None


def test_workspace_path_is_declared_so_delivery_need_not_re_parse_the_summary() -> None:
    """Delivery pushes the candidate commit from this worktree.

    It is the one fact delivery could not get from the view, which is why the
    finalizer kept its own json.loads() of a free-text field.
    """

    view = _task(status=TaskStatus.SUCCEEDED, result_summary=_runner_document()).to_view()

    assert view.evidence is not None
    assert view.evidence.workspace_path == "C:/ws"


def test_a_missing_workspace_path_is_null_rather_than_an_empty_path() -> None:
    """An empty string would resolve to the current directory downstream."""

    for summary in (_runner_document(workspacePath=None), _runner_document(workspacePath="")):
        view = _task(status=TaskStatus.SUCCEEDED, result_summary=summary).to_view()
        assert view.evidence is not None
        assert view.evidence.workspace_path is None


def test_changed_files_defaults_to_empty_rather_than_none() -> None:
    for summary in (_runner_document(changedFiles=[]), _runner_document(changedFiles=None)):
        view = _task(status=TaskStatus.SUCCEEDED, result_summary=summary).to_view()
        assert view.evidence is not None
        assert view.evidence.changed_files == ()
