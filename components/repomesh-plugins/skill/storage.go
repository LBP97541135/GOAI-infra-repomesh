package skill

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Store struct {
	Pool *pgxpool.Pool
}

const schemaSQL = `
CREATE SCHEMA IF NOT EXISTS capability_management;

CREATE TABLE IF NOT EXISTS capability_management.skills (
	id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	name TEXT NOT NULL UNIQUE,
	scenario TEXT NOT NULL,
	target_agent_role TEXT NOT NULL,
	created_by TEXT NOT NULL DEFAULT '',
	created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS capability_management.skill_versions (
	id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	skill_id UUID NOT NULL REFERENCES capability_management.skills(id),
	version TEXT NOT NULL,
	status TEXT NOT NULL DEFAULT 'draft',
	content TEXT NOT NULL,
	content_hash TEXT NOT NULL DEFAULT '',
	created_by TEXT NOT NULL DEFAULT '',
	created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	UNIQUE (skill_id, version)
);
CREATE INDEX IF NOT EXISTS idx_skill_versions_status
	ON capability_management.skill_versions (status);

CREATE TABLE IF NOT EXISTS capability_management.skill_test_questions (
	id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	skill_id UUID NOT NULL REFERENCES capability_management.skills(id),
	kind TEXT NOT NULL,
	question TEXT NOT NULL,
	expected JSONB NOT NULL DEFAULT '{}'::jsonb,
	provided_by TEXT NOT NULL,
	created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS capability_management.skill_evaluation_runs (
	id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	version_id UUID NOT NULL REFERENCES capability_management.skill_versions(id),
	question_id UUID NOT NULL REFERENCES capability_management.skill_test_questions(id),
	arm TEXT NOT NULL,
	blinded_label TEXT NOT NULL,
	answer JSONB NOT NULL DEFAULT '{}'::jsonb,
	judged_by TEXT,
	result TEXT NOT NULL,
	run_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_version
	ON capability_management.skill_evaluation_runs (version_id);

CREATE TABLE IF NOT EXISTS capability_management.skill_approvals (
	id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	version_id UUID NOT NULL UNIQUE REFERENCES capability_management.skill_versions(id),
	subject_role TEXT NOT NULL,
	reviewer_kind TEXT NOT NULL,
	reviewer_user_id TEXT,
	review_status TEXT NOT NULL DEFAULT 'pending',
	reviewed_at TIMESTAMPTZ,
	review_note TEXT,
	recused BOOLEAN NOT NULL DEFAULT FALSE,
	conclusion TEXT,
	decided_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS capability_management.agent_skill_bindings (
	id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	agent_id UUID NOT NULL,
	version_id UUID NOT NULL REFERENCES capability_management.skill_versions(id),
	source TEXT NOT NULL,
	bound_at TIMESTAMPTZ NOT NULL DEFAULT now(),
	active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_bindings_agent_active
	ON capability_management.agent_skill_bindings (agent_id) WHERE active;

CREATE TABLE IF NOT EXISTS capability_management.skill_update_suggestions (
	id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	task_id UUID,
	skill_id UUID NOT NULL REFERENCES capability_management.skills(id),
	suggestion TEXT NOT NULL,
	status TEXT NOT NULL DEFAULT 'pending',
	decided_by TEXT,
	decided_at TIMESTAMPTZ,
	created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS capability_management.mcp_server_policies (
	id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	server_name TEXT NOT NULL UNIQUE,
	timeout_seconds INT NOT NULL DEFAULT 30,
	max_retries INT NOT NULL DEFAULT 0,
	retryable_only_reads BOOLEAN NOT NULL DEFAULT TRUE,
	degraded_block_writes BOOLEAN NOT NULL DEFAULT TRUE,
	required_task_features JSONB NOT NULL DEFAULT '[]'::jsonb
);
`

func (s *Store) EnsureSchema(ctx context.Context) error {
	_, err := s.Pool.Exec(ctx, schemaSQL)
	return err
}

