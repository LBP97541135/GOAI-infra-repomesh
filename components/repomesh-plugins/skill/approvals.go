package skill

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"time"
)

// CreateApproval opens the tiered-review record for a version. The reviewer kind
// follows the subject role: worker→Manager, manager→Leader, leader→human.
func (s *Store) CreateApproval(ctx context.Context, versionID, subjectRole string) (*Approval, error) {
	if _, err := s.GetVersion(ctx, versionID); err != nil {
		return nil, err
	}
	row := s.Pool.QueryRow(ctx, `
		INSERT INTO capability_management.skill_approvals (version_id, subject_role, reviewer_kind)
		VALUES ($1, $2, $3)
		ON CONFLICT (version_id) DO UPDATE SET subject_role = EXCLUDED.subject_role,
			reviewer_kind = EXCLUDED.reviewer_kind, review_status = 'pending',
			reviewed_at = NULL, review_note = NULL, recused = FALSE, conclusion = NULL, decided_at = NULL
		RETURNING id, version_id, subject_role, reviewer_kind, reviewer_user_id, review_status,
			reviewed_at, review_note, recused, conclusion, decided_at`,
		versionID, subjectRole, string(ReviewerFor(subjectRole)))
	return scanApproval(row)
}

func (s *Store) GetApproval(ctx context.Context, versionID string) (*Approval, error) {
	row := s.Pool.QueryRow(ctx, `
		SELECT id, version_id, subject_role, reviewer_kind, reviewer_user_id, review_status,
			reviewed_at, review_note, recused, conclusion, decided_at
		FROM capability_management.skill_approvals WHERE version_id = $1`, versionID)
	return scanApproval(row)
}

// DecideApproval records the reviewer's verdict. Recusal escalates one level up
// (Manager 回避→Leader, Leader 回避→人工); the row's reviewer_kind is updated.
func (s *Store) DecideApproval(ctx context.Context, versionID string, recused bool,
	note string, conclusion string, reviewerUserID *string) (*Approval, error) {

	ap, err := s.GetApproval(ctx, versionID)
	if err != nil {
		return nil, err
	}
	if ap.ReviewStatus == "approved" || ap.ReviewStatus == "rejected" {
		return nil, Refused("skill_approval_decided", "approval for version %s is already %s", versionID, ap.ReviewStatus)
	}
	if recused {
		next, ok := Escalate(ap.ReviewerKind)
		if !ok {
			// human reviewer recusing: no higher level, treat as rejection
			_, err = s.Pool.Exec(ctx, `
				UPDATE capability_management.skill_approvals
				SET review_status = 'rejected', review_note = $2, recused = TRUE, decided_at = now()
				WHERE version_id = $1`, versionID, note)
			if err != nil {
				return nil, err
			}
			return s.GetApproval(ctx, versionID)
		}
		if _, err := s.Pool.Exec(ctx, `
			UPDATE capability_management.skill_approvals
			SET reviewer_kind = $2, review_status = 'pending', recused = TRUE, review_note = $3
			WHERE version_id = $1`, versionID, string(next), note); err != nil {
			return nil, err
		}
		return s.GetApproval(ctx, versionID)
	}

	status := "approved"
	if conclusion != "" && conclusion != "approve" {
		status = "rejected"
	}
	_, err = s.Pool.Exec(ctx, `
		UPDATE capability_management.skill_approvals
		SET review_status = $2, reviewer_user_id = $3, reviewed_at = now(),
			review_note = $4, conclusion = $5, decided_at = now()
		WHERE version_id = $1`,
		versionID, status, reviewerUserID, note, conclusion)
	if err != nil {
		return nil, err
	}
	return s.GetApproval(ctx, versionID)
}

func scanApproval(row rowScanner) (*Approval, error) {
	ap := &Approval{}
	err := row.Scan(&ap.ID, &ap.VersionID, &ap.SubjectRole, (*string)(&ap.ReviewerKind), &ap.ReviewerUserID,
		&ap.ReviewStatus, &ap.ReviewedAt, &ap.ReviewNote, &ap.Recused, &ap.Conclusion, &ap.DecidedAt)
	if err != nil {
		return nil, err
	}
	return ap, nil
}

type Approval struct {
	ID             string
	VersionID      string
	SubjectRole    string
	ReviewerKind   ReviewerKind
	ReviewerUserID *string
	ReviewStatus   string
	ReviewedAt     *time.Time
	ReviewNote     *string
	Recused        bool
	Conclusion     *string
	DecidedAt      *time.Time
}

// --- AB blind evaluation -------------------------------------------------

// BuildArms creates the two blind labels for one question. Answers are recorded
// against opaque labels ("A"/"B") and only unblinded afterwards, so the judge
// cannot tell which arm used the skill.
func (s *Store) BuildArms(ctx context.Context, questionID string) (string, string, string, string, error) {
	q, err := s.GetQuestion(ctx, questionID)
	if err != nil {
		return "", "", "", "", err
	}
	labelA, err := randomLabel()
	if err != nil {
		return "", "", "", "", err
	}
	labelB, err := randomLabel()
	if err != nil {
		return "", "", "", "", err
	}
	// Randomly decide which label covers the "with skill" arm.
	withLabel, withoutLabel := labelA, labelB
	if labelA[0] == 'B' {
		withLabel, withoutLabel = labelB, labelA
	}
	_ = q
	return withLabel, withoutLabel, labelA, labelB, nil
}

