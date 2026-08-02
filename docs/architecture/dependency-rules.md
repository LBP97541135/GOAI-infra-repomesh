# Dependency Rules

Allowed direction inside a module:

```text
api -> application -> domain
infrastructure -> ports <- application
bootstrap -> api + infrastructure + integrations
```

Rules enforced by `tests/architecture/test_module_boundaries.py`:

- Domain code cannot import FastAPI, HTTP clients, settings, database libraries, or integrations.
- A module cannot import another module's domain, application, ports, API, or infrastructure.
- Cross-module Python imports must go through the producer's `contracts` module.
- The top-level API only aggregates module routers; it contains no business behavior.
- Every business module must contain `README.md` and a valid `module.toml`.

Integrations may implement module ports. Modules must not import a concrete integration from
domain or application code. Concrete selection belongs in `repomesh.bootstrap`.
## First-Party Component Boundaries

- RepoMesh modules depend on provider-neutral ports and public module contracts.
- `src/repomesh_runner` depends only on Runtime v1 concepts and Runner-owned ports.
- `components/agentteams` implements Runtime v1 behavior without importing RepoMesh Python code.
- `src/repomesh/integrations/agentteams` is the protocol mapper between product and runtime planes.
- Cross-plane DTOs originate in `contracts/runtime`; component-private models are never shared APIs.
