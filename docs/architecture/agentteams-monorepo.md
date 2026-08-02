# AgentTeams Monorepo Integration

## Decision

AgentTeams source is shipped in the RepoMesh repository so one checkout contains the complete
product and can build a unified deployment. This is a source and delivery integration, not an
in-process merge.

- RepoMesh remains the Python business control plane and source of truth for projects, specs,
  tasks, context, validation, delivery, recovery, and audit.
- AgentTeams remains the Go runtime control plane for Manager, Worker, Team, Matrix rooms, shared
  files, gateway credentials, and Worker container lifecycle.
- Communication crosses the existing provider-neutral ports through Controller HTTP and Matrix
  client APIs. No RepoMesh business module imports AgentTeams internal packages.

## Upstream Provenance

| Field | Value |
| --- | --- |
| Upstream | `https://github.com/agentscope-ai/AgentTeams.git` |
| Imported tag | `v1.2.0` |
| Imported commit | `793db242257a569d911b1aa59c1cd554af78511f` |
| Local prefix | `components/agentteams` |
| License | Apache-2.0 |
| Import method | `git subtree --squash` |

The upstream `LICENSE` is preserved at `components/agentteams/LICENSE`. Do not delete or replace
upstream copyright and license notices.

## Updating Upstream

Start from a clean worktree and use an exact reviewed tag or commit:

```bash
git subtree pull \
  --prefix=components/agentteams \
  https://github.com/agentscope-ai/AgentTeams.git \
  <reviewed-tag-or-commit> \
  --squash
```

After an update:

1. Record the exact resolved commit here and in
   `src/repomesh/integrations/agentteams/upstream.toml`.
2. Run AgentTeams Controller tests and RepoMesh adapter contract tests.
3. Re-run the live compatibility test against the built Controller.
4. Review API, CRD, Matrix, storage, credential, and Worker runtime changes before deployment.

Never update from a floating `main` or `latest` reference in a release branch.

## Local Changes

Prefer these extension points in order:

1. RepoMesh Adapter or application service outside the subtree.
2. A new AgentTeams Worker runtime or plugin with a narrow contract.
3. A focused Controller patch only when the public extension points cannot satisfy the requirement.

Local patches inside `components/agentteams` require an issue describing why an upstream adapter,
plugin, or contribution is insufficient. Keep patches small enough to rebase during subtree pulls.

## Build And Run

AgentTeams retains its upstream build system:

```bash
make -C components/agentteams/agentteams-controller test
make -C components/agentteams build
```

The build command creates local images from the checked-in source. The installer uses published
versioned images unless its `AGENTTEAMS_INSTALL_*_IMAGE` overrides are set, so local AgentTeams
source changes must be built and explicitly selected before integration testing.

For a release-image local installation, run the installer from the checked-in source instead of
downloading a floating script:

```bash
bash components/agentteams/install/agentteams-install.sh
```

On Windows PowerShell 7+:

```powershell
& .\components\agentteams\install\agentteams-install.ps1
```

The installer owns AgentTeams containers and its embedded Matrix/MinIO/gateway data. RepoMesh's
root `compose.yaml` owns RepoMesh PostgreSQL and the RepoMesh API image. In full-platform mode, the
API joins AgentTeams' external `agentteams-net` and calls the internal Controller and Matrix ports;
those management ports remain unexposed on the host.

The next deployment milestone is a single versioned Helm release that contains both control planes
while preserving these ownership boundaries.