type Skill struct {
	ID              string
	Name            string
	Scenario        string
	TargetAgentRole string
	CreatedBy       string
	CreatedAt       time.Time
}

type SkillVersion struct {
	ID          string
	SkillID     string
	Version     string
	Status      Status
	Content     string
	ContentHash string
	CreatedBy   string
	CreatedAt   time.Time
	UpdatedAt   time.Time
}

type TestQuestion struct {
	ID         string
	SkillID    string
	Kind       QuestionKind
	Question   string
	Expected   map[string]any
	ProvidedBy string
	CreatedAt  time.Time
}

type EvalRun struct {
	ID           string
	VersionID    string
	QuestionID   string
	Arm          string
	BlindedLabel string
	Answer       map[string]any
	JudgedBy     *string
	Result       string
	RunAt        time.Time
}

type McpPolicy struct {
	ID                  string
	ServerName          string
	TimeoutSeconds      int
	MaxRetries          int
	RetryableOnlyReads  bool
	DegradedBlockWrites bool
	RequiredTaskFeatures []string
}

var semverRe = regexp.MustCompile(`^\d+\.\d+\.\d+$`)

func ValidSemver(v string) bool { return semverRe.MatchString(v) }

func ContentHash(content string) string {
	sum := sha256.Sum256([]byte(content))
	return "sha256:" + hex.EncodeToString(sum[:])
}

func (s *Store) RegisterSkill(ctx context.Context, name, scenario, targetRole, createdBy string) (*Skill, error) {
	row := s.Pool.QueryRow(ctx, `
		INSERT INTO capability_management.skills (name, scenario, target_agent_role, created_by)
		VALUES ($1, $2, $3, $4)
		ON CONFLICT (name) DO UPDATE SET scenario = EXCLUDED.scenario, target_agent_role = EXCLUDED.target_agent_role
		RETURNING id, name, scenario, target_agent_role, created_by, created_at`,
		name, scenario, targetRole, createdBy)
	sk := &Skill{}
	if err := row.Scan(&sk.ID, &sk.Name, &sk.Scenario, &sk.TargetAgentRole, &sk.CreatedBy, &sk.CreatedAt); err != nil {
		return nil, err
	}
	return sk, nil
}

func (s *Store) GetSkillByName(ctx context.Context, name string) (*Skill, error) {
	row := s.Pool.QueryRow(ctx,
		`SELECT id, name, scenario, target_agent_role, created_by, created_at
		 FROM capability_management.skills WHERE name = $1`, name)
	sk := &Skill{}
	if err := row.Scan(&sk.ID, &sk.Name, &sk.Scenario, &sk.TargetAgentRole, &sk.CreatedBy, &sk.CreatedAt); err != nil {
		return nil, err
	}
	return sk, nil
}

