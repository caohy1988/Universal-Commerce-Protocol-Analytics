"""Parse UCP JSON responses into structured analytics fields.

Understands the checkout object schema, totals array, payment instruments,
fulfillment extension, discount extension, messages array, and the
ucp metadata envelope.

Aligned with the official UCP specification at
https://github.com/Universal-Commerce-Protocol/ucp
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from ucp_analytics.events import UCPEventType


class UCPResponseParser:
    """Extract analytics-relevant fields from UCP request/response bodies."""

    # ------------------------------------------------------------------ #
    # Classify event type from HTTP method + path + body
    # ------------------------------------------------------------------ #

    @classmethod
    def classify(
        cls,
        method: str,
        path: str,
        status_code: int,
        response_body: Optional[dict],
        request_body: Optional[dict] = None,
    ) -> UCPEventType:
        """Derive the UCP event type from the HTTP request + response."""
        m = method.upper()
        p = path.rstrip("/")

        # /.well-known/ucp  →  discovery
        if p.endswith("/.well-known/ucp"):
            return UCPEventType.PROFILE_DISCOVERED

        # /checkout-sessions  POST  → created
        if re.search(r"/checkout-sessions/?$", p) and m == "POST":
            return UCPEventType.CHECKOUT_SESSION_CREATED

        # /checkout-sessions/{id}/complete  POST  → completed
        if re.search(r"/checkout-sessions/[^/]+/complete$", p) and m == "POST":
            return UCPEventType.CHECKOUT_SESSION_COMPLETED

        # /checkout-sessions/{id}/cancel  POST  → canceled
        if re.search(r"/checkout-sessions/[^/]+/cancel$", p) and m == "POST":
            return UCPEventType.CHECKOUT_SESSION_CANCELED

        # /checkout-sessions/{id}  PUT  → updated (or escalation)
        if re.search(r"/checkout-sessions/[^/]+$", p) and m == "PUT":
            if response_body and response_body.get("status") == "requires_escalation":
                return UCPEventType.CHECKOUT_ESCALATION
            return UCPEventType.CHECKOUT_SESSION_UPDATED

        # /checkout-sessions/{id}  GET  → get
        if re.search(r"/checkout-sessions/[^/]+$", p) and m == "GET":
            return UCPEventType.CHECKOUT_SESSION_GET

        # /carts  POST  → created
        if re.search(r"/carts/?$", p) and m == "POST":
            return UCPEventType.CART_CREATED

        # /carts/{id}/cancel  POST  → canceled
        if re.search(r"/carts/[^/]+/cancel$", p) and m == "POST":
            return UCPEventType.CART_CANCELED

        # /carts/{id}  PUT  → updated
        if re.search(r"/carts/[^/]+$", p) and m == "PUT":
            return UCPEventType.CART_UPDATED

        # /carts/{id}  GET  → get
        if re.search(r"/carts/[^/]+$", p) and m == "GET":
            return UCPEventType.CART_GET

        # /orders (strict: /orders or /orders/{id}, not /reorder etc.)
        if re.search(r"/orders(?:/[^/]+)?$", p):
            if m == "POST":
                return UCPEventType.ORDER_CREATED
            # Check response body status for order lifecycle events
            if response_body and isinstance(response_body, dict):
                order_status = response_body.get("status", "")
                if order_status == "delivered":
                    return UCPEventType.ORDER_DELIVERED
                if order_status == "returned":
                    return UCPEventType.ORDER_RETURNED
                if order_status in ("canceled", "cancelled"):
                    return UCPEventType.ORDER_CANCELED
            return UCPEventType.ORDER_UPDATED

        # Webhook paths for order lifecycle
        # Upstream: POST /webhooks/partners/{partner_id}/events/order
        if re.search(r"/webhooks?/", p):
            # Webhook errors should still classify as errors
            if status_code and status_code >= 400:
                return UCPEventType.ERROR
            # Check for upstream partner webhook format
            # Payload is in the request body; response is just ack
            if re.search(r"/webhooks?/partners/[^/]+/events/order", p):
                body = (
                    request_body
                    if request_body and isinstance(request_body, dict)
                    else response_body
                )
                if body and isinstance(body, dict):
                    order_status = body.get("status", "")
                    if order_status == "shipped":
                        return UCPEventType.ORDER_SHIPPED
                    if order_status == "delivered":
                        return UCPEventType.ORDER_DELIVERED
                    if order_status == "returned":
                        return UCPEventType.ORDER_RETURNED
                    if order_status in ("canceled", "cancelled"):
                        return UCPEventType.ORDER_CANCELED
                return UCPEventType.ORDER_UPDATED
            # Legacy: /webhooks/order-delivered etc.
            if re.search(r"/webhooks?/order[_-]delivered", p):
                return UCPEventType.ORDER_DELIVERED
            if re.search(r"/webhooks?/order[_-]returned", p):
                return UCPEventType.ORDER_RETURNED
            if re.search(r"/webhooks?/order[_-]canceled", p):
                return UCPEventType.ORDER_CANCELED
            # Generic webhook → treat as order update
            return UCPEventType.ORDER_UPDATED

        # Identity linking (strict: /identity or /oauth paths)
        if re.search(r"/(?:identity|oauth)(?:/|$)", p):
            # /identity/revoke or DELETE → revoked
            if "/revoke" in p or m == "DELETE":
                return UCPEventType.IDENTITY_LINK_REVOKED
            # /identity/callback or /oauth/callback → completed
            if "/callback" in p:
                return UCPEventType.IDENTITY_LINK_COMPLETED
            return UCPEventType.IDENTITY_LINK_INITIATED

        # Simulate shipping (samples server testing endpoint)
        if "/simulate-shipping" in p:
            return UCPEventType.ORDER_SHIPPED

        # Errors
        if status_code and status_code >= 400:
            return UCPEventType.ERROR

        return UCPEventType.REQUEST

    # ------------------------------------------------------------------ #
    # JSON-RPC classification (MCP / A2A transports)
    # ------------------------------------------------------------------ #

    # Map tool/action names to equivalent HTTP method + path
    _TOOL_TO_HTTP: Dict[str, tuple] = {
        # MCP tool names
        "create_checkout": ("POST", "/checkout-sessions"),
        "update_checkout": ("PUT", "/checkout-sessions/{id}"),
        "complete_checkout": ("POST", "/checkout-sessions/{id}/complete"),
        "cancel_checkout": ("POST", "/checkout-sessions/{id}/cancel"),
        "get_checkout": ("GET", "/checkout-sessions/{id}"),
        "create_cart": ("POST", "/carts"),
        "update_cart": ("PUT", "/carts/{id}"),
        "cancel_cart": ("POST", "/carts/{id}/cancel"),
        "get_cart": ("GET", "/carts/{id}"),
        "create_order": ("POST", "/orders"),
        "get_order": ("GET", "/orders/{id}"),
        "discover": ("GET", "/.well-known/ucp"),
        "discover_merchant": ("GET", "/.well-known/ucp"),
        "simulate_shipping": ("POST", "/testing/simulate-shipping/{id}"),
        "order_event_webhook": ("POST", "/webhooks/partners/{id}/events/order"),
        "add_to_checkout": ("PUT", "/checkout-sessions/{id}"),
        "remove_from_checkout": ("PUT", "/checkout-sessions/{id}"),
        "update_customer_details": ("PUT", "/checkout-sessions/{id}"),
        "start_payment": ("PUT", "/checkout-sessions/{id}"),
        "link_identity": ("POST", "/identity"),
        "revoke_identity": ("DELETE", "/identity/revoke"),
        "negotiate_capability": ("POST", "/capabilities/negotiate"),
        # A2A action prefixes (a2a.ucp.*)
        "a2a.ucp.checkout.create": ("POST", "/checkout-sessions"),
        "a2a.ucp.checkout.update": ("PUT", "/checkout-sessions/{id}"),
        "a2a.ucp.checkout.complete": ("POST", "/checkout-sessions/{id}/complete"),
        "a2a.ucp.checkout.cancel": ("POST", "/checkout-sessions/{id}/cancel"),
        "a2a.ucp.checkout.get": ("GET", "/checkout-sessions/{id}"),
        "a2a.ucp.cart.create": ("POST", "/carts"),
        "a2a.ucp.cart.update": ("PUT", "/carts/{id}"),
        "a2a.ucp.cart.cancel": ("POST", "/carts/{id}/cancel"),
        "a2a.ucp.cart.get": ("GET", "/carts/{id}"),
        "a2a.ucp.order.create": ("POST", "/orders"),
        "a2a.ucp.order.get": ("GET", "/orders/{id}"),
        "a2a.ucp.discover": ("GET", "/.well-known/ucp"),
        "a2a.ucp.identity.link": ("POST", "/identity"),
        "a2a.ucp.identity.revoke": ("DELETE", "/identity/revoke"),
        "a2a.ucp.capability.negotiate": ("POST", "/capabilities/negotiate"),
    }

    @classmethod
    def classify_jsonrpc(
        cls,
        tool_name: str,
        status_code: int = 200,
        response_body: Optional[dict] = None,
    ) -> UCPEventType:
        """Classify a JSON-RPC tool/action name into a UCP event type.

        Used for MCP (tools/call) and A2A (tasks/send) transports.
        Maps tool names to HTTP equivalents, then delegates to classify().
        """
        # Capability negotiation keywords (check before _TOOL_TO_HTTP
        # since /capabilities/negotiate doesn't match classify() patterns)
        if "negotiate" in tool_name or "capability" in tool_name:
            return UCPEventType.CAPABILITY_NEGOTIATED

        mapping = cls._TOOL_TO_HTTP.get(tool_name)
        if mapping:
            method, path = mapping
            return cls.classify(method, path, status_code, response_body)

        # Handle A2A DataPart keys like "add_to_checkout" → update
        if "add_to" in tool_name or "remove_from" in tool_name or "update" in tool_name:
            if "checkout" in tool_name:
                return cls.classify(
                    "PUT", "/checkout-sessions/{id}", status_code, response_body
                )
            if "cart" in tool_name:
                return cls.classify("PUT", "/carts/{id}", status_code, response_body)

        return UCPEventType.REQUEST

    # ------------------------------------------------------------------ #
    # Extract checkout & commerce fields from a UCP JSON body
    # ------------------------------------------------------------------ #

    @classmethod
    def extract(cls, body: Optional[dict]) -> Dict[str, Any]:
        """Extract analytics fields from a UCP checkout/order JSON body.

        Works with both request bodies (partial) and response bodies (full).
        Returns a dict of field_name → value; callers merge into UCPEvent.
        """
        if not body or not isinstance(body, dict):
            return {}

        result: Dict[str, Any] = {}

        # --- session / order id ---
        raw_id = body.get("id", "")
        id_str = str(raw_id) if raw_id else ""
        if id_str:
            # Heuristic: order objects have checkout_id; checkout objects don't
            if "checkout_id" in body:
                result["order_id"] = id_str
                result["checkout_session_id"] = body["checkout_id"]
            else:
                result["checkout_session_id"] = id_str

        if "order_id" in body:
            result["order_id"] = body["order_id"]

        # --- order confirmation in checkout response (spec: checkout.order) ---
        order_obj = body.get("order")
        if isinstance(order_obj, dict):
            order_id = order_obj.get("id")
            if order_id:
                result["order_id"] = str(order_id)
            permalink = order_obj.get("permalink_url")
            if permalink:
                result["permalink_url"] = permalink

        # --- permalink_url (direct on order objects) ---
        if "permalink_url" in body:
            result["permalink_url"] = body["permalink_url"]

        # --- status ---
        if "status" in body:
            # Only write checkout_status for checkout responses, not orders/carts
            if "checkout_id" not in body:
                status_val = body["status"]
                _CHECKOUT_STATUSES = {
                    "incomplete", "requires_escalation", "ready_for_complete",
                    "complete_in_progress", "completed", "canceled",
                }
                if status_val in _CHECKOUT_STATUSES:
                    result["checkout_status"] = status_val

        # --- currency ---
        if "currency" in body:
            result["currency"] = body["currency"]

        # --- totals array ---
        cls._extract_totals(body.get("totals"), result)

        # --- line items ---
        items = body.get("line_items")
        if isinstance(items, list) and items:
            result["line_item_count"] = len(items)
            result["line_items_json"] = json.dumps(items, default=str)

        # --- ucp metadata envelope ---
        cls._extract_ucp_metadata(body.get("ucp"), result)

        # --- discovery: payment.handlers at top level (sibling of ucp) ---
        cls._extract_discovery_payment(body.get("payment"), result)

        # --- payment (spec: payment.instruments[], fallback: payment.handlers[]) ---
        cls._extract_payment(body, result)

        # --- fulfillment extension ---
        cls._extract_fulfillment(body.get("fulfillment"), result)

        # --- discount extension ---
        cls._extract_discounts(body.get("discounts"), result)

        # --- checkout metadata ---
        if "expires_at" in body:
            result["expires_at"] = body["expires_at"]
        if "continue_url" in body:
            result["continue_url"] = body["continue_url"]

        # --- identity linking ---
        if "provider" in body:
            result["identity_provider"] = body["provider"]
        if "scope" in body:
            result["identity_scope"] = body["scope"]
        # Nested identity object
        identity = body.get("identity")
        if isinstance(identity, dict):
            if "provider" in identity:
                result["identity_provider"] = identity["provider"]
            if "scope" in identity:
                result["identity_scope"] = identity["scope"]

        # --- messages (errors / warnings from the server) ---
        messages = body.get("messages")
        if isinstance(messages, list) and messages:
            result["messages_json"] = json.dumps(messages, default=str)
            for msg in messages:
                if isinstance(msg, dict) and msg.get("type") == "error":
                    result["error_code"] = msg.get("code")
                    result["error_message"] = msg.get("content")
                    result["error_severity"] = msg.get("severity")
                    break

        # --- links ---
        links = body.get("links")
        if isinstance(links, list):
            for link in links:
                if isinstance(link, dict) and link.get("type") == "order":
                    result["order_id"] = result.get("order_id") or link.get("url")

        # Drop None values
        return {k: v for k, v in result.items() if v is not None}

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def _extract_totals(cls, totals: Any, result: Dict[str, Any]) -> None:
        """Parse the UCP totals array into individual amount fields.

        Spec total types: items_discount, subtotal, discount, fulfillment,
        tax, fee, total.
        """
        if not isinstance(totals, list):
            return
        for item in totals:
            if not isinstance(item, dict):
                continue
            t_type = item.get("type", "")
            amount = item.get("amount")
            if amount is None:
                continue
            if t_type == "items_discount":
                result["items_discount_amount"] = amount
            elif t_type == "subtotal":
                result["subtotal_amount"] = amount
            elif t_type == "discount":
                result["discount_amount"] = amount
            elif t_type == "fulfillment":
                result["fulfillment_amount"] = amount
            elif t_type == "tax":
                result["tax_amount"] = amount
            elif t_type == "fee":
                result["fee_amount"] = amount
            elif t_type == "total":
                result["total_amount"] = amount

    @classmethod
    def _extract_ucp_metadata(cls, ucp_meta: Any, result: Dict[str, Any]) -> None:
        """Parse the UCP metadata envelope.

        Per the Python SDK and samples, capabilities are arrays of objects
        with a ``name`` field::

            [{"name": "dev.ucp.shopping.checkout", "version": "2026-01-11"}]

        For robustness, also handles an object-keyed format where capability
        names are dict keys (e.g. ``{"dev.ucp.shopping.checkout": [...]}``)
        by normalizing to the array format.
        """
        if not isinstance(ucp_meta, dict):
            return

        result["ucp_version"] = ucp_meta.get("version")

        caps_raw = ucp_meta.get("capabilities")
        if caps_raw:
            caps_list = cls._normalize_registry(caps_raw)
            if caps_list:
                result["capabilities_json"] = json.dumps(caps_list, default=str)

    @classmethod
    def _extract_discovery_payment(cls, payment: Any, result: Dict[str, Any]) -> None:
        """Extract payment handler info from the discovery profile.

        In the SDK, discovery responses place payment handlers at the
        top level as a sibling of ``ucp``::

            {"ucp": {...}, "payment": {"handlers": [...]}}

        This is separate from ``_extract_payment()`` which handles
        the ``payment`` object inside checkout/order responses.
        """
        if not isinstance(payment, dict):
            return
        handlers = payment.get("handlers")
        if not isinstance(handlers, list) or not handlers:
            return
        # Only set if _extract_payment hasn't already found an instrument
        if "payment_handler_id" not in result:
            first = handlers[0]
            if isinstance(first, dict):
                result["payment_handler_id"] = first.get("id") or first.get("name")

    @classmethod
    def _normalize_registry(cls, raw: Any) -> list:
        """Normalize capabilities to a flat list for analytics storage.

        The SDK/samples use an array format (primary)::

            [{"name": "dev.ucp.shopping.checkout", "version": "2026-01-11"}]

        Also handles an object-keyed dict format for robustness::

            {"dev.ucp.shopping.checkout": [{"version": "2026-01-11"}]}
        """
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            flat = []
            for domain_name, entries in raw.items():
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, dict):
                            item = {"name": domain_name, **entry}
                            flat.append(item)
                elif isinstance(entries, dict):
                    flat.append({"name": domain_name, **entries})
            return flat
        return []

    @classmethod
    def _extract_payment(cls, body: dict, result: Dict[str, Any]) -> None:
        """Extract payment fields from checkout/order responses.

        The SDK ``PaymentResponse`` contains both ``handlers[]`` (merchant
        configs) and ``instruments[]`` (buyer payment methods).  Instruments
        are preferred for analytics since they carry ``handler_id``, ``type``,
        and ``brand``.
        """
        payment = body.get("payment") or {}
        payment_data = body.get("payment_data") or {}

        # payment_data (from complete request/response)
        if isinstance(payment_data, dict) and payment_data:
            result["payment_handler_id"] = payment_data.get(
                "handler_id"
            ) or payment_data.get("id")
            result["payment_instrument_type"] = payment_data.get("type")
            result["payment_brand"] = payment_data.get("brand")
            return

        if not isinstance(payment, dict) or not payment:
            return

        # Spec format: payment.instruments[] (each instrument has handler_id)
        instruments = payment.get("instruments")
        if isinstance(instruments, list) and instruments:
            first = instruments[0]
            if isinstance(first, dict):
                result["payment_handler_id"] = first.get("handler_id") or first.get(
                    "id"
                )
                result["payment_instrument_type"] = first.get("type")
                result["payment_brand"] = first.get("brand")
            return

        # Legacy/demo format: payment.handlers[]
        handlers = payment.get("handlers")
        if isinstance(handlers, list) and handlers:
            first = handlers[0]
            if isinstance(first, dict):
                result["payment_handler_id"] = first.get("id")
                result["payment_instrument_type"] = first.get("type")
                result["payment_brand"] = first.get("brand")
            return

        # Direct fields
        result["payment_handler_id"] = payment.get("handler_id") or payment.get("id")
        result["payment_instrument_type"] = payment.get("type")
        result["payment_brand"] = payment.get("brand")

    @classmethod
    def _extract_fulfillment(cls, fulfillment: Any, result: Dict[str, Any]) -> None:
        """Extract fulfillment fields.

        Handles both checkout fulfillment (methods[]) and order fulfillment
        (expectations[]/events[]).
        """
        if not isinstance(fulfillment, dict):
            return

        # Checkout: fulfillment.methods[]
        methods = fulfillment.get("methods")
        if isinstance(methods, list) and methods:
            first = methods[0]
            if isinstance(first, dict):
                result["fulfillment_type"] = first.get("type")
                dests = first.get("destinations", [])
                if isinstance(dests, list) and dests:
                    dest = dests[0]
                    if isinstance(dest, dict):
                        # SDK: destination is a PostalAddress (direct fields)
                        # or has a nested address object
                        country = dest.get("address_country")
                        if not country:
                            addr = dest.get("address")
                            if isinstance(addr, dict):
                                country = addr.get("address_country")
                        result["fulfillment_destination_country"] = country
            return

        # Order: fulfillment.expectations[]
        expectations = fulfillment.get("expectations")
        if isinstance(expectations, list) and expectations:
            first = expectations[0]
            if isinstance(first, dict):
                result["fulfillment_type"] = first.get("method_type") or first.get(
                    "type"
                )
                dest = first.get("destination")
                if isinstance(dest, dict):
                    result["fulfillment_destination_country"] = dest.get(
                        "address_country"
                    )

    @classmethod
    def _extract_discounts(cls, discounts: Any, result: Dict[str, Any]) -> None:
        """Extract discount extension fields.

        Spec: discounts.codes (input), discounts.applied (output).
        """
        if not isinstance(discounts, dict):
            return

        codes = discounts.get("codes")
        if isinstance(codes, list) and codes:
            result["discount_codes_json"] = json.dumps(codes, default=str)

        applied = discounts.get("applied")
        if isinstance(applied, list) and applied:
            result["discount_applied_json"] = json.dumps(applied, default=str)
