"""Tests for UCPResponseParser."""

import json

from ucp_analytics.events import UCPEventType
from ucp_analytics.parser import UCPResponseParser


class TestClassify:
    def test_discovery(self):
        assert (
            UCPResponseParser.classify("GET", "/.well-known/ucp", 200, None)
            == UCPEventType.PROFILE_DISCOVERED
        )

    def test_create_checkout(self):
        assert (
            UCPResponseParser.classify("POST", "/checkout-sessions", 201, {})
            == UCPEventType.CHECKOUT_SESSION_CREATED
        )

    def test_update_checkout(self):
        assert (
            UCPResponseParser.classify(
                "PUT",
                "/checkout-sessions/chk_123",
                200,
                {"status": "ready_for_complete"},
            )
            == UCPEventType.CHECKOUT_SESSION_UPDATED
        )

    def test_update_escalation(self):
        assert (
            UCPResponseParser.classify(
                "PUT",
                "/checkout-sessions/chk_123",
                200,
                {"status": "requires_escalation"},
            )
            == UCPEventType.CHECKOUT_ESCALATION
        )

    def test_complete_checkout(self):
        assert (
            UCPResponseParser.classify(
                "POST", "/checkout-sessions/chk_123/complete", 200, {}
            )
            == UCPEventType.CHECKOUT_SESSION_COMPLETED
        )

    def test_cancel_checkout(self):
        assert (
            UCPResponseParser.classify(
                "POST", "/checkout-sessions/chk_123/cancel", 200, {}
            )
            == UCPEventType.CHECKOUT_SESSION_CANCELED
        )

    def test_get_checkout(self):
        assert (
            UCPResponseParser.classify("GET", "/checkout-sessions/chk_123", 200, {})
            == UCPEventType.CHECKOUT_SESSION_GET
        )

    # --- Cart endpoints ---

    def test_create_cart(self):
        assert (
            UCPResponseParser.classify("POST", "/carts", 201, {})
            == UCPEventType.CART_CREATED
        )

    def test_get_cart(self):
        assert (
            UCPResponseParser.classify("GET", "/carts/cart_123", 200, {})
            == UCPEventType.CART_GET
        )

    def test_update_cart(self):
        assert (
            UCPResponseParser.classify("PUT", "/carts/cart_123", 200, {})
            == UCPEventType.CART_UPDATED
        )

    def test_cancel_cart(self):
        assert (
            UCPResponseParser.classify("POST", "/carts/cart_123/cancel", 200, {})
            == UCPEventType.CART_CANCELED
        )

    # --- Other ---

    def test_error(self):
        # Error fallback applies to paths that don't match a specific UCP pattern
        assert (
            UCPResponseParser.classify("POST", "/some/unknown/path", 500, {})
            == UCPEventType.ERROR
        )

    def test_order(self):
        assert (
            UCPResponseParser.classify("POST", "/orders", 201, {})
            == UCPEventType.ORDER_CREATED
        )

    def test_simulate_shipping(self):
        assert (
            UCPResponseParser.classify(
                "POST", "/testing/simulate-shipping/order_123", 200, {}
            )
            == UCPEventType.ORDER_SHIPPED
        )

    # --- Order lifecycle (status-based) ---

    def test_order_delivered(self):
        assert (
            UCPResponseParser.classify(
                "GET", "/orders/order_123", 200, {"status": "delivered"}
            )
            == UCPEventType.ORDER_DELIVERED
        )

    def test_order_returned(self):
        assert (
            UCPResponseParser.classify(
                "GET", "/orders/order_123", 200, {"status": "returned"}
            )
            == UCPEventType.ORDER_RETURNED
        )

    def test_order_canceled(self):
        assert (
            UCPResponseParser.classify(
                "GET", "/orders/order_123", 200, {"status": "canceled"}
            )
            == UCPEventType.ORDER_CANCELED
        )

    def test_order_canceled_british_spelling(self):
        assert (
            UCPResponseParser.classify(
                "GET", "/orders/order_123", 200, {"status": "cancelled"}
            )
            == UCPEventType.ORDER_CANCELED
        )

    # --- Webhook paths ---

    def test_webhook_order_delivered(self):
        assert (
            UCPResponseParser.classify("POST", "/webhooks/order-delivered", 200, {})
            == UCPEventType.ORDER_DELIVERED
        )

    def test_webhook_order_returned(self):
        assert (
            UCPResponseParser.classify("POST", "/webhook/order-returned", 200, {})
            == UCPEventType.ORDER_RETURNED
        )

    def test_webhook_order_canceled(self):
        assert (
            UCPResponseParser.classify("POST", "/webhooks/order_canceled", 200, {})
            == UCPEventType.ORDER_CANCELED
        )

    # --- Identity sub-paths ---

    def test_identity_initiated(self):
        assert (
            UCPResponseParser.classify("POST", "/identity", 200, {})
            == UCPEventType.IDENTITY_LINK_INITIATED
        )

    def test_identity_callback(self):
        assert (
            UCPResponseParser.classify("GET", "/identity/callback", 200, {})
            == UCPEventType.IDENTITY_LINK_COMPLETED
        )

    def test_oauth_callback(self):
        assert (
            UCPResponseParser.classify("GET", "/oauth/callback", 200, {})
            == UCPEventType.IDENTITY_LINK_COMPLETED
        )

    def test_identity_revoke(self):
        assert (
            UCPResponseParser.classify("POST", "/identity/revoke", 200, {})
            == UCPEventType.IDENTITY_LINK_REVOKED
        )

    def test_identity_delete(self):
        assert (
            UCPResponseParser.classify("DELETE", "/identity/link_123", 200, {})
            == UCPEventType.IDENTITY_LINK_REVOKED
        )