func (s *Store) ListSkills(ctx context.Context) ([]Skill, error) {
	rows, err := s.Pool.Query(ctx,
		`SELECT id, name, scenario, target_agent_role, created_by, created_at
		 FROM capability_management.skills ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Skill
	for rows.Next() {
		var sk Skill
		if err := rows.Scan(&sk.ID, &sk.Name, &sk.Scenario, &sk.TargetAgentRole, &sk.CreatedBy, &sk.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, sk)
	}
	return out, rows.Err()
}

func (s *Store) RegisterVersion(ctx context.Context, skillID, version, content, createdBy string) (*SkillVersion, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var dup string
	err = tx.QueryRow(ctx, `
		SELECT id FROM capability_management.skill_versions
		WHERE skill_id = $1 AND version = $2 AND status IN ('draft','evaluating','canary','promoted')`,
		skillID, version).Scan(&dup)
	if err == nil {
		return nil, Refused("skill_version_conflict", "version %s already exists in an active state", version)
	}
	if err != pgx.ErrNoRows {
		return nil, err
	}

	row := tx.QueryRow(ctx, `
		INSERT INTO capability_management.skill_versions (skill_id, version, status, content, content_hash, created_by)
		VALUES ($1, $2, $3, $4, $5, $6)
		RETURNING id, skill_id, version, status, content, content_hash, created_by, created_at, updated_at`,
		skillID, version, string(StatusDraft), content, ContentHash(content), createdBy)
	v := &SkillVersion{}
	if err := row.Scan(&v.ID, &v.SkillID, &v.Version, (*string)(&v.Status), &v.Content, &v.ContentHash, &v.CreatedBy, &v.CreatedAt, &v.UpdatedAt); err != nil {
		return nil, err
	}
	return v, tx.Commit(ctx)
}

func (s *Store) GetVersion(ctx context.Context, id string) (*SkillVersion, error) {
	row := s.Pool.QueryRow(ctx, `
		SELECT id, skill_id, version, status, content, content_hash, created_by, created_at, updated_at
		FROM capability_management.skill_versions WHERE id = $1`, id)
	return scanVersion(row)
}

func (s *Store) ListVersions(ctx context.Context, skillID string) ([]SkillVersion, error) {
	rows, err := s.Pool.Query(ctx, `
		SELECT id, skill_id, version, status, content, content_hash, created_by, created_at, updated_at
		FROM capability_management.skill_versions WHERE skill_id = $1 ORDER BY updated_at DESC`, skillID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []SkillVersion
	for rows.Next() {
		v, err := scanVersion(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *v)
	}
	return out, rows.Err()
}

type rowScanner interface{ Scan(dest ...any) error }

func scanVersion(row rowScanner) (*SkillVersion, error) {
	v := &SkillVersion{}
	err := row.Scan(&v.ID, &v.SkillID, &v.Version, (*string)(&v.Status), &v.Content, &v.ContentHash, &v.CreatedBy, &v.CreatedAt, &v.UpdatedAt)
	if err != nil {
		return nil, err
	}
	return v, nil
}

func (s *Store) Transition(ctx context.Context, id string, to Status) (*SkillVersion, error) {
	tag, err := s.Pool.Exec(ctx, `
		UPDATE capability_management.skill_versions
		SET status = $2, updated_at = now() WHERE id = $1`, id, string(to))
	if err != nil {
		return nil, err
	}
	if tag.RowsAffected() == 0 {
		return nil, fmt.Errorf("skill version %s not found", id)
	}
	return s.GetVersion(ctx, id)
}

func (s *Store) RecordRun(ctx context.Context, versionID, questionID, arm, blindedLabel string,
	answer map[string]any, judgedBy *string, result string) (*EvalRun, error) {

	var status Status
	if err := s.Pool.QueryRow(ctx,
		`SELECT status FROM capability_management.skill_versions WHERE id = $1`, versionID).Scan((*string)(&status)); err != nil {
		return nil, err
	}
	if status != StatusEvaluating && status != StatusCanary {
		return nil, Refused("skill_evaluation_refused",
			"evaluations are only accepted in evaluating or canary state, got %s", status)
	}

	row := s.Pool.QueryRow(ctx, `
		INSERT INTO capability_management.skill_evaluation_runs
			(version_id, question_id, arm, blinded_label, answer, judged_by, result)
		VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
		RETURNING id, version_id, question_id, arm, blinded_label, answer::text, judged_by, result, run_at`,
		versionID, questionID, arm, blindedLabel, mustJSON(answer), judgedBy, result)
	run := &EvalRun{}
	var answerText string
	if err := row.Scan(&run.ID, &run.VersionID, &run.QuestionID, &run.Arm, &run.BlindedLabel, &answerText, &run.JudgedBy, &run.Result, &run.RunAt); err != nil {
		return nil, err
	}
	run.Answer = parseJSON(answerText)

	if status == StatusCanary && result == ResultFail {
		if _, err := s.Transition(ctx, versionID, StatusRolledBack); err != nil {
			return nil, err
		}
	}
	return run, nil
}

func (s *Store) ListRuns(ctx context.Context, versionID string) ([]EvalRun, error) {
	rows, err := s.Pool.Query(ctx, `
		SELECT id, version_id, question_id, arm, blinded_label, answer::text, judged_by, result, run_at
		FROM capability_management.skill_evaluation_runs WHERE version_id = $1 ORDER BY run_at`, versionID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []EvalRun
	for rows.Next() {
		var r EvalRun
		var answerText string
		if err := rows.Scan(&r.ID, &r.VersionID, &r.QuestionID, &r.Arm, &r.BlindedLabel, &answerText, &r.JudgedBy, &r.Result, &r.RunAt); err != nil {
			return nil, err
		}
		r.Answer = parseJSON(answerText)
		out = append(out, r)
	}
	return out, rows.Err()
}

// CleanGate mirrors Python's _require_clean_gate: across the whole history of the
// skill there must be at least one pass and zero fails.
func (s *Store) CleanGateOK(ctx context.Context, skillID string) (bool, error) {
	var pass, fail int
	err := s.Pool.QueryRow(ctx, `
		SELECT
			COALESCE(SUM(CASE WHEN r.result = 'pass' THEN 1 ELSE 0 END), 0),
			COALESCE(SUM(CASE WHEN r.result = 'fail' THEN 1 ELSE 0 END), 0)
		FROM capability_management.skill_evaluation_runs r
		JOIN capability_management.skill_versions v ON v.id = r.version_id
		WHERE v.skill_id = $1`, skillID).Scan(&pass, &fail)
	if err != nil {
		return false, err
	}
	return pass >= 1 && fail == 0, nil
}

// CanaryWindowOK mirrors Python's _require_canary_window_pass: runs recorded after
// the version entered canary need at least one pass and zero fails.
func (s *Store) CanaryWindowOK(ctx context.Context, versionID string) (bool, error) {
	var pass, fail int
	err := s.Pool.QueryRow(ctx, `
		SELECT
			COALESCE(SUM(CASE WHEN r.result = 'pass' THEN 1 ELSE 0 END), 0),
			COALESCE(SUM(CASE WHEN r.result = 'fail' THEN 1 ELSE 0 END), 0)
		FROM capability_management.skill_evaluation_runs r
		WHERE r.version_id = $1 AND r.run_at >= (SELECT updated_at FROM capability_management.skill_versions WHERE id = $1)`,
		versionID).Scan(&pass, &fail)
	if err != nil {
		return false, err
	}
	return pass >= 1 && fail == 0, nil
}

// ResolveCurrent: latest promoted wins; otherwise the newest canary.
func (s *Store) ResolveCurrent(ctx context.Context, skillID string) (*SkillVersion, error) {
	v, err := s.queryOneVersion(ctx, `
		SELECT id, skill_id, version, status, content, content_hash, created_by, created_at, updated_at
		FROM capability_management.skill_versions
		WHERE skill_id = $1 AND status = 'promoted'
		ORDER BY updated_at DESC LIMIT 1`, skillID)
	if err != nil {
		return nil, err
	}
	if v != nil {
		return v, nil
	}
	return s.queryOneVersion(ctx, `
		SELECT id, skill_id, version, status, content, content_hash, created_by, created_at, updated_at
		FROM capability_management.skill_versions
		WHERE skill_id = $1 AND status = 'canary'
		ORDER BY updated_at DESC LIMIT 1`, skillID)
}

func (s *Store) queryOneVersion(ctx context.Context, sql string, args ...any) (*SkillVersion, error) {
	rows, err := s.Pool.Query(ctx, sql, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	if !rows.Next() {
		return nil, rows.Err()
	}
	return scanVersion(rows)
}

func trimLower(s string) string { return strings.ToLower(strings.TrimSpace(s)) }
