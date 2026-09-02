"""Tests for the deployment-topology parsers (mechanism ④).

Covers compose (``services.<name>.depends_on``) and Kubernetes
(workload labels / Service selectors) parsing in
``application/deploy_parsers.py``. The contract is strict: only *concrete*
references are targets and only *declared* service names are identities —
a ``${...}`` placeholder, an unknown manifest kind, or a malformed file
must contribute nothing.
"""

from repomesh.modules.repository_intelligence.application.deploy_parsers import (
    DeployParseResult,
    DeployTarget,
    is_compose_filename,
    parse_deploy_file,
)


def _targets(filename: str, content: str) -> tuple[str, ...]:
    return tuple(t.name for t in parse_deploy_file(filename, content).targets)


def _identities(filename: str, content: str) -> tuple[str, ...]:
    return parse_deploy_file(filename, content).identities


# ---------------------------------------------------------------------------
# Compose — services.<name>.depends_on
# ---------------------------------------------------------------------------


class TestCompose:
    def test_depends_on_list_yields_targets(self) -> None:
        content = (
            "services:\n"
            "  checkout-service:\n"
            "    build: .\n"
            "    depends_on:\n"
            "      - payment-service\n"
            "      - ts-auth-service\n"
        )
        assert _targets("docker-compose.yml", content) == (
            "payment-service",
            "ts-auth-service",
        )

    def test_depends_on_dict_long_syntax_yields_names(self) -> None:
        content = (
            "services:\n"
            "  checkout-service:\n"
            "    depends_on:\n"
            "      payment-service:\n"
            "        condition: service_healthy\n"
        )
        assert _targets("docker-compose.yml", content) == ("payment-service",)

    def test_service_names_are_identities(self) -> None:
        content = (
            "services:\n"
            "  checkout-service:\n"
            "    build: .\n"
            "  db:\n"
            "    image: postgres:16\n"
        )
        assert _identities("compose.yaml", content) == (
            "checkout-service",
            "db",
        )

    def test_no_services_contributes_nothing(self) -> None:
        assert _targets("docker-compose.yml", "version: '3'\n") == ()
        assert _identities("docker-compose.yml", "version: '3'\n") == ()

    def test_placeholder_depends_on_skipped(self) -> None:
        content = (
            "services:\n"
            "  app:\n"
            "    depends_on:\n"
            "      - ${UPSTREAM_SERVICE}\n"
            "      - ts-payment-service\n"
        )
        assert _targets("docker-compose.yml", content) == ("ts-payment-service",)

    def test_malformed_compose_yields_empty(self) -> None:
        assert _targets("docker-compose.yml", "services: [unclosed\n") == ()
        assert _identities("docker-compose.yml", "services: [unclosed\n") == ()

    def test_target_contract_fixed_mechanism_and_confidence(self) -> None:
        content = (
            "services:\n"
            "  app:\n"
            "    depends_on:\n"
            "      - ts-payment-service\n"
        )
        result = parse_deploy_file("docker-compose.yml", content)
        assert result.targets == (
            DeployTarget(
                name="ts-payment-service",
                mechanism="DEPLOY",
                confidence="declared",
            ),
        )

    def test_empty_result_defaults(self) -> None:
        assert parse_deploy_file("pipeline.yaml", "steps: []\n") == DeployParseResult()


# ---------------------------------------------------------------------------
# Kubernetes — workload labels (identities) + Service selectors (targets)
# ---------------------------------------------------------------------------


