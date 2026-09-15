// Package skill is the Go rewrite of the capability_management plugin:
// skill version lifecycle with gate-kept evaluation, tiered approvals,
// AB blind comparison, MCP call governance, and preset capability assembly.
package skill

import (
	"fmt"
)

// Version status: five-state lifecycle carried over from the Python module.
type Status string

const (
	StatusDraft      Status = "draft"
	StatusEvaluating Status = "evaluating"
	StatusCanary     Status = "canary"
	StatusPromoted   Status = "promoted"
	StatusRolledBack Status = "rolled_back"
)

var activeStatuses = map[Status]bool{
	StatusDraft:      true,
	StatusEvaluating: true,
	StatusCanary:     true,
	StatusPromoted:   true,
}

// Active reports whether the version still participates in duplicate-name checks.
func (s Status) Active() bool { return activeStatuses[s] }

var allowedTransitions = map[Status][]Status{
	StatusDraft:      {StatusEvaluating},
	StatusEvaluating: {StatusCanary},
	StatusCanary:     {StatusPromoted, StatusRolledBack},
	StatusPromoted:   {StatusRolledBack},
	StatusRolledBack: {},
}

// LifecycleRefused maps to HTTP 409 {"code","detail"} at the API layer.
type LifecycleRefused struct {
	Code    string
	Message string
}

func (e *LifecycleRefused) Error() string { return e.Code + ": " + e.Message }

func Refused(code, format string, args ...any) *LifecycleRefused {
	return &LifecycleRefused{Code: code, Message: fmt.Sprintf(format, args...)}
}

func AssertTransition(from, to Status) error {
	for _, next := range allowedTransitions[from] {
		if next == to {
			return nil
		}
	}
	return Refused("skill_transition_refused", "cannot move skill version from %s to %s", from, to)
}

// Evaluation run vocabulary.
const (
	ArmWith    = "with"
	ArmWithout = "without"

	ResultPass = "pass"
	ResultFail = "fail"
)

type QuestionKind string

const (
	QuestionSuccess             QuestionKind = "success"
	QuestionBusinessFailure     QuestionKind = "business_failure"
	QuestionPrerequisiteMissing QuestionKind = "prerequisite_missing"
)

type ReviewerKind string

const (
	ReviewerManager ReviewerKind = "manager"
	ReviewerLeader  ReviewerKind = "leader"
	ReviewerHuman   ReviewerKind = "human"
)

// Agent roles (统一叫法: Leader=总 leader, Manager=仓库 leader).
const (
	RoleLeader  = "leader"
	RoleManager = "manager"
	RoleWorker  = "worker"
)

// ReviewerFor implements the tiered review rule: worker 的 Skill 由 Manager 审,
// manager 的由 Leader 审, leader 的由人工审.
func ReviewerFor(subjectRole string) ReviewerKind {
	switch subjectRole {
	case RoleWorker:
		return ReviewerManager
	case RoleManager:
		return ReviewerLeader
	default:
		return ReviewerHuman
	}
}

// Escalate moves review one level up after a recusal; returns false when already at human level.
func Escalate(r ReviewerKind) (ReviewerKind, bool) {
	switch r {
	case ReviewerManager:
		return ReviewerLeader, true
	case ReviewerLeader:
		return ReviewerHuman, true
	}
	return ReviewerHuman, false
}

// Binding sources for agent_skill_bindings.
const (
	BindingApprovalRelease = "approval_release"
	BindingRevisionAuto    = "revision_auto"
)

// Suggestion statuses for skill_update_suggestions.
const (
	SuggestionPending  = "pending"
	SuggestionAdopted  = "adopted"
	SuggestionRejected = "rejected"
)

// Similarity threshold below which a revision is treated as a major change
// (改动 > 30% 强制重审).
const MinAutoReleaseSimilarity = 0.7
