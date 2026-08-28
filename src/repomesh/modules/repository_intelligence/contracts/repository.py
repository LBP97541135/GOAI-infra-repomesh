import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RepositorySelected:
    """Published after a human confirms a repository for a project."""

    project_id: UUID
    repository_id: UUID
    classification: str


@dataclass(frozen=True, slots=True)
class IssueIntakeCommand:
    """Contract v0.3 §1: create an issue by materialising its first draft snapshot.

    The intake owns no new entity: an issue *is* a project_id, and creating one
    means persisting the earliest PlanSnapshot (plan_version=1, no execution
    plan). ``title`` is intentionally absent — it derives from the requirement
    text (single source of truth). ``organization_id`` is an optional
    cross-check only (v0.3 §6 S-4): the workspace of record still derives from
    the actor; when the field is present and disagrees with the actor's
    organization the request is rejected instead of silently trusting either.
    """

    requirement_text: str
    created_by_agent_id: UUID
    idempotency_key: str
    organization_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class IssueIntakeReceipt:
    """``created`` is False when the idempotency key replayed an existing issue."""

    project_id: UUID
    created: bool


class CreateIssueIntake(Protocol):
    async def execute(self, command: IssueIntakeCommand) -> IssueIntakeReceipt: ...


# ---------------------------------------------------------------------------
# Discovery chain (contract v0.4)
# ---------------------------------------------------------------------------

#: Version of the ``plan_snapshots.discovery`` block written by this module.
DISCOVERY_SCHEMA_VERSION = 1

#: Step keys inside the block, in chain order. ``approval`` is not a pipeline
#: step but it is voided like one, so it sits in the same sequence.
DISCOVERY_STEPS: tuple[str, ...] = ("analysis", "candidates", "classification", "approval")

#: Pipeline Step 0..3 → the GUI stepper's 1..4 (v0.4 §3.2 / Q14). Both
#: numberings are already published; the mapping is stated rather than one of
#: them being retired, and it is spelled once here.
GUI_STEP_OF: dict[str, int] = {
    "analysis": 1,
    "candidates": 2,
    "classification": 3,
    "approval": 3,
    "plan": 4,
}

_TIERS = ("required", "maybe", "excluded")


def tier_of(status: str) -> str:
    """``ConfirmationResult.status`` (upper) → the GUI's tier (lower).

    The pipeline emits ``"REQUIRED"``/``"MAYBE"``/``"EXCLUDED"`` as bare
    strings and the panel renders lowercase. One conversion, here, so the two
    spellings cannot drift into two vocabularies.
    """

    lowered = str(status).strip().lower()
    return lowered if lowered in _TIERS else "required"


@dataclass(frozen=True, slots=True)
class DiscoveryStepCommand:
    """One write trigger against an issue's discovery chain (v0.4 §4.3).

    ``requirement`` is deliberately absent from every step: the text of record
    is the draft snapshot's ``requirement_text`` (§4.3), and from Step 1 on it
    is the analysis's ``analyzed_requirement``. Accepting it from the browser
    would let a user submit a requirement that differs from the one on screen.
    """

    issue_id: UUID
    created_by_agent_id: UUID
    idempotency_key: str
    #: Step 0 only: answers to the previous run's follow-up questions.
    answers: tuple[tuple[str, str], ...] = ()
    #: Step 0 only: record "continue, ignoring N questions" without re-running.
    force_continue: bool = False
    #: Step 1 only. ``None`` inherits the composition-root default
    #: (``REPOMESH_DISCOVERY_CANDIDATE_LIMIT``); the panel never sends its
    #: own, script callers may. Resolved by the chain service before the
    #: block records it, so the read model always shows the number that
    #: actually ran.
    limit: int | None = None
    entry_point: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryApprovalCommand:
    """The organization leader's decision on a classification (v0.4 §5.2).

    ``adjustments`` and ``decision`` arrive together on purpose: the panel is
    "retier inline, then release", and splitting them would create a persisted
    "changed but not decided" state nobody designed a display for.
    """

    issue_id: UUID
    decided_by_agent_id: UUID
    idempotency_key: str
    decision: str
    reason: str
    evidence_version: str
    adjustments: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveryTaskReceipt:
    """202 for an accepted trigger; ``task_id`` is None on an idempotent replay."""

    task_id: UUID | None
    step: int
    replayed: bool


