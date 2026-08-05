# Coding Agent Adapters

Discovery surface for coding CLIs: binary resolution, authentication probes,
native session metadata normalization, feedback rendering, and a preview of the
interactive launch shape.

This package does **not** own unattended execution. Launch shapes here were
transcribed from the upstream reference recorded in `AdapterSpec.source_revision`
and describe interactive invocations; several were measured to fail or silently
no-op when spawned headless. Real runs go through the protocol drivers in
`src/repomesh_runner/drivers`, selected by profile in
`src/repomesh_runner/profiles.py`.

Read `execution_status` on a spec or manifest before treating an adapter as
runnable:

- `unverified` — listed and probeable, launch shape never validated.
- `superseded_by_driver` — a verified Runner driver profile exists and wins.

Process supervision, permissions, workspace cleanup, artifact collection, and
run state remain owned by Agent Runtime and the Runner.
