"""The discovery chain's pure derivations (contract v0.4 §2.2, §3.2, §5.3).

These four functions are the only implementations of "which step is this on",
"what tiering is in force" and "what was approved". The read model projects
them and the write path feeds the same values into integration, so a mistake
here is a mistake in both directions at once — which is exactly why they are
tested apart from HTTP.

§3.2 lists seven ordered rules and the panel renders eleven states off them.
Each rule below is exercised at the point it becomes the *first* one to match,
because "first match wins" is the part that can silently regress: a rule that
is right in isolation can still be shadowed by the one before it.
"""

from __future__ import annotations

from repomesh.modules.repository_intelligence.application.dependency_graph import GraphEdge
from repomesh.modules.repository_intelligence.application.discovery_chain import (
    _supplement_candidates,
)
from repomesh.modules.repository_intelligence.contracts import (
    classification_fingerprint,
    discovery_step,
    discovery_step_state,
    effective_tiers,
    tier_of,
)


def _classification(**kwargs) -> dict:
    block = {"required": [], "maybe": [], "excluded": []}
    block.update(kwargs)
    return block


PASSED = {"sufficient": True, "questions": []}
STALLED = {"sufficient": False, "questions": ["改成什么行为？"]}
OVERRIDDEN = {
    "sufficient": False,
    "questions": ["改成什么行为？"],
    "forced_continue": {"ignored_question_count": 1},
}


class TestStepRules:
    """§3.2's seven rules, in order, each at the point it first matches."""

    def test_rule_1_nothing_yet_is_cell_1(self):
        assert discovery_step(None) == 1
        assert discovery_step({}) == 1
        assert discovery_step({"analysis": None}) == 1

    def test_rule_2_an_analysis_that_did_not_pass_stays_in_cell_1(self):
        # The distinction rule 2 exists for: analysis *ran*, so rule 1 no
        # longer applies, but there are unanswered questions.
        assert discovery_step({"analysis": STALLED}) == 1

    def test_rule_2_does_not_hold_once_the_questions_are_overridden(self):
        assert discovery_step({"analysis": OVERRIDDEN}) == 2

    def test_rule_3_a_passed_analysis_without_candidates_is_cell_2(self):
        assert discovery_step({"analysis": PASSED}) == 2
        assert discovery_step({"analysis": PASSED, "candidates": None}) == 2

    def test_rule_4_candidates_without_a_classification_is_cell_3(self):
        block = {"analysis": PASSED, "candidates": {"items": [{"repository_name": "a"}]}}
        assert discovery_step(block) == 3

    def test_rule_5_an_unapproved_classification_stays_in_cell_3(self):
        block = {
            "analysis": PASSED,
            "candidates": {"items": [{"repository_name": "a"}]},
            "classification": _classification(),
        }
        assert discovery_step(block) == 3
        # Both non-approved states are cell 3: waiting, and sent back.
        assert discovery_step({**block, "approval": {"state": "not_requested"}}) == 3
        assert discovery_step({**block, "approval": {"state": "changes_requested"}}) == 3

    def test_rules_6_and_7_are_both_cell_4(self):
        """Approved: cell 4 whether or not the plan has been generated yet.

        The two rules differ in ``step_state``, not in the cell — which is why
        ``discovery_step`` takes no "has a plan" argument at all.
        """

        block = {
            "analysis": PASSED,
            "candidates": {"items": [{"repository_name": "a"}]},
            "classification": _classification(),
            "approval": {"state": "approved"},
        }
        assert discovery_step(block) == 4
        assert discovery_step_state(block, has_plan=False) == "idle"
        assert discovery_step_state(block, has_plan=True) == "done"


class TestStepState:
    def test_a_step_in_flight_reports_running(self):
        block = {"analysis": PASSED}
        assert discovery_step_state(block, has_plan=False, running_step=2) == "running"

    def test_a_task_on_another_step_does_not_colour_this_one(self):
        """Running is about *this* cell, not about the issue being busy."""

        block = {"analysis": PASSED}  # cell 2
        assert discovery_step_state(block, has_plan=False, running_step=1) == "idle"

    def test_an_error_on_the_current_step_reports_failed(self):
        block = {"analysis": {**STALLED, "error": {"message": "429"}}}
        assert discovery_step(block) == 1
        assert discovery_step_state(block, has_plan=False) == "failed"

    def test_running_wins_over_a_previous_error_on_the_same_step(self):
        """A retry in flight is running, not still failed — the old error is
        about the attempt that ended, and showing it beside a spinner reads as
        a live failure."""

        block = {"analysis": {**STALLED, "error": {"message": "429"}}}
        assert discovery_step_state(block, has_plan=False, running_step=1) == "running"

    def test_an_error_on_a_finished_step_does_not_colour_a_later_one(self):
        block = {
            "analysis": PASSED,
            "candidates": {"items": [], "error": None},
        }
        assert discovery_step_state(block, has_plan=False) == "idle"