def classification_fingerprint(classification: dict[str, Any] | None) -> str:
    """``sha256`` over the tiering an approver actually looked at (§5.3).

    Same shape as the materialize gate's scope fingerprint: canonical JSON of
    the sorted facts, hashed. Only the tiering is covered — repository name and
    tier, plus the supplemented names — because that is what the approver is
    approving. Confidence and rationale wobble between runs of the same model
    without changing the decision, and folding them in would 409 an approval
    over prose that moved.
    """

    if classification is None:
        return ""
    pairs = sorted(
        (str(item.get("repository", "")), tier_of(str(item.get("status", ""))))
        for tier in _TIERS
        for item in (classification.get(tier) or ())
    )
    payload = json.dumps(
        {
            "tiers": pairs,
            # The supplemented set, spelled once (``supplements[].repository``)
            # — derived deterministically so the fingerprint stays stable.
            "supplemented_repos": sorted(
                {
                    str(s.get("repository", ""))
                    for s in (classification.get("supplements") or ())
                }
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def effective_tiers(classification: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The tiering in force: the model's call with the approver's edits on top.

    Both are kept in the block — ``required``/``maybe``/``excluded`` stay as
    the model produced them and ``adjustments`` records what a human changed —
    because overwriting the first with the second deletes the only evidence of
    what the model said. Deriving the answer is therefore a read-time job, and
    this is its one implementation: the read model projects it and Step 3 feeds
    it into integration, so the plan is built from the same tiering the panel
    displays.

    ``original_tier`` is **null unless ``adjusted`` is true**. It exists to
    describe a change, and echoing the current tier back when nothing changed
    invites a panel to render "was required, now required". The two nullable
    cases stay distinguishable through ``adjusted``: ``adjusted: false`` with a
    null original means untouched, while ``adjusted: true`` with a null
    original means the approver added a repository the model never tiered.
    """

    if classification is None:
        return []
    rows: dict[str, dict[str, Any]] = {}
    modelled: dict[str, str] = {}
    for tier in _TIERS:
        for item in classification.get(tier) or ():
            name = str(item.get("repository", ""))
            modelled[name] = tier
            rows[name] = {
                "repository": name,
                "tier": tier,
                "adjusted": False,
                "original_tier": None,
            }
    for adjustment in classification.get("adjustments") or ():
        name = str(adjustment.get("repository", ""))
        tier = tier_of(str(adjustment.get("to", "")))
        original = modelled.get(name)
        if name not in rows:
            # An adjustment naming a repository the model never tiered still
            # counts: the approver added it deliberately, and dropping it would
            # silently ignore a human decision.
            rows[name] = {
                "repository": name,
                "tier": tier,
                "adjusted": True,
                "original_tier": None,
            }
            continue
        adjusted = tier != original
        rows[name]["tier"] = tier
        rows[name]["adjusted"] = adjusted
        rows[name]["original_tier"] = original if adjusted else None
    return [rows[name] for name in sorted(rows)]


def discovery_step(discovery: dict[str, Any] | None) -> int:
    """Which of the GUI stepper's four cells the chain is standing in (§3.2).

    First rule that matches wins. §3.2's last two rules both land on cell 4 —
    "approved, waiting for the plan" and "the plan is here" are the same cell
    in a different state — so whether a plan exists is a ``step_state``
    question and is deliberately not a parameter here.
    """

    block = discovery or {}
    analysis = block.get("analysis")
    if not analysis:
        return 1
    # Stopped at the follow-up questions: still cell 1, waiting for answers or
    # for someone to override.
    if not analysis.get("sufficient") and not analysis.get("forced_continue"):
        return 1
    if not block.get("candidates"):
        return 2
    if not block.get("classification"):
        return 3
    if (block.get("approval") or {}).get("state") != "approved":
        return 3
    return 4


def discovery_step_state(
    discovery: dict[str, Any] | None,
    *,
    has_plan: bool,
    running_step: int | None = None,
) -> str:
    """``idle`` / ``running`` / ``failed`` / ``done`` for the current cell.

    A step that failed shows its own error rather than a spinner or a fake
    percentage: the block records the server's own message and the panel shows
    it (§0.1 rule 4).
    """

    step = discovery_step(discovery)
    if running_step is not None and running_step == step:
        return "running"
    block = discovery or {}
    errored = {
        GUI_STEP_OF[name]
        for name in ("analysis", "candidates", "classification")
        if (block.get(name) or {}).get("error")
    }
    if (block.get("plan") or {}).get("error"):
        errored.add(GUI_STEP_OF["plan"])
    if step in errored:
        return "failed"
    if step == 4 and has_plan:
        return "done"
    return "idle"
