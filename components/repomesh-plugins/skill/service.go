package skill

import (
	"context"
)

// Service is the lifecycle facade the API layer talks to. It owns the state
// machine plus both gates, mirroring the Python SkillRegistryService.
type Service struct {
	Store *Store
}

func NewService(store *Store) *Service { return &Service{Store: store} }

func (svc *Service) RegisterVersion(ctx context.Context, skillName, version, content, createdBy string) (*SkillVersion, error) {
	if !ValidSemver(version) {
		return nil, Refused("skill_version_invalid", "version %q is not semantic (MAJOR.MINOR.PATCH)", version)
	}
	sk, err := svc.Store.GetSkillByName(ctx, skillName)
	if err != nil {
		return nil, Refused("skill_not_found", "skill %q is not registered", skillName)
	}
	return svc.Store.RegisterVersion(ctx, sk.ID, version, content, createdBy)
}

func (svc *Service) Transition(ctx context.Context, versionID string, to Status) (*SkillVersion, error) {
	v, err := svc.Store.GetVersion(ctx, versionID)
	if err != nil {
		return nil, err
	}
	if err := AssertTransition(v.Status, to); err != nil {
		return nil, err
	}
	return svc.Store.Transition(ctx, versionID, to)
}

func (svc *Service) StartEvaluation(ctx context.Context, versionID string) (*SkillVersion, error) {
	return svc.Transition(ctx, versionID, StatusEvaluating)
}

// EnterCanary enforces the clean gate: across the skill's whole history there
// must be at least one pass and zero fails.
func (svc *Service) EnterCanary(ctx context.Context, versionID string) (*SkillVersion, error) {
	v, err := svc.Store.GetVersion(ctx, versionID)
	if err != nil {
		return nil, err
	}
	ok, err := svc.Store.CleanGateOK(ctx, v.SkillID)
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, Refused("skill_gate_failed",
			"clean gate not satisfied for skill %s: need at least one pass and zero fails in history", v.SkillID)
	}
	return svc.Transition(ctx, versionID, StatusCanary)
}

// Promote enforces the canary-window gate: runs recorded after the version
// entered canary need at least one pass and zero fails.
func (svc *Service) Promote(ctx context.Context, versionID string) (*SkillVersion, error) {
	ok, err := svc.Store.CanaryWindowOK(ctx, versionID)
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, Refused("skill_gate_failed",
			"canary window gate not satisfied for version %s: need at least one pass and zero fails since canary started", versionID)
	}
	return svc.Transition(ctx, versionID, StatusPromoted)
}

func (svc *Service) Rollback(ctx context.Context, versionID string) (*SkillVersion, error) {
	return svc.Transition(ctx, versionID, StatusRolledBack)
}

func (svc *Service) ResolveCurrent(ctx context.Context, skillName string) (*SkillVersion, error) {
	sk, err := svc.Store.GetSkillByName(ctx, skillName)
	if err != nil {
		return nil, Refused("skill_not_found", "skill %q is not registered", skillName)
	}
	return svc.Store.ResolveCurrent(ctx, sk.ID)
}

// RecordRun delegates to the store, which enforces evaluating/canary-only and
// the canary auto-rollback on fail.
func (svc *Service) RecordRun(ctx context.Context, versionID, questionID, arm, blindedLabel string,
	answer map[string]any, judgedBy *string, result string) (*EvalRun, error) {
	switch result {
	case ResultPass, ResultFail:
	default:
		return nil, Refused("skill_evaluation_refused", "result must be pass or fail, got %q", result)
	}
	switch arm {
	case ArmWith, ArmWithout:
	default:
		return nil, Refused("skill_evaluation_refused", "arm must be with or without, got %q", arm)
	}
	return svc.Store.RecordRun(ctx, versionID, questionID, arm, blindedLabel, answer, judgedBy, result)
}

// ReleaseOrAutoPass is the composition of tiered approval + revision auto-release:
//   - a version with an approved tiered review is bound via approval_release;
//   - a small revision (token similarity >= 0.7 vs the promoted version) skips
//     re-review and binds via revision_auto;
//   - a major revision (>30% changed) must go through full review.
func (svc *Service) ReleaseOrAutoPass(ctx context.Context, versionID string, agentID string) (string, error) {
	v, err := svc.Store.GetVersion(ctx, versionID)
	if err != nil {
		return "", err
	}
	sk, err := svc.Store.getSkillByID(ctx, v.SkillID)
	if err != nil {
		return "", err
	}
	ap, err := svc.Store.GetApproval(ctx, versionID)
	if err != nil || ap.ReviewStatus != "approved" {
		// No approved review yet — check the small-revision auto pass.
		current, _ := svc.Store.ResolveCurrent(ctx, sk.ID)
		if current != nil && TokenSimilarity(current.Content, v.Content) >= MinAutoReleaseSimilarity {
			if err := svc.Store.BindAgent(ctx, agentID, versionID, BindingRevisionAuto); err != nil {
				return "", err
			}
			return BindingRevisionAuto, nil
		}
		return "", Refused("skill_approval_required",
			"version %s needs tiered review (%s) before release", versionID, string(ReviewerFor(sk.TargetAgentRole)))
	}
	if err := svc.Store.BindAgent(ctx, agentID, versionID, BindingApprovalRelease); err != nil {
		return "", err
	}
	return BindingApprovalRelease, nil
}