class TestEffectiveTiers:
    def test_untouched_rows_report_no_original(self):
        """``original_tier`` describes a change; without one it is null.

        Echoing the current tier back invites a panel to render "was required,
        now required".
        """

        rows = effective_tiers(
            _classification(required=[{"repository": "a", "status": "REQUIRED"}])
        )
        assert rows == [
            {
                "repository": "a",
                "tier": "required",
                "adjusted": False,
                "original_tier": None,
            }
        ]

    def test_an_adjustment_keeps_both_opinions(self):
        rows = effective_tiers(
            _classification(
                maybe=[{"repository": "a", "status": "MAYBE"}],
                adjustments=[{"repository": "a", "from": "MAYBE", "to": "REQUIRED"}],
            )
        )
        assert rows == [
            {
                "repository": "a",
                "tier": "required",
                "adjusted": True,
                "original_tier": "maybe",
            }
        ]

    def test_a_repository_the_approver_added_is_adjusted_with_no_original(self):
        """Distinguishable from an untouched row by ``adjusted``, which is why
        a null original can safely mean two things."""

        rows = effective_tiers(
            _classification(
                adjustments=[{"repository": "new", "to": "REQUIRED"}],
            )
        )
        assert rows == [
            {
                "repository": "new",
                "tier": "required",
                "adjusted": True,
                "original_tier": None,
            }
        ]

    def test_an_adjustment_back_to_the_original_tier_is_not_an_adjustment(self):
        """Retier and undo leaves no change, so nothing claims one happened."""

        rows = effective_tiers(
            _classification(
                maybe=[{"repository": "a", "status": "MAYBE"}],
                adjustments=[
                    {"repository": "a", "to": "REQUIRED"},
                    {"repository": "a", "to": "MAYBE"},
                ],
            )
        )
        assert rows[0]["tier"] == "maybe"
        assert rows[0]["adjusted"] is False
        assert rows[0]["original_tier"] is None

    def test_no_classification_is_an_empty_list_not_an_error(self):
        assert effective_tiers(None) == []


class TestFingerprint:
    def test_the_same_tiering_in_a_different_order_is_the_same_evidence(self):
        """Otherwise an approval would 409 because two lists were built in a
        different order, which is not a change to anything anyone read."""

        first = _classification(
            required=[
                {"repository": "b", "status": "REQUIRED"},
                {"repository": "a", "status": "REQUIRED"},
            ],
            supplements=[{"repository": "z"}, {"repository": "y"}],
        )
        second = _classification(
            required=[
                {"repository": "a", "status": "REQUIRED"},
                {"repository": "b", "status": "REQUIRED"},
            ],
            supplements=[{"repository": "y"}, {"repository": "z"}],
        )
        assert classification_fingerprint(first) == classification_fingerprint(second)

    def test_moving_one_repository_changes_the_evidence(self):
        before = _classification(required=[{"repository": "a", "status": "REQUIRED"}])
        after = _classification(maybe=[{"repository": "a", "status": "MAYBE"}])
        assert classification_fingerprint(before) != classification_fingerprint(after)

    def test_rewritten_prose_does_not_change_the_evidence(self):
        """A re-run that produces the same tiering with differently worded
        reasons must not invalidate an approval: the decision was about which
        repositories are in, and that has not moved."""

        before = _classification(
            required=[
                {"repository": "a", "status": "REQUIRED", "reason": "涉及通知模板"}
            ]
        )
        after = _classification(
            required=[
                {"repository": "a", "status": "REQUIRED", "reason": "模板逻辑在这里"}
            ]
        )
        assert classification_fingerprint(before) == classification_fingerprint(after)


class TestTierMapping:
    def test_the_pipelines_upper_case_becomes_the_panels_lower_case(self):
        assert tier_of("REQUIRED") == "required"
        assert tier_of("Maybe") == "maybe"
        assert tier_of("excluded") == "excluded"

    def test_an_unrecognised_status_falls_back_to_required(self):
        """The confirmation parser already defaults unknown statuses to
        REQUIRED as its safety choice; dropping the repository here instead
        would quietly remove it from the plan."""

        assert tier_of("") == "required"
        assert tier_of("WEIRD") == "required"


