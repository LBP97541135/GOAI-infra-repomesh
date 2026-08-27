"""Shared-resource config parsers — mechanism ③ (SHARED_RESOURCE) evidence.

Extracts *resource identifiers* from application configuration files
(``application.yml`` / ``application.properties``): the database instance
behind a datasource URL, the Redis host, the MQ broker address, the
object-storage bucket. Two repositories that declare the same identifier
share that resource.

This is deliberately different from mechanisms ① ②: the identifier names a
*resource*, not a repository, so the graph never resolves it through the
service registry. Instead, every repository declaring ``DATABASE:orders-db``
shares the resource with every other one that declares it
(docs/chenwenhui/仓库扫描链路问题清单-2026-08-25.md §③).

Only *declared, concrete* identifiers are evidence. A ``${...}`` placeholder
is not a resource we can name, so it is skipped; a plain business property
(application name, log level) is not a shared resource, so it is skipped;
an H2 in-memory database is not shared across processes, so it is skipped.

The evidence *surface* — which files scan_remote even reads — is decided in
``scan_remote._find_resource_files``: ``.env`` files are excluded there
(local environment overrides, often carrying secrets), so the dotenv branch
of :func:`parse_resource_config` is only reachable by an explicit caller.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

_MECHANISM = "SHARED_RESOURCE"
_CONFIDENCE = "declared"


@dataclass(frozen=True, slots=True)
class ResourceTarget:
    """One shared resource identifier extracted from a config file.

    ``name`` is the normalised identifier, namespaced by resource kind so
    identifiers of different kinds never collide (``DATABASE:orders`` and
    ``BUCKET:orders`` are different resources). scan_remote maps it 1:1
    into a ``DepEvidence`` with ``mechanism="SHARED_RESOURCE"`` and
    ``confidence="declared"``.
    """

    name: str
    mechanism: str = _MECHANISM
    confidence: str = _CONFIDENCE


def parse_resource_config(filename: str, content: str) -> tuple[ResourceTarget, ...]:
    """Parse *content* of a config file named *filename*.

    YAML (``*.yml``/``*.yaml``) goes through PyYAML ``safe_load_all``;
    ``*.properties`` through the stdlib key=value parser; ``*.env`` through
    the dotenv-ish parser. Unknown file kinds contribute nothing. Never
    raises: an unparseable config file yields an empty tuple, so one bad
    file can never fail a scan.
    """

    fname = filename.rsplit("/", 1)[-1].lower()
    if fname.endswith((".yml", ".yaml")):
        identifiers = _yaml_identifiers(content)
    elif fname.endswith(".properties"):
        identifiers = _extract_identifiers(_parse_key_value(content, colon=True))
    elif fname.endswith(".env"):
        identifiers = _extract_identifiers(_parse_key_value(content, colon=False))
    else:
        return ()
    return tuple(ResourceTarget(name=identifier) for identifier in identifiers)


# ---------------------------------------------------------------------------
# Resource-identifier extraction
# ---------------------------------------------------------------------------

#: Recognised database URL schemes. ``jdbc:`` covers jdbc:mysql/jdbc:postgresql
#: and friends; the bare ``*://`` schemes cover non-JDBC drivers.
_DB_SCHEMES = ("jdbc:", "mysql://", "postgres://", "postgresql://", "mariadb://")


def _extract_identifiers(flat: dict[str, str]) -> tuple[str, ...]:
    """Map flat key/value pairs to normalised resource identifiers.

    Keys are matched after lower-casing and mapping ``_`` to ``.``, which
    lets one rule set cover dot-separated properties/YAML keys and
    SCREAMING_SNAKE env names (``SPRING_REDIS_HOST`` → ``spring.redis.host``).
    Deduplicates case-insensitively, first occurrence wins.
    """

    normalized = {
        key.lower().replace("_", "."): value for key, value in flat.items()
    }
    found: list[str] = []
    seen: set[str] = set()
    for key, value in normalized.items():
        value = value.strip()
        # A value that still contains ``$`` is an unresolved placeholder
        # (``${DB_URL}``, ``${REDIS_HOST:-cache}``) — not a concrete
        # resource we can name. ``$`` never legitimately appears in a
        # host, database or bucket name.
        if not value or "$" in value:
            continue
        identifier = _identifier_for(key, value, normalized)
        if identifier is None:
            continue
        dedup_key = identifier.lower()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        found.append(identifier)
    return tuple(found)


def _identifier_for(key: str, value: str, flat: dict[str, str]) -> str | None:
    """One resource identifier for a single key/value pair, or ``None``."""

    if key.endswith(".url") and (
        "datasource" in key or key in ("db.url", "database.url")
    ):
        database = _database_name(value)
        return f"DATABASE:{database}" if database else None
    if "redis" in key and key.endswith(".url") and "://" in value:
        host_port = _host_port(value)
        return f"REDIS:{host_port}" if host_port else None
    if "redis" in key and key.endswith(".host"):
        host_port = _host_port(value)
        # A value that already carries its port (``cache-01:6379``) is the
        # full identifier; a sibling ``*.port`` key is only appended when
        # the host alone would lose it. Never ``cache-01:6379:6379``.
        if ":" in host_port:
            return f"REDIS:{host_port}"
        port = _sibling_port(flat, key)
        return f"REDIS:{host_port}:{port}" if port else f"REDIS:{host_port}"
    if "kafka" in key and key.endswith((".bootstrap.servers", ".bootstrap-servers")):
        return f"MQ:{value}"
    if "rabbitmq" in key and key.endswith(".host"):
        host_port = _host_port(value)
        if ":" in host_port:
            return f"MQ:{host_port}"
        port = _sibling_port(flat, key)
        return f"MQ:{host_port}:{port}" if port else f"MQ:{host_port}"
    if "rocketmq" in key and key.endswith((".name-server", ".name.server")):
        return f"MQ:{value}"
    if "bucket" in key:
        return f"BUCKET:{value}"
    return None


def _database_name(url: str) -> str | None:
    """The database instance name from a JDBC / driver URL.

    ``jdbc:mysql://host:3306/orders-db?useSSL=false`` → ``orders-db``;
    ``postgres://user:pass@host:5432/orders`` → ``orders``. URLs without a
    database path (``jdbc:mysql://host:3306/``) and in-memory H2 databases
    name no shared resource and yield ``None``.
    """

    value = url.strip()
    if not value.startswith(_DB_SCHEMES) or "://" not in value or "h2:mem" in value:
        return None
    body = value.split("://", 1)[1]
    if "/" not in body:
        return None
    path = body.split("/", 1)[1]
    for separator in ("?", ";", "#"):
        path = path.split(separator, 1)[0]
    database = path.strip().strip("/")
    return database or None


def _host_port(value: str) -> str:
    """Normalise a host[:port] value: strip scheme, path and query.

    ``redis://cache-01:6379/0`` → ``cache-01:6379``; ``rabbit-01`` →
    ``rabbit-01``; ``127.0.0.1`` stays ``127.0.0.1``.
    """

    host_port = value.strip()
    if "://" in host_port:
        host_port = host_port.split("://", 1)[1]
    host_port = host_port.split("/", 1)[0]
    host_port = host_port.split("?", 1)[0]
    return host_port.strip()


def _sibling_port(flat: dict[str, str], key: str) -> str:
    """An explicit port from the sibling ``*.port`` key.

    ``spring.redis.host`` looks for ``spring.redis.port``. Ports embedded in
    the host value itself are handled by the caller, which decides whether a
    sibling is even needed — this helper never duplicates them.
    """

    port_key = key[: -len("host")] + "port"
    return flat.get(port_key, "").strip()


# ---------------------------------------------------------------------------
# YAML (PyYAML) — application*.yml / bootstrap.yml
# ---------------------------------------------------------------------------


def _yaml_identifiers(content: str) -> tuple[str, ...]:
    """Parse one or more YAML documents into resource identifiers.

    Uses ``safe_load_all`` (never code execution) and merges every document
    (``application.yml`` commonly splits env profiles with ``---``).
    """

    try:
        documents = list(yaml.safe_load_all(content))
    except Exception:  # noqa: BLE001 — a scan survives any one bad config
        return ()
    flat: dict[str, str] = {}
    for document in documents:
        if isinstance(document, dict):
            flat.update(_flatten(document))
    return _extract_identifiers(flat)


def _flatten(data: object, prefix: str = "") -> dict[str, str]:
    """Flatten nested YAML into dot-separated leaf key/value pairs.

    Lists are indexed (``kafka.consumer[0].topic``); only scalar leaves are
    kept, so a config file that declares no resources yields an empty dict.
    """

    out: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if not isinstance(key, str):
                continue
            path = f"{prefix}.{key}" if prefix else key
            out.update(_flatten(value, path))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            out.update(_flatten(item, f"{prefix}[{index}]"))
    elif isinstance(data, str):
        out[prefix] = data
    elif isinstance(data, (int, float, bool)):
        out[prefix] = str(data)
    return out


# ---------------------------------------------------------------------------
# Properties / dotenv (standard library) — application*.properties / *.env
# ---------------------------------------------------------------------------


def _parse_key_value(content: str, *, colon: bool) -> dict[str, str]:
    """Parse a key=value file, optionally also accepting ``key: value``.

    Handles ``#`` comments, blank lines, and (for env files) quote stripping
    and inline comments. Continuation lines are not supported — out of scope
    for application config, and a multi-line value simply stays unparsed.
    """

    flat: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        if not colon and line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" in line:
            key, _, value = line.partition("=")
        elif colon and ":" in line:
            key, _, value = line.partition(":")
        else:
            continue
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if colon:
            # Java properties: a '#' preceded by whitespace starts a comment.
            hash_index = value.find(" #")
            if hash_index != -1:
                value = value[:hash_index].strip()
        else:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            else:
                hash_index = value.find(" #")
                if hash_index != -1:
                    value = value[:hash_index].strip()
        flat[key] = value
    return flat
