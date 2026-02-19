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
| **ADK plugin** *(optional)* | Google ADK agent | `Runner(plugins=[UCPAdkPlugin(...)])` |

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
git clone https://github.com/Universal-Commerce-Protocol/analytics.git
cd analytics
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

### Agent / platform client (HTTPX)

```python
import httpx
from ucp_analytics import UCPAnalyticsTracker, UCPClientEventHook

tracker = UCPAnalyticsTracker(project_id="my-project", app_name="shopping_agent")
hook = UCPClientEventHook(tracker)

client = httpx.AsyncClient(event_hooks={"response": [hook]})
```

Every call to `/checkout-sessions`, `/.well-known/ucp`, `/orders`, etc.
is automatically classified and written to BigQuery.

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
| Order webhooks | `order_created`, `order_shipped`, … |

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

## Dashboard Queries

See [`dashboards/queries.sql`](dashboards/queries.sql) for 10 ready-to-use
BigQuery queries: checkout funnel, revenue by merchant, payment handler mix,
capability adoption, error analysis, escalation rate, latency percentiles,
fulfillment geography, session timeline, and discovery-to-checkout rate.

## Contributing

We welcome community contributions. See the UCP
[Contribution Guide](https://github.com/Universal-Commerce-Protocol/ucp/blob/main/CONTRIBUTING.md)
for details.

## License

UCP is an open-source project under the
[Apache License 2.0](LICENSE).