class TestExtract:
    # Sample checkout response using SDK/samples-aligned format
    SAMPLE_CHECKOUT_RESPONSE = {
        "ucp": {
            "version": "2026-01-11",
            "capabilities": [
                {"name": "dev.ucp.shopping.checkout", "version": "2026-01-11"},
                {
                    "name": "dev.ucp.shopping.fulfillment",
                    "version": "2026-01-11",
                    "extends": "dev.ucp.shopping.checkout",
                },
            ],
        },
        "id": "chk_abc123",
        "status": "ready_for_complete",
        "currency": "USD",
        "line_items": [
            {
                "id": "li_1",
                "item": {
                    "id": "item_1",
                    "title": "Rose Bouquet",
                    "price": 2500,
                },
                "quantity": 2,
            },
        ],
        "totals": [
            {"type": "subtotal", "amount": 5000},
            {"type": "tax", "amount": 400},
            {"type": "fulfillment", "amount": 599},
            {"type": "total", "amount": 5999},
        ],
        "payment": {
            "handlers": [
                {
                    "id": "google_pay",
                    "name": "com.google.pay",
                    "version": "2026-01-11",
                    "spec": "https://example.com/spec",
                    "config_schema": "https://example.com/schema",
                    "instrument_schemas": [],
                    "config": {},
                },
            ],
            "instruments": [
                {
                    "id": "instr_1",
                    "handler_id": "google_pay",
                    "type": "wallet",
                    "brand": "google_pay",
                },
            ],
        },
        "fulfillment": {
            "methods": [
                {
                    "id": "method_1",
                    "type": "shipping",
                    "line_item_ids": ["li_1"],
                    "destinations": [
                        {
                            "id": "dest_1",
                            "address_country": "US",
                            "postal_code": "94043",
                        },
                    ],
                }
            ]
        },
        "discounts": {
            "codes": ["SUMMER20"],
            "applied": [
                {"code": "SUMMER20", "title": "Summer Sale", "amount": 500},
            ],
        },
        "expires_at": "2026-01-12T00:00:00Z",
        "messages": [
            {
                "type": "error",
                "code": "missing",
                "content": "Phone required",
                "severity": "recoverable",
            },
        ],
    }

    def test_extract_checkout_fields(self):
        fields = UCPResponseParser.extract(self.SAMPLE_CHECKOUT_RESPONSE)

        assert fields["checkout_session_id"] == "chk_abc123"
        assert fields["checkout_status"] == "ready_for_complete"
        assert fields["currency"] == "USD"
        assert fields["subtotal_amount"] == 5000
        assert fields["tax_amount"] == 400
        assert fields["fulfillment_amount"] == 599
        assert fields["total_amount"] == 5999
        assert fields["line_item_count"] == 1
        assert fields["ucp_version"] == "2026-01-11"
        assert fields["payment_handler_id"] == "google_pay"
        assert fields["payment_instrument_type"] == "wallet"
        assert fields["fulfillment_type"] == "shipping"
        assert fields["fulfillment_destination_country"] == "US"
        assert fields["error_code"] == "missing"
        assert fields["error_severity"] == "recoverable"
        assert fields["expires_at"] == "2026-01-12T00:00:00Z"

    def test_extract_capabilities_array(self):
        """SDK/samples: capabilities are an array with name fields."""
        fields = UCPResponseParser.extract(self.SAMPLE_CHECKOUT_RESPONSE)
        assert "capabilities_json" in fields
        caps = json.loads(fields["capabilities_json"])
        names = [c["name"] for c in caps]
        assert "dev.ucp.shopping.checkout" in names
        assert "dev.ucp.shopping.fulfillment" in names

    def test_extract_capabilities_object_keyed_compat(self):
        """Robustness: object-keyed capabilities are normalized to array."""
        body = {
            "ucp": {
                "version": "2026-01-11",
                "capabilities": {
                    "dev.ucp.shopping.checkout": [{"version": "2026-01-11"}],
                    "dev.ucp.shopping.fulfillment": [{"version": "2026-01-11"}],
                },
            },
        }
        fields = UCPResponseParser.extract(body)
        caps = json.loads(fields["capabilities_json"])
        assert len(caps) == 2
        names = [c["name"] for c in caps]
        assert "dev.ucp.shopping.checkout" in names

    def test_extract_payment_instruments(self):
        """Spec: payment.instruments[] with handler_id."""
        body = {
            "payment": {
                "instruments": [
                    {
                        "id": "instr_1",
                        "handler_id": "com.stripe",
                        "type": "card",
                        "brand": "visa",
                    },
                ]
            }
        }
        fields = UCPResponseParser.extract(body)
        assert fields["payment_handler_id"] == "com.stripe"
        assert fields["payment_instrument_type"] == "card"
        assert fields["payment_brand"] == "visa"

    def test_extract_payment_handlers_only(self):
        """Checkout payment with only handlers (no instruments selected yet)."""
        body = {
            "payment": {
                "handlers": [
                    {
                        "id": "gpay",
                        "name": "google.pay",
                        "version": "2026-01-11",
                        "spec": "https://example.com",
                        "config_schema": "https://example.com",
                        "instrument_schemas": [],
                        "config": {},
                    },
                ]
            }
        }
        fields = UCPResponseParser.extract(body)
        assert fields["payment_handler_id"] == "gpay"

    def test_extract_discovery_payment_handlers(self):
        """SDK: discovery has payment.handlers at top level (sibling of ucp)."""
        body = {
            "ucp": {
                "version": "2026-01-11",
                "capabilities": [
                    {"name": "dev.ucp.shopping.checkout", "version": "2026-01-11"},
                ],
            },
            "payment": {
                "handlers": [
                    {
                        "id": "mock_handler",
                        "name": "com.mock.payment",
                        "version": "2026-01-11",
                    },
                ],
            },
        }
        fields = UCPResponseParser.extract(body)
        assert fields["payment_handler_id"] == "mock_handler"
        assert fields["ucp_version"] == "2026-01-11"

    def test_extract_payment_data(self):
        """payment_data from complete request/response."""
        body = {
            "payment_data": {
                "handler_id": "com.stripe",
                "type": "card",
                "brand": "visa",
            }
        }
        fields = UCPResponseParser.extract(body)
        assert fields["payment_handler_id"] == "com.stripe"

    def test_extract_discounts(self):
        """Spec: discount extension with codes and applied."""
        body = {
            "discounts": {
                "codes": ["SAVE10", "LOYALTY"],
                "applied": [
                    {"code": "SAVE10", "title": "Save 10%", "amount": 1000},
                ],
            }
        }
        fields = UCPResponseParser.extract(body)
        codes = json.loads(fields["discount_codes_json"])
        assert codes == ["SAVE10", "LOYALTY"]
        applied = json.loads(fields["discount_applied_json"])
        assert applied[0]["code"] == "SAVE10"

    def test_extract_order_confirmation_in_checkout(self):
        """Spec: checkout.order is a nested object with id and permalink_url."""
        body = {
            "id": "chk_123",
            "status": "completed",
            "order": {
                "id": "order_abc",
                "permalink_url": "https://shop.example.com/orders/order_abc",
            },
        }
        fields = UCPResponseParser.extract(body)
        assert fields["order_id"] == "order_abc"
        assert fields["permalink_url"] == ("https://shop.example.com/orders/order_abc")

    def test_extract_order_object(self):
        """Order with checkout_id and permalink_url."""
        order = {
            "id": "order_xyz",
            "checkout_id": "chk_abc",
            "status": "shipped",
            "permalink_url": "https://shop.example.com/orders/order_xyz",
            "fulfillment": {
                "expectations": [
                    {
                        "method_type": "shipping",
                        "status": "shipped",
                        "destination": {
                            "address_country": "US",
                            "postal_code": "94043",
                        },
                        "line_items": [{"id": "li_1", "quantity": 1}],
                    },
                ],
            },
        }
        fields = UCPResponseParser.extract(order)
        assert fields["order_id"] == "order_xyz"
        assert fields["checkout_session_id"] == "chk_abc"
        assert fields["permalink_url"] == ("https://shop.example.com/orders/order_xyz")
        assert fields["fulfillment_type"] == "shipping"
        assert fields["fulfillment_destination_country"] == "US"

    def test_extract_totals_all_spec_types(self):
        """All 7 spec total types are extracted."""
        body = {
            "totals": [
                {"type": "items_discount", "amount": 200},
                {"type": "subtotal", "amount": 5000},
                {"type": "discount", "amount": 500},
                {"type": "fulfillment", "amount": 599},
                {"type": "tax", "amount": 400},
                {"type": "fee", "amount": 100},
                {"type": "total", "amount": 5399},
            ]
        }
        fields = UCPResponseParser.extract(body)
        assert fields["items_discount_amount"] == 200
        assert fields["subtotal_amount"] == 5000
        assert fields["discount_amount"] == 500
        assert fields["fulfillment_amount"] == 599
        assert fields["tax_amount"] == 400
        assert fields["fee_amount"] == 100
        assert fields["total_amount"] == 5399

    def test_extract_continue_url(self):
        """continue_url for escalation."""
        body = {
            "status": "requires_escalation",
            "continue_url": "https://shop.example.com/checkout/escalate",
        }
        fields = UCPResponseParser.extract(body)
        assert fields["continue_url"] == ("https://shop.example.com/checkout/escalate")

    def test_extract_identity_fields(self):
        """Identity provider and scope from response body."""
        body = {"provider": "google", "scope": "openid email"}
        fields = UCPResponseParser.extract(body)
        assert fields["identity_provider"] == "google"
        assert fields["identity_scope"] == "openid email"

    def test_extract_identity_nested(self):
        """Identity fields from nested identity object."""
        body = {"identity": {"provider": "github", "scope": "read:user"}}
        fields = UCPResponseParser.extract(body)
        assert fields["identity_provider"] == "github"
        assert fields["identity_scope"] == "read:user"

    def test_extract_empty(self):
        assert UCPResponseParser.extract(None) == {}
        assert UCPResponseParser.extract({}) == {}


