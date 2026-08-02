# AgentTeams Monorepo Integration

## Decision

AgentTeams is a first-party RepoMesh runtime component. One checkout contains the complete product,
and one release owns its source, images, deployment, and compatibility. It remains a separate Go
control-plane process rather than an in-process Python dependency.

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

Use these ownership rules:

1. Change `contracts/runtime` first for behavior crossing a process boundary.
2. Put coding execution in RepoMesh Runner and product behavior in RepoMesh modules.
3. Reuse an AgentTeams runtime or plugin when it owns the required behavior.
4. Change the Go Controller when desired-state reconciliation, lifecycle, or enforcement belongs
   in the runtime control plane.

Before the first local Controller patch, create a RepoMesh product fork of AgentTeams. Keep the
official repository as upstream, import exact reviewed product-fork commits, and record both fork
and upstream revisions. Keep patches focused enough to review during upstream merges.

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
