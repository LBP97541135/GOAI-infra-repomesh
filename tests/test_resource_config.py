"""Tests for the shared-resource config extractors (mechanism ③).

Covers YAML / properties / dotenv parsing in
``application/resource_config.py``. The contract is strict: only *concrete,
declared* resource identifiers are evidence — a ``${...}`` placeholder, an
in-memory H2 database, or a plain business property must extract nothing.
"""

from repomesh.modules.repository_intelligence.application.resource_config import (
    ResourceTarget,
    parse_resource_config,
)


def _names(filename: str, content: str) -> tuple[str, ...]:
    return tuple(target.name for target in parse_resource_config(filename, content))


def _targets(filename: str, content: str) -> tuple[ResourceTarget, ...]:
    return parse_resource_config(filename, content)


# ---------------------------------------------------------------------------
# YAML (application.yml)
# ---------------------------------------------------------------------------


class TestYaml:
    def test_datasource_url_yields_database_identifier(self) -> None:
        content = (
            "spring:\n"
            "  datasource:\n"
            "    url: jdbc:mysql://db-01:3306/orders-db?useSSL=false\n"
            "    username: app\n"
        )
        assert _names("application.yml", content) == ("DATABASE:orders-db",)

    def test_flat_datasource_key(self) -> None:
        content = "datasource.url: jdbc:postgresql://host:5432/checkout-db\n"
        assert _names("application.yml", content) == ("DATABASE:checkout-db",)

    def test_postgres_url_with_credentials(self) -> None:
        content = "spring.datasource.url: postgres://user:pass@db-02:5432/payments\n"
        assert _names("application.yml", content) == ("DATABASE:payments",)

    def test_redis_host_and_port_combined(self) -> None:
        content = (
            "spring:\n"
            "  redis:\n"
            "    host: cache-01\n"
            "    port: 6379\n"
        )
        assert _names("application.yml", content) == ("REDIS:cache-01:6379",)

    def test_redis_url_form(self) -> None:
        content = "spring.data.redis.url: redis://cache-02:6380/0\n"
        assert _names("application.yml", content) == ("REDIS:cache-02:6380",)

    def test_redis_host_without_port_keeps_bare_host(self) -> None:
        content = "spring.redis.host: cache-01\n"
        assert _names("application.yml", content) == ("REDIS:cache-01",)

    def test_kafka_bootstrap_servers(self) -> None:
        content = (
            "spring:\n"
            "  kafka:\n"
            "    bootstrap-servers: broker-01:9092,broker-02:9092\n"
        )
        assert _names("application.yml", content) == (
            "MQ:broker-01:9092,broker-02:9092",
        )

    def test_rabbitmq_host_and_port(self) -> None:
        content = (
            "spring:\n"
            "  rabbitmq:\n"
            "    host: rabbit-01\n"
            "    port: 5672\n"
        )
        assert _names("application.yml", content) == ("MQ:rabbit-01:5672",)

    def test_rocketmq_name_server(self) -> None:
        content = "rocketmq.name-server: ns-01:9876\n"
        assert _names("application.yml", content) == ("MQ:ns-01:9876",)

    def test_object_storage_bucket(self) -> None:
        content = "oss.bucket: orders-assets\n"
        assert _names("application.yml", content) == ("BUCKET:orders-assets",)

    def test_multiple_resources_in_one_file(self) -> None:
        content = (
            "spring:\n"
            "  datasource:\n"
            "    url: jdbc:mysql://db-01:3306/orders-db\n"
            "  redis:\n"
            "    host: cache-01\n"
            "    port: 6379\n"
            "oss:\n"
            "  bucket: orders-assets\n"
        )
        assert _names("application.yml", content) == (
            "DATABASE:orders-db",
            "REDIS:cache-01:6379",
            "BUCKET:orders-assets",
        )

    def test_multi_document_yaml_merges_documents(self) -> None:
        content = (
            "spring:\n"
            "  datasource:\n"
            "    url: jdbc:mysql://db-01:3306/orders-db\n"
            "---\n"
            "spring:\n"
            "  redis:\n"
            "    host: cache-01\n"
        )
        assert _names("application.yml", content) == (
            "DATABASE:orders-db",
            "REDIS:cache-01",
        )

    def test_business_only_config_extracts_nothing(self) -> None:
        content = (
            "server:\n"
            "  port: 8080\n"
            "spring:\n"
            "  application:\n"
            "    name: ts-order-service\n"
            "logging:\n"
            "  level:\n"
            "    root: INFO\n"
        )
        assert _names("application.yml", content) == ()

    def test_placeholder_value_is_not_evidence(self) -> None:
        content = (
            "spring:\n"
            "  datasource:\n"
            "    url: ${DB_URL}\n"
        )
        assert _names("application.yml", content) == ()

    def test_placeholder_with_default_is_not_evidence(self) -> None:
        """${REDIS_HOST:-cache-01} is still unresolved — never a resource."""
        content = "spring.redis.host: ${REDIS_HOST:-cache-01}\n"
        assert _names("application.yml", content) == ()

    def test_in_memory_h2_database_is_not_shared(self) -> None:
        content = "spring.datasource.url: jdbc:h2:mem:testdb\n"
        assert _names("application.yml", content) == ()

    def test_url_without_database_path_extracts_nothing(self) -> None:
        content = "spring.datasource.url: jdbc:mysql://db-01:3306/\n"
        assert _names("application.yml", content) == ()

    def test_malformed_yaml_yields_empty_not_an_exception(self) -> None:
        assert _names("application.yml", "spring: [unclosed\n") == ()

    def test_resource_identifiers_are_kind_namespaced(self) -> None:
        """A database and a bucket named the same thing are not the same."""
        content = (
            "spring.datasource.url: jdbc:mysql://db-01:3306/orders\n"
            "oss.bucket: orders\n"
        )
        assert _names("application.yml", content) == ("DATABASE:orders", "BUCKET:orders")


