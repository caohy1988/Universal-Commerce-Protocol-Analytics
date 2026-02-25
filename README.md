# Universal Commerce Protocol Analytics

**BigQuery-backed commerce analytics for the
[Universal Commerce Protocol (UCP)](https://ucp.dev).**

[Documentation](https://ucp.dev) |
[Specification](https://ucp.dev/specification/overview) |
[Discussions](https://github.com/Universal-Commerce-Protocol/ucp/discussions)

## Overview

UCP defines the protocol for agentic commerce — but ships no observability.
This package automatically captures checkout sessions, order lifecycle,
payment flows, capability negotiation, and identity linking events into
BigQuery for funnel analysis, error debugging, latency monitoring, and
revenue attribution.

Three integration points — pick any or combine:

| Integration | Side | One-liner |
|---|---|---|
| **FastAPI middleware** | Merchant server | `app.add_middleware(UCPAnalyticsMiddleware, tracker=t)` |
| **HTTPX event hook** | Agent / platform | `httpx.AsyncClient(event_hooks={"response": [hook]})` |
| **ADK plugin** *(optional)* | Google ADK agent | `Runner(plugins=[UCPAgentAnalyticsPlugin(...)])` |

```
 Platform (Agent)                    Business (Merchant)
 ┌───────────────┐                   ┌───────────────────┐
 │ httpx client   │                   │ FastAPI server     │
 │ + EventHook  ─────── REST ──────────► + Middleware     │
 └───────┬───────┘                   └────────┬──────────┘
         │                                    │
         └──────────┬─────────────────────────┘
                    ▼
           UCPAnalyticsTracker
           ├── UCPResponseParser   (classify + extract)
           └── AsyncBigQueryWriter (batch + flush)
                    │
                    ▼
               BigQuery
          PARTITION BY timestamp
          CLUSTER BY event_type,
            checkout_session_id,
            merchant_host
```

## Installation

```bash
# Core (tracker + HTTPX hook)
pip install ucp-analytics

# With FastAPI middleware
pip install ucp-analytics[fastapi]

# With Google ADK plugin adapter
pip install ucp-analytics[adk]
```

Or install from source:

```bash
git clone https://github.com/haiyuan-eng-google/Universal-Commerce-Protocol-Analytics.git
cd Universal-Commerce-Protocol-Analytics
uv sync
```

## Quick Start

### Merchant server (FastAPI)

Add two lines to your UCP `server.py`:

```python
from ucp_analytics import UCPAnalyticsTracker, UCPAnalyticsMiddleware

tracker = UCPAnalyticsTracker(project_id="my-project", app_name="flower_shop")
app.add_middleware(UCPAnalyticsMiddleware, tracker=tracker)

@app.on_event("shutdown")
async def shutdown():
    await tracker.close()
```

> **Note:** `UCPAnalyticsMiddleware` requires the `[fastapi]` extra.
> The middleware is lazy-loaded so the core package works without starlette installed.

### Agent / platform client (HTTPX)

```python
import httpx
from ucp_analytics import UCPAnalyticsTracker, UCPClientEventHook

tracker = UCPAnalyticsTracker(project_id="my-project", app_name="shopping_agent")
hook = UCPClientEventHook(tracker)

client = httpx.AsyncClient(event_hooks={"response": [hook]})
```

Every call to `/checkout-sessions`, `/.well-known/ucp`, `/orders`, `/carts`,
`/webhooks`, etc. is automatically classified and written to BigQuery.

## Events Tracked

Events are auto-classified from HTTP method + path + response status:

| HTTP Operation | Event Type |
|---|---|
| `GET /.well-known/ucp` | `profile_discovered` |
| `POST /checkout-sessions` | `checkout_session_created` |
| `PUT /checkout-sessions/{id}` | `checkout_session_updated` |
| `PUT /checkout-sessions/{id}` *(status=requires_escalation)* | `checkout_escalation` |
| `POST /checkout-sessions/{id}/complete` | `checkout_session_completed` |
| `POST /checkout-sessions/{id}/cancel` | `checkout_session_canceled` |
| `GET /checkout-sessions/{id}` | `checkout_session_get` |
| `POST /carts` | `cart_created` |
| `GET /carts/{id}` | `cart_get` |
| `PUT /carts/{id}` | `cart_updated` |
| `POST /carts/{id}/cancel` | `cart_canceled` |
| `POST /orders` | `order_created` |
| `GET /orders/{id}` | `order_updated` |
| `POST /webhooks/partners/{id}/events/order` | *(by request body status)* |
| Any unmatched path, status >= 400 | `error` |

Webhook paths use the **request body** (order payload) for classification since
the response is typically an ack. Webhook 4xx/5xx responses classify as `error`.

## Configuration

```python
UCPAnalyticsTracker(
    project_id="my-project",        # required — GCP project
    dataset_id="ucp_analytics",     # BigQuery dataset
    table_id="ucp_events",          # BigQuery table
    app_name="flower_shop",         # tags every event
    batch_size=50,                  # flush every N events
    auto_create_table=True,         # create table on first write
    redact_pii=False,               # redact email, phone, address
    custom_metadata={"env": "prod"},
)
```

The underlying `AsyncBigQueryWriter` also accepts `max_buffer_size`
(default: 10,000) to cap in-memory buffering when BigQuery is unreachable.

**BigQuery schema notes (v0.2 spec alignment):** The schema uses `fulfillment_amount`
(replacing the earlier `shipping_amount`) to align with UCP spec total types. Additional
fields include `items_discount_amount`, `fee_amount`, `discount_codes_json`,
`discount_applied_json` (discount extension), `expires_at`, `continue_url`
(checkout metadata), and `permalink_url` (order permalink).

## Dashboard Queries

See [`dashboards/queries.sql`](dashboards/queries.sql) for 10 ready-to-use
BigQuery queries: checkout funnel, revenue by merchant, payment handler mix,
capability adoption, error analysis, escalation rate, latency percentiles,
fulfillment geography, session timeline, and discovery-to-checkout rate.

## Repository Structure

```
Universal-Commerce-Protocol-Analytics/
├── src/ucp_analytics/
│   ├── __init__.py                 # public API exports (lazy-loads middleware)
│   ├── events.py                   # UCPEvent, UCPEventType, CheckoutStatus
│   ├── parser.py                   # classify HTTP→event, extract fields
│   ├── writer.py                   # AsyncBigQueryWriter (batch + DDL)
│   ├── tracker.py                  # UCPAnalyticsTracker (orchestrator)
│   ├── middleware.py               # FastAPI/Starlette ASGI middleware
│   ├── client_hooks.py             # HTTPX event hook for agent clients
│   └── adk_plugin.py              # optional ADK BasePlugin adapter
├── tests/
│   ├── test_parser.py              # classify + extract unit tests
│   ├── test_events.py              # UCPEvent + enum tests
│   ├── test_tracker.py             # tracker + PII redaction tests
│   ├── test_writer.py              # buffer, flush, retry, DDL tests
│   └── test_client_hooks.py        # HTTPX hook tests
├── examples/
│   ├── _demo_utils.py              # shared BQ config + helpers
│   ├── e2e_demo.py                 # Self-contained E2E demo (no GCP)
│   ├── bq_demo.py                  # All 27 event types, 3 transports
│   ├── bq_adk_demo.py             # ADK plugin demo, all 27 types
│   └── ...                         # scenarios, cart, order, identity demos
├── docs/
│   ├── design_doc.md               # Design document
│   └── bigquery-ucp-analytics.md   # BigQuery integration guide
├── pyproject.toml                  # hatchling + uv + ruff
└── LICENSE                         # Apache 2.0
```


## Contributing

We welcome community contributions. See the UCP
[Contribution Guide](https://github.com/Universal-Commerce-Protocol/ucp/blob/main/CONTRIBUTING.md)
for details.

## License

UCP is an open-source project under the
[Apache License 2.0](LICENSE).
