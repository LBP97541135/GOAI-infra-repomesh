package skill

import (
	"testing"
)

func TestAssertTransition(t *testing.T) {
	ok := [][2]Status{
		{StatusDraft, StatusEvaluating},
		{StatusEvaluating, StatusCanary},
		{StatusCanary, StatusPromoted},
		{StatusCanary, StatusRolledBack},
		{StatusPromoted, StatusRolledBack},
	}
	for _, pair := range ok {
		if err := AssertTransition(pair[0], pair[1]); err != nil {
			t.Errorf("expected %s -> %s allowed, got %v", pair[0], pair[1], err)
		}
	}
	bad := [][2]Status{
		{StatusDraft, StatusPromoted},
		{StatusDraft, StatusCanary},
		{StatusEvaluating, StatusPromoted},
		{StatusPromoted, StatusCanary},
		{StatusPromoted, StatusEvaluating},
		{StatusRolledBack, StatusDraft},
		{StatusRolledBack, StatusPromoted},
	}
	for _, pair := range bad {
		if err := AssertTransition(pair[0], pair[1]); err == nil {
			t.Errorf("expected %s -> %s refused", pair[0], pair[1])
		}
	}
}

func TestReviewerFor(t *testing.T) {
	cases := map[string]ReviewerKind{
		RoleWorker:  ReviewerManager,
		RoleManager: ReviewerLeader,
		RoleLeader:  ReviewerHuman,
	}
	for role, want := range cases {
		if got := ReviewerFor(role); got != want {
			t.Errorf("ReviewerFor(%q) = %q, want %q", role, got, want)
		}
	}
}

func TestEscalate(t *testing.T) {
	if next, ok := Escalate(ReviewerManager); !ok || next != ReviewerLeader {
		t.Errorf("manager escalation should go to leader, got %q ok=%v", next, ok)
	}
	if next, ok := Escalate(ReviewerLeader); !ok || next != ReviewerHuman {
		t.Errorf("leader escalation should go to human, got %q ok=%v", next, ok)
	}
	if _, ok := Escalate(ReviewerHuman); ok {
		t.Error("human escalation should not be possible")
	}
}

func TestIsWriteOperation(t *testing.T) {
	writes := []string{"create_issue", "push_files", "merge_pr", "update_policy", "start_run", "submit_review", "write_file"}
	for _, op := range writes {
		if !isWriteOperation(op) {
			t.Errorf("%q should be a write", op)
		}
	}
	reads := []string{"get_file", "list_issues", "search_code", "read_tree", "query"}
	for _, op := range reads {
		if isWriteOperation(op) {
			t.Errorf("%q should not be a write", op)
		}
	}
}

func TestTokenSimilarity(t *testing.T) {
	if TokenSimilarity("alpha beta gamma", "alpha beta gamma") != 1 {
		t.Error("identical content should be similarity 1")
	}
	if TokenSimilarity("", "") != 1 {
		t.Error("empty content should be similarity 1")
	}
	sim := TokenSimilarity("alpha beta gamma delta", "alpha beta gamma epsilon")
	if sim <= MinAutoReleaseSimilarity {
		t.Errorf("small revision similarity %v should exceed %v", sim, MinAutoReleaseSimilarity)
	}
	major := TokenSimilarity("alpha beta", "one two three four five six seven eight nine ten")
	if major >= MinAutoReleaseSimilarity {
		t.Errorf("major rewrite similarity %v should be below %v", major, MinAutoReleaseSimilarity)
	}
}

func TestAssemble(t *testing.T) {
	bundle, err := Assemble(RoleManager, "cross-repo-test-team", nil)
	if err != nil {
		t.Fatalf("assemble manager: %v", err)
	}
	found := map[string]bool{}
	for _, s := range bundle.Skills {
		found[s] = true
	}
	for _, want := range []string{"repository-spec-authoring", "task-decomposition", "cross-repo-test"} {
		if !found[want] {
			t.Errorf("manager cross-repo-test-team bundle missing %q", want)
		}
	}

	// playwright requires web_e2e feature
	bundle, err = Assemble(RoleWorker, DefaultTeamProfile, []string{"web_e2e"})
	if err != nil {
		t.Fatalf("assemble worker: %v", err)
	}
	hasPlaywright := false
	for _, s := range bundle.Servers {
		if s == "playwright" {
			hasPlaywright = true
		}
	}
	if !hasPlaywright {
		t.Error("worker bundle with web_e2e should include playwright")
	}

	bundle, err = Assemble(RoleWorker, DefaultTeamProfile, nil)
	if err != nil {
		t.Fatalf("assemble worker no features: %v", err)
	}
	for _, s := range bundle.Servers {
		if s == "playwright" {
			t.Error("playwright should be excluded without web_e2e")
		}
	}

	if _, err := Assemble("stranger", DefaultTeamProfile, nil); err == nil {
		t.Error("unknown role must be refused (fail-closed)")
	}
}

func TestContentHashStable(t *testing.T) {
	h1 := ContentHash("hello")
	h2 := ContentHash("hello")
	if h1 != h2 || len(h1) != 7+64 {
		t.Errorf("content hash unstable or wrong length: %s", h1)
	}
	if ContentHash("hello2") == h1 {
		t.Error("different content must hash differently")
	}
}
