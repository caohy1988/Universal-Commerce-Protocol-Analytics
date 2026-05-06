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

try:
    from pydantic import BaseModel, RootModel, ValidationError
    from ucp_sdk.models.schemas.shopping.checkout import Checkout
    from ucp_sdk.models.schemas.shopping.checkout_create_request import (
        CheckoutCreateRequest,
    )
    from ucp_sdk.models.schemas.shopping.checkout_update_request import (
        CheckoutUpdateRequest,
    )
    from ucp_sdk.models.schemas.shopping.order import Order
    from ucp_sdk.models.schemas.shopping.order_create_request import OrderCreateRequest
    from ucp_sdk.models.schemas.shopping.order_update_request import OrderUpdateRequest
except ImportError:  # pragma: no cover - exercised when sdk is not installed
    BaseModel = RootModel = ValidationError = None  # type: ignore[assignment]
    SDK_MODEL_CANDIDATES: tuple[tuple[str, Any], ...] = ()
else:
    SDK_MODEL_CANDIDATES = (
        ("checkout", Checkout),
        ("order", Order),
        ("checkout_create_request", CheckoutCreateRequest),
        ("checkout_update_request", CheckoutUpdateRequest),
        ("order_create_request", OrderCreateRequest),
        ("order_update_request", OrderUpdateRequest),
    )


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

        # Server failures are tracked as error events before route heuristics.
        if status_code and status_code >= 500:
            return UCPEventType.ERROR

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

        # Simulate shipping (samples server testing endpoint)
        if "/simulate-shipping" in p:
            return UCPEventType.ORDER_SHIPPED

        # /orders  webhooks or GETs
        if "/orders" in p or "/order" in p:
            if m == "POST":
                return UCPEventType.ORDER_CREATED
            return UCPEventType.ORDER_UPDATED

        # Identity linking
        if "/identity" in p or "/oauth" in p:
            return UCPEventType.IDENTITY_LINK_INITIATED

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

        sdk_fields = cls._extract_with_sdk_models(body)
        if sdk_fields is not None:
            return sdk_fields

        return cls._extract_from_mapping(body)

    # ------------------------------------------------------------------ #
    # SDK-backed extraction
    # ------------------------------------------------------------------ #

    @classmethod
    def _extract_with_sdk_models(cls, body: dict) -> Optional[Dict[str, Any]]:
        """Validate a body with ucp-sdk models, then map typed data to fields."""
        parsed = cls._parse_sdk_body(body)
        if parsed is None:
            return None

        kind, model = parsed
        # Request models intentionally allow partial commerce payloads. Keep
        # legacy/demo dict extraction for those, while still using SDK
        # validation as the gate that says "this is a UCP-shaped body."
        result: Dict[str, Any] = (
            cls._extract_from_mapping(body) if kind.endswith("_request") else {}
        )

        if kind.startswith("checkout"):
            checkout_id = getattr(model, "id", None)
            if checkout_id:
                result["checkout_session_id"] = str(checkout_id)
            status = getattr(model, "status", None)
            if status:
                result["checkout_status"] = str(status)
        elif kind.startswith("order"):
            order_id = getattr(model, "id", None)
            if order_id:
                result["order_id"] = str(order_id)
            checkout_id = getattr(model, "checkout_id", None)
            if checkout_id:
                result["checkout_session_id"] = str(checkout_id)

        currency = getattr(model, "currency", None)
        if currency:
            result["currency"] = str(currency)

        cls._extract_totals_model(getattr(model, "totals", None), result)
        cls._extract_line_items_model(getattr(model, "line_items", None), result)
        cls._extract_ucp_metadata_model(getattr(model, "ucp", None), result)
        cls._extract_payment_model(getattr(model, "payment", None), result)
        cls._extract_fulfillment_model(getattr(model, "fulfillment", None), result)
        cls._extract_messages_model(getattr(model, "messages", None), result)
        cls._extract_links_model(getattr(model, "links", None), result)

        return {k: v for k, v in result.items() if v is not None}

    @classmethod
    def _parse_sdk_body(cls, body: dict) -> Optional[tuple[str, Any]]:
        for kind, model_cls in SDK_MODEL_CANDIDATES:
            try:
                return kind, model_cls.model_validate(body)
            except ValidationError:
                continue
        return None

    @classmethod
    def _extract_totals_model(cls, totals: Any, result: Dict[str, Any]) -> None:
        for item in cls._as_sequence(cls._root_value(totals)):
            total_type = cls._get_value(item, "type")
            amount = cls._root_value(cls._get_value(item, "amount"))
            if total_type is None or amount is None:
                continue
            cls._set_total_amount(str(total_type), amount, result)

    @classmethod
    def _extract_line_items_model(cls, line_items: Any, result: Dict[str, Any]) -> None:
        items = cls._as_sequence(line_items)
        if not items:
            return
        result["line_item_count"] = len(items)
        result["line_items_json"] = json.dumps(
            [cls._model_dump(item) for item in items],
            default=str,
        )

    @classmethod
    def _extract_ucp_metadata_model(cls, ucp_meta: Any, result: Dict[str, Any]) -> None:
        if ucp_meta is None:
            return
        meta = cls._root_value(ucp_meta)
        version = cls._root_value(getattr(meta, "version", None))
        if version:
            result["ucp_version"] = str(version)

        caps = getattr(meta, "capabilities", None)
        if not caps:
            return

        caps_json = cls._model_dump(caps)
        result["capabilities_json"] = json.dumps(caps_json, default=str)

        extensions = []
        if isinstance(caps_json, dict):
            for cap_entries in caps_json.values():
                for cap in cls._as_sequence(cap_entries):
                    if isinstance(cap, dict) and cap.get("extends"):
                        extensions.append(cap)
        elif isinstance(caps_json, list):
            extensions = [
                cap for cap in caps_json if isinstance(cap, dict) and cap.get("extends")
            ]
        if extensions:
            result["extensions_json"] = json.dumps(extensions, default=str)

    @classmethod
    def _extract_payment_model(cls, payment: Any, result: Dict[str, Any]) -> None:
        instruments = cls._as_sequence(getattr(payment, "instruments", None))
        if not instruments:
            return
        selected = next(
            (item for item in instruments if getattr(item, "selected", False)),
            instruments[0],
        )
        selected_json = cls._model_dump(selected)
        result["payment_handler_id"] = getattr(selected, "handler_id", None)
        result["payment_instrument_type"] = getattr(selected, "type", None)
        if isinstance(selected_json, dict):
            display = selected_json.get("display")
            if isinstance(display, dict):
                result["payment_brand"] = display.get("brand")
            result["payment_brand"] = result.get("payment_brand") or selected_json.get(
                "brand"
            )

    @classmethod
    def _extract_fulfillment_model(
        cls, fulfillment: Any, result: Dict[str, Any]
    ) -> None:
        if fulfillment is None:
            return
        data = cls._model_dump(fulfillment)
        if not isinstance(data, dict):
            return
        methods = data.get("methods")
        if isinstance(methods, list) and methods:
            first = methods[0]
            if isinstance(first, dict):
                result["fulfillment_type"] = first.get("type")
                dests = first.get("destinations", [])
                if isinstance(dests, list) and dests and isinstance(dests[0], dict):
                    result["fulfillment_destination_country"] = dests[0].get(
                        "address_country"
                    )

    @classmethod
    def _extract_messages_model(cls, messages: Any, result: Dict[str, Any]) -> None:
        items = cls._as_sequence(messages)
        if not items:
            return
        messages_json = [cls._model_dump(cls._root_value(item)) for item in items]
        result["messages_json"] = json.dumps(messages_json, default=str)
        for item in items:
            message = cls._root_value(item)
            if cls._get_value(message, "type") == "error":
                result["error_code"] = cls._root_value(cls._get_value(message, "code"))
                result["error_message"] = cls._get_value(message, "content")
                result["error_severity"] = cls._get_value(message, "severity")
                break

    @classmethod
    def _extract_links_model(cls, links: Any, result: Dict[str, Any]) -> None:
        for link in cls._as_sequence(links):
            data = cls._model_dump(link)
            if isinstance(data, dict) and data.get("type") == "order":
                result["order_id"] = result.get("order_id") or data.get("url")

    # ------------------------------------------------------------------ #
    # Legacy dict extraction
    # ------------------------------------------------------------------ #

    @classmethod
    def _extract_from_mapping(cls, body: dict) -> Dict[str, Any]:
        """Extract from unvalidated dicts for legacy/demo payloads."""
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
                exts = [c for c in caps if isinstance(c, dict) and c.get("extends")]
                if exts:
                    result["extensions_json"] = json.dumps(exts, default=str)

        # --- payment ---
        payment = body.get("payment") or body.get("payment_data") or {}
        if isinstance(payment, dict) and payment:
            result["payment_handler_id"] = payment.get("handler_id") or payment.get(
                "id"
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
                    result["fulfillment_destination_country"] = dests[0].get(
                        "address_country"
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
            cls._set_total_amount(t_type, amount, result)

    @staticmethod
    def _set_total_amount(total_type: str, amount: Any, result: Dict[str, Any]) -> None:
        if total_type == "subtotal":
            result["subtotal_amount"] = amount
        elif total_type == "tax":
            result["tax_amount"] = amount
        elif total_type in {"shipping", "fulfillment"}:
            result["shipping_amount"] = amount
        elif total_type in {"discount", "items_discount"}:
            result["discount_amount"] = amount
        elif total_type == "total":
            result["total_amount"] = amount

    @staticmethod
    def _root_value(value: Any) -> Any:
        if RootModel is not None and isinstance(value, RootModel):
            return value.root
        return value

    @staticmethod
    def _as_sequence(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return []

    @staticmethod
    def _model_dump(value: Any) -> Any:
        if BaseModel is not None and isinstance(value, BaseModel):
            return value.model_dump(mode="json", by_alias=True, exclude_none=True)
        if RootModel is not None and isinstance(value, RootModel):
            return value.model_dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(value, dict):
            return {
                str(UCPResponseParser._root_value(k)): UCPResponseParser._model_dump(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [UCPResponseParser._model_dump(v) for v in value]
        return value

    @staticmethod
    def _get_value(value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)
