"""Deployment-topology parsers — mechanism ④ (DEPLOY) evidence.

Extracts *deployment references* from compose and Kubernetes manifests:
which service a deployment waits for (``services.<name>.depends_on`` in a
compose file), which app a Service fronts (``spec.selector`` in a k8s
manifest).

This is deliberately *service* semantics, not resource semantics: unlike
mechanism ③ (shared resources, matched identifier-to-identifier), a
deployment reference names a *service*, so the graph resolves it through
the service registry exactly like mechanisms ①②
(docs/chenwenhui/仓库扫描链路问题清单-2026-08-25.md §④).

Each parsed file contributes two things:

- ``targets`` — references to other services: DEPLOY evidence with
  ``confidence="declared"`` (a discovery hint, never topology).
- ``identities`` — the service names this repository itself deploys
  (compose service names, k8s workload ``app`` labels, Service
  ``metadata.name``). scan_remote registers them as deploy identities, so
  another repository's ``depends_on``/selector reference that names them
  resolves back to this repository.

Defensive contract, identical to mechanisms ①②③: unparseable content
yields an empty result, never an exception — one bad manifest can never
fail a scan. A ``${...}`` placeholder is not a concrete deployment
reference and is skipped.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

_MECHANISM = "DEPLOY"
_CONFIDENCE = "declared"


@dataclass(frozen=True, slots=True)
class DeployTarget:
    """One deployment reference extracted from a deploy manifest.

    ``name`` is the referenced service name as written (a ``depends_on``
    entry, a Service selector value). scan_remote maps it 1:1 into a
    ``DepEvidence`` with ``mechanism="DEPLOY"`` and
    ``confidence="declared"``; the graph resolves the name to a catalog
    repository through the service registry.
    """

    name: str
    mechanism: str = _MECHANISM
    confidence: str = _CONFIDENCE


@dataclass(frozen=True, slots=True)
class DeployParseResult:
    """What one deploy manifest contributes to the card and the graph.

    ``targets`` become DEPLOY evidence; ``identities`` become the
    repository's deploy aliases in the service registry.
    """

    targets: tuple[DeployTarget, ...] = ()
    identities: tuple[str, ...] = ()


def is_compose_filename(filename: str) -> bool:
    """True for docker-compose / compose manifests (``*.yml``/``*.yaml``)."""

    fname = filename.rsplit("/", 1)[-1].lower()
    return fname.startswith(("docker-compose", "compose")) and fname.endswith(
        (".yml", ".yaml")
    )


def parse_deploy_file(filename: str, content: str) -> DeployParseResult:
    """Parse *content* of a deploy manifest named *filename*.

    Compose manifests dispatch by filename; every other YAML is inspected
    by ``kind`` (Deployment/StatefulSet/DaemonSet/Service) so a
    ``deployment.yaml``, a ``service.yaml`` or a Helm-rendered manifest all
    parse regardless of their directory. Unknown content contributes
    nothing. Never raises.
    """

    if is_compose_filename(filename):
        return _parse_compose(content)
    return _parse_k8s(content)


# ---------------------------------------------------------------------------
# Compose — services.<name>.depends_on
# ---------------------------------------------------------------------------


def _parse_compose(content: str) -> DeployParseResult:
    """Extract DEPLOY targets and service identities from a compose file.

    ``services`` maps a service name to its spec. Every service name is an
    identity this repository declares it deploys; every ``depends_on``
    entry (short list form or long dict form) is a reference to another
    service.
    """

    targets: list[DeployTarget] = []
    identities: list[str] = []
    seen_targets: set[str] = set()
    seen_identities: set[str] = set()
    for document in _safe_load_all(content):
        if not isinstance(document, dict):
            continue
        services = document.get("services")
        if not isinstance(services, dict):
            continue
        for service_name, spec in services.items():
            if isinstance(service_name, str):
                _add_unique(identities, seen_identities, service_name)
            if not isinstance(spec, dict):
                continue
            for dep in _depends_on_names(spec.get("depends_on")):
                _add_unique_target(targets, seen_targets, dep)
    return DeployParseResult(
        targets=tuple(targets),
        identities=tuple(identities),
    )


def _depends_on_names(value: object) -> tuple[str, ...]:
    """``depends_on`` as a list of names or a dict (long syntax) → names.

    ``[payment-service, db]`` and ``{payment-service: {condition: …}}``
    both yield the service names. A ``${...}`` placeholder names no
    concrete service and is skipped.
    """

    names: list[str] = []
    if isinstance(value, list):
        candidates = (item for item in value if isinstance(item, str))
    elif isinstance(value, dict):
        candidates = (name for name in value if isinstance(name, str))
    else:
        return ()
    for candidate in candidates:
        name = candidate.strip()
        if name and "$" not in name:
            names.append(name)
    return tuple(names)


# ---------------------------------------------------------------------------
# Kubernetes — workload labels (identities) + Service selectors (targets)
# ---------------------------------------------------------------------------

#: Workload kinds whose ``app`` labels name the service this repo deploys.
_K8S_WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet")

#: Label/selector keys that name the application a workload or Service
#: belongs to. ``app`` is the legacy convention, ``app.kubernetes.io/name``
#: the current recommended one.
_K8S_APP_LABEL_KEYS = ("app", "app.kubernetes.io/name")


def _parse_k8s(content: str) -> DeployParseResult:
    """Extract deploy identities and selector targets from k8s manifests.

    Deployment-family workloads contribute their ``app`` label as an
    identity (this repo deploys that service). A Service contributes its
    ``metadata.name`` as an identity *and* its ``spec.selector`` app value
    as a target — the Service fronts that app, so the Service's owner
    depends on the app being deployed. Multi-document streams are handled
    by ``safe_load_all``.
    """

    targets: list[DeployTarget] = []
    identities: list[str] = []
    seen_targets: set[str] = set()
    seen_identities: set[str] = set()
    for document in _safe_load_all(content):
        if not isinstance(document, dict):
            continue
        kind = document.get("kind")
        if kind in _K8S_WORKLOAD_KINDS:
            label = _workload_app_label(document)
            if label:
                _add_unique(identities, seen_identities, label)
        elif kind == "Service":
            name = _metadata_name(document)
            if name:
                _add_unique(identities, seen_identities, name)
            selected = _service_selector_app(document)
            if selected:
                _add_unique_target(targets, seen_targets, selected)
    return DeployParseResult(
        targets=tuple(targets),
        identities=tuple(identities),
    )


def _workload_app_label(document: dict) -> str | None:
    """The app name a Deployment/StatefulSet/DaemonSet runs.

    Reads ``spec.template.metadata.labels`` first (the pod labels, which
    is what selectors actually match), then ``metadata.labels``.
    """

    labels = _nested(document, ("spec", "template", "metadata", "labels"))
    if not isinstance(labels, dict):
        labels = _nested(document, ("metadata", "labels"))
    return _label_value(labels)


def _service_selector_app(document: dict) -> str | None:
    """The app a Service routes to, from ``spec.selector``."""

    selector = _nested(document, ("spec", "selector"))
    return _label_value(selector)


def _label_value(labels: object) -> str | None:
    """A concrete app value from a labels/selector mapping, or ``None``."""

    if not isinstance(labels, dict):
        return None
    for key in _K8S_APP_LABEL_KEYS:
        value = labels.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if value and "$" not in value:
            return value
    return None


def _metadata_name(document: dict) -> str | None:
    """``metadata.name`` of a manifest, when concrete."""

    metadata = _nested(document, ("metadata",))
    if not isinstance(metadata, dict):
        return None
    name = metadata.get("name")
    if not isinstance(name, str):
        return None
    name = name.strip()
    if name and "$" not in name:
        return name
    return None


def _nested(document: dict, path: tuple[str, ...]) -> object:
    """Descend a dict path, guarding non-dict nodes along the way."""

    node: object = document
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _safe_load_all(content: str) -> list[dict]:
    """Parse a multi-document YAML stream into dict documents.

    Never executes code (``safe_load_all``) and never raises: a malformed
    manifest yields an empty list so the scan survives it.
    """

    try:
        return [
            document
            for document in yaml.safe_load_all(content)
            if isinstance(document, dict)
        ]
    except Exception:  # noqa: BLE001 — a scan survives any one bad manifest
        return []


def _add_unique(items: list[str], seen: set[str], value: str) -> None:
    """Append *value* case-insensitively deduplicated, first wins."""

    stripped = value.strip()
    if not stripped:
        return
    key = stripped.lower()
    if key in seen:
        return
    seen.add(key)
    items.append(stripped)


def _add_unique_target(
    items: list[DeployTarget], seen: set[str], value: str
) -> None:
    """Append a :class:`DeployTarget` deduplicated, first wins.

    Compose ``depends_on`` entries and Service selector values are service
    *names*; they become DeployTargets so scan_remote can read ``.name``,
    ``.mechanism`` and ``.confidence`` uniformly for both parser paths.
    """

    stripped = value.strip()
    if not stripped:
        return
    key = stripped.lower()
    if key in seen:
        return
    seen.add(key)
    items.append(DeployTarget(name=stripped))
