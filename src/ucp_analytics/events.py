"""UCP event types and data model.

Types are aligned with UCP spec capabilities (checkout, order, identity linking)
and the checkout state machine (incomplete → requires_escalation →
ready_for_complete → completed | canceled).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class UCPEventType(str, Enum):
    """UCP event types aligned with protocol capabilities and operations."""

    # Checkout lifecycle (maps to REST operations on /checkout-sessions)
    CHECKOUT_SESSION_CREATED = "checkout_session_created"
    CHECKOUT_SESSION_GET = "checkout_session_get"
    CHECKOUT_SESSION_UPDATED = "checkout_session_updated"
    CHECKOUT_SESSION_COMPLETED = "checkout_session_completed"
    CHECKOUT_SESSION_CANCELED = "checkout_session_canceled"
    CHECKOUT_ESCALATION = "checkout_escalation"

    # Cart lifecycle (maps to REST operations on /carts)
    CART_CREATED = "cart_created"
    CART_GET = "cart_get"
    CART_UPDATED = "cart_updated"
    CART_CANCELED = "cart_canceled"

    # Order lifecycle (webhook-based in UCP)
    ORDER_CREATED = "order_created"
    ORDER_UPDATED = "order_updated"
    ORDER_SHIPPED = "order_shipped"
    ORDER_DELIVERED = "order_delivered"
    ORDER_RETURNED = "order_returned"
    ORDER_CANCELED = "order_canceled"

    # Identity linking (OAuth 2.0)
    IDENTITY_LINK_INITIATED = "identity_link_initiated"
    IDENTITY_LINK_COMPLETED = "identity_link_completed"
    IDENTITY_LINK_REVOKED = "identity_link_revoked"

    # Payment
    PAYMENT_HANDLER_NEGOTIATED = "payment_handler_negotiated"
    PAYMENT_INSTRUMENT_SELECTED = "payment_instrument_selected"
    PAYMENT_COMPLETED = "payment_completed"
    PAYMENT_FAILED = "payment_failed"

    # Discovery & capability negotiation
    PROFILE_DISCOVERED = "profile_discovered"
    CAPABILITY_NEGOTIATED = "capability_negotiated"

    # Generic HTTP-level events (fallback)
    REQUEST = "request"
    ERROR = "error"


class CheckoutStatus(str, Enum):
    """UCP checkout session statuses (spec-defined state machine)."""

    INCOMPLETE = "incomplete"
    REQUIRES_ESCALATION = "requires_escalation"
    READY_FOR_COMPLETE = "ready_for_complete"
    COMPLETE_IN_PROGRESS = "complete_in_progress"
    COMPLETED = "completed"
    CANCELED = "canceled"


# ---------------------------------------------------------------------------
# Event data class
# ---------------------------------------------------------------------------


@dataclass
class UCPEvent:
    """A single UCP analytics event row destined for BigQuery.

    Fields are a superset covering all UCP capabilities.  Unused fields
    remain None and are dropped before BigQuery insert.
    """

    # --- identity ---
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # --- context ---
    app_name: str = ""
    merchant_host: str = ""  # business endpoint host
    platform_profile_url: str = ""  # UCP-Agent header value
    transport: str = "rest"  # rest | mcp | a2a | embedded

    # --- HTTP ---
    http_method: str = ""
    http_path: str = ""
    http_status_code: Optional[int] = None
    idempotency_key: str = ""
    request_id: str = ""

    # --- checkout ---
    checkout_session_id: Optional[str] = None
    checkout_status: Optional[str] = None
    order_id: Optional[str] = None

    # --- financial (minor units / cents, spec total types) ---
    currency: Optional[str] = None
    items_discount_amount: Optional[int] = None
    subtotal_amount: Optional[int] = None
    discount_amount: Optional[int] = None
    fulfillment_amount: Optional[int] = None
    tax_amount: Optional[int] = None
    fee_amount: Optional[int] = None
    total_amount: Optional[int] = None

    # --- line items ---
    line_items_json: Optional[str] = None
    line_item_count: Optional[int] = None

    # --- payment ---
    payment_handler_id: Optional[str] = None
    payment_instrument_type: Optional[str] = None
    payment_brand: Optional[str] = None

    # --- capabilities & extensions ---
    ucp_version: Optional[str] = None
    capabilities_json: Optional[str] = None
    extensions_json: Optional[str] = None

    # --- identity linking ---
    identity_provider: Optional[str] = None
    identity_scope: Optional[str] = None

    # --- fulfillment ---
    fulfillment_type: Optional[str] = None
    fulfillment_destination_country: Optional[str] = None

    # --- discount extension ---
    discount_codes_json: Optional[str] = None
    discount_applied_json: Optional[str] = None

    # --- checkout metadata ---
    expires_at: Optional[str] = None
    continue_url: Optional[str] = None

    # --- order ---
    permalink_url: Optional[str] = None

    # --- messages / errors ---
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    error_severity: Optional[str] = None
    messages_json: Optional[str] = None

    # --- performance ---
    latency_ms: Optional[float] = None

    # --- custom ---
    custom_metadata_json: Optional[str] = None

    def to_bq_row(self) -> Dict[str, Any]:
        """Serialize to a BigQuery-insertable dict (drop None fields)."""
        return {k: v for k, v in self.__dict__.items() if v is not None}