# ---------------------------------------------------------------------------
# properties (application.properties)
# ---------------------------------------------------------------------------


class TestProperties:
    def test_datasource_url(self) -> None:
        content = (
            "spring.datasource.url=jdbc:mysql://db-01:3306/orders-db\n"
            "spring.datasource.username=app\n"
        )
        assert _names("application.properties", content) == ("DATABASE:orders-db",)

    def test_colon_separator_supported(self) -> None:
        content = "spring.datasource.url: jdbc:mysql://db-01:3306/orders-db\n"
        assert _names("application.properties", content) == ("DATABASE:orders-db",)

    def test_inline_comment_stripped(self) -> None:
        content = "oss.bucket=orders-assets # primary bucket\n"
        assert _names("application.properties", content) == ("BUCKET:orders-assets",)

    def test_comment_and_blank_lines_ignored(self) -> None:
        content = (
            "# datasource\n"
            "\n"
            "spring.datasource.url=jdbc:mysql://db-01:3306/orders-db\n"
        )
        assert _names("application.properties", content) == ("DATABASE:orders-db",)


# ---------------------------------------------------------------------------
# dotenv (*.env)
# ---------------------------------------------------------------------------


class TestDotenv:
    def test_screaming_snake_keys_normalised(self) -> None:
        content = (
            "DB_URL=postgres://user:pass@db-01:5432/orders-db\n"
            "REDIS_HOST=cache-01\n"
            "REDIS_PORT=6379\n"
            "STORAGE_BUCKET=orders-assets\n"
        )
        assert _names(".env", content) == (
            "DATABASE:orders-db",
            "REDIS:cache-01:6379",
            "BUCKET:orders-assets",
        )

    def test_quoted_values_unquoted(self) -> None:
        content = "REDIS_HOST='cache-01'\n"
        assert _names(".env", content) == ("REDIS:cache-01",)

    def test_export_prefix_supported(self) -> None:
        content = (
            "export DB_URL=postgres://user:pass@db-01:5432/orders-db\n"
            "export REDIS_HOST=cache-01\n"
        )
        assert _names(".env", content) == (
            "DATABASE:orders-db",
            "REDIS:cache-01",
        )

    def test_inline_comment_after_unquoted_value(self) -> None:
        content = "REDIS_HOST=cache-01 # cache cluster\n"
        assert _names(".env", content) == ("REDIS:cache-01",)

    def test_placeholder_skipped(self) -> None:
        content = "DB_URL=$DB_URL\n"
        assert _names(".env", content) == ()


# ---------------------------------------------------------------------------
# Aggregation contract
# ---------------------------------------------------------------------------


class TestContract:
    def test_mechanism_and_confidence_are_fixed(self) -> None:
        targets = _targets(
            "application.yml",
            "spring.datasource.url: jdbc:mysql://db-01:3306/orders-db\n",
        )
        assert targets == (
            ResourceTarget(
                name="DATABASE:orders-db",
                mechanism="SHARED_RESOURCE",
                confidence="declared",
            ),
        )

    def test_duplicate_identifiers_deduplicated_case_insensitively(self) -> None:
        content = (
            "spring.datasource.url: jdbc:mysql://db-01:3306/Orders-Db\n"
            "datasource:\n"
            "  url: jdbc:mysql://db-02:3306/orders-db\n"
        )
        assert _names("application.yml", content) == ("DATABASE:Orders-Db",)

    def test_unknown_file_kind_returns_empty(self) -> None:
        assert _names("deployment.yaml", "anything") == ()
        assert _names("config.json", "{}") == ()
