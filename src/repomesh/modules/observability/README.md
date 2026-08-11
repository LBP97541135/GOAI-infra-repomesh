# Observability

Owns immutable audit records, trace hierarchy, metrics, model/tool usage, costs, and the delivery
timeline. It consumes events and must not become an alternate write path into business modules.

The module is currently planned. Existing OpenTelemetry instrumentation and Runner event emission
are platform producers, not an observability source of truth. Their durable projection and query
API belong here when the module is implemented.
