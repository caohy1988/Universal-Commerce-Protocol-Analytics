"""Parse UCP JSON responses into structured analytics fields.

Understands the checkout object schema, totals array, payment handlers,
fulfillment extension, discount extension, messages array, and the
ucp metadata envelope.
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

        # /orders  webhooks or GETs
        if "/orders" in p or "/order" in p:
            if m == "POST":
                return UCPEventType.ORDER_CREATED
            return UCPEventType.ORDER_UPDATED

        # Identity linking
        if "/identity" in p or "/oauth" in p:
            return UCPEventType.IDENTITY_LINK_INITIATED

        # Simulate shipping (samples server testing endpoint)
        if "/simulate-shipping" in p:
            return UCPEventType.ORDER_SHIPPED

        # Errors
        if status_code and status_code >= 400:
            return UCPEventType.ERROR

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

        # --- status ---
        if "status" in body:
            result["checkout_status"] = body["status"]

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
        ucp_meta = body.get("ucp")
        if isinstance(ucp_meta, dict):
            result["ucp_version"] = ucp_meta.get("version")
            caps = ucp_meta.get("capabilities", [])
            if caps:
                result["capabilities_json"] = json.dumps(caps, default=str)
                exts = [
                    c for c in caps
                    if isinstance(c, dict) and c.get("extends")
                ]
                if exts:
                    result["extensions_json"] = json.dumps(exts, default=str)

        # --- payment ---
        payment = body.get("payment") or body.get("payment_data") or {}
        if isinstance(payment, dict) and payment:
            result["payment_handler_id"] = (
                payment.get("handler_id") or payment.get("id")
            )
            result["payment_instrument_type"] = payment.get("type")
            result["payment_brand"] = payment.get("brand")
            # handlers list (from create response)
            handlers = payment.get("handlers")
            if isinstance(handlers, list) and handlers:
                result["payment_handler_id"] = handlers[0].get("id")

        # --- fulfillment extension ---
        fulfillment = body.get("fulfillment")
        if isinstance(fulfillment, dict):
            methods = fulfillment.get("methods", [])
            if isinstance(methods, list) and methods:
                first = methods[0]
                result["fulfillment_type"] = first.get("type")
                dests = first.get("destinations", [])
                if isinstance(dests, list) and dests:
                    result["fulfillment_destination_country"] = (
                        dests[0].get("address_country")
                    )

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
        """Parse the UCP totals array into individual amount fields."""
        if not isinstance(totals, list):
            return
        for item in totals:
            if not isinstance(item, dict):
                continue
            t_type = item.get("type", "")
            amount = item.get("amount")
            if amount is None:
                continue
            if t_type == "subtotal":
                result["subtotal_amount"] = amount
            elif t_type == "tax":
                result["tax_amount"] = amount
            elif t_type == "shipping":
                result["shipping_amount"] = amount
            elif t_type == "discount":
                result["discount_amount"] = amount
            elif t_type == "total":
                result["total_amount"] = amount
