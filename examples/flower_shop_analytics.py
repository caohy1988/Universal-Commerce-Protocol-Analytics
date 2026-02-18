"""
Example: Add UCP Analytics to the official UCP Samples flower shop server.

This shows how to instrument the reference UCP merchant server
(Universal-Commerce-Protocol/samples/rest/python/server) with
BigQuery analytics using a single middleware line.

Setup:
    1. Clone the repos as described in the UCP samples README
    2. pip install ucp-analytics[fastapi]
    3. Add the middleware to server.py (see below)
    4. Run the server + client as normal — events flow to BigQuery

Minimal change to server.py:
    ```python
    # At the top of server.py, add:
    from ucp_analytics import UCPAnalyticsTracker, UCPAnalyticsMiddleware

    # After app = FastAPI(...), add:
    tracker = UCPAnalyticsTracker(
        project_id="my-gcp-project",
        app_name="flower_shop",
    )
    app.add_middleware(UCPAnalyticsMiddleware, tracker=tracker)

    # In the shutdown event:
    @app.on_event("shutdown")
    async def shutdown():
        await tracker.close()
    ```

That's it. Every checkout-session create/update/complete, every
discovery call, every order webhook — all captured in BigQuery.
"""

import asyncio
import httpx
from ucp_analytics import UCPAnalyticsTracker, UCPClientEventHook


async def demo_client_side_tracking():
    """Demo: Track UCP calls from the agent/platform (client) side.

    This mirrors the simple_happy_path_client.py from the UCP samples
    but with analytics automatically captured via HTTPX event hook.
    """

    # 1. Create tracker
    tracker = UCPAnalyticsTracker(
        project_id="my-gcp-project",
        dataset_id="ucp_analytics",
        table_id="ucp_events",
        app_name="shopping_agent",
        redact_pii=True,
    )

    # 2. Create HTTPX client with UCP event hook
    hook = UCPClientEventHook(tracker)
    async with httpx.AsyncClient(
        base_url="http://localhost:8182",
        event_hooks={"response": [hook]},
        headers={"UCP-Agent": 'profile="https://agent.example.com/profile"'},
    ) as client:

        # 3. Discover merchant capabilities
        print("=== Discovery ===")
        resp = await client.get("/.well-known/ucp")
        profile = resp.json()
        print(f"UCP version: {profile['ucp']['version']}")
        capabilities = profile["ucp"]["capabilities"]
        print(f"Capabilities: {[c['name'] for c in capabilities]}")

        # 4. Create checkout session
        print("\n=== Create Checkout ===")
        resp = await client.post(
            "/checkout-sessions",
            json={
                "line_items": [
                    {
                        "item": {"id": "rose_bouquet_01", "title": "Rose Bouquet"},
                        "quantity": 1,
                    }
                ],
                "currency": "USD",
            },
        )
        checkout = resp.json()
        session_id = checkout["id"]
        print(f"Session: {session_id}")
        print(f"Status: {checkout['status']}")
        print(f"Total: ${checkout['totals'][-1]['amount'] / 100:.2f}")

        # 5. Update with buyer info + fulfillment
        print("\n=== Update Checkout ===")
        resp = await client.put(
            f"/checkout-sessions/{session_id}",
            json={
                **checkout,
                "buyer": {
                    "email": "jane@example.com",
                    "first_name": "Jane",
                    "last_name": "Doe",
                },
                "fulfillment": {
                    "methods": [
                        {
                            "id": "ship_1",
                            "type": "shipping",
                            "destinations": [
                                {
                                    "id": "home",
                                    "address_country": "US",
                                    "address_region": "CA",
                                    "postal_code": "94043",
                                }
                            ],
                        }
                    ]
                },
            },
        )
        updated = resp.json()
        print(f"Status: {updated['status']}")

        # 6. Complete checkout
        print("\n=== Complete Checkout ===")
        resp = await client.post(
            f"/checkout-sessions/{session_id}/complete",
            json={
                "payment_data": {
                    "handler_id": "gpay",
                    "type": "card",
                    "brand": "visa",
                }
            },
        )
        completed = resp.json()
        print(f"Status: {completed['status']}")
        print(f"Order: {completed.get('order_id', 'N/A')}")

    # 7. Flush & close
    await tracker.close()

    print("\n=== Analytics Ready ===")
    print("""
    -- Checkout funnel for this session
    SELECT event_type, checkout_status, total_amount, latency_ms
    FROM `my-gcp-project.ucp_analytics.ucp_events`
    ORDER BY timestamp;
    """)


if __name__ == "__main__":
    asyncio.run(demo_client_side_tracking())
