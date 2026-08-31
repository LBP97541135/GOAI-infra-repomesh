# Skill Registry Specification

Status: P0 implementation complete; live PostgreSQL concurrency acceptance pending

## Goal

Turn static Skill presets into governed, versioned runtime releases while preserving role policy
and reviewed local wrappers.

## Model

- `Skill`: stable identity, title, allowed roles, source.
- `SkillVersion`: immutable semantic version, content hash, wrapper path, lifecycle state.
- `SkillRelease`: one stable release and at most one canary release per Skill.
- `SkillAssignment`: immutable Task/Run allocation to a concrete version and release.

Static presets bootstrap Skill identities and remain the fallback when Registry data is absent.

## Version States

`draft -> evaluating -> canary -> stable -> deprecated`; failure paths are
`evaluating -> rejected` and `canary -> rolled_back`.

## Runtime

Resolution first applies existing role/feature permissions, then selects a Registry version. A
stable hash of `skill_id + task_id` assigns canary traffic, so retries stay on the same version.
Runner task, context manifest, and trace root freeze Skill id/version/release/assignment evidence.

## Safety

- Wrapper paths remain under the configured capability root.
- Version content hash must match materialized wrapper content.
- One stable and one canary release per Skill via partial unique indexes.
- No model/tool call runs inside release transactions.
