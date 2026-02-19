#!/usr/bin/env python3
"""
BigQuery E2E Demo — Real BigQuery Integration
===============================================

Runs a mini UCP merchant server + shopping agent client, captures analytics
events using the real UCPAnalyticsTracker → BigQuery pipeline, then queries
BigQuery to verify the data landed correctly.

Requires:
    - gcloud auth application-default login (or service account)
    - BigQuery API enabled

Usage:
    uv run python examples/bq_demo.py
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ucp_analytics import (
    UCPAnalyticsMiddleware,
    UCPAnalyticsTracker,
    UCPClientEventHook,
)

# ==========================================================================
# Config
# ==========================================================================

PROJECT_ID = "test-project-0728-467323"
DATASET_ID = "ucp_analytics"
TABLE_ID = "ucp_events"
APP_NAME = "bq_demo"

# ==========================================================================
# Mini UCP Merchant Server
# ==========================================================================

SESSIONS: Dict[str, dict] = {}
ORDERS: Dict[str, dict] = {}
UCP_VERSION = "2026-01-11"

PRODUCTS = {
    "bouquet_roses": {"title": "Red Rose Bouquet", "price": 2999},
    "sunflower_bunch": {"title": "Sunflower Bunch", "price": 1999},
}

# Server-side tracker (middleware will use this)
server_tracker: UCPAnalyticsTracker | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if server_tracker:
        await server_tracker.close()


app = FastAPI(title="UCP Flower Shop (BQ Demo)", lifespan=lifespan)


PAYMENT_HANDLERS = [
    {
        "id": "mock_payment_handler",
        "name": "com.mock.payment",
        "version": UCP_VERSION,
        "spec": "https://ucp.dev/specs/mock",
        "config_schema": "https://ucp.dev/schemas/mock.json",
        "instrument_schemas": [],
        "config": {},
    },
]


@app.get("/.well-known/ucp")
async def discovery():
    return {
        "ucp": {
            "version": UCP_VERSION,
            "services": {
                "dev.ucp.shopping": {
                    "version": UCP_VERSION,
                    "spec": "https://ucp.dev/specs/shopping",
                    "rest": {
                        "schema": "https://ucp.dev/services/shopping/openapi.json",
                        "endpoint": "http://localhost:8199",
                    },
                },
            },
            "capabilities": [
                {"name": "dev.ucp.shopping.checkout", "version": UCP_VERSION},
                {
                    "name": "dev.ucp.shopping.fulfillment",
                    "version": UCP_VERSION,
                    "extends": "dev.ucp.shopping.checkout",
                },
                {
                    "name": "dev.ucp.shopping.discount",
                    "version": UCP_VERSION,
                    "extends": "dev.ucp.shopping.checkout",
                },
            ],
        },
        "payment": {
            "handlers": PAYMENT_HANDLERS,
        },
    }


@app.post("/checkout-sessions")
async def create_checkout(request: Request):
    body = await request.json()
    session_id = f"chk_{uuid.uuid4().hex[:12]}"

    line_items = []
    subtotal = 0
    for item in body.get("line_items", []):
        product_id = item.get("item", {}).get("id", "")
        product = PRODUCTS.get(product_id, {"title": product_id, "price": 0})
        qty = item.get("quantity", 1)
        li = {
            "id": f"li_{uuid.uuid4().hex[:8]}",
            "item": {
                "id": product_id,
                "title": product["title"],
                "price": product["price"],
            },
            "quantity": qty,
        }
        line_items.append(li)
        subtotal += product["price"] * qty

    tax = round(subtotal * 0.0875)
    total = subtotal + tax

    session = {
        "ucp": {
            "version": UCP_VERSION,
            "capabilities": [
                {"name": "dev.ucp.shopping.checkout", "version": UCP_VERSION},
                {"name": "dev.ucp.shopping.fulfillment", "version": UCP_VERSION},
            ],
        },
        "id": session_id,
        "status": "incomplete",
        "currency": body.get("currency", "USD"),
        "line_items": line_items,
        "totals": [
            {"type": "subtotal", "amount": subtotal},
            {"type": "tax", "amount": tax},
            {"type": "total", "amount": total},
        ],
        "buyer": body.get("buyer", {}),
        "payment": {
            "handlers": PAYMENT_HANDLERS,
            "instruments": [
                {
                    "id": "instr_card",
                    "handler_id": "mock_payment_handler",
                    "type": "card",
                    "brand": "Visa",
                },
            ],
        },
        "messages": [],
        "links": [],
    }
    SESSIONS[session_id] = session
    return JSONResponse(session, status_code=201)


@app.put("/checkout-sessions/{session_id}")
async def update_checkout(session_id: str, request: Request):
    if session_id not in SESSIONS:
        return JSONResponse({"error": "not_found"}, status_code=404)

    body = await request.json()
    session = SESSIONS[session_id]

    if "buyer" in body:
        session["buyer"] = body["buyer"]

    if "fulfillment" in body:
        session["fulfillment"] = body["fulfillment"]
        shipping = 599
        session["totals"].append({"type": "fulfillment", "amount": shipping})
        subtotal = next(
            t["amount"] for t in session["totals"] if t["type"] == "subtotal"
        )
        tax = next(t["amount"] for t in session["totals"] if t["type"] == "tax")
        session["totals"] = [t for t in session["totals"] if t["type"] != "total"]
        session["totals"].append({"type": "total", "amount": subtotal + tax + shipping})

    if "discounts" in body:
        discount_amount = 500
        session["totals"].append({"type": "discount", "amount": discount_amount})
        total_entry = next(t for t in session["totals"] if t["type"] == "total")
        total_entry["amount"] -= discount_amount
        session["discounts"] = {
            "codes": body["discounts"].get("codes", []),
            "applied": [
                {
                    "code": "FLOWERS10",
                    "title": "Flower discount",
                    "amount": discount_amount,
                }
            ],
        }

    has_buyer = bool(session.get("buyer", {}).get("email"))
    has_fulfillment = "fulfillment" in session
    session["status"] = (
        "ready_for_complete" if (has_buyer and has_fulfillment) else "incomplete"
    )

    SESSIONS[session_id] = session
    return JSONResponse(session)


@app.post("/checkout-sessions/{session_id}/complete")
async def complete_checkout(session_id: str, request: Request):
    if session_id not in SESSIONS:
        return JSONResponse({"error": "not_found"}, status_code=404)

    body = await request.json()
    session = SESSIONS[session_id]
    payment = body.get("payment", {})

    if not payment.get("instruments"):
        session["status"] = "requires_escalation"
        session["continue_url"] = "https://shop.example.com/checkout/escalate"
        session["messages"] = [
            {
                "type": "error",
                "code": "missing_payment",
                "content": "Payment instrument required",
                "severity": "requires_buyer_input",
            },
        ]
        SESSIONS[session_id] = session
        return JSONResponse(session, status_code=400)

    order_id = f"order_{uuid.uuid4().hex[:10]}"
    session["status"] = "completed"
    session["order"] = {
        "id": order_id,
        "permalink_url": f"https://shop.example.com/orders/{order_id}",
    }

    ORDERS[order_id] = {
        "id": order_id,
        "checkout_id": session_id,
        "permalink_url": f"https://shop.example.com/orders/{order_id}",
        "status": "confirmed",
        "line_items": session["line_items"],
        "totals": session["totals"],
        "fulfillment": {
            "expectations": [
                {
                    "id": "exp_1",
                    "method_type": "shipping",
                    "destination": {"address_country": "US", "postal_code": "94043"},
                    "line_items": [
                        {"id": li["id"], "quantity": li["quantity"]}
                        for li in session["line_items"]
                    ],
                    "description": "Standard Shipping",
                },
            ],
        },
    }

    SESSIONS[session_id] = session
    return JSONResponse(session)


@app.post("/testing/simulate-shipping/{order_id}")
async def simulate_shipping(order_id: str):
    if order_id not in ORDERS:
        return JSONResponse({"error": "not_found"}, status_code=404)
    ORDERS[order_id]["status"] = "shipped"
    ORDERS[order_id]["fulfillment"]["events"] = [
        {
            "type": "shipped",
            "tracking_number": "9400111899223456789012",
            "carrier": "USPS",
        },
    ]
    return JSONResponse(ORDERS[order_id])


# ==========================================================================
# Shopping Agent (HTTPX + UCPClientEventHook → BigQuery)
# ==========================================================================


async def run_shopping_agent(client_tracker: UCPAnalyticsTracker):
    print("\n" + "=" * 70)
    print("  UCP ANALYTICS — BigQuery E2E Demo")
    print("=" * 70)

    hook = UCPClientEventHook(client_tracker)

    async with httpx.AsyncClient(
        base_url="http://localhost:8199",
        event_hooks={"response": [hook]},
        headers={
            "UCP-Agent": 'profile="https://agent.example.com/profile"',
            "Content-Type": "application/json",
        },
    ) as client:
        # Step 1: Discovery
        print("\n-- Step 1: Discover Merchant --")
        resp = await client.get("/.well-known/ucp")
        profile = resp.json()
        print(f"   UCP version: {profile['ucp']['version']}")
        caps = [c["name"] for c in profile["ucp"]["capabilities"]]
        print(f"   Capabilities: {caps}")

        # Step 2: Create Checkout
        print("\n-- Step 2: Create Checkout --")
        resp = await client.post(
            "/checkout-sessions",
            json={
                "line_items": [
                    {"item": {"id": "bouquet_roses"}, "quantity": 2},
                    {"item": {"id": "sunflower_bunch"}, "quantity": 1},
                ],
                "buyer": {"full_name": "Jane Doe"},
                "currency": "USD",
            },
            headers={
                "Idempotency-Key": str(uuid.uuid4()),
                "Request-Id": str(uuid.uuid4()),
            },
        )
        checkout = resp.json()
        session_id = checkout["id"]
        total = next(t["amount"] for t in checkout["totals"] if t["type"] == "total")
        print(f"   Session: {session_id}")
        print(f"   Status: {checkout['status']}")
        print(f"   Total: ${total / 100:.2f}")

        # Step 3: Update — buyer + fulfillment
        print("\n-- Step 3: Update (buyer + fulfillment) --")
        resp = await client.put(
            f"/checkout-sessions/{session_id}",
            json={
                "buyer": {
                    "full_name": "Jane Doe",
                    "email": "jane@example.com",
                },
                "fulfillment": {
                    "methods": [
                        {
                            "id": "method_1",
                            "type": "shipping",
                            "line_item_ids": [],
                            "destinations": [
                                {
                                    "id": "dest_1",
                                    "address_country": "US",
                                    "postal_code": "94043",
                                    "address_locality": "Mountain View",
                                    "address_region": "CA",
                                },
                            ],
                        }
                    ]
                },
            },
        )
        updated = resp.json()
        total = next(t["amount"] for t in updated["totals"] if t["type"] == "total")
        print(f"   Status: {updated['status']}")
        print(f"   Total (with fulfillment): ${total / 100:.2f}")

        # Step 4: Update — apply discount
        print("\n-- Step 4: Apply Discount --")
        resp = await client.put(
            f"/checkout-sessions/{session_id}",
            json={"discounts": {"codes": ["FLOWERS10"]}},
        )
        discounted = resp.json()
        total = next(t["amount"] for t in discounted["totals"] if t["type"] == "total")
        print(f"   Discount applied, new total: ${total / 100:.2f}")

        # Step 5: Complete Checkout
        print("\n-- Step 5: Complete Checkout --")
        resp = await client.post(
            f"/checkout-sessions/{session_id}/complete",
            json={
                "payment": {
                    "instruments": [
                        {
                            "id": "instr_card",
                            "handler_id": "com.mock.payment",
                            "type": "card",
                            "brand": "Visa",
                            "credential": {"type": "token", "token": "tok_success"},
                        },
                    ],
                },
            },
            headers={
                "Idempotency-Key": str(uuid.uuid4()),
                "Request-Id": str(uuid.uuid4()),
            },
        )
        completed = resp.json()
        order_info = completed.get("order", {})
        print(f"   Status: {completed['status']}")
        print(f"   Order: {order_info.get('id')}")
        print(f"   Permalink: {order_info.get('permalink_url')}")
        order_id = order_info["id"]

        # Step 6: Simulate shipping
        print("\n-- Step 6: Simulate Shipping --")
        resp = await client.post(f"/testing/simulate-shipping/{order_id}")
        shipped = resp.json()
        print(f"   Order status: {shipped['status']}")

    return session_id, order_id


# ==========================================================================
# Verify in BigQuery
# ==========================================================================


async def verify_bigquery(session_id: str):
    from google.cloud import bigquery

    print("\n" + "=" * 70)
    print("  BIGQUERY VERIFICATION")
    print("=" * 70)

    client = bigquery.Client(project=PROJECT_ID)
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

    # Wait a moment for streaming buffer to be queryable
    print("\n   Waiting 10s for BigQuery streaming buffer...")
    await asyncio.sleep(10)

    # Query events for this session
    query = f"""
    SELECT event_type, checkout_status, total_amount, fulfillment_amount,
           payment_handler_id, ucp_version, latency_ms, discount_codes_json,
           permalink_url, http_method, http_path
    FROM `{table_ref}`
    WHERE app_name = '{APP_NAME}'
      AND checkout_session_id = '{session_id}'
    ORDER BY timestamp
    """
    print(f"\n   Querying BigQuery for session {session_id}...")

    rows = list(client.query(query).result())

    if not rows:
        # Also check events without checkout_session_id (discovery, shipping)
        query_all = f"""
        SELECT event_type, checkout_status, total_amount, fulfillment_amount,
               payment_handler_id, ucp_version, latency_ms, http_method, http_path
        FROM `{table_ref}`
        WHERE app_name = '{APP_NAME}'
        ORDER BY timestamp DESC
        LIMIT 20
        """
        rows = list(client.query(query_all).result())

    if not rows:
        print("\n   WARNING: No rows found in BigQuery yet.")
        print("   Streaming inserts may take up to 90 seconds to be queryable.")
        print(f"   Run this query manually later:")
        print(
            f"   SELECT * FROM `{table_ref}` WHERE app_name = '{APP_NAME}' ORDER BY timestamp"
        )
        return

    print(f"\n   Found {len(rows)} events in BigQuery:")
    print(f"   {'#':<3} {'Event Type':<30} {'Status':<22} {'Total':>9} {'Latency':>8}")
    print("   " + "-" * 75)

    for i, row in enumerate(rows, 1):
        total_str = f"${row.total_amount / 100:.2f}" if row.total_amount else ""
        status_str = row.checkout_status or ""
        latency_str = f"{row.latency_ms:.0f}ms" if row.latency_ms else ""
        print(
            f"   {i:<3} {row.event_type:<30} {status_str:<22} {total_str:>9} {latency_str:>8}"
        )

    # Verify key fields
    event_types = {row.event_type for row in rows}
    print(f"\n   Event types captured: {sorted(event_types)}")

    checks = [
        ("checkout_session_created" in event_types, "checkout_session_created"),
        ("checkout_session_updated" in event_types, "checkout_session_updated"),
        ("checkout_session_completed" in event_types, "checkout_session_completed"),
    ]

    print("\n   Verification:")
    all_ok = True
    for ok, label in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"     [{status}] {label}")

    # Check spec-aligned fields
    has_fulfillment_amount = any(row.fulfillment_amount for row in rows)
    has_payment_handler = any(row.payment_handler_id for row in rows)
    has_ucp_version = any(row.ucp_version for row in rows)

    print(
        f"     [{'PASS' if has_fulfillment_amount else 'FAIL'}] fulfillment_amount extracted"
    )
    print(
        f"     [{'PASS' if has_payment_handler else 'FAIL'}] payment_handler_id extracted"
    )
    print(f"     [{'PASS' if has_ucp_version else 'FAIL'}] ucp_version extracted")

    if not has_fulfillment_amount:
        all_ok = False
    if not has_payment_handler:
        all_ok = False

    if all_ok:
        print("\n   All BigQuery verifications passed!")
    else:
        print("\n   Some verifications failed — check streaming buffer delay.")

    client.close()


# ==========================================================================
# Main
# ==========================================================================


async def main():
    # Create trackers — one for the server side, one for the client side
    global server_tracker
    server_tracker = UCPAnalyticsTracker(
        project_id=PROJECT_ID,
        dataset_id=DATASET_ID,
        table_id=TABLE_ID,
        app_name=APP_NAME,
        batch_size=1,  # Flush every event for the demo
        auto_create_table=True,
    )
    app.add_middleware(UCPAnalyticsMiddleware, tracker=server_tracker)

    client_tracker = UCPAnalyticsTracker(
        project_id=PROJECT_ID,
        dataset_id=DATASET_ID,
        table_id=TABLE_ID,
        app_name=APP_NAME,
        batch_size=1,
        auto_create_table=True,
    )

    # Start server
    config = uvicorn.Config(app, host="127.0.0.1", port=8199, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    # Wait for server
    for _ in range(50):
        try:
            async with httpx.AsyncClient() as c:
                await c.get("http://127.0.0.1:8199/.well-known/ucp")
            break
        except httpx.ConnectError:
            await asyncio.sleep(0.1)

    try:
        # Run agent
        session_id, order_id = await run_shopping_agent(client_tracker)

        # Flush all events
        print("\n   Flushing events to BigQuery...")
        await client_tracker.close()
        await server_tracker.close()

        # Verify in BigQuery
        await verify_bigquery(session_id)

    finally:
        server.should_exit = True
        await server_task

    print("\n   Done!")


if __name__ == "__main__":
    asyncio.run(main())
