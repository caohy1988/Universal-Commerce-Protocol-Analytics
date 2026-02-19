#!/usr/bin/env python3
"""
ADK + BigQuery Demo — UCPAgentAnalyticsPlugin
===============================================

Demonstrates the ADK plugin adapter that wraps UCPAnalyticsTracker.
Uses a simulated ADK tool flow (no LLM calls needed) to show that
tool callbacks properly capture UCP events to BigQuery.

Requires:
    - gcloud auth application-default login
    - BigQuery API enabled
    - pip install ucp-analytics[adk]

Usage:
    uv run python examples/bq_adk_demo.py
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

from ucp_analytics.adk_plugin import UCPAgentAnalyticsPlugin

# ==========================================================================
# Config
# ==========================================================================

PROJECT_ID = "test-project-0728-467323"
DATASET_ID = "ucp_analytics"
TABLE_ID = "ucp_events"
APP_NAME = "bq_adk_demo"


# ==========================================================================
# Simulated ADK Tool Interface
# ==========================================================================


class MockTool:
    """Simulates an ADK tool object."""

    def __init__(self, name: str):
        self.name = name


class MockToolContext:
    """Simulates an ADK tool context."""

    def __init__(self, app_name: str = APP_NAME):
        self.app_name = app_name
        self._id = id(self)  # unique per invocation


# ==========================================================================
# Simulated UCP Tool Results (spec-aligned)
# ==========================================================================

PAYMENT_HANDLERS = [
    {
        "id": "mock_payment_handler",
        "name": "com.mock.payment",
        "version": "2026-01-11",
        "spec": "https://ucp.dev/specs/mock",
        "config_schema": "https://ucp.dev/schemas/mock.json",
        "instrument_schemas": [],
        "config": {},
    },
]

DISCOVERY_RESULT = {
    "ucp": {
        "version": "2026-01-11",
        "services": {
            "dev.ucp.shopping": {
                "version": "2026-01-11",
                "spec": "https://ucp.dev/specs/shopping",
                "rest": {
                    "schema": "https://ucp.dev/services/shopping/openapi.json",
                    "endpoint": "https://flower-shop.example.com",
                },
            },
        },
        "capabilities": [
            {"name": "dev.ucp.shopping.checkout", "version": "2026-01-11"},
            {
                "name": "dev.ucp.shopping.fulfillment",
                "version": "2026-01-11",
                "extends": "dev.ucp.shopping.checkout",
            },
        ],
    },
    "payment": {
        "handlers": PAYMENT_HANDLERS,
    },
}

CHECKOUT_CREATED_RESULT = {
    "ucp": {
        "version": "2026-01-11",
        "capabilities": [
            {"name": "dev.ucp.shopping.checkout", "version": "2026-01-11"},
        ],
    },
    "id": f"chk_adk_{uuid.uuid4().hex[:8]}",
    "status": "incomplete",
    "currency": "USD",
    "line_items": [
        {
            "id": "li_1",
            "item": {"id": "roses", "title": "Red Roses", "price": 2999},
            "quantity": 1,
        },
    ],
    "totals": [
        {"type": "subtotal", "amount": 2999},
        {"type": "tax", "amount": 262},
        {"type": "total", "amount": 3261},
    ],
    "payment": {
        "handlers": PAYMENT_HANDLERS,
        "instruments": [
            {
                "id": "instr_1",
                "handler_id": "mock_payment_handler",
                "type": "card",
                "brand": "Visa",
            },
        ],
    },
    "messages": [],
    "links": [],
}

CHECKOUT_UPDATED_RESULT = {
    **CHECKOUT_CREATED_RESULT,
    "status": "ready_for_complete",
    "fulfillment": {
        "methods": [
            {
                "id": "method_1",
                "type": "shipping",
                "line_item_ids": ["li_1"],
                "destinations": [
                    {"id": "dest_1", "address_country": "US", "postal_code": "94043"},
                ],
            },
        ],
    },
    "totals": [
        {"type": "subtotal", "amount": 2999},
        {"type": "fulfillment", "amount": 599},
        {"type": "tax", "amount": 262},
        {"type": "total", "amount": 3860},
    ],
}

ORDER_ID = f"order_adk_{uuid.uuid4().hex[:8]}"

CHECKOUT_COMPLETED_RESULT = {
    **CHECKOUT_UPDATED_RESULT,
    "status": "completed",
    "order": {
        "id": ORDER_ID,
        "permalink_url": f"https://flower-shop.example.com/orders/{ORDER_ID}",
    },
}

ORDER_SHIPPED_RESULT = {
    "id": ORDER_ID,
    "checkout_id": CHECKOUT_CREATED_RESULT["id"],
    "permalink_url": f"https://flower-shop.example.com/orders/{ORDER_ID}",
    "status": "shipped",
    "line_items": CHECKOUT_CREATED_RESULT["line_items"],
    "totals": CHECKOUT_UPDATED_RESULT["totals"],
    "fulfillment": {
        "expectations": [
            {
                "id": "exp_1",
                "method_type": "shipping",
                "destination": {"address_country": "US", "postal_code": "94043"},
                "line_items": [{"id": "li_1", "quantity": 1}],
            },
        ],
        "events": [
            {
                "id": "evt_1",
                "type": "shipped",
                "tracking_number": "94001118992234",
                "carrier": "USPS",
                "occurred_at": "2026-02-19T10:00:00Z",
                "line_items": [{"id": "li_1", "quantity": 1}],
            },
        ],
    },
}


# ==========================================================================
# Run simulated ADK tool flow
# ==========================================================================


async def simulate_tool_call(
    plugin: UCPAgentAnalyticsPlugin,
    tool_name: str,
    tool_args: dict,
    result: dict,
    delay_ms: float = 50,
):
    """Simulate before_tool → delay → after_tool ADK callback flow."""
    tool = MockTool(tool_name)
    ctx = MockToolContext()

    await plugin.before_tool_callback(
        tool=tool,
        tool_args=tool_args,
        tool_context=ctx,
    )

    # Simulate tool execution time
    await asyncio.sleep(delay_ms / 1000)

    await plugin.after_tool_callback(
        tool=tool,
        tool_args=tool_args,
        tool_context=ctx,
        result=result,
    )

    return result


async def run_adk_demo():
    print("\n" + "=" * 70)
    print("  UCP ANALYTICS — ADK Plugin BigQuery Demo")
    print("=" * 70)

    # Create ADK plugin (writes to BigQuery)
    plugin = UCPAgentAnalyticsPlugin(
        project_id=PROJECT_ID,
        dataset_id=DATASET_ID,
        table_id=TABLE_ID,
        app_name=APP_NAME,
        batch_size=1,  # Flush every event
        track_all_tools=False,
    )

    session_id = CHECKOUT_CREATED_RESULT["id"]

    # Step 1: Discovery
    print("\n-- Step 1: discover_merchant --")
    await simulate_tool_call(
        plugin,
        "discover_merchant",
        {},
        DISCOVERY_RESULT,
        delay_ms=30,
    )
    print("   Captured discovery event")

    # Step 2: Create checkout
    print("\n-- Step 2: create_checkout --")
    await simulate_tool_call(
        plugin,
        "create_checkout",
        {"line_items": [{"item_id": "roses", "quantity": 1}]},
        CHECKOUT_CREATED_RESULT,
        delay_ms=80,
    )
    print(f"   Session: {session_id}")
    print(f"   Status: {CHECKOUT_CREATED_RESULT['status']}")

    # Step 3: Update checkout
    print("\n-- Step 3: update_checkout --")
    await simulate_tool_call(
        plugin,
        "update_checkout",
        {"session_id": session_id, "buyer": {"email": "jane@example.com"}},
        CHECKOUT_UPDATED_RESULT,
        delay_ms=60,
    )
    print(f"   Status: {CHECKOUT_UPDATED_RESULT['status']}")

    # Step 4: Complete checkout
    print("\n-- Step 4: complete_checkout --")
    await simulate_tool_call(
        plugin,
        "complete_checkout",
        {"session_id": session_id, "payment_instrument": "instr_1"},
        CHECKOUT_COMPLETED_RESULT,
        delay_ms=120,
    )
    print(f"   Status: {CHECKOUT_COMPLETED_RESULT['status']}")
    print(f"   Order: {ORDER_ID}")

    # Step 5: Non-UCP tool (should be skipped)
    print("\n-- Step 5: get_weather (non-UCP, should be skipped) --")
    await simulate_tool_call(
        plugin,
        "get_weather",
        {"location": "San Francisco"},
        {"temperature": 68, "condition": "sunny"},
        delay_ms=20,
    )
    print("   Skipped (not a UCP tool)")

    # Flush and close
    print("\n   Flushing events to BigQuery...")
    await plugin.close()

    return session_id


async def verify_adk_bigquery(session_id: str):
    from google.cloud import bigquery

    print("\n" + "=" * 70)
    print("  ADK BIGQUERY VERIFICATION")
    print("=" * 70)

    client = bigquery.Client(project=PROJECT_ID)
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

    print("\n   Waiting 10s for BigQuery streaming buffer...")
    await asyncio.sleep(10)

    query = f"""
    SELECT event_type, checkout_status, total_amount, fulfillment_amount,
           payment_handler_id, ucp_version, latency_ms, app_name,
           checkout_session_id
    FROM `{table_ref}`
    WHERE app_name = '{APP_NAME}'
    ORDER BY timestamp DESC
    LIMIT 10
    """
    print(f"\n   Querying BigQuery for ADK events...")

    rows = list(client.query(query).result())

    if not rows:
        print("\n   WARNING: No rows found yet. Streaming buffer delay (~90s).")
        print(
            f"   Run manually: SELECT * FROM `{table_ref}` WHERE app_name = '{APP_NAME}'"
        )
        client.close()
        return

    print(f"\n   Found {len(rows)} ADK events in BigQuery:")
    print(f"   {'#':<3} {'Event Type':<30} {'Status':<22} {'Total':>9} {'Latency':>8}")
    print("   " + "-" * 75)

    for i, row in enumerate(rows, 1):
        total_str = f"${row.total_amount / 100:.2f}" if row.total_amount else ""
        status_str = row.checkout_status or ""
        latency_str = f"{row.latency_ms:.0f}ms" if row.latency_ms else ""
        print(
            f"   {i:<3} {row.event_type:<30} {status_str:<22} {total_str:>9} {latency_str:>8}"
        )

    event_types = {row.event_type for row in rows}
    print(f"\n   Event types: {sorted(event_types)}")

    # Verify only UCP events (not get_weather)
    print("\n   Verification:")
    checks = [
        (
            "request" in event_types or "profile_discovered" in event_types,
            "Discovery event captured",
        ),
        ("checkout_session_created" in event_types, "Checkout created captured"),
        (
            "checkout_session_updated" in event_types
            or "checkout_session_completed" in event_types,
            "Checkout lifecycle captured",
        ),
        (
            not any("weather" in (row.event_type or "") for row in rows),
            "Non-UCP tool correctly skipped",
        ),
    ]

    all_ok = True
    for ok, label in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"     [{status}] {label}")

    has_latency = any(row.latency_ms and row.latency_ms > 0 for row in rows)
    print(
        f"     [{'PASS' if has_latency else 'FAIL'}] Latency captured from tool timing"
    )
    if not has_latency:
        all_ok = False

    if all_ok:
        print("\n   All ADK BigQuery verifications passed!")
    else:
        print("\n   Some verifications failed.")

    client.close()


async def main():
    session_id = await run_adk_demo()
    await verify_adk_bigquery(session_id)
    print("\n   Done!")


if __name__ == "__main__":
    asyncio.run(main())
