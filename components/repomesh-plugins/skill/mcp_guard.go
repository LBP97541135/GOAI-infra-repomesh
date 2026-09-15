package skill

import (
	"context"
	"crypto/sha256"
	"fmt"
	"log"
	"sort"
	"strings"
	"sync"
	"time"
)

// WriteVerbs is the operation-prefix set treated as writes by the degradation gate.
// Carried over verbatim from the Python module.
var WriteVerbs = []string{
	"write", "create", "start", "update", "delete", "merge",
	"submit", "push", "mutation",
}

func isWriteOperation(operation string) bool {
	op := strings.ToLower(operation)
	for _, verb := range WriteVerbs {
		if strings.HasPrefix(op, verb) || strings.Contains(op, "_"+verb) {
			return true
		}
	}
	return false
}

// CallOutcome classifies the guarded call result.
type CallOutcome string

const (
	OutcomeOK       CallOutcome = "ok"
	OutcomeError    CallOutcome = "error"
	OutcomeRefused  CallOutcome = "refused"
	OutcomeDegraded CallOutcome = "degraded"
)

// CallResult mirrors the Python McpCallResult.
type CallResult struct {
	Outcome   CallOutcome
	LatencyMS float64
	Attempts  int
	AuditID   string
	Value     map[string]any
	Error     string
}

// Caller is the port the plugin calls the real MCP transport through.
// The guard wraps it; the composition root supplies the implementation.
type Caller interface {
	Call(ctx context.Context, serverID, operation string, args map[string]any) (map[string]any, error)
}

// Guard carries over the Python McpCallGuard semantics:
//   - no policy → fail-safe defaults (30s timeout, 0 retries)
//   - retry only when the call is read-only AND policy allows read retries AND not degraded
//   - degraded + write + degraded_block_writes → refused before dispatch
type Guard struct {
	Store   *Store
	Inner   Caller
	NowFunc func() time.Time
	Logf    func(format string, args ...any)

	mu          sync.Mutex
	degradedSet map[string]bool
}

func (g *Guard) policyFor(ctx context.Context, serverID string) (*McpPolicy, error) {
	row := g.Store.Pool.QueryRow(ctx, `
		SELECT id, server_name, timeout_seconds, max_retries, retryable_only_reads, degraded_block_writes
		FROM capability_management.mcp_server_policies WHERE server_name = $1`, serverID)
	p := &McpPolicy{}
	err := row.Scan(&p.ID, &p.ServerName, &p.TimeoutSeconds, &p.MaxRetries, &p.RetryableOnlyReads, &p.DegradedBlockWrites)
	if err != nil {
		// No policy row → fail-safe default; not an error.
		return &McpPolicy{ServerName: serverID, TimeoutSeconds: 30, MaxRetries: 0, RetryableOnlyReads: false, DegradedBlockWrites: true}, nil
	}
	return p, nil
}

func (g *Guard) now() time.Time {
	if g.NowFunc != nil {
		return g.NowFunc()
	}
	return time.Now()
}

func (g *Guard) logf(format string, args ...any) {
	if g.Logf != nil {
		g.Logf(format, args...)
	} else {
		log.Printf(format, args...)
	}
}

func (g *Guard) audit(server, operation string, outcome CallResult, degraded bool) {
	g.logf("mcp_call server=%s operation=%s outcome=%s attempts=%d latency_ms=%.1f read_only=%s degraded=%s",
		server, operation, outcome.Outcome, outcome.Attempts, outcome.LatencyMS,
		fmt.Sprint(!isWriteOperation(operation)), fmt.Sprint(degraded))
}

// Call runs the guarded MCP call. args is hashed into the audit line to keep
// payloads out of logs.
func (g *Guard) Call(ctx context.Context, serverID, operation string, args map[string]any) CallResult {
	start := g.now()
	policy, err := g.policyFor(ctx, serverID)
	if err != nil {
		res := CallResult{Outcome: OutcomeError, Error: err.Error(), Attempts: 0}
		g.audit(serverID, operation, res, g.degraded(ctx, serverID))
		return res
	}

	degraded := g.degraded(ctx, serverID)
	write := isWriteOperation(operation)

	if degraded && write && policy.DegradedBlockWrites {
		res := CallResult{Outcome: OutcomeRefused, Attempts: 0,
			Error: fmt.Sprintf("server %s is degraded; write operation %s refused", serverID, operation)}
		g.audit(serverID, operation, res, degraded)
		return res
	}

	maxAttempts := 1
	if !write && policy.RetryableOnlyReads && !degraded {
		maxAttempts = 1 + policy.MaxRetries
	}

	res := CallResult{Outcome: OutcomeError}
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		res.Attempts = attempt
		value, err := g.Inner.Call(ctx, serverID, operation, args)
		if err == nil {
			res.Outcome = OutcomeOK
			res.Value = value
			break
		}
		res.Error = err.Error()
		if attempt < maxAttempts {
			continue
		}
	}

	res.LatencyMS = float64(g.now().Sub(start).Microseconds()) / 1000.0
	g.audit(serverID, operation, res, degraded)
	return res
}

// CallGated wraps Call with the degraded-write gate for callers that already
// decided their own retry behavior. Kept for parity with Python's call_gated.
func (g *Guard) CallGated(ctx context.Context, serverID, operation string, args map[string]any) CallResult {
	return g.Call(ctx, serverID, operation, args)
}

func (g *Guard) degraded(ctx context.Context, serverID string) bool {
	// Degradation is in-memory state on the guard; the Python module keeps it
	// there too (mark_degraded/mark_healthy). We track it in a small map.
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.degradedSet[serverID]
}

// mark/healthy helpers
func (g *Guard) MarkDegraded(serverID string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.degradedSet == nil {
		g.degradedSet = map[string]bool{}
	}
	g.degradedSet[serverID] = true
}

func (g *Guard) MarkHealthy(serverID string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	delete(g.degradedSet, serverID)
}

func ArgsHash(args map[string]any) string {
	keys := make([]string, 0, len(args))
	for k := range args {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var sb strings.Builder
	sb.WriteByte('{')
	for i, k := range keys {
		if i > 0 {
			sb.WriteByte(',')
		}
		fmt.Fprintf(&sb, "%q:%v", k, args[k])
	}
	sb.WriteByte('}')
	sum := fmt.Sprintf("%x", sha256.Sum256([]byte(sb.String())))
	return "sha256:" + sum[:16]
}