class TestK8s:
    def test_deployment_template_labels_name_the_service(self) -> None:
        content = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: checkout\n"
            "spec:\n"
            "  template:\n"
            "    metadata:\n"
            "      labels:\n"
            "        app: ts-checkout-service\n"
        )
        assert _identities("deployment.yaml", content) == ("ts-checkout-service",)
        assert _targets("deployment.yaml", content) == ()

    def test_metadata_labels_fallback(self) -> None:
        content = (
            "apiVersion: apps/v1\n"
            "kind: StatefulSet\n"
            "metadata:\n"
            "  labels:\n"
            "    app.kubernetes.io/name: ts-payment-service\n"
        )
        assert _identities("statefulset.yaml", content) == ("ts-payment-service",)

    def test_service_name_identity_and_selector_target(self) -> None:
        content = (
            "apiVersion: v1\n"
            "kind: Service\n"
            "metadata:\n"
            "  name: checkout-svc\n"
            "spec:\n"
            "  selector:\n"
            "    app: ts-checkout-service\n"
        )
        assert _identities("service.yaml", content) == ("checkout-svc",)
        assert _targets("service.yaml", content) == ("ts-checkout-service",)

    def test_selector_target_differs_from_service_name(self) -> None:
        """A Service fronts an app that may live in another repository."""
        content = (
            "apiVersion: v1\n"
            "kind: Service\n"
            "metadata:\n"
            "  name: orders-public\n"
            "spec:\n"
            "  selector:\n"
            "    app: ts-order-service\n"
        )
        assert _identities("service.yaml", content) == ("orders-public",)
        assert _targets("service.yaml", content) == ("ts-order-service",)

    def test_multi_document_manifest_aggregates(self) -> None:
        content = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: checkout\n"
            "spec:\n"
            "  template:\n"
            "    metadata:\n"
            "      labels:\n"
            "        app: ts-checkout-service\n"
            "---\n"
            "apiVersion: v1\n"
            "kind: Service\n"
            "metadata:\n"
            "  name: checkout-svc\n"
            "spec:\n"
            "  selector:\n"
            "    app: ts-payment-service\n"
        )
        assert _identities("k8s/all.yaml", content) == (
            "ts-checkout-service",
            "checkout-svc",
        )
        assert _targets("k8s/all.yaml", content) == ("ts-payment-service",)

    def test_unknown_kinds_contribute_nothing(self) -> None:
        content = (
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: app-config\n"
        )
        assert _targets("configmap.yaml", content) == ()
        assert _identities("configmap.yaml", content) == ()

    def test_non_manifest_yaml_contributes_nothing(self) -> None:
        content = "pipeline:\n  stages: [build, deploy]\n"
        assert _targets("pipeline.yaml", content) == ()
        assert _identities("pipeline.yaml", content) == ()

    def test_malformed_manifest_yields_empty(self) -> None:
        assert _targets("deployment.yaml", "kind: [unclosed\n") == ()
        assert _identities("deployment.yaml", "kind: [unclosed\n") == ()

    def test_placeholder_label_is_not_an_identity(self) -> None:
        content = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: checkout\n"
            "spec:\n"
            "  template:\n"
            "    metadata:\n"
            "      labels:\n"
            "        app: ${APP_NAME}\n"
        )
        assert _identities("deployment.yaml", content) == ()
        assert _targets("deployment.yaml", content) == ()

    def test_duplicate_identifiers_deduplicated_case_insensitively(self) -> None:
        content = (
            "apiVersion: v1\n"
            "kind: Service\n"
            "metadata:\n"
            "  name: Checkout-Svc\n"
            "spec:\n"
            "  selector:\n"
            "    app: ts-checkout-service\n"
            "---\n"
            "apiVersion: v1\n"
            "kind: Service\n"
            "metadata:\n"
            "  name: checkout-svc\n"
            "spec:\n"
            "  selector:\n"
            "    app: ts-other-service\n"
        )
        assert _identities("service.yaml", content) == ("Checkout-Svc",)
        assert _targets("service.yaml", content) == (
            "ts-checkout-service",
            "ts-other-service",
        )


# ---------------------------------------------------------------------------
# Dispatch — filename routes to the right parser
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_compose_yml_and_yaml_route_to_compose(self) -> None:
        for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml"):
            assert is_compose_filename(name)
            result = parse_deploy_file(name, "services:\n  app:\n    depends_on:\n      - x\n")
            assert [t.name for t in result.targets] == ["x"]

    def test_deployment_yaml_routes_to_k8s(self) -> None:
        assert not is_compose_filename("deployment.yaml")
        result = parse_deploy_file(
            "deployment.yaml",
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n"
            "  name: checkout\nspec:\n  template:\n    metadata:\n"
            "      labels:\n        app: ts-checkout-service\n",
        )
        assert result.identities == ("ts-checkout-service",)

    def test_unknown_file_kind_contributes_nothing(self) -> None:
        assert parse_deploy_file("README.md", "services:\n  app:\n") == DeployParseResult()
        assert parse_deploy_file("application.yml", "spring:\n  app:\n") == DeployParseResult()