func randomLabel() (string, error) {
	buf := make([]byte, 4)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return "blind-" + hex.EncodeToString(buf), nil
}

func (s *Store) GetQuestion(ctx context.Context, id string) (*TestQuestion, error) {
	row := s.Pool.QueryRow(ctx, `
		SELECT id, skill_id, kind::text, question, expected::text, provided_by, created_at
		FROM capability_management.skill_test_questions WHERE id = $1`, id)
	q := &TestQuestion{}
	var expectedText string
	if err := row.Scan(&q.ID, &q.SkillID, (*string)(&q.Kind), &q.Question, &expectedText, &q.ProvidedBy, &q.CreatedAt); err != nil {
		return nil, err
	}
	q.Expected = parseJSON(expectedText)
	return q, nil
}

func (s *Store) AddQuestion(ctx context.Context, skillID string, kind QuestionKind,
	question string, expected map[string]any, providedBy string) (*TestQuestion, error) {

	switch ReviewerKind(providedBy) {
	case ReviewerManager, ReviewerLeader, ReviewerHuman:
	default:
		return nil, Refused("skill_question_refused", "question provider must be manager, leader or human, got %q", providedBy)
	}
	row := s.Pool.QueryRow(ctx, `
		INSERT INTO capability_management.skill_test_questions (skill_id, kind, question, expected, provided_by)
		VALUES ($1, $2, $3, $4::jsonb, $5)
		RETURNING id, skill_id, kind::text, question, expected::text, provided_by, created_at`,
		skillID, string(kind), question, mustJSON(expected), providedBy)
	q := &TestQuestion{}
	var expectedText string
	if err := row.Scan(&q.ID, &q.SkillID, (*string)(&q.Kind), &q.Question, &expectedText, &q.ProvidedBy, &q.CreatedAt); err != nil {
		return nil, err
	}
	q.Expected = parseJSON(expectedText)
	return q, nil
}

// --- bindings ------------------------------------------------------------

func (s *Store) BindAgent(ctx context.Context, agentID, versionID, source string) error {
	_, err := s.Pool.Exec(ctx, `
		INSERT INTO capability_management.agent_skill_bindings (agent_id, version_id, source, active)
		VALUES ($1, $2, $3, TRUE)`, agentID, versionID, source)
	return err
}

func (s *Store) UnbindAgent(ctx context.Context, bindingID string) error {
	_, err := s.Pool.Exec(ctx,
		`UPDATE capability_management.agent_skill_bindings SET active = FALSE WHERE id = $1`, bindingID)
	return err
}

func (s *Store) ActiveBindings(ctx context.Context, agentID string) ([]map[string]any, error) {
	rows, err := s.Pool.Query(ctx, `
		SELECT b.id, b.version_id, v.version, v.content_hash, b.source, b.bound_at
		FROM capability_management.agent_skill_bindings b
		JOIN capability_management.skill_versions v ON v.id = b.version_id
		WHERE b.agent_id = $1 AND b.active`, agentID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []map[string]any
	for rows.Next() {
		var id, versionID, version, contentHash, source string
		var boundAt time.Time
		if err := rows.Scan(&id, &versionID, &version, &contentHash, &source, &boundAt); err != nil {
			return nil, err
		}
		out = append(out, map[string]any{
			"id": id, "version_id": versionID, "version": version,
			"content_hash": contentHash, "source": source, "bound_at": boundAt,
		})
	}
	return out, rows.Err()
}

// --- update suggestions (task-end 沉淀) -----------------------------------

func (s *Store) AddSuggestion(ctx context.Context, taskID *string, skillID, suggestion string) (map[string]any, error) {
	row := s.Pool.QueryRow(ctx, `
		INSERT INTO capability_management.skill_update_suggestions (task_id, skill_id, suggestion)
		VALUES ($1, $2, $3)
		RETURNING id, status, created_at`, taskID, skillID, suggestion)
	var id, status string
	var createdAt time.Time
	if err := row.Scan(&id, &status, &createdAt); err != nil {
		return nil, err
	}
	return map[string]any{"id": id, "status": status, "created_at": createdAt}, nil
}

func (s *Store) DecideSuggestion(ctx context.Context, id, decision, decidedBy string) error {
	switch decision {
	case SuggestionAdopted, SuggestionRejected:
	default:
		return Refused("skill_suggestion_refused", "decision must be adopted or rejected, got %q", decision)
	}
	tag, err := s.Pool.Exec(ctx, `
		UPDATE capability_management.skill_update_suggestions
		SET status = $2, decided_by = $3, decided_at = now() WHERE id = $1 AND status = 'pending'`, id, decision, decidedBy)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return Refused("skill_suggestion_refused", "suggestion %s is not pending", id)
	}
	return nil
}

func (s *Store) getSkillByID(ctx context.Context, id string) (*Skill, error) {
	row := s.Pool.QueryRow(ctx,
		`SELECT id, name, scenario, target_agent_role, created_by, created_at
		 FROM capability_management.skills WHERE id = $1`, id)
	sk := &Skill{}
	if err := row.Scan(&sk.ID, &sk.Name, &sk.Scenario, &sk.TargetAgentRole, &sk.CreatedBy, &sk.CreatedAt); err != nil {
		return nil, fmt.Errorf("skill %s not found: %w", id, err)
	}
	return sk, nil
}