class TestClassifyJsonRPC:
    """Tests for classify_jsonrpc() — MCP and A2A tool name mapping."""

    def test_mcp_create_checkout(self):
        assert (
            UCPResponseParser.classify_jsonrpc("create_checkout")
            == UCPEventType.CHECKOUT_SESSION_CREATED
        )

    def test_mcp_complete_checkout(self):
        assert (
            UCPResponseParser.classify_jsonrpc("complete_checkout")
            == UCPEventType.CHECKOUT_SESSION_COMPLETED
        )

    def test_mcp_cancel_checkout(self):
        assert (
            UCPResponseParser.classify_jsonrpc("cancel_checkout")
            == UCPEventType.CHECKOUT_SESSION_CANCELED
        )

    def test_mcp_discover(self):
        assert (
            UCPResponseParser.classify_jsonrpc("discover_merchant")
            == UCPEventType.PROFILE_DISCOVERED
        )

    def test_mcp_create_cart(self):
        assert (
            UCPResponseParser.classify_jsonrpc("create_cart")
            == UCPEventType.CART_CREATED
        )

    def test_mcp_create_order(self):
        assert (
            UCPResponseParser.classify_jsonrpc("create_order")
            == UCPEventType.ORDER_CREATED
        )

    def test_mcp_get_order_delivered(self):
        assert (
            UCPResponseParser.classify_jsonrpc(
                "get_order", 200, {"status": "delivered"}
            )
            == UCPEventType.ORDER_DELIVERED
        )

    def test_a2a_checkout_create(self):
        assert (
            UCPResponseParser.classify_jsonrpc("a2a.ucp.checkout.create")
            == UCPEventType.CHECKOUT_SESSION_CREATED
        )

    def test_a2a_checkout_complete(self):
        assert (
            UCPResponseParser.classify_jsonrpc("a2a.ucp.checkout.complete")
            == UCPEventType.CHECKOUT_SESSION_COMPLETED
        )

    def test_a2a_identity_link(self):
        assert (
            UCPResponseParser.classify_jsonrpc("a2a.ucp.identity.link")
            == UCPEventType.IDENTITY_LINK_INITIATED
        )

    def test_a2a_identity_revoke(self):
        assert (
            UCPResponseParser.classify_jsonrpc("a2a.ucp.identity.revoke")
            == UCPEventType.IDENTITY_LINK_REVOKED
        )

    def test_negotiate_capability(self):
        assert (
            UCPResponseParser.classify_jsonrpc("negotiate_capability")
            == UCPEventType.CAPABILITY_NEGOTIATED
        )

    def test_a2a_capability_negotiate(self):
        assert (
            UCPResponseParser.classify_jsonrpc("a2a.ucp.capability.negotiate")
            == UCPEventType.CAPABILITY_NEGOTIATED
        )

    def test_add_to_checkout(self):
        assert (
            UCPResponseParser.classify_jsonrpc("add_to_checkout")
            == UCPEventType.CHECKOUT_SESSION_UPDATED
        )

    def test_remove_from_checkout(self):
        assert (
            UCPResponseParser.classify_jsonrpc("remove_from_checkout")
            == UCPEventType.CHECKOUT_SESSION_UPDATED
        )

    def test_start_payment(self):
        assert (
            UCPResponseParser.classify_jsonrpc("start_payment")
            == UCPEventType.CHECKOUT_SESSION_UPDATED
        )

    def test_update_customer_details(self):
        assert (
            UCPResponseParser.classify_jsonrpc("update_customer_details")
            == UCPEventType.CHECKOUT_SESSION_UPDATED
        )

    def test_unknown_tool(self):
        assert UCPResponseParser.classify_jsonrpc("get_weather") == UCPEventType.REQUEST


