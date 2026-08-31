# Skill Evaluation And Promotion Specification

Status: accepted for implementation

Evaluation records dataset id/version, task completion rate, test pass rate, human rework rate,
token cost, duration, and tool/MCP error rate. A version may enter canary only when completion and
test thresholds pass and error/rework thresholds remain below limits. Promotion and rollback lock
the Skill release rows and update only Registry state; evaluations run outside that transaction.

Default P0 gate: completion >= 0.90, tests >= 0.90, rework <= 0.20, tool errors <= 0.10. Thresholds
are recorded with the evaluation so later policy changes do not rewrite history.