class _StubGraph:
    """Minimal graph double exposing the two queries the supplement uses."""

    def __init__(self, forward=None, reverse=None):
        self._forward = forward or {}
        self._reverse = reverse or {}

    def forward_dependencies(self, name):
        return list(self._forward.get(name, ()))

    def reverse_dependencies(self, name):
        return list(self._reverse.get(name, ()))


class TestSupplementCandidates:
    """The PM's graph pre-supplement (discovery_chain._supplement_candidates).

    Deterministic contract: first-degree neighbours in both directions,
    confirmed edges before declared, candidate-order stability, a hard cap,
    and every entry carrying the edge evidence that brought it in.
    """

    def _edge(self, producer, consumer, confidence="confirmed"):
        return GraphEdge(
            producer=producer,
            consumer=consumer,
            confidence=confidence,
            mechanism="SOURCE",
            match_reason=f"{consumer} 依赖 {producer}",
        )

    def test_first_degree_neighbours_in_both_directions(self):
        """Whom I depend on (forward) and who depends on me (reverse) both
        join the list, each via the candidate whose edge pulled it in."""

        graph = _StubGraph(
            forward={
                "candidate-a": [self._edge("producer-x", "candidate-a")],
            },
            reverse={
                "candidate-a": [self._edge("candidate-a", "consumer-y")],
            },
        )
        supplements = _supplement_candidates(graph, ["candidate-a"], cap=10)
        by_name = {s.repository: s for s in supplements}
        assert set(by_name) == {"producer-x", "consumer-y"}
        assert by_name["producer-x"].via == "candidate-a"
        assert by_name["consumer-y"].via == "candidate-a"

    def test_known_candidates_are_not_re_added(self):
        graph = _StubGraph(
            reverse={
                "candidate-a": [
                    self._edge("candidate-a", "candidate-b"),
                    self._edge("candidate-a", "new-comer"),
                ],
            },
        )
        supplements = _supplement_candidates(graph, ["candidate-a", "candidate-b"], cap=10)
        assert [s.repository for s in supplements] == ["new-comer"]

    def test_confirmed_edges_sort_before_declared(self):
        graph = _StubGraph(
            reverse={
                "candidate-a": [
                    self._edge("candidate-a", "weak-link", confidence="declared"),
                    self._edge("candidate-a", "hard-link", confidence="confirmed"),
                ],
            },
        )
        supplements = _supplement_candidates(graph, ["candidate-a"], cap=10)
        assert [s.repository for s in supplements] == ["hard-link", "weak-link"]
        assert [s.confidence for s in supplements] == ["confirmed", "declared"]

    def test_cap_truncates_after_confidence_sort(self):
        graph = _StubGraph(
            reverse={
                "candidate-a": [
                    self._edge("candidate-a", "one", confidence="declared"),
                    self._edge("candidate-a", "two", confidence="confirmed"),
                    self._edge("candidate-a", "three", confidence="confirmed"),
                ],
            },
        )
        supplements = _supplement_candidates(graph, ["candidate-a"], cap=2)
        assert [s.repository for s in supplements] == ["two", "three"]

    def test_cap_zero_disables_the_supplement(self):
        graph = _StubGraph(
            reverse={"candidate-a": [self._edge("candidate-a", "new-comer")]},
        )
        assert _supplement_candidates(graph, ["candidate-a"], cap=0) == []

    def test_a_repo_reached_via_two_candidates_is_added_once(self):
        graph = _StubGraph(
            reverse={
                "candidate-a": [self._edge("candidate-a", "shared")],
                "candidate-b": [self._edge("candidate-b", "shared")],
            },
        )
        supplements = _supplement_candidates(graph, ["candidate-a", "candidate-b"], cap=10)
        assert [s.repository for s in supplements] == ["shared"]
        # First candidate to reach it wins the `via` (stable, deterministic).
        assert supplements[0].via == "candidate-a"

    def test_no_recursive_cascade(self):
        """A supplemented repo's own neighbours do not cascade — first degree
        only, or a dense cluster floods the confirmation list."""

        graph = _StubGraph(
            reverse={
                "candidate-a": [self._edge("candidate-a", "new-comer")],
                "new-comer": [self._edge("new-comer", "deeper")],
            },
        )
        supplements = _supplement_candidates(graph, ["candidate-a"], cap=10)
        assert [s.repository for s in supplements] == ["new-comer"]

    def test_evidence_fields_are_carried_verbatim(self):
        graph = _StubGraph(
            reverse={
                "candidate-a": [self._edge("candidate-a", "new-comer")],
            },
        )
        supplements = _supplement_candidates(graph, ["candidate-a"], cap=10)
        assert supplements[0].mechanism == "SOURCE"
        assert supplements[0].match_reason == "new-comer 依赖 candidate-a"
        assert supplements[0].confidence == "confirmed"