class TestWebhookClassification:
    """Tests for upstream partner webhook path classification."""

    def test_partner_webhook_shipped_via_request_body(self):
        """Upstream: order payload is in request_body, response is ack."""
        order = {"id": "order_1", "checkout_id": "chk_1", "status": "shipped"}
        assert (
            UCPResponseParser.classify(
                "POST",
                "/webhooks/partners/p1/events/order",
                200,
                {"status": "ok"},
                request_body=order,
            )
            == UCPEventType.ORDER_SHIPPED
        )

    def test_partner_webhook_delivered_via_request_body(self):
        order = {"id": "order_1", "checkout_id": "chk_1", "status": "delivered"}
        assert (
            UCPResponseParser.classify(
                "POST",
                "/webhooks/partners/p1/events/order",
                200,
                {"status": "ok"},
                request_body=order,
            )
            == UCPEventType.ORDER_DELIVERED
        )

    def test_partner_webhook_returned(self):
        assert (
            UCPResponseParser.classify(
                "POST",
                "/webhooks/partners/p1/events/order",
                200,
                {"status": "returned"},
            )
            == UCPEventType.ORDER_RETURNED
        )

    def test_partner_webhook_canceled(self):
        assert (
            UCPResponseParser.classify(
                "POST",
                "/webhooks/partners/p1/events/order",
                200,
                {"status": "canceled"},
            )
            == UCPEventType.ORDER_CANCELED
        )

    def test_partner_webhook_cancelled_british(self):
        assert (
            UCPResponseParser.classify(
                "POST",
                "/webhooks/partners/p1/events/order",
                200,
                {"status": "cancelled"},
            )
            == UCPEventType.ORDER_CANCELED
        )

    def test_partner_webhook_no_body(self):
        assert (
            UCPResponseParser.classify(
                "POST",
                "/webhooks/partners/p1/events/order",
                200,
                None,
            )
            == UCPEventType.ORDER_UPDATED
        )

    def test_generic_webhook_fallback(self):
        assert (
            UCPResponseParser.classify(
                "POST", "/webhooks/some-other-event", 200, {}
            )
            == UCPEventType.ORDER_UPDATED
        )

    def test_webhook_error_500(self):
        """Webhook 5xx should classify as error, not order_updated."""
        assert (
            UCPResponseParser.classify(
                "POST", "/webhooks/partners/p1/events/order", 500, {}
            )
            == UCPEventType.ERROR
        )

    def test_webhook_error_400(self):
        """Webhook 4xx should classify as error."""
        assert (
            UCPResponseParser.classify(
                "POST", "/webhooks/some-event", 400, {}
            )
            == UCPEventType.ERROR
        )


class TestCheckoutStatusScoping:
    """Tests that checkout_status is only set for checkout responses."""

    def test_checkout_status_set_for_checkout(self):
        body = {"id": "chk_123", "status": "completed"}
        fields = UCPResponseParser.extract(body)
        assert fields["checkout_status"] == "completed"

    def test_checkout_status_not_set_for_order(self):
        body = {
            "id": "order_xyz",
            "checkout_id": "chk_abc",
            "status": "shipped",
        }
        fields = UCPResponseParser.extract(body)
        assert "checkout_status" not in fields

    def test_checkout_status_not_set_for_unknown_status(self):
        body = {"id": "cart_abc", "status": "active"}
        fields = UCPResponseParser.extract(body)
        assert "checkout_status" not in fields

    def test_checkout_status_requires_escalation(self):
        body = {"id": "chk_123", "status": "requires_escalation"}
        fields = UCPResponseParser.extract(body)
        assert fields["checkout_status"] == "requires_escalation"
