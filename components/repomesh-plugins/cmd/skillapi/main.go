// Command skillapi serves the skill plugin's HTTP surface, mirroring the
// Python capability-governance API: /api/v1/capability-governance/*.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/agentscope-ai/RepoMesh/repomesh-plugins/skill"
	"github.com/jackc/pgx/v5/pgxpool"
)

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	pool, err := pgxpool.New(ctx, env("DATABASE_URL", "postgres://repomesh:repomesh@localhost:5432/repomesh"))
	if err != nil {
		log.Fatalf("pgxpool: %v", err)
	}
	defer pool.Close()

	store := &skill.Store{Pool: pool}
	if err := store.EnsureSchema(ctx); err != nil {
		log.Fatalf("ensure schema: %v", err)
	}
	// Seeds are idempotent and must not block startup on failure.
	if err := skill.SeedMcpPolicies(ctx, store); err != nil {
		log.Printf("seed mcp policies (non-blocking): %v", err)
	}
	if err := skill.SeedSkills(ctx, store, "system-seed"); err != nil {
		log.Printf("seed skills (non-blocking): %v", err)
	}

	svc := skill.NewService(store)
	mux := http.NewServeMux()
	registerRoutes(mux, svc)

	addr := env("LISTEN_ADDR", ":8102")
	server := &http.Server{Addr: addr, Handler: mux}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
	}()
	log.Printf("skill plugin API listening on %s", addr)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatalf("serve: %v", err)
	}
}

func registerRoutes(mux *http.ServeMux, svc *skill.Service) {
	prefix := "POST /api/v1/capability-governance/"

	mux.HandleFunc("GET /api/v1/capability-governance/skill-versions", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"skills": []any{}})
		_ = svc
	})

	mux.HandleFunc(prefix+"skill-versions", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			SkillName string `json:"skill_id"`
			Version   string `json:"version"`
			Content   string `json:"content"`
			CreatedBy string `json:"created_by"`
		}
		if !decode(w, r, &body) {
			return
		}
		v, err := svc.RegisterVersion(r.Context(), body.SkillName, body.Version, body.Content, body.CreatedBy)
		handle(w, v, err)
	})

	mux.HandleFunc(prefix+"skill-versions/evaluations", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			VersionID    string         `json:"version_id"`
			QuestionID   string         `json:"question_id"`
			Arm          string         `json:"arm"`
			BlindedLabel string         `json:"blinded_label"`
			Answer       map[string]any `json:"answer"`
			JudgedBy     *string        `json:"judged_by"`
			Result       string         `json:"result"`
		}
		if !decode(w, r, &body) {
			return
		}
		run, err := svc.RecordRun(r.Context(), body.VersionID, body.QuestionID, body.Arm,
			body.BlindedLabel, body.Answer, body.JudgedBy, body.Result)
		handle(w, run, err)
	})

	mux.HandleFunc(prefix+"skill-versions/evaluate", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			VersionID string `json:"version_id"`
		}
		if !decode(w, r, &body) {
			return
		}
		v, err := svc.StartEvaluation(r.Context(), body.VersionID)
		handle(w, v, err)
	})

	mux.HandleFunc(prefix+"skill-versions/canary", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			VersionID string `json:"version_id"`
		}
		if !decode(w, r, &body) {
			return
		}
		v, err := svc.EnterCanary(r.Context(), body.VersionID)
		handle(w, v, err)
	})

	mux.HandleFunc(prefix+"skill-versions/promote", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			VersionID string `json:"version_id"`
		}
		if !decode(w, r, &body) {
			return
		}
		v, err := svc.Promote(r.Context(), body.VersionID)
		handle(w, v, err)
	})

	mux.HandleFunc(prefix+"skill-versions/rollback", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			VersionID string `json:"version_id"`
		}
		if !decode(w, r, &body) {
			return
		}
		v, err := svc.Rollback(r.Context(), body.VersionID)
		handle(w, v, err)
	})

	mux.HandleFunc("GET /api/v1/capability-governance/mcp-policies", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"policies": []any{}})
	})

	mux.HandleFunc("POST /api/v1/capability-governance/assemble", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Role         string   `json:"role"`
			Profile      string   `json:"profile"`
			TaskFeatures []string `json:"task_features"`
		}
		if !decode(w, r, &body) {
			return
		}
		bundle, err := skill.Assemble(body.Role, body.Profile, body.TaskFeatures)
		handle(w, bundle, err)
	})
}

func decode(w http.ResponseWriter, r *http.Request, into any) bool {
	if err := json.NewDecoder(r.Body).Decode(into); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"code": "bad_request", "detail": err.Error()})
		return false
	}
	return true
}

func handle(w http.ResponseWriter, v any, err error) {
	if err != nil {
		var refused *skill.LifecycleRefused
		if errors.As(err, &refused) {
			writeJSON(w, http.StatusConflict, map[string]any{"code": refused.Code, "detail": refused.Message})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]any{"code": "internal", "detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, v)
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
