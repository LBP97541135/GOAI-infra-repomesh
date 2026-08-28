"""The approval exchange, pinned to what codex 0.149.1 actually sends and takes.

Every fixture below is copied verbatim from a live capture (RepoMesh evidence
``27-approval-probe-*.json``, 2026-08-28): a real ``codex app-server`` under
``sandbox_mode = "read-only"``, driven through one turn that had to ask.

The capture exists because this exchange had never happened. The driver's
approval answer was written from codex's documented review vocabulary and
covered by fake-process tests, and the configuration it ran under approved
everything locally, so no approval request ever reached it — the words were
wrong for as long as nothing asked. When something finally did, codex answered::

    failed to deserialize CommandExecutionRequestApprovalResponse:
    unknown variant `approved`, expected one of `accept`, `acceptForSession`,
    `acceptWithExecpolicyAmendment`, `applyNetworkPolicyAmendment`, `decline`,
    `cancel`

and told the model the approval had *failed* — so an allow and a deny arrived at
the model as the same outcome, which is the one failure a permission gate may
not have.

These tests are deliberately about the wire and nothing else. Whether the policy
decides well is ``test_executor``'s subject; whether the driver's decision is
spelled in a word codex accepts is this file's, and it is the half a fake peer
cannot check.
"""

import pytest

from repomesh_runner.drivers.app_server import (
    APPROVAL_VERDICTS,
    _approval_tool_name,
    _is_approval_method,
)
from repomesh_runner.drivers.base import PermissionDecision

CAPTURED_COMMAND_APPROVAL = {
    "method": "item/commandExecution/requestApproval",
    "id": 0,
    "params": {
        "threadId": "01a0480f-4c8c-7593-a45e-ad28ddfb8468",
        "turnId": "01a0480f-4d23-75b2-bd8f-0a8031f034f8",
        "itemId": "exec-de8f5827-40e7-475d-9b69-5f1138a04077",
        "startedAtMs": 1787915365189,
        "environmentId": "local",
        "command": (
            '"C:\\\\Windows\\\\System32\\\\WindowsPowerShell\\\\v1.0\\\\powershell.exe" '
            "-Command 'echo PROBE_OK'"
        ),
        "cwd": "D:\\Project4work\\.repomesh-v1-live\\probe-wd",
        "commandActions": [{"type": "unknown", "command": "echo PROBE_OK"}],
        "proposedExecpolicyAmendment": ["echo", "PROBE_OK"],
        "availableDecisions": [
            "accept",
            {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["echo", "PROBE_OK"]}},
            "cancel",
        ],
    },
}
"""One ``item/commandExecution/requestApproval``, exactly as it arrived."""

ACCEPTED_VARIANTS = frozenset(
    {
        "accept",
        "acceptForSession",
        "acceptWithExecpolicyAmendment",
        "applyNetworkPolicyAmendment",
        "decline",
        "cancel",
    }
)
"""codex 0.149.1's own list, quoted from the error it raised at a word not on it."""


def test_every_decision_is_spelled_in_a_word_codex_accepts():
    """The regression, stated as the thing that was untrue.

    Total over the enum on purpose: a decision with no verdict would be a
    ``KeyError`` inside the answer path, which reaches codex as no answer at all.
    """

    assert set(APPROVAL_VERDICTS) == set(PermissionDecision)
    for decision, verdict in APPROVAL_VERDICTS.items():
        assert verdict in ACCEPTED_VARIANTS, f"{decision.value} answers with an unknown variant"


def test_allow_and_deny_are_the_two_words_the_live_capture_confirmed():
    """Not merely *in* the enum — the two that were driven end to end.

    ``accept`` ran the command and its write landed on disk; ``decline`` refused
    it with ``Rejected("rejected by user")``, which is codex's own wording for a
    decision, and is what a deserialization failure is not.
    """

    assert APPROVAL_VERDICTS[PermissionDecision.ALLOW] == "accept"
    assert APPROVAL_VERDICTS[PermissionDecision.DENY] == "decline"


def test_no_decision_is_answered_with_the_words_that_were_refused():
    """The specific mistake, kept nameable so it cannot come back quietly."""

    assert not {"approved", "denied", "abort"} & set(APPROVAL_VERDICTS.values())


def test_the_captured_request_is_recognised_as_an_approval():
    """``_is_approval_method`` is not changed by this fix, and this is why."""

    assert _is_approval_method(CAPTURED_COMMAND_APPROVAL["method"])


def test_the_captured_request_carries_no_item_and_is_read_from_its_params():
    """The shape the answer path has to survive, and it is not the assumed one.

    The request has no ``item`` member at all — the command, the cwd and the
    decisions are top-level params — so a reader that only looked at ``item``
    would see an empty mapping and name the tool after the method. The fallback
    is what makes the tool name right, so it is pinned against the real request
    rather than against a constructed one.
    """

    params = CAPTURED_COMMAND_APPROVAL["params"]
    assert "item" not in params
    named = _approval_tool_name(CAPTURED_COMMAND_APPROVAL["method"], params, {})
    assert named == "commandExecution"


def test_the_request_id_is_an_integer_the_answer_must_echo():
    """codex numbers its requests from zero, so ``id`` is falsy on the first one.

    Worth pinning: an answer path that treated a missing id and ``id == 0`` the
    same would drop the very first approval of every session.
    """

    assert CAPTURED_COMMAND_APPROVAL["id"] == 0
    assert isinstance(CAPTURED_COMMAND_APPROVAL["id"], int)


@pytest.mark.parametrize("decision", list(PermissionDecision))
def test_the_answer_is_a_bare_decision_string(decision):
    """``{"decision": "<variant>"}`` — a string, never a nested object.

    ``availableDecisions`` mixes bare strings with objects
    (``acceptWithExecpolicyAmendment`` carries an amendment), so a future
    reader might reasonably think every answer is an object. The two this
    driver sends are not, and both were accepted as plain strings live.
    """

    assert isinstance(APPROVAL_VERDICTS[decision], str)
