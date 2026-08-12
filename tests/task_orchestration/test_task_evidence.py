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


# --------------------------------------------------------------- A-18 -------
# The defect: a task the agent itself said it could not verify rendered as a
# clean success, immediately before the merge approval. The shape below is not
# invented for the test — it is the live row for task 6ba476ab (run d261dbb4),
# key for key, with the prose abridged and its first and last sentences left
# verbatim.

UNVERIFIED_SUMMARY = (
    "Implementation is complete. I could not execute anything to verify it — see below.\n"
    "\n"
    "## Blockers and gaps — read before accepting\n"
    "\n"
    "1. **Nothing was executed.** The sandbox refused every `python` invocation "
    '("requires approval") and `git` as well. I have not run `scripts/run_tests.py` '
    "or the new tests, so \"code compiles / existing tests pass\" is reviewed-by-reading "
    "only, not verified. Please re-run before merging."
)


def _unverified_runner_document() -> str:
    """The live A-18 payload: a completed run that executed nothing.

    Note which keys are absent as well as which are empty — the write-back that
    produced the live row dropped ``testCommand`` and ``artifacts`` entirely,
    so the projection has to survive their absence, not just their nullity.
    """

    return json.dumps(
        {
            "baseSha": BASE,
            "changedFiles": ["src/checkout/tax_calculator.py", "src/checkout/service.py"],
            "commitSha": HEAD,
            "runId": str(RUN_ID),
            "summary": UNVERIFIED_SUMMARY,
            "testResults": [],
            "workspacePath": "C:/ws",
        },
        sort_keys=True,
    )


def test_the_live_unverified_payload_reports_verified_false() -> None:
    view = _task(
        status=TaskStatus.SUCCEEDED, result_summary=_unverified_runner_document()
    ).to_view()

    assert view.evidence is not None
    # Succeeded as a *run*: the commit is real and the files were written.
    assert view.status is TaskStatus.SUCCEEDED
    assert view.evidence.commit_sha == HEAD
    # And verified as *work*: no. Nothing here judges the prose; the empty test
    # results decide it on their own.
    assert view.evidence.verified is False
    assert view.evidence.test_results == ()
    assert view.evidence.test_command is None
    assert view.evidence.artifact_count == 0


def test_the_agents_own_words_survive_verbatim() -> None:
    """No summarising, no truncating, no re-wording — this is the whole point."""

    view = _task(
        status=TaskStatus.SUCCEEDED, result_summary=_unverified_runner_document()
    ).to_view()

    assert view.evidence is not None
    assert view.evidence.summary_text == UNVERIFIED_SUMMARY
    assert "Nothing was executed." in view.evidence.summary_text
    assert "Please re-run before merging." in view.evidence.summary_text


def test_prose_blockers_are_not_mined_into_the_blockers_list() -> None:
    """The live payload declares no blocker list, so the list stays empty.

    The agent wrote its blockers as a markdown section titled by itself. Pulling
    them out means matching on a heading the producer never promised, which
    would report "0 blockers" for the next agent that writes "## Caveats" — a
    fabricated distinction, and the same defect class as A-18. The words are
    still shown; they are shown from ``summary_text``.
    """

    view = _task(
        status=TaskStatus.SUCCEEDED, result_summary=_unverified_runner_document()
    ).to_view()

    assert view.evidence is not None
    assert view.evidence.blockers == ()


def test_declared_blockers_pass_through_verbatim() -> None:
    """When a Runner does declare them structurally, nothing is normalised."""

    declared = [
        "Nothing was executed. Please re-run before merging.",
        "New tests live where scripts/run_tests.py does not discover them.",
    ]
    view = _task(
        status=TaskStatus.SUCCEEDED, result_summary=_runner_document(blockers=declared)
    ).to_view()

    assert view.evidence is not None
    assert view.evidence.blockers == tuple(declared)


def test_a_blockers_field_that_is_not_a_list_of_strings_is_not_blockers() -> None:
    for raw in ("one big string", {"1": "a"}, [{"text": "a"}], ["", "   "], None):
        view = _task(
            status=TaskStatus.SUCCEEDED, result_summary=_runner_document(blockers=raw)
        ).to_view()
        assert view.evidence is not None
        assert view.evidence.blockers == (), raw


def test_executed_and_passing_tests_are_the_only_way_to_be_verified() -> None:
    passing = _task(
        status=TaskStatus.SUCCEEDED,
        result_summary=_runner_document(
            testCommand="pytest", testResults=[{"command": "pytest", "exitCode": 0}]
        ),
    ).to_view()
    assert passing.evidence is not None
    assert passing.evidence.verified is True
    assert passing.evidence.test_command == "pytest"

    failing = _task(
        status=TaskStatus.SUCCEEDED,
        result_summary=_runner_document(
            testResults=[{"command": "pytest", "exitCode": 0}, {"command": "ruff", "exitCode": 1}]
        ),
    ).to_view()
    assert failing.evidence is not None
    # A run that recorded a non-zero exit code did not verify itself, whatever
    # its terminal status says.
    assert failing.evidence.verified is False


def test_an_unreadable_test_entry_is_dropped_rather_than_given_an_exit_code() -> None:
    """An invented ``0`` reads as a pass and an invented ``-1`` as a failure.

    Neither is a fact the Runner reported, so the entry is not evidence.
    """

    view = _task(
        status=TaskStatus.SUCCEEDED,
        result_summary=_runner_document(
            testResults=[
                {"command": "pytest", "exitCode": 0},
                {"command": "ruff"},
                {"exitCode": 0},
                {"command": "mypy", "exitCode": "0"},
                "pytest",
            ]
        ),
    ).to_view()

    assert view.evidence is not None
    assert [result.command for result in view.evidence.test_results] == ["pytest"]


def test_artifacts_are_counted_not_described() -> None:
    """Presence is the whole claim — nothing downstream can fetch one yet."""

    view = _task(
        status=TaskStatus.SUCCEEDED,
        result_summary=_runner_document(
            artifacts=[
                {"kind": "log", "uri": "s3://a", "contentHash": "0" * 64},
                {"kind": "log", "uri": "s3://b", "contentHash": "1" * 64},
            ]
        ),
    ).to_view()

    assert view.evidence is not None
    assert view.evidence.artifact_count == 2


def test_a_test_result_summary_falls_back_across_the_runner_keys() -> None:
    """Kept identical to delivery's old free-text read, which fed the snapshot."""

    view = _task(
        status=TaskStatus.SUCCEEDED,
        result_summary=_runner_document(
            testResults=[
                {"command": "pytest", "exitCode": 1, "stderr": "boom"},
                {"command": "ruff", "exitCode": 1, "stdout": "3 issues"},
                {"command": "mypy", "exitCode": 0, "summary": "ok"},
                {"command": "bare", "exitCode": 0},
            ]
        ),
    ).to_view()

    assert view.evidence is not None
    assert [result.summary for result in view.evidence.test_results] == [
        "boom",
        "3 issues",
        "ok",
        "",
    ]
