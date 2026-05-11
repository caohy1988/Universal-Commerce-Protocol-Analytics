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

    def test_catalog_search(self):
        assert (
            UCPResponseParser.classify("POST", "/catalog/search", 200, {})
            == UCPEventType.CATALOG_SEARCH
        )

    def test_catalog_lookup(self):
        assert (
            UCPResponseParser.classify("POST", "/catalog/lookup", 200, {})
            == UCPEventType.CATALOG_LOOKUP
        )

    def test_catalog_product_get(self):
        assert (
            UCPResponseParser.classify("POST", "/catalog/product", 200, {})
            == UCPEventType.CATALOG_PRODUCT_GET
        )

    def test_catalog_search_under_base_path(self):
        # OpenAPI paths are relative to the discovered REST endpoint;
        # real merchants commonly mount UCP under /ucp/v1, /api/v2, …
        assert (
            UCPResponseParser.classify("POST", "/ucp/v1/catalog/search", 200, {})
            == UCPEventType.CATALOG_SEARCH
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

    # --- OAuth 2.0 + OpenID identity-linking flow ---

    def test_oauth_authorization_metadata_discovery(self):
        # RFC 8414 — auth-server metadata discovery is the start of an
        # identity-linking flow.
        assert (
            UCPResponseParser.classify(
                "GET", "/.well-known/oauth-authorization-server", 200, {}
            )
            == UCPEventType.IDENTITY_LINK_INITIATED
        )

    def test_openid_configuration_discovery(self):
        assert (
            UCPResponseParser.classify(
                "GET", "/.well-known/openid-configuration", 200, {}
            )
            == UCPEventType.IDENTITY_LINK_INITIATED
        )

    def test_oauth_protected_resource_discovery(self):
        # RFC 9728 — protected-resource metadata.
        assert (
            UCPResponseParser.classify(
                "GET", "/.well-known/oauth-protected-resource", 200, {}
            )
            == UCPEventType.IDENTITY_LINK_INITIATED
        )

    def test_oauth_discovery_under_mounted_base(self):
        # OAuth discovery paths can be mounted under a prefix the same
        # way other UCP REST paths can.
        assert (
            UCPResponseParser.classify(
                "GET",
                "/api/v1/.well-known/oauth-authorization-server",
                200,
                {},
            )
            == UCPEventType.IDENTITY_LINK_INITIATED
        )

    def test_oauth2_authorize_initiated(self):
        assert (
            UCPResponseParser.classify("GET", "/oauth2/authorize", 200, {})
            == UCPEventType.IDENTITY_LINK_INITIATED
        )

    def test_oauth2_token_completed(self):
        # Token issuance is the moment the identity link becomes usable.
        assert (
            UCPResponseParser.classify("POST", "/oauth2/token", 200, {})
            == UCPEventType.IDENTITY_LINK_COMPLETED
        )

    def test_oauth2_revoke_revoked(self):
        assert (
            UCPResponseParser.classify("POST", "/oauth2/revoke", 200, {})
            == UCPEventType.IDENTITY_LINK_REVOKED
        )

    def test_oauth2_jwks_initiated(self):
        # JWKS endpoint is part of the OAuth metadata surface — fetch
        # before any token validation. Keep it under INITIATED rather
        # than minting a separate event type for v0.
        assert (
            UCPResponseParser.classify("GET", "/oauth2/jwks", 200, {})
            == UCPEventType.IDENTITY_LINK_INITIATED
        )

    def test_oauth2_token_under_mounted_base(self):
        assert (
            UCPResponseParser.classify("POST", "/api/v1/oauth2/token", 200, {})
            == UCPEventType.IDENTITY_LINK_COMPLETED
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

    # --- Context (request-body Context object) ---

    def test_extract_context_on_checkout_create(self):
        """Checkout-create requests carry a top-level `context` object
        with intent, language, currency, and eligibility per
        source/schemas/shopping/types/context.json."""
        body = {
            "context": {
                "intent": "buy a birthday gift for mom",
                "language": "en-US",
                "currency": "USD",
                "eligibility": [
                    "dev.example.loyalty_member",
                    "dev.example.first_time_buyer",
                ],
            },
            "line_items": [
                {"item": {"id": "sku_rose"}, "quantity": 1},
            ],
        }
        fields = UCPResponseParser.extract(body)
        assert fields["context_intent"] == "buy a birthday gift for mom"
        assert fields["context_language"] == "en-US"
        assert fields["context_currency"] == "USD"
        assert "dev.example.loyalty_member" in fields["context_eligibility_json"]
        assert "dev.example.first_time_buyer" in fields["context_eligibility_json"]

    def test_extract_context_on_cart_create(self):
        """Cart-create requests carry the same Context object shape."""
        body = {
            "context": {
                "intent": "stock up for the week",
                "language": "fr-CA",
                "currency": "CAD",
            },
            "line_items": [{"item": {"id": "sku_milk"}, "quantity": 2}],
        }
        fields = UCPResponseParser.extract(body)
        assert fields["context_intent"] == "stock up for the week"
        assert fields["context_language"] == "fr-CA"
        assert fields["context_currency"] == "CAD"
        # No eligibility on this body — the column should be absent.
        assert "context_eligibility_json" not in fields

    def test_extract_context_on_catalog_search(self):
        """Catalog-search requests use the same Context shape; intent
        in particular is the high-value signal for relevance analytics."""
        body = {
            "context": {
                "intent": "vegan running shoes under 100 dollars",
                "language": "en",
                "currency": "USD",
            },
            "query": "running shoes",
        }
        fields = UCPResponseParser.extract(body)
        assert fields["context_intent"] == "vegan running shoes under 100 dollars"
        assert fields["context_language"] == "en"
        assert fields["context_currency"] == "USD"

    def test_extract_context_partial(self):
        """A context object may carry only a subset of properties; we
        only populate the columns that are present in the source body."""
        body = {"context": {"language": "ja-JP"}}
        fields = UCPResponseParser.extract(body)
        assert fields["context_language"] == "ja-JP"
        assert "context_intent" not in fields
        assert "context_currency" not in fields
        assert "context_eligibility_json" not in fields

    def test_extract_context_address_fields_not_captured_yet(self):
        """Address fields on context (address_country, address_region,
        postal_code) are PII and intentionally deferred to a later slice
        with the redaction policy. This test pins that current behavior
        so no one accidentally surfaces them as scalar columns without
        also wiring redaction."""
        body = {
            "context": {
                "intent": "ship to office",
                "address_country": "US",
                "address_region": "CA",
                "postal_code": "94043",
            },
        }
        fields = UCPResponseParser.extract(body)
        assert fields["context_intent"] == "ship to office"
        assert "context_address_country" not in fields
        assert "context_address_region" not in fields
        assert "context_postal_code" not in fields

    def test_extract_no_context_object(self):
        """Bodies without a context object don't populate any of the
        new columns."""
        body = {"id": "chk_123", "status": "ready_for_complete"}
        fields = UCPResponseParser.extract(body)
        assert "context_intent" not in fields
        assert "context_language" not in fields
        assert "context_currency" not in fields
        assert "context_eligibility_json" not in fields

    # --- payment_handlers[*].available_instruments ---

    def test_extract_payment_available_instruments_array_shape(self):
        """body.ucp.payment_handlers as an array of handler objects."""
        body = {
            "ucp": {
                "version": "2026-04-08",
                "payment_handlers": [
                    {
                        "id": "gpay",
                        "name": "Google Pay",
                        "available_instruments": [
                            {"type": "card", "brand": "visa"},
                            {"type": "card", "brand": "mastercard"},
                        ],
                    },
                    {
                        "id": "stripe",
                        "name": "Stripe",
                        "available_instruments": [
                            {"type": "card", "brand": "amex"},
                        ],
                    },
                ],
            },
        }
        fields = UCPResponseParser.extract(body)
        instruments = json.loads(fields["payment_available_instruments_json"])
        assert len(instruments) == 2
        gpay = next(h for h in instruments if h.get("id") == "gpay")
        assert len(gpay["available_instruments"]) == 2
        stripe = next(h for h in instruments if h.get("id") == "stripe")
        assert stripe["available_instruments"][0]["brand"] == "amex"

    def test_extract_payment_available_instruments_dict_keyed_shape(self):
        """body.ucp.payment_handlers as a dict keyed by handler name —
        the same registry shape capabilities use."""
        body = {
            "ucp": {
                "version": "2026-04-08",
                "payment_handlers": {
                    "dev.example.gpay": [
                        {
                            "id": "gpay",
                            "available_instruments": [{"type": "wallet"}],
                        }
                    ],
                },
            },
        }
        fields = UCPResponseParser.extract(body)
        instruments = json.loads(fields["payment_available_instruments_json"])
        assert len(instruments) == 1
        # _normalize_registry stamps the dict key as `name` on the entry.
        assert instruments[0]["name"] == "dev.example.gpay"
        assert instruments[0]["available_instruments"][0]["type"] == "wallet"

    def test_extract_payment_handlers_without_instruments_dropped(self):
        """A handler that doesn't declare available_instruments isn't
        useful for the instruments-offered KPI; drop it from the
        stored payload so the column reflects only handlers that
        publish a registry."""
        body = {
            "ucp": {
                "payment_handlers": [
                    {"id": "gpay", "available_instruments": [{"type": "card"}]},
                    {"id": "applepay"},  # no available_instruments
                    {"id": "stripe", "available_instruments": []},  # empty
                ],
            },
        }
        fields = UCPResponseParser.extract(body)
        instruments = json.loads(fields["payment_available_instruments_json"])
        assert len(instruments) == 1
        assert instruments[0]["id"] == "gpay"

    def test_extract_payment_handlers_with_malformed_instruments_dropped(
        self,
    ):
        """payment_handler.json defines available_instruments as an
        array. Any other shape (string, dict, scalar) is malformed
        and would break dashboard assumptions around
        JSON_QUERY_ARRAY(...available_instruments...). Drop the
        handler so a single bad sender can't corrupt the column
        contract for everyone querying the table."""
        body = {
            "ucp": {
                "payment_handlers": [
                    {"id": "gpay", "available_instruments": [{"type": "card"}]},
                    # String instead of array — malformed.
                    {"id": "bad-string", "available_instruments": "card"},
                    # Dict instead of array — malformed.
                    {"id": "bad-dict", "available_instruments": {"type": "card"}},
                    # Scalar truthy values that pre-fix would have
                    # survived `if h.get(...)`.
                    {"id": "bad-int", "available_instruments": 1},
                    {"id": "bad-bool", "available_instruments": True},
                ],
            },
        }
        fields = UCPResponseParser.extract(body)
        instruments = json.loads(fields["payment_available_instruments_json"])
        assert len(instruments) == 1
        assert instruments[0]["id"] == "gpay"

    def test_extract_payment_available_instruments_does_not_misread_payment_object(
        self,
    ):
        """The new column sources from body.ucp.payment_handlers, not
        body.payment.handlers — the latter is selected/submitted
        instruments on a checkout response, not the handler-declaration
        registry. Pin this so the column doesn't accidentally start
        sourcing from the wrong path."""
        body = {
            "ucp": {"version": "2026-04-08"},
            # body.payment.handlers — NOT the source for this column.
            "payment": {
                "handlers": [{"id": "gpay", "type": "wallet", "brand": "google_pay"}]
            },
        }
        fields = UCPResponseParser.extract(body)
        # Existing payment_handler_id still extracted for backwards compat.
        assert fields["payment_handler_id"] == "gpay"
        # But the new available-instruments column stays absent because
        # ucp.payment_handlers is missing.
        assert "payment_available_instruments_json" not in fields

    def test_extract_no_payment_handlers_in_ucp(self):
        """No ucp.payment_handlers → column absent."""
        body = {"ucp": {"version": "2026-04-08"}}
        fields = UCPResponseParser.extract(body)
        assert "payment_available_instruments_json" not in fields

    # --- messages[]: per-severity code lists + identity_optional ---

    def test_extract_message_info_codes(self):
        """Info-severity codes collect into message_info_codes_json,
        order-preserved and deduped."""
        body = {
            "messages": [
                {"type": "info", "code": "tax_rounded_up"},
                {"type": "info", "code": "identity_optional"},
                {"type": "info", "code": "tax_rounded_up"},  # duplicate
            ]
        }
        fields = UCPResponseParser.extract(body)
        codes = json.loads(fields["message_info_codes_json"])
        assert codes == ["tax_rounded_up", "identity_optional"]
        assert fields["identity_optional_present"] is True

    def test_extract_message_warning_codes(self):
        body = {
            "messages": [
                {"type": "warning", "code": "shipping_delayed"},
                {"type": "warning", "code": "stock_low"},
                {"type": "warning", "code": "shipping_delayed"},  # duplicate
            ]
        }
        fields = UCPResponseParser.extract(body)
        codes = json.loads(fields["message_warning_codes_json"])
        assert codes == ["shipping_delayed", "stock_low"]
        # No info codes → no flag.
        assert "identity_optional_present" not in fields

    def test_extract_mixed_severities_in_one_pass(self):
        """A real checkout response carries multiple severities at once.
        Single-pass walk must populate all three columns plus the
        legacy error_code from the first error."""
        body = {
            "messages": [
                {
                    "type": "info",
                    "code": "tax_rounded_up",
                    "content": "Tax rounded up by $0.01",
                },
                {
                    "type": "warning",
                    "code": "shipping_delayed",
                    "content": "Shipping may be delayed",
                },
                {
                    "type": "error",
                    "code": "missing_email",
                    "content": "Email is required",
                    "severity": "recoverable",
                },
                {"type": "info", "code": "identity_optional"},
                {
                    "type": "error",
                    "code": "should_not_overwrite_first_error",
                    "content": "...",
                },
            ]
        }
        fields = UCPResponseParser.extract(body)
        # Legacy error_* columns: first error only.
        assert fields["error_code"] == "missing_email"
        assert fields["error_severity"] == "recoverable"
        # Per-severity lists.
        info = json.loads(fields["message_info_codes_json"])
        assert info == ["tax_rounded_up", "identity_optional"]
        warnings = json.loads(fields["message_warning_codes_json"])
        assert warnings == ["shipping_delayed"]
        # Identity-optional flag picked up from the info pass.
        assert fields["identity_optional_present"] is True

    def test_identity_optional_present_false_when_other_info_codes(self):
        """Three-state semantics: when info codes exist but none is
        identity_optional, the flag must be False — not NULL. NULL is
        reserved for rows with no info codes at all (no denominator
        contribution). Without this distinction the C11 KPI denominator
        would conflate 'observed unsigned' with 'never observed'."""
        body = {
            "messages": [
                {"type": "info", "code": "tax_rounded_up"},
                {"type": "info", "code": "shipping_estimated"},
            ]
        }
        fields = UCPResponseParser.extract(body)
        # info codes observed → flag must land as a concrete BOOL.
        assert fields["identity_optional_present"] is False
        # And the codes themselves are still in the JSON column.
        codes = json.loads(fields["message_info_codes_json"])
        assert codes == ["tax_rounded_up", "shipping_estimated"]

    def test_identity_optional_flag_only_on_info_severity(self):
        """The convenience flag matches the info-code 'identity_optional'
        specifically; an error/warning code with the same string is a
        different signal and should NOT trip the flag."""
        body = {
            "messages": [
                {"type": "warning", "code": "identity_optional"},
                {"type": "error", "code": "identity_optional"},
            ]
        }
        fields = UCPResponseParser.extract(body)
        # info_codes empty → flag absent.
        assert "identity_optional_present" not in fields
        assert "message_info_codes_json" not in fields
        warnings = json.loads(fields["message_warning_codes_json"])
        assert warnings == ["identity_optional"]

    def test_extract_messages_only_errors_no_info_warning_columns(self):
        body = {
            "messages": [
                {
                    "type": "error",
                    "code": "missing_phone",
                    "content": "Phone is required",
                },
            ]
        }
        fields = UCPResponseParser.extract(body)
        assert fields["error_code"] == "missing_phone"
        assert "message_info_codes_json" not in fields
        assert "message_warning_codes_json" not in fields
        assert "identity_optional_present" not in fields

    def test_extract_messages_skips_malformed_codes(self):
        """Non-string or empty codes are dropped from the per-severity
        lists. A single bad sender shouldn't pollute the column with
        unfilterable values."""
        body = {
            "messages": [
                {"type": "info", "code": "valid_code"},
                {"type": "info", "code": ""},  # empty string
                {"type": "info", "code": None},  # null
                {"type": "info", "code": 42},  # non-string
                {"type": "info"},  # missing code
                {"type": "info", "code": "another_valid_code"},
            ]
        }
        fields = UCPResponseParser.extract(body)
        codes = json.loads(fields["message_info_codes_json"])
        assert codes == ["valid_code", "another_valid_code"]

    def test_extract_no_messages_no_columns(self):
        body = {"id": "chk_123", "status": "ready_for_complete"}
        fields = UCPResponseParser.extract(body)
        assert "message_info_codes_json" not in fields
        assert "message_warning_codes_json" not in fields
        assert "identity_optional_present" not in fields
        assert "messages_json" not in fields
        # No messages → no eligibility flag denominator, all three NULL.
        assert "eligibility_accepted_present" not in fields
        assert "eligibility_not_accepted_present" not in fields
        assert "eligibility_invalid_present" not in fields

    # --- A5: eligibility verification outcome (info + error severity) ---

    def test_eligibility_accepted_sets_accepted_true_others_false(self):
        """`eligibility_accepted` as info severity (the typical shape)
        sets accepted=TRUE and the other two FALSE — the trio is
        mutually exclusive in well-formed responses, so when one fires
        the dashboard can read FALSE for the other two as a concrete
        signal, not 'unknown'."""
        body = {
            "messages": [
                {
                    "type": "info",
                    "code": "eligibility_accepted",
                    "content": "Loyalty member discount applies",
                },
            ]
        }
        fields = UCPResponseParser.extract(body)
        assert fields["eligibility_accepted_present"] is True
        assert fields["eligibility_not_accepted_present"] is False
        assert fields["eligibility_invalid_present"] is False

    def test_eligibility_not_accepted_sets_only_that_flag_true(self):
        body = {
            "messages": [
                {
                    "type": "info",
                    "code": "eligibility_not_accepted",
                    "content": "Item not eligible for promo",
                },
            ]
        }
        fields = UCPResponseParser.extract(body)
        assert fields["eligibility_accepted_present"] is False
        assert fields["eligibility_not_accepted_present"] is True
        assert fields["eligibility_invalid_present"] is False

    def test_eligibility_invalid_walked_from_error_severity(self):
        """`eligibility_invalid` is canonically `error` severity in
        upstream's `error_code` enum, while the other two are
        typically `info`. The cross-severity walk must pick this code
        up from the error message — without that, every
        `eligibility_invalid` row would underpopulate the trio.
        Pin that the legacy error_code column also still gets
        populated from the same message."""
        body = {
            "messages": [
                {
                    "type": "error",
                    "code": "eligibility_invalid",
                    "content": "Eligibility claim malformed",
                    "severity": "recoverable",
                },
            ]
        }
        fields = UCPResponseParser.extract(body)
        assert fields["eligibility_accepted_present"] is False
        assert fields["eligibility_not_accepted_present"] is False
        assert fields["eligibility_invalid_present"] is True
        # Legacy first-error column still populated from the same msg.
        assert fields["error_code"] == "eligibility_invalid"

    def test_eligibility_outcome_codes_independent_of_other_codes(self):
        """A non-eligibility info code in the same response must NOT
        falsely populate the trio. The denominator is "an eligibility
        outcome code was observed", not "any info code was observed"
        — otherwise every checkout that ships `tax_rounded_up` would
        report eligibility_*_present = FALSE for all three, polluting
        the KPI."""
        body = {
            "messages": [
                {"type": "info", "code": "tax_rounded_up"},
                {"type": "info", "code": "identity_optional"},
            ]
        }
        fields = UCPResponseParser.extract(body)
        assert "eligibility_accepted_present" not in fields
        assert "eligibility_not_accepted_present" not in fields
        assert "eligibility_invalid_present" not in fields

    def test_eligibility_no_codes_leaves_all_three_null(self):
        """No eligibility outcome code in messages → all three NULL,
        not FALSE. NULL is reserved for 'verification did not
        surface', which is a different signal from 'verification ran
        and a different outcome fired'."""
        body = {
            "messages": [
                {"type": "warning", "code": "shipping_delayed"},
            ],
            "context": {
                "eligibility": [{"claim": "loyalty_member"}],
            },
        }
        fields = UCPResponseParser.extract(body)
        # Even with context.eligibility authored, absence of an
        # outcome code keeps the trio NULL. The eligibility claim
        # payload lives in its own column.
        assert "eligibility_accepted_present" not in fields
        assert "eligibility_not_accepted_present" not in fields
        assert "eligibility_invalid_present" not in fields
        assert fields["context_eligibility_json"] == json.dumps(
            [{"claim": "loyalty_member"}]
        )

    def test_eligibility_duplicate_codes_collapse(self):
        """Set-based capture: duplicate codes don't change the
        outcome. Pin that a sender that emits the same eligibility
        code twice produces the same flag values as a single
        emission — no quadratic JSON growth, no flicker."""
        body = {
            "messages": [
                {"type": "info", "code": "eligibility_accepted"},
                {"type": "info", "code": "eligibility_accepted"},
            ]
        }
        fields = UCPResponseParser.extract(body)
        assert fields["eligibility_accepted_present"] is True
        assert fields["eligibility_not_accepted_present"] is False
        assert fields["eligibility_invalid_present"] is False

    def test_eligibility_two_codes_simultaneously_both_true(self):
        """The trio is mutually exclusive in well-formed responses,
        but if a malformed sender ships two outcome codes in the
        same row we record what they sent — both flags True, the
        third False. Analysts can detect the conflict via
        messages_json. This is the signal-fidelity guarantee:
        analytics records observed reality, not normalized reality."""
        body = {
            "messages": [
                {"type": "info", "code": "eligibility_accepted"},
                {
                    "type": "error",
                    "code": "eligibility_invalid",
                    "content": "Claim invalidated downstream",
                },
            ]
        }
        fields = UCPResponseParser.extract(body)
        assert fields["eligibility_accepted_present"] is True
        assert fields["eligibility_invalid_present"] is True
        assert fields["eligibility_not_accepted_present"] is False

    def test_eligibility_malformed_messages_skipped(self):
        """Non-string codes / missing code field don't crash and
        don't pollute the eligibility trio. A single bad sender
        shouldn't take down the row's other extracted fields."""
        body = {
            "messages": [
                {"type": "info", "code": None},
                {"type": "info", "code": 42},
                {"type": "info"},  # missing code
                "not-a-dict",  # malformed message entry
                {"type": "info", "code": "eligibility_accepted"},
            ]
        }
        fields = UCPResponseParser.extract(body)
        assert fields["eligibility_accepted_present"] is True
        assert fields["eligibility_not_accepted_present"] is False
        assert fields["eligibility_invalid_present"] is False

    def test_eligibility_alongside_unrelated_messages(self):
        """Real responses carry a mix of unrelated codes and possibly
        an eligibility outcome. Pin that the outcome trio coexists
        cleanly with the existing per-severity code lists and the
        first-error capture, exercising the single-pass loop."""
        body = {
            "messages": [
                {"type": "info", "code": "tax_rounded_up"},
                {"type": "warning", "code": "shipping_delayed"},
                {"type": "info", "code": "eligibility_not_accepted"},
                {
                    "type": "error",
                    "code": "missing_email",
                    "content": "Email required",
                },
            ]
        }
        fields = UCPResponseParser.extract(body)
        assert fields["eligibility_not_accepted_present"] is True
        assert fields["eligibility_accepted_present"] is False
        assert fields["eligibility_invalid_present"] is False
        # Existing columns remain unaffected.
        info = json.loads(fields["message_info_codes_json"])
        assert info == ["tax_rounded_up", "eligibility_not_accepted"]
        warnings = json.loads(fields["message_warning_codes_json"])
        assert warnings == ["shipping_delayed"]
        assert fields["error_code"] == "missing_email"

    def test_context_eligibility_queryable_via_existing_column(self):
        """The context.eligibility[] payload from C1 already lands in
        context_eligibility_json. A5 doesn't change that column — pin
        that the new outcome trio coexists with it, so dashboards can
        join on `JSON_QUERY(context_eligibility_json, '$[0].claim')`
        and `eligibility_accepted_present = TRUE` in the same query."""
        body = {
            "context": {
                "eligibility": [
                    {"claim": "loyalty_member", "tier": "gold"},
                ],
            },
            "messages": [
                {"type": "info", "code": "eligibility_accepted"},
            ],
        }
        fields = UCPResponseParser.extract(body)
        # JSON column queryable by JSON_QUERY downstream.
        eligibility = json.loads(fields["context_eligibility_json"])
        assert eligibility == [{"claim": "loyalty_member", "tier": "gold"}]
        # Outcome trio populated independently from the claim payload.
        assert fields["eligibility_accepted_present"] is True
        assert fields["eligibility_not_accepted_present"] is False
        assert fields["eligibility_invalid_present"] is False

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

    def test_mcp_catalog_search(self):
        assert (
            UCPResponseParser.classify_jsonrpc("catalog_search")
            == UCPEventType.CATALOG_SEARCH
        )

    def test_mcp_catalog_lookup(self):
        assert (
            UCPResponseParser.classify_jsonrpc("catalog_lookup")
            == UCPEventType.CATALOG_LOOKUP
        )

    def test_mcp_catalog_product_get(self):
        assert (
            UCPResponseParser.classify_jsonrpc("get_product")
            == UCPEventType.CATALOG_PRODUCT_GET
        )

    def test_a2a_catalog_search(self):
        assert (
            UCPResponseParser.classify_jsonrpc("a2a.ucp.catalog.search")
            == UCPEventType.CATALOG_SEARCH
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
        # Webhook detected by path, body has no recognizable lifecycle
        # status → ORDER_WEBHOOK_RECEIVED (B5b: don't pivot taxonomy on
        # URL format). Distinct from ORDER_UPDATED, which is reserved
        # for REST-driven /orders/{id} updates from the business side.
        assert (
            UCPResponseParser.classify(
                "POST",
                "/webhooks/partners/p1/events/order",
                200,
                None,
            )
            == UCPEventType.ORDER_WEBHOOK_RECEIVED
        )

    def test_generic_webhook_fallback(self):
        assert (
            UCPResponseParser.classify("POST", "/webhooks/some-other-event", 200, {})
            == UCPEventType.ORDER_WEBHOOK_RECEIVED
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
            UCPResponseParser.classify("POST", "/webhooks/some-event", 400, {})
            == UCPEventType.ERROR
        )

    # ---- B5b: platform-provided webhook URLs + header fallback ----

    def test_platform_provided_webhook_url_via_extra_prefix(self):
        """UCP order.md: 'The URL format is platform-specific.' A
        platform that publishes its webhook destination as `/events`
        is the canonical motivation for this row -- the default
        `/webhook(s)` filter alone misses it. The classifier must
        accept operator-configured prefixes via webhook_path_prefixes
        and route the request through the order-webhook branch."""
        result = UCPResponseParser.classify(
            "POST",
            "/events",
            200,
            response_body=None,
            request_body={"id": "order_xyz", "status": "shipped"},
            webhook_path_prefixes=("/events",),
        )
        assert result == UCPEventType.ORDER_SHIPPED

    def test_header_based_webhook_fallback_unknown_path(self):
        """When the URL is not a known UCP path, presence of
        Webhook-Id + Webhook-Timestamp on the request must trigger
        the order-webhook branch -- this is the safety net for
        platforms whose webhook URL the operator hasn't enumerated.
        UCP order.md requires both headers on every order webhook,
        so the pair is a strong fingerprint."""
        result = UCPResponseParser.classify(
            "POST",
            "/hooks/abc-123",
            200,
            response_body={"status": "ok"},
            request_body={"id": "order_xyz", "status": "delivered"},
            request_headers={
                "Webhook-Id": "evt_42",
                "Webhook-Timestamp": "1767225600",
            },
        )
        assert result == UCPEventType.ORDER_DELIVERED

    def test_header_fallback_suppressed_on_known_rest_path(self):
        """Webhook headers on /checkout-sessions can only come from a
        buggy or malicious sender -- the URL determines the operation
        on known UCP REST endpoints. The classifier must NOT route
        such requests into the webhook branch even though the headers
        are present."""
        result = UCPResponseParser.classify(
            "POST",
            "/checkout-sessions",
            201,
            response_body={"id": "chk_xyz", "status": "ready_for_complete"},
            request_headers={
                "Webhook-Id": "evt_definitely_not_a_webhook",
                "Webhook-Timestamp": "1767225600",
            },
        )
        # /checkout-sessions POST → CHECKOUT_SESSION_CREATED, not any
        # ORDER_* type.
        assert result == UCPEventType.CHECKOUT_SESSION_CREATED

    def test_header_only_one_of_pair_does_not_trigger(self):
        """Standard Webhooks ships Webhook-Id AND Webhook-Timestamp
        together; either alone is not a valid delivery. A request
        with only one of the pair must not trigger the header
        fallback -- otherwise senders that happen to use a similarly
        named header for unrelated purposes would be misdetected."""
        result_id_only = UCPResponseParser.classify(
            "POST",
            "/api/v1/random",
            200,
            response_body={},
            request_headers={"Webhook-Id": "evt_42"},
        )
        result_ts_only = UCPResponseParser.classify(
            "POST",
            "/api/v1/random",
            200,
            response_body={},
            request_headers={"Webhook-Timestamp": "1767225600"},
        )
        # Falls through past the webhook branch — the path doesn't
        # match any UCP marker either, so we land on the generic
        # REQUEST fallback rather than ORDER_*.
        assert result_id_only != UCPEventType.ORDER_WEBHOOK_RECEIVED
        assert result_ts_only != UCPEventType.ORDER_WEBHOOK_RECEIVED

    def test_header_fallback_with_body_lifecycle_status(self):
        """The webhook detection (header pair) and the lifecycle
        derivation (body status) are independent: detection enters
        the branch, body picks the specific event type. Pin that
        body status drives taxonomy regardless of which signal got
        us into the branch."""
        for status, expected in [
            ("shipped", UCPEventType.ORDER_SHIPPED),
            ("delivered", UCPEventType.ORDER_DELIVERED),
            ("returned", UCPEventType.ORDER_RETURNED),
            ("canceled", UCPEventType.ORDER_CANCELED),
            ("cancelled", UCPEventType.ORDER_CANCELED),
        ]:
            result = UCPResponseParser.classify(
                "POST",
                "/ucp-events/incoming",
                200,
                response_body={"status": "ok"},
                request_body={"status": status},
                request_headers={
                    "Webhook-Id": f"evt_{status}",
                    "Webhook-Timestamp": "1767225600",
                },
            )
            assert result == expected, f"status={status}"

    def test_header_fallback_no_lifecycle_status_emits_webhook_received(self):
        """A webhook detected by headers but with no body lifecycle
        status emits ORDER_WEBHOOK_RECEIVED. This is distinct from
        ORDER_UPDATED (REST-driven business->platform updates) and
        the new B5b taxonomy: the URL no longer determines the
        type, the body does, and absence-of-status has its own
        first-class type."""
        result = UCPResponseParser.classify(
            "POST",
            "/hooks/123",
            200,
            response_body={"status": "ok"},
            request_body={"id": "order_xyz"},
            request_headers={
                "Webhook-Id": "evt_42",
                "Webhook-Timestamp": "1767225600",
            },
        )
        assert result == UCPEventType.ORDER_WEBHOOK_RECEIVED

    def test_legacy_url_segment_fallback_still_works(self):
        """Senders that don't include status in body but use the
        legacy URL-segment convention (`/webhooks/order-delivered`)
        still classify correctly. Body-driven derivation takes
        precedence; URL segment is the fallback for body-less
        senders."""
        result = UCPResponseParser.classify(
            "POST",
            "/webhooks/order-delivered",
            200,
            response_body=None,
            request_body=None,
        )
        assert result == UCPEventType.ORDER_DELIVERED


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
