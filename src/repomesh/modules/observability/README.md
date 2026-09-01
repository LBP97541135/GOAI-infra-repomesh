# Observability

Owns immutable audit records, trace hierarchy, metrics, model/tool usage, costs, and the delivery
timeline. It consumes events and must not become an alternate write path into business modules.

The module implements durable usage, trace and log projections, query APIs, alert evaluation,
idempotent notification/action responses, bounded telemetry retention, issue correlation, and
operational readiness checks. Deployment-specific notification, backup/restore, and capacity
source adapters remain external infrastructure concerns and are reported as unavailable rather
than inferred.
