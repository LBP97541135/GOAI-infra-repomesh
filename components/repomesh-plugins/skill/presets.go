package skill

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
)

// Builtin skill and MCP definitions carried over from the Python presets.
// Skills are the 14 role-assigned SKILL.md docs shipped under capabilities/skills/.

type SkillPreset struct {
	ID       string
	Title    string
	Role     string
	Required bool
}

var SkillPresets = []SkillPreset{
	{ID: "project-intake", Title: "项目立项", Role: RoleLeader, Required: true},
	{ID: "cross-repo-planning", Title: "跨仓库规划", Role: RoleLeader, Required: true},
	{ID: "delivery-governance", Title: "交付治理", Role: RoleLeader, Required: true},
	{ID: "repository-spec-authoring", Title: "仓库规格撰写", Role: RoleManager, Required: true},
	{ID: "task-decomposition", Title: "任务拆解", Role: RoleManager, Required: true},
	{ID: "code-review", Title: "代码评审", Role: RoleManager, Required: true},
	{ID: "test-review", Title: "测试评审", Role: RoleManager, Required: true},
	{ID: "worker-dispatch", Title: "任务派发", Role: RoleManager, Required: true},
	{ID: "worker-result-evaluation", Title: "结果验收", Role: RoleManager, Required: true},
	{ID: "task-execution", Title: "任务执行", Role: RoleWorker, Required: true},
	{ID: "self-test", Title: "自测", Role: RoleWorker, Required: true},
	{ID: "blocker-reporting", Title: "阻塞上报", Role: RoleWorker, Required: true},
	{ID: "tdd", Title: "测试驱动开发", Role: RoleWorker, Required: false},
	{ID: "cross-repo-test", Title: "跨仓库联测", Role: RoleWorker, Required: false},
	{ID: "integration-run", Title: "集成运行", Role: RoleWorker, Required: false},
}

type McpPreset struct {
	ID                  string
	Role                string
	TimeoutSeconds      int
	MaxRetries          int
	RetryableOnlyReads  bool
	DegradedBlockWrites bool
	ConditionalOn       string // required task feature; empty = unconditional
}

var McpPresets = []McpPreset{
	{ID: "github", Role: "", TimeoutSeconds: 30, MaxRetries: 2, RetryableOnlyReads: true, DegradedBlockWrites: true},
	{ID: "context7", Role: "", TimeoutSeconds: 20, MaxRetries: 2, RetryableOnlyReads: true, DegradedBlockWrites: true},
	{ID: "playwright", Role: "", TimeoutSeconds: 60, MaxRetries: 1, RetryableOnlyReads: true, DegradedBlockWrites: true, ConditionalOn: "web_e2e"},
	{ID: "repomesh-task-control", Role: "", TimeoutSeconds: 10, MaxRetries: 0, RetryableOnlyReads: false, DegradedBlockWrites: false},
}

// TeamProfile is an additive overlay: default adds nothing, extra profiles
// append extra skills per role (附加式, never remove).
type TeamProfile struct {
	Name   string
	Extras map[string][]string // role -> skill IDs
}

var TeamProfiles = []TeamProfile{
	{Name: "default", Extras: map[string][]string{}},
	{Name: "cross-repo-test-team", Extras: map[string][]string{
		RoleManager: {"cross-repo-test"},
		RoleWorker:  {"integration-run"},
	}},
}

const DefaultTeamProfile = "default"

// Bundle is what an agent gets after assembly.
type Bundle struct {
	Role    string
	Skills  []string
	Servers []string
}

