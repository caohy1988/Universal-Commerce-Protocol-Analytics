"""Tests for UCPResponseParser."""

import pytest
from ucp_analytics.parser import UCPResponseParser
from ucp_analytics.events import UCPEventType


class TestClassify:
    def test_discovery(self):
        assert UCPResponseParser.classify(
            "GET", "/.well-known/ucp", 200, None
        ) == UCPEventType.PROFILE_DISCOVERED

    def test_create_checkout(self):
        assert UCPResponseParser.classify(
            "POST", "/checkout-sessions", 201, {}
        ) == UCPEventType.CHECKOUT_SESSION_CREATED

    def test_update_checkout(self):
        assert UCPResponseParser.classify(
            "PUT", "/checkout-sessions/chk_123", 200, {"status": "ready_for_complete"}
        ) == UCPEventType.CHECKOUT_SESSION_UPDATED

    def test_update_escalation(self):
        assert UCPResponseParser.classify(
            "PUT", "/checkout-sessions/chk_123", 200, {"status": "requires_escalation"}
        ) == UCPEventType.CHECKOUT_ESCALATION

    def test_complete_checkout(self):
        assert UCPResponseParser.classify(
            "POST", "/checkout-sessions/chk_123/complete", 200, {}
        ) == UCPEventType.CHECKOUT_SESSION_COMPLETED

    def test_cancel_checkout(self):
        assert UCPResponseParser.classify(
            "POST", "/checkout-sessions/chk_123/cancel", 200, {}
        ) == UCPEventType.CHECKOUT_SESSION_CANCELED

    def test_get_checkout(self):
        assert UCPResponseParser.classify(
            "GET", "/checkout-sessions/chk_123", 200, {}
        ) == UCPEventType.CHECKOUT_SESSION_GET

    def test_error(self):
        assert UCPResponseParser.classify(
            "POST", "/checkout-sessions", 500, {}
        ) == UCPEventType.ERROR

    def test_order(self):
        assert UCPResponseParser.classify(
            "POST", "/orders", 201, {}
        ) == UCPEventType.ORDER_CREATED

    def test_simulate_shipping(self):
        assert UCPResponseParser.classify(
            "POST", "/testing/simulate-shipping/order_123", 200, {}
        ) == UCPEventType.ORDER_SHIPPED


class TestExtract:
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
            {"id": "li_1", "item": {"id": "item_1", "title": "Rose Bouquet", "price": 2500}, "quantity": 2},
        ],
        "totals": [
            {"type": "subtotal", "amount": 5000},
            {"type": "tax", "amount": 400},
            {"type": "shipping", "amount": 599},
            {"type": "total", "amount": 5999},
        ],
        "payment": {
            "handlers": [
                {"id": "gpay", "type": "wallet", "brand": "google_pay"},
            ]
        },
        "fulfillment": {
            "methods": [
                {
                    "type": "shipping",
                    "destinations": [
                        {"address_country": "US", "postal_code": "94043"},
                    ],
                }
            ]
        },
        "messages": [
            {"type": "error", "code": "missing", "content": "Phone required", "severity": "recoverable"},
        ],
    }

    def test_extract_checkout_fields(self):
        fields = UCPResponseParser.extract(self.SAMPLE_CHECKOUT_RESPONSE)

        assert fields["checkout_session_id"] == "chk_abc123"
        assert fields["checkout_status"] == "ready_for_complete"
        assert fields["currency"] == "USD"
        assert fields["subtotal_amount"] == 5000
        assert fields["tax_amount"] == 400
        assert fields["shipping_amount"] == 599
        assert fields["total_amount"] == 5999
        assert fields["line_item_count"] == 1
        assert fields["ucp_version"] == "2026-01-11"
        assert fields["payment_handler_id"] == "gpay"
        assert fields["fulfillment_type"] == "shipping"
        assert fields["fulfillment_destination_country"] == "US"
        assert fields["error_code"] == "missing"
        assert fields["error_severity"] == "recoverable"

    def test_extract_extensions(self):
        fields = UCPResponseParser.extract(self.SAMPLE_CHECKOUT_RESPONSE)
        assert "extensions_json" in fields
        assert "fulfillment" in fields["extensions_json"]

    def test_extract_empty(self):
        assert UCPResponseParser.extract(None) == {}
        assert UCPResponseParser.extract({}) == {}

    def test_extract_order_object(self):
        order = {
            "id": "order_xyz",
            "checkout_id": "chk_abc",
            "status": "shipped",
        }
        fields = UCPResponseParser.extract(order)
        assert fields["order_id"] == "order_xyz"
        assert fields["checkout_session_id"] == "chk_abc"
