"""Runtime call-declaration parsers — mechanism ② (RUNTIME_CALL) evidence.

Extracts the *target* of a runtime call from framework declarations —
``@FeignClient(name=…)``, ``@DubboReference(interfaceClass=…)``, gRPC
stub factories — replacing the first-generation string guessing
(``_JAVA_SERVICE_PATTERNS``, docs/chenwenhui/仓库扫描链路问题清单-2026-08-25.md
§问题 2.1).

The rule is the same for every framework: only the *declared* name is
evidence. A ``@FeignClient(name="ts-order-service")`` attribute is what
Spring Cloud actually registers with the discovery service; a string
literal that merely *looks* like a service name is not. Extracting the
latter is exactly what the deleted regexes did, and what this module
refuses to do.

Every extractor returns a :class:`CallTarget` — a service identifier plus
the mechanism/confidence pair scan_remote turns into
``DepEvidence(mechanism="RUNTIME_CALL", confidence="confirmed")``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MECHANISM = "RUNTIME_CALL"
_CONFIDENCE = "confirmed"


@dataclass(frozen=True, slots=True)
class CallTarget:
    """One declared runtime call: the identifier of the called service.

    ``name`` is the identifier as declared — a Feign ``name``/``value``,
    a Dubbo interface class name, or the service name a gRPC stub
    client targets. scan_remote maps it 1:1 into a ``DepEvidence``.
    """

    name: str
    mechanism: str = _MECHANISM
    confidence: str = _CONFIDENCE


def parse_call_declarations(content: str) -> tuple[CallTarget, ...]:
    """Extract declared call targets from one source file.

    Runs every framework extractor and deduplicates case-insensitively
    (first occurrence wins). Never raises: a file that declares nothing
    yields an empty tuple, so one unreadable file can never fail a scan.
    """

    found: list[CallTarget] = []
    seen: set[str] = set()
    for name in _extract_names(content):
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(CallTarget(name=name))
    return tuple(found)


def _extract_names(content: str) -> tuple[str, ...]:
    names: list[str] = []
    names.extend(_feign_targets(content))
    names.extend(_dubbo_targets(content))
    names.extend(_grpc_targets(content))
    return tuple(names)


# ---------------------------------------------------------------------------
# Feign clients (Spring Cloud OpenFeign)
# ---------------------------------------------------------------------------


_FEIGN_ANNOTATION_RE = re.compile(r"@FeignClient\s*\(\s*([^)]*)\)")
_BARE_STRING_RE = re.compile(r'\s*["\']([^"\']+)["\']')


def _attr(body: str, attr: str) -> str | None:
    """The string literal of one annotation attribute, if present."""
    match = re.search(rf'\b{re.escape(attr)}\s*=\s*["\']([^"\']+)["\']', body)
    return match.group(1) if match else None


def _feign_targets(text: str) -> tuple[str, ...]:
    """Extract the service name from ``@FeignClient`` declarations.

    The service name is the ``name`` attribute, falling back to ``value``
    (the single-argument form ``@FeignClient("ts-order-service")`` assigns
    ``value``). A client configured only with ``url=`` names no service,
    so it contributes nothing.
    """

    found: list[str] = []
    for match in _FEIGN_ANNOTATION_RE.finditer(text):
        body = match.group(1)
        name = _attr(body, "name")
        if name is None:
            name = _attr(body, "value")
        if name is None:
            bare = _BARE_STRING_RE.match(body)
            if bare is not None:
                name = bare.group(1)
        if name:
            found.append(name)
    return tuple(found)


# ---------------------------------------------------------------------------
# Dubbo references (Apache Dubbo / legacy alibaba)
# ---------------------------------------------------------------------------


_DUBBO_REFERENCE_RE = re.compile(r"@(?:DubboReference|Reference)\s*\(\s*([^)]*)\)")
_DUBBO_INTERFACE_CLASS_RE = re.compile(
    r"\binterfaceClass\s*=\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\.class"
)
_DUBBO_INTERFACE_NAME_RE = re.compile(r'\binterfaceName\s*=\s*["\']([^"\']+)["\']')


def _dubbo_targets(text: str) -> tuple[str, ...]:
    """Extract the interface a Dubbo consumer references.

    The called service is identified by its interface — the consumer
    declares it as ``interfaceClass`` (a class literal) or
    ``interfaceName`` (a string). A ``@Reference`` without either declares
    nothing we can anchor on, so it is skipped.
    """

    found: list[str] = []
    for match in _DUBBO_REFERENCE_RE.finditer(text):
        body = match.group(1)
        name: str | None = None
        interface_class = _DUBBO_INTERFACE_CLASS_RE.search(body)
        if interface_class is not None:
            name = interface_class.group(1)
        else:
            interface_name = _DUBBO_INTERFACE_NAME_RE.search(body)
            if interface_name is not None:
                name = interface_name.group(1)
        if name:
            found.append(name)
    return tuple(found)


# ---------------------------------------------------------------------------
# gRPC stub clients (Java / Python / Go generated code)
# ---------------------------------------------------------------------------


_GRPC_STUB_RE = re.compile(
    r"(?P<java>[A-Za-z_]\w*)Grpc\.new[A-Za-z_]*Stub\s*\("
    r"|(?P<pymod>[A-Za-z_]\w*)_pb2_grpc\.(?P<py>[A-Za-z_]\w*)Stub\s*\("
    r"|\.New(?P<go>[A-Za-z_]\w*)Client\s*\("
)


def _grpc_targets(text: str) -> tuple[str, ...]:
    """Extract the service name from generated gRPC stub constructors.

    Generated code names the target twice: ``OrderServiceGrpc`` /
    ``order_pb2_grpc.OrderServiceStub`` / ``orderpb.NewOrderServiceClient``.
    The service name is the ``OrderService`` part, and the pattern is
    anchored to the generated-code shape so ordinary method calls never
    match.
    """

    found: list[str] = []
    for match in _GRPC_STUB_RE.finditer(text):
        name = match.group("java") or match.group("py") or match.group("go")
        if name:
            found.append(name)
    return tuple(found)