// Assemble is fail-closed: an unknown role or unknown skill ID aborts assembly,
// mirroring the Python PresetCapabilityAssembler raising PermissionError.
func Assemble(role, profile string, taskFeatures []string) (*Bundle, error) {
	switch role {
	case RoleLeader, RoleManager, RoleWorker:
	default:
		return nil, fmt.Errorf("permission refused: unknown role %q", role)
	}
	if profile == "" {
		profile = DefaultTeamProfile
	}

	features := map[string]bool{}
	for _, f := range taskFeatures {
		features[f] = true
	}

	bundle := &Bundle{Role: role}
	skillSet := map[string]bool{}
	for _, preset := range SkillPresets {
		if preset.Role == role {
			skillSet[preset.ID] = true
		}
	}
	for _, tp := range TeamProfiles {
		if tp.Name != profile {
			continue
		}
		for _, sid := range tp.Extras[role] {
			skillSet[sid] = true
		}
	}
	// Validate: every referenced skill must exist in presets.
	known := map[string]bool{}
	for _, preset := range SkillPresets {
		known[preset.ID] = true
	}
	for sid := range skillSet {
		if !known[sid] {
			return nil, fmt.Errorf("permission refused: unknown skill %q", sid)
		}
		bundle.Skills = append(bundle.Skills, sid)
	}

	for _, m := range McpPresets {
		if m.ConditionalOn != "" && !features[m.ConditionalOn] {
			continue
		}
		bundle.Servers = append(bundle.Servers, m.ID)
	}
	return bundle, nil
}

// SeedContentPath is where SKILL.md docs live relative to the process root.
const SeedContentPath = "capabilities/skills"

// SeedSkills registers the 14 preset skills and, for each, a 1.0.0 version
// promoted directly ("种子即基线"). Idempotent: re-running never resurrects a
// rolled-back seed and never duplicates rows. Seed failure does not block startup.
func SeedSkills(ctx context.Context, store *Store, createdBy string) error {
	for _, preset := range SkillPresets {
		sk, err := store.RegisterSkill(ctx, preset.ID, preset.Title, preset.Role, createdBy)
		if err != nil {
			return fmt.Errorf("seed skill %s: %w", preset.ID, err)
		}
		content := seedContent(preset.ID)
		_, err = store.registerSeedVersion(ctx, sk.ID, "1.0.0", content, createdBy)
		if err != nil {
			return fmt.Errorf("seed version for %s: %w", preset.ID, err)
		}
	}
	return nil
}

// SeedMcpPolicies upserts the four default policies. Idempotent: an existing
// row short-circuits. Callers must tolerate errors without blocking startup.
func SeedMcpPolicies(ctx context.Context, store *Store) error {
	for _, m := range McpPresets {
		features := "[]"
		if m.ConditionalOn != "" {
			features = fmt.Sprintf("%q", m.ConditionalOn)
		}
		_, err := store.Pool.Exec(ctx, `
			INSERT INTO capability_management.mcp_server_policies
				(server_name, timeout_seconds, max_retries, retryable_only_reads, degraded_block_writes, required_task_features)
			VALUES ($1, $2, $3, $4, $5, $6::jsonb)
			ON CONFLICT (server_name) DO NOTHING`,
			m.ID, m.TimeoutSeconds, m.MaxRetries, m.RetryableOnlyReads, m.DegradedBlockWrites, features)
		if err != nil {
			return fmt.Errorf("seed mcp policy %s: %w", m.ID, err)
		}
	}
	return nil
}

func seedContent(skillID string) string {
	content, err := os.ReadFile(filepath.Join(SeedContentPath, skillID, "SKILL.md"))
	if err != nil || len(content) == 0 {
		return fmt.Sprintf("# %s\n\nSeeded placeholder; replace with the real SKILL.md.\n", skillID)
	}
	return string(content)
}

// registerSeedVersion inserts version 1.0.0 directly as promoted, bypassing
// evaluation. If (skill_id, "1.0.0") exists in any state, it short-circuits —
// it must never overwrite a rolled-back seed.
func (s *Store) registerSeedVersion(ctx context.Context, skillID, version, content, createdBy string) (*SkillVersion, error) {
	var exists bool
	err := s.Pool.QueryRow(ctx,
		`SELECT EXISTS(SELECT 1 FROM capability_management.skill_versions WHERE skill_id = $1 AND version = $2)`,
		skillID, version).Scan(&exists)
	if err != nil {
		return nil, err
	}
	if exists {
		return nil, nil
	}
	row := s.Pool.QueryRow(ctx, `
		INSERT INTO capability_management.skill_versions (skill_id, version, status, content, content_hash, created_by)
		VALUES ($1, $2, $3, $4, $5, $6)
		RETURNING id, skill_id, version, status, content, content_hash, created_by, created_at, updated_at`,
		skillID, version, string(StatusPromoted), content, ContentHash(content), createdBy)
	return scanVersion(row)
}
