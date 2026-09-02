"""Tests for the runtime call-declaration extractors (mechanism ②).

Covers FeignClient, Dubbo reference and gRPC stub extraction in
``application/call_declarations.py``. The contract is strict: only names
*declared to a framework* are evidence — an ordinary string literal that
merely resembles a service name must extract nothing.
"""

from repomesh.modules.repository_intelligence.application.call_declarations import (
    CallTarget,
    parse_call_declarations,
)


def _names(content: str) -> tuple[str, ...]:
    return tuple(target.name for target in parse_call_declarations(content))


# ---------------------------------------------------------------------------
# FeignClient
# ---------------------------------------------------------------------------


class TestFeign:
    def test_name_attribute(self) -> None:
        content = "@FeignClient(name = \"ts-order-service\")"
        assert _names(content) == ("ts-order-service",)

    def test_value_attribute(self) -> None:
        content = "@FeignClient(value = \"ts-payment-service\")"
        assert _names(content) == ("ts-payment-service",)

    def test_bare_positional_argument_is_the_service_name(self) -> None:
        content = "@FeignClient(\"ts-notification-service\")"
        assert _names(content) == ("ts-notification-service",)

    def test_multiline_annotation_with_extra_attributes(self) -> None:
        content = (
            "@FeignClient(\n"
            "    name = \"ts-auth-service\",\n"
            "    url = \"http://localhost:8080\",\n"
            "    configuration = AuthClientConfig.class\n"
            ")\n"
        )
        assert _names(content) == ("ts-auth-service",)

    def test_url_only_client_names_no_service(self) -> None:
        """A Feign client pointed at a raw URL has no registered service name."""
        content = "@FeignClient(url = \"https://example.com/api\")"
        assert _names(content) == ()

    def test_single_quotes(self) -> None:
        content = "@FeignClient(name = 'ts-cart-service')"
        assert _names(content) == ("ts-cart-service",)


# ---------------------------------------------------------------------------
# Dubbo references
# ---------------------------------------------------------------------------


class TestDubbo:
    def test_dubbo_reference_interface_class(self) -> None:
        content = "@DubboReference(interfaceClass = OrderService.class)"
        assert _names(content) == ("OrderService",)

    def test_dubbo_reference_fully_qualified_interface(self) -> None:
        content = "@DubboReference(interfaceClass = com.acme.OrderService.class)"
        assert _names(content) == ("com.acme.OrderService",)

    def test_legacy_reference_annotation(self) -> None:
        content = "@Reference(interfaceClass = PaymentService.class)"
        assert _names(content) == ("PaymentService",)

    def test_interface_name_string_attribute(self) -> None:
        content = '@DubboReference(interfaceName = "com.acme.NotifyService")'
        assert _names(content) == ("com.acme.NotifyService",)

    def test_reference_without_interface_declares_nothing(self) -> None:
        content = "@Reference(check = false, timeout = 3000)"
        assert _names(content) == ()


# ---------------------------------------------------------------------------
# gRPC stub clients
# ---------------------------------------------------------------------------


class TestGrpc:
    def test_java_blocking_stub(self) -> None:
        content = "OrderServiceGrpc.newBlockingStub(channel).getOrder(request);"
        assert _names(content) == ("OrderService",)

    def test_java_future_stub(self) -> None:
        content = "InventoryServiceGrpc.newFutureStub(channel)"
        assert _names(content) == ("InventoryService",)

    def test_python_stub(self) -> None:
        content = "order_pb2_grpc.OrderServiceStub(channel)"
        assert _names(content) == ("OrderService",)

    def test_go_client_constructor(self) -> None:
        content = "client := orderpb.NewOrderServiceClient(conn)"
        assert _names(content) == ("OrderService",)


# ---------------------------------------------------------------------------
# Negative cases — the old string guessing is gone
# ---------------------------------------------------------------------------


class TestDoesNotGuess:
    def test_plain_service_name_string_literal(self) -> None:
        """A bare "ts-order-service" string is not a declaration."""
        content = 'String target = "ts-order-service";'
        assert _names(content) == ()

    def test_rest_template_url_is_not_a_declaration(self) -> None:
        content = 'restTemplate.getForObject("http://ts-order-service/api", String.class);'
        assert _names(content) == ()

    def test_routing_key_string_is_not_a_declaration(self) -> None:
        content = 'sendService.send("ts-order-created");'
        assert _names(content) == ()

    def test_get_service_url_utility_call_is_not_a_declaration(self) -> None:
        content = 'getServiceUrl("ts-notification-service");'
        assert _names(content) == ()

    def test_comment_mentioning_a_service_name(self) -> None:
        content = "// calls ts-order-service via Feign\n"
        assert _names(content) == ()

    def test_unrelated_annotation(self) -> None:
        content = "@RestController\npublic class OrderController {}"
        assert _names(content) == ()


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


class TestAggregation:
    def test_multiple_targets_across_frameworks(self) -> None:
        content = (
            "@FeignClient(name = \"ts-order-service\")\n"
            "@DubboReference(interfaceClass = NotifyService.class)\n"
            "OrderServiceGrpc.newBlockingStub(channel)"
        )
        names = _names(content)
        assert "ts-order-service" in names
        assert "NotifyService" in names
        assert "OrderService" in names
        # Same service declared twice collapses to one target.
        assert len(names) == len(set(n.lower() for n in names))

    def test_deduplicated_case_insensitively(self) -> None:
        content = (
            "@FeignClient(name = \"TS-ORDER-SERVICE\")\n"
            "@FeignClient(name = \"ts-order-service\")\n"
        )
        assert _names(content) == ("TS-ORDER-SERVICE",)

    def test_empty_content(self) -> None:
        assert _names("") == ()
        assert parse_call_declarations("") == ()

    def test_target_carries_the_runtime_call_mechanism(self) -> None:
        (target,) = parse_call_declarations('@FeignClient(name = "ts-order-service")')
        assert isinstance(target, CallTarget)
        assert target.mechanism == "RUNTIME_CALL"
        assert target.confidence == "confirmed"
