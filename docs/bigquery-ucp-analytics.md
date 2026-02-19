# BigQuery Commerce Analytics for UCP

## Overview

This plugin provides **structured commerce observability** for agents and merchants
using the [Universal Commerce Protocol (UCP)](https://ucp.dev). It captures checkout
sessions, cart operations, order lifecycle, payment flows, capability negotiation,
and identity linking events into Google BigQuery for funnel analysis, error debugging,
latency monitoring, and revenue attribution.

Three integration points — pick any or combine:

| Integration | Side | How |
|---|---|---|
| **FastAPI middleware** | Merchant server | Intercepts inbound UCP HTTP traffic |
| **HTTPX event hook** | Agent / platform client | Intercepts outbound UCP HTTP calls |
| **ADK plugin** | Google ADK agent | Wraps tool callbacks into UCP events |

All three route events through the same `UCPAnalyticsTracker` → `AsyncBigQueryWriter`
pipeline, which batches rows and streams them into a partitioned, clustered BigQuery table.

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

> **Spec alignment:** All field names, total types, payment structures, and metadata
> envelopes follow the [official UCP specification](https://github.com/Universal-Commerce-Protocol/ucp).

---

## Key Use Cases

- **Checkout funnel analysis** — Measure conversion from discovery → cart → checkout → completion
- **Revenue attribution** — Track GMV by merchant, payment handler, fulfillment type, and geography
- **Latency monitoring** — Percentile breakdowns (p50/p95/p99) per operation type
- **Error debugging** — Surface escalations, failed payments, and server error messages
- **Capability adoption** — Track which UCP capabilities and extensions merchants support
- **Discount effectiveness** — Analyze discount code usage and applied discount allocations
- **Agent performance** — Compare checkout success rates across ADK agents

---

## Prerequisites

| Requirement | Details |
|---|---|
| **GCP Project** | BigQuery API enabled |
| **Auth (local)** | `gcloud auth application-default login` |
| **Python** | 3.10+ |
| **Package** | `pip install ucp-analytics` (core) |

### Enable BigQuery API

```bash
gcloud services enable bigquery.googleapis.com
```

### Authenticate

```bash
gcloud auth application-default login
```

### Required IAM Roles

| Role | Scope | Purpose |
|---|---|---|
| `roles/bigquery.jobUser` | Project | Run queries and streaming inserts |
| `roles/bigquery.dataEditor` | Dataset | Write event rows |

> **Note:** If `auto_create_table=True` (the default), the service account also needs
> `roles/bigquery.dataOwner` at the dataset level to create the table and dataset on
> first write.

---

## Installation

```bash
# Core (tracker + HTTPX hook)
pip install ucp-analytics

# With FastAPI middleware support
pip install ucp-analytics[fastapi]

# With Google ADK plugin adapter
pip install ucp-analytics[adk]

# All extras
pip install ucp-analytics[fastapi,adk]
```

Or install from source with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/haiyuan-eng-google/Universal-Commerce-Protocol-Analytics.git
cd Universal-Commerce-Protocol-Analytics
uv sync              # core
uv sync --all-extras # all extras
```

---

## Usage

### Integration 1: FastAPI Middleware (Merchant Server)

Add two lines to your UCP merchant server to capture every inbound checkout,
cart, order, and discovery request:

```python
from fastapi import FastAPI
from ucp_analytics import UCPAnalyticsTracker, UCPAnalyticsMiddleware

app = FastAPI()

tracker = UCPAnalyticsTracker(
    project_id="my-gcp-project",
    app_name="flower_shop",
)
app.add_middleware(UCPAnalyticsMiddleware, tracker=tracker)

@app.on_event("shutdown")
async def shutdown():
    await tracker.close()
```

**How it works:**

1. The middleware checks if the request path matches a UCP operation
   (`/checkout-sessions`, `/carts`, `/.well-known/ucp`, `/orders`, `/identity`)
2. Reads the request body (for POST/PUT/PATCH)
3. Lets the handler execute normally and measures latency
4. Reads the response body
5. Passes both to `UCPAnalyticsTracker.record_http()` as a fire-and-forget task
6. Non-UCP paths pass through with zero overhead

> **Requires:** `pip install ucp-analytics[fastapi]`

### Integration 2: HTTPX Client Hook (Agent / Platform)

Instrument your shopping agent's HTTP client to capture every outbound UCP call:

```python
import httpx
from ucp_analytics import UCPAnalyticsTracker, UCPClientEventHook

tracker = UCPAnalyticsTracker(
    project_id="my-gcp-project",
    app_name="shopping_agent",
)
hook = UCPClientEventHook(tracker)

async with httpx.AsyncClient(
    event_hooks={"response": [hook]},
) as client:
    # Every UCP call is automatically tracked
    resp = await client.get("https://merchant.example.com/.well-known/ucp")
    resp = await client.post(
        "https://merchant.example.com/checkout-sessions",
        json={"line_items": [{"item_id": "roses", "quantity": 1}]},
    )

await tracker.close()
```

**How it works:**

1. The hook fires after every HTTP response
2. Checks if the request path contains a UCP pattern
3. Reads both request and response bodies
4. Records latency from `response.elapsed`
5. Passes everything to `UCPAnalyticsTracker.record_http()`

### Integration 3: ADK Plugin (Google ADK Agent)

For agents built with the [Google Agent Development Kit](https://google.github.io/adk-docs/),
the `UCPAgentAnalyticsPlugin` wraps UCP analytics into ADK's `BasePlugin` interface:

```python
from google.adk.runners import InMemoryRunner
from ucp_analytics.adk_plugin import UCPAgentAnalyticsPlugin

plugin = UCPAgentAnalyticsPlugin(
    project_id="my-gcp-project",
    dataset_id="ucp_analytics",
    app_name="adk_shopping_agent",
    batch_size=1,         # flush every event for demos
    track_all_tools=False, # only record UCP tools
)

runner = InMemoryRunner(agent=my_agent, plugins=[plugin])

# ... run your agent ...

await plugin.close()
```

**How it works:**

1. `before_tool_callback` records a start timestamp
2. `after_tool_callback` fires after the tool returns
3. The plugin checks if the tool name matches a UCP pattern (e.g., `create_checkout`, `discover_merchant`)
4. Maps the tool name to an equivalent HTTP operation via `_TOOL_TO_HTTP` lookup table
5. Classifies the event type using the same `UCPResponseParser` as HTTP integrations
6. Extracts structured fields from the tool result
7. Computes latency from the before/after timing gap
8. Writes the event to BigQuery
9. Non-UCP tools (e.g., `get_weather`) are silently skipped

> **Requires:** `pip install ucp-analytics[adk]`

#### ADK Tool Name Mapping

The plugin maps ADK tool names to equivalent UCP HTTP operations for accurate
event classification:

| Tool Name | HTTP Equivalent | Event Type |
|---|---|---|
| `discover_merchant` | `GET /.well-known/ucp` | `profile_discovered` |
| `create_checkout` | `POST /checkout-sessions` | `checkout_session_created` |
| `update_checkout` | `PUT /checkout-sessions/{id}` | `checkout_session_updated` |
| `complete_checkout` | `POST /checkout-sessions/{id}/complete` | `checkout_session_completed` |
| `cancel_checkout` | `POST /checkout-sessions/{id}/cancel` | `checkout_session_canceled` |
| `create_cart` | `POST /carts` | `cart_created` |
| `update_cart` | `PUT /carts/{id}` | `cart_updated` |
| `cancel_cart` | `POST /carts/{id}/cancel` | `cart_canceled` |
| `create_order` | `POST /orders` | `order_created` |
| `get_weather` | *(not a UCP tool)* | *(skipped)* |

Tools not in the mapping are skipped by default. Set `track_all_tools=True` to
record them as generic `request` events.

### Integration 4: Direct API

For custom integrations, call the tracker directly:

```python
from ucp_analytics import UCPAnalyticsTracker

tracker = UCPAnalyticsTracker(project_id="my-gcp-project")

event = await tracker.record_http(
    method="POST",
    path="/checkout-sessions",
    status_code=201,
    response_body={
        "id": "chk_abc123",
        "status": "incomplete",
        "currency": "USD",
        "totals": [
            {"type": "subtotal", "amount": 2999},
            {"type": "total", "amount": 2999},
        ],
    },
    latency_ms=42.5,
)

await tracker.close()
```

Or construct events manually:

```python
from ucp_analytics import UCPAnalyticsTracker, UCPEvent

tracker = UCPAnalyticsTracker(project_id="my-gcp-project")

event = UCPEvent(
    event_type="checkout_session_created",
    app_name="my_app",
    checkout_session_id="chk_abc123",
    checkout_status="incomplete",
    currency="USD",
    total_amount=2999,
    latency_ms=42.5,
)
await tracker.record_event(event)

await tracker.close()
```

---

## Configuration Options

### `UCPAnalyticsTracker`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_id` | `str` | *(required)* | Google Cloud project ID |
| `dataset_id` | `str` | `"ucp_analytics"` | BigQuery dataset name |
| `table_id` | `str` | `"ucp_events"` | BigQuery table name |
| `app_name` | `str` | `""` | Application name tag on every event |
| `batch_size` | `int` | `50` | Flush to BigQuery every N events |
| `auto_create_table` | `bool` | `True` | Create dataset + table on first write |
| `redact_pii` | `bool` | `False` | Redact PII fields before writing |
| `pii_fields` | `list[str]` | `["email", "phone", ...]` | Fields to redact when `redact_pii=True` |
| `custom_metadata` | `dict[str, str]` | `None` | Static key-value pairs added as JSON to every event |

### `AsyncBigQueryWriter`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_id` | `str` | *(required)* | Google Cloud project ID |
| `dataset_id` | `str` | *(required)* | BigQuery dataset name |
| `table_id` | `str` | `"ucp_events"` | BigQuery table name |
| `batch_size` | `int` | `50` | Events per write batch |
| `auto_create_table` | `bool` | `True` | Auto-create table on first write |
| `max_buffer_size` | `int` | `10000` | In-memory buffer cap; oldest events dropped when full |

### `UCPAgentAnalyticsPlugin` (ADK)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_id` | `str` | *(required)* | Google Cloud project ID |
| `dataset_id` | `str` | `"ucp_analytics"` | BigQuery dataset name |
| `table_id` | `str` | `"ucp_events"` | BigQuery table name |
| `app_name` | `str` | `""` | Application name tag |
| `batch_size` | `int` | `50` | Flush every N events |
| `track_all_tools` | `bool` | `False` | Record non-UCP tools as generic `request` events |
| `redact_pii` | `bool` | `False` | Redact PII fields |
| `custom_metadata` | `dict[str, str]` | `None` | Static metadata on every event |

---

## BigQuery Schema Reference (`ucp_events`)

The table is automatically created with daily partitioning on `timestamp` and
clustering on `event_type`, `checkout_session_id`, `merchant_host`.

### Identity

| Field | Type | Mode | Description |
|---|---|---|---|
| `event_id` | `STRING` | `REQUIRED` | Unique UUID per event |
| `event_type` | `STRING` | `REQUIRED` | Classified event type (see [Event Types](#event-types)) |
| `timestamp` | `TIMESTAMP` | `REQUIRED` | UTC event time (ISO 8601) |

### Context

| Field | Type | Mode | Description |
|---|---|---|---|
| `app_name` | `STRING` | `NULLABLE` | Application identifier (e.g., `"flower_shop"`) |
| `merchant_host` | `STRING` | `NULLABLE` | Merchant endpoint hostname |
| `platform_profile_url` | `STRING` | `NULLABLE` | `UCP-Agent` request header value |
| `transport` | `STRING` | `NULLABLE` | Transport protocol: `rest`, `mcp`, `a2a`, `embedded` |

### HTTP

| Field | Type | Mode | Description |
|---|---|---|---|
| `http_method` | `STRING` | `NULLABLE` | HTTP method (`GET`, `POST`, `PUT`) |
| `http_path` | `STRING` | `NULLABLE` | Request path (e.g., `/checkout-sessions`) |
| `http_status_code` | `INTEGER` | `NULLABLE` | HTTP response status code |
| `idempotency_key` | `STRING` | `NULLABLE` | `Idempotency-Key` request header |
| `request_id` | `STRING` | `NULLABLE` | `Request-Id` request header |

### Checkout

| Field | Type | Mode | Description |
|---|---|---|---|
| `checkout_session_id` | `STRING` | `NULLABLE` | Checkout session identifier |
| `checkout_status` | `STRING` | `NULLABLE` | Session state: `incomplete`, `requires_escalation`, `ready_for_complete`, `completed`, `canceled` |
| `order_id` | `STRING` | `NULLABLE` | Order ID (from `checkout.order.id` or direct) |

### Financial (Minor Units)

All amounts are in **minor currency units** (cents for USD). The seven total types
are defined by the UCP spec:

| Field | Type | Mode | Description |
|---|---|---|---|
| `currency` | `STRING` | `NULLABLE` | ISO 4217 currency code (e.g., `USD`) |
| `items_discount_amount` | `INTEGER` | `NULLABLE` | From `totals[type=items_discount]` — per-item discounts |
| `subtotal_amount` | `INTEGER` | `NULLABLE` | From `totals[type=subtotal]` — sum of line items |
| `discount_amount` | `INTEGER` | `NULLABLE` | From `totals[type=discount]` — order-level discount |
| `fulfillment_amount` | `INTEGER` | `NULLABLE` | From `totals[type=fulfillment]` — shipping / delivery |
| `tax_amount` | `INTEGER` | `NULLABLE` | From `totals[type=tax]` |
| `fee_amount` | `INTEGER` | `NULLABLE` | From `totals[type=fee]` — platform / service fees |
| `total_amount` | `INTEGER` | `NULLABLE` | From `totals[type=total]` — final charged amount |

### Line Items

| Field | Type | Mode | Description |
|---|---|---|---|
| `line_items_json` | `JSON` | `NULLABLE` | Full line items array |
| `line_item_count` | `INTEGER` | `NULLABLE` | Number of line items |

### Payment

| Field | Type | Mode | Description |
|---|---|---|---|
| `payment_handler_id` | `STRING` | `NULLABLE` | Payment handler reverse-domain ID (e.g., `com.stripe.payment`) |
| `payment_instrument_type` | `STRING` | `NULLABLE` | Instrument type: `card`, `bank_transfer`, etc. |
| `payment_brand` | `STRING` | `NULLABLE` | Card brand: `Visa`, `Mastercard`, etc. |

### Capabilities

| Field | Type | Mode | Description |
|---|---|---|---|
| `ucp_version` | `STRING` | `NULLABLE` | UCP protocol version (e.g., `2026-01-11`) |
| `capabilities_json` | `JSON` | `NULLABLE` | Capabilities array from `ucp.capabilities` (per SDK: array of `{name, version}` objects) |
| `extensions_json` | `JSON` | `NULLABLE` | Extensions metadata |

### Identity Linking

| Field | Type | Mode | Description |
|---|---|---|---|
| `identity_provider` | `STRING` | `NULLABLE` | OAuth identity provider |
| `identity_scope` | `STRING` | `NULLABLE` | Requested OAuth scope |

### Fulfillment

| Field | Type | Mode | Description |
|---|---|---|---|
| `fulfillment_type` | `STRING` | `NULLABLE` | `shipping`, `pickup`, `digital`, `service` |
| `fulfillment_destination_country` | `STRING` | `NULLABLE` | ISO 3166-1 alpha-2 country code |

### Discount Extension

| Field | Type | Mode | Description |
|---|---|---|---|
| `discount_codes_json` | `JSON` | `NULLABLE` | Input discount codes (from `discounts.codes`) |
| `discount_applied_json` | `JSON` | `NULLABLE` | Applied discounts with allocations (from `discounts.applied`) |

### Checkout Metadata

| Field | Type | Mode | Description |
|---|---|---|---|
| `expires_at` | `TIMESTAMP` | `NULLABLE` | Checkout session expiration time |
| `continue_url` | `STRING` | `NULLABLE` | URL for escalation / human handoff |

### Order

| Field | Type | Mode | Description |
|---|---|---|---|
| `permalink_url` | `STRING` | `NULLABLE` | Order status page URL (from `order.permalink_url`) |

### Errors & Messages

| Field | Type | Mode | Description |
|---|---|---|---|
| `error_code` | `STRING` | `NULLABLE` | Error code from server messages |
| `error_message` | `STRING` | `NULLABLE` | Error description |
| `error_severity` | `STRING` | `NULLABLE` | Error severity level |
| `messages_json` | `JSON` | `NULLABLE` | Full messages array from response |

### Performance

| Field | Type | Mode | Description |
|---|---|---|---|
| `latency_ms` | `FLOAT` | `NULLABLE` | End-to-end request latency in milliseconds |

### Custom

| Field | Type | Mode | Description |
|---|---|---|---|
| `custom_metadata_json` | `JSON` | `NULLABLE` | User-defined key-value metadata |

---

## Event Types

Events are auto-classified from the HTTP method, path, and response status. The
classifier handles all UCP resource types:

### Checkout Events

| Event Type | Trigger | Description |
|---|---|---|
| `checkout_session_created` | `POST /checkout-sessions` | New checkout session started |
| `checkout_session_get` | `GET /checkout-sessions/{id}` | Checkout session retrieved |
| `checkout_session_updated` | `PUT /checkout-sessions/{id}` | Buyer info, fulfillment, or items updated |
| `checkout_escalation` | `PUT /checkout-sessions/{id}` (status=`requires_escalation`) | Agent cannot proceed; human handoff needed |
| `checkout_session_completed` | `POST /checkout-sessions/{id}/complete` | Checkout completed, order placed |
| `checkout_session_canceled` | `POST /checkout-sessions/{id}/cancel` | Checkout abandoned or canceled |

### Cart Events

| Event Type | Trigger | Description |
|---|---|---|
| `cart_created` | `POST /carts` | New cart created |
| `cart_get` | `GET /carts/{id}` | Cart retrieved |
| `cart_updated` | `PUT /carts/{id}` | Cart items or metadata updated |
| `cart_canceled` | `POST /carts/{id}/cancel` | Cart abandoned or canceled |

### Order Events

| Event Type | Trigger | Description |
|---|---|---|
| `order_created` | `POST /orders` | Order created |
| `order_updated` | `GET /orders/{id}` | Order status polled (generic) |
| `order_shipped` | Shipping simulation | Order shipped (fulfillment event) |
| `order_delivered` | `GET /orders/{id}` (status=`delivered`) or webhook | Order delivered to buyer |
| `order_returned` | `GET /orders/{id}` (status=`returned`) or webhook | Order returned by buyer |
| `order_canceled` | `GET /orders/{id}` (status=`canceled`) or webhook | Order canceled |

### Discovery & Capability Events

| Event Type | Trigger | Description |
|---|---|---|
| `profile_discovered` | `GET /.well-known/ucp` | Merchant UCP profile fetched |
| `capability_negotiated` | Capability exchange / A2A negotiation | Capabilities agreed upon |

### Identity Events

| Event Type | Trigger | Description |
|---|---|---|
| `identity_link_initiated` | `POST /identity` or `/oauth` | Identity linking started |
| `identity_link_completed` | `GET /identity/callback` or `/oauth/callback` | Identity linked via OAuth callback |
| `identity_link_revoked` | `POST /identity/revoke` or `DELETE /identity/*` | Identity link removed |

### Payment Events

| Event Type | Trigger | Description |
|---|---|---|
| `payment_handler_negotiated` | Handler selection | Payment handler agreed upon |
| `payment_instrument_selected` | Instrument selection | Buyer selects payment instrument |
| `payment_completed` | Successful payment | Payment processed |
| `payment_failed` | Failed payment | Payment declined or errored |

### Fallback Events

| Event Type | Trigger | Description |
|---|---|---|
| `request` | Unmatched UCP path | Generic request (no specific classification) |
| `error` | HTTP status >= 400 | Server or client error |

---

## Event Payload Example

A `checkout_session_completed` event row in BigQuery:

```json
{
  "event_id": "670cf848-070c-4a2b-b8e1-2c4f1e8d3a5b",
  "event_type": "checkout_session_completed",
  "timestamp": "2026-02-19T10:30:00.000Z",
  "app_name": "flower_shop",
  "merchant_host": "flower-shop.example.com",
  "http_method": "POST",
  "http_path": "/checkout-sessions/chk_abc123/complete",
  "http_status_code": 200,
  "checkout_session_id": "chk_abc123",
  "checkout_status": "completed",
  "order_id": "order_xyz789",
  "currency": "USD",
  "subtotal_amount": 7997,
  "fulfillment_amount": 599,
  "tax_amount": 700,
  "total_amount": 8796,
  "discount_amount": 500,
  "line_item_count": 3,
  "payment_handler_id": "com.stripe.payment",
  "payment_instrument_type": "card",
  "payment_brand": "Visa",
  "ucp_version": "2026-01-11",
  "fulfillment_type": "shipping",
  "fulfillment_destination_country": "US",
  "discount_codes_json": "[\"FLOWERS10\"]",
  "permalink_url": "https://flower-shop.example.com/orders/order_xyz789",
  "latency_ms": 142.5
}
```

---

## PII Redaction

Enable PII redaction to automatically mask sensitive fields before they reach BigQuery:

```python
tracker = UCPAnalyticsTracker(
    project_id="my-gcp-project",
    redact_pii=True,
    pii_fields=["email", "phone", "first_name", "last_name",
                "phone_number", "street_address", "postal_code"],
)
```

When enabled, any matching field in the request or response body is replaced
with `"[REDACTED]"` before extraction. This applies recursively to nested objects
and arrays.

Default PII fields: `email`, `phone`, `first_name`, `last_name`, `phone_number`,
`street_address`, `postal_code`.

---

## Dual Capture

When both the **server middleware** and the **client hook** are active (e.g., during
integration testing or in a platform that both serves and consumes UCP), each UCP
operation produces **two events** — one from each side. This is expected behavior
and useful for comparing server-side vs. client-side latency.

To query only one side, filter by `app_name`:

```sql
-- Server-side events only
SELECT * FROM `project.ucp_analytics.ucp_events`
WHERE app_name = 'flower_shop';

-- Client-side events only
SELECT * FROM `project.ucp_analytics.ucp_events`
WHERE app_name = 'shopping_agent';
```

---

## Advanced Analytics Queries

### Checkout Funnel (Daily Conversion)

```sql
SELECT
    DATE(timestamp) AS day,
    COUNT(CASE WHEN event_type = 'checkout_session_created'   THEN 1 END) AS started,
    COUNT(CASE WHEN event_type = 'checkout_session_updated'   THEN 1 END) AS updated,
    COUNT(CASE WHEN event_type = 'checkout_session_completed' THEN 1 END) AS completed,
    COUNT(CASE WHEN event_type = 'checkout_session_canceled'  THEN 1 END) AS canceled,
    SAFE_DIVIDE(
        COUNT(CASE WHEN event_type = 'checkout_session_completed' THEN 1 END),
        COUNT(CASE WHEN event_type = 'checkout_session_created'   THEN 1 END)
    ) AS conversion_rate
FROM `project.ucp_analytics.ucp_events`
GROUP BY day
ORDER BY day DESC;
```

### Revenue by Merchant

```sql
SELECT
    merchant_host,
    COUNT(DISTINCT checkout_session_id) AS transactions,
    SUM(total_amount) / 100.0 AS revenue_dollars,
    AVG(total_amount) / 100.0 AS avg_order_value,
    currency
FROM `project.ucp_analytics.ucp_events`
WHERE event_type = 'checkout_session_completed'
GROUP BY merchant_host, currency
ORDER BY revenue_dollars DESC;
```

### Latency Percentiles by Operation

```sql
SELECT
    event_type,
    COUNT(*) AS calls,
    APPROX_QUANTILES(latency_ms, 100)[OFFSET(50)] AS p50_ms,
    APPROX_QUANTILES(latency_ms, 100)[OFFSET(95)] AS p95_ms,
    APPROX_QUANTILES(latency_ms, 100)[OFFSET(99)] AS p99_ms,
    MAX(latency_ms) AS max_ms
FROM `project.ucp_analytics.ucp_events`
WHERE latency_ms IS NOT NULL
GROUP BY event_type
ORDER BY p95_ms DESC;
```

### Payment Handler Mix

```sql
SELECT
    payment_handler_id,
    payment_brand,
    COUNT(*) AS transactions,
    SUM(total_amount) / 100.0 AS revenue_dollars,
    AVG(latency_ms) AS avg_latency_ms
FROM `project.ucp_analytics.ucp_events`
WHERE event_type = 'checkout_session_completed'
  AND payment_handler_id IS NOT NULL
GROUP BY payment_handler_id, payment_brand
ORDER BY transactions DESC;
```

### Capability Adoption Across Merchants

```sql
SELECT
    JSON_VALUE(cap, '$.name') AS capability_name,
    JSON_VALUE(cap, '$.version') AS capability_version,
    COUNT(DISTINCT merchant_host) AS merchant_count,
    COUNT(DISTINCT checkout_session_id) AS session_count
FROM `project.ucp_analytics.ucp_events`,
    UNNEST(JSON_QUERY_ARRAY(capabilities_json)) AS cap
WHERE capabilities_json IS NOT NULL
GROUP BY capability_name, capability_version
ORDER BY session_count DESC;
```

### Error Analysis

```sql
SELECT
    error_code,
    error_severity,
    error_message,
    COUNT(*) AS occurrences,
    COUNT(DISTINCT checkout_session_id) AS affected_sessions,
    COUNT(DISTINCT merchant_host) AS affected_merchants
FROM `project.ucp_analytics.ucp_events`
WHERE error_code IS NOT NULL
GROUP BY error_code, error_severity, error_message
ORDER BY occurrences DESC;
```

### Discount Effectiveness

```sql
SELECT
    JSON_VALUE(code) AS discount_code,
    COUNT(*) AS usage_count,
    SUM(discount_amount) / 100.0 AS total_discount_dollars,
    SUM(total_amount) / 100.0 AS total_revenue_dollars,
    AVG(discount_amount) / NULLIF(AVG(total_amount), 0) AS avg_discount_pct
FROM `project.ucp_analytics.ucp_events`,
    UNNEST(JSON_QUERY_ARRAY(discount_codes_json)) AS code
WHERE event_type = 'checkout_session_completed'
  AND discount_codes_json IS NOT NULL
GROUP BY discount_code
ORDER BY usage_count DESC;
```

### Session Timeline (Debug a Specific Checkout)

```sql
SELECT
    timestamp,
    event_type,
    checkout_status,
    http_method,
    http_path,
    http_status_code,
    total_amount,
    error_code,
    error_message,
    latency_ms
FROM `project.ucp_analytics.ucp_events`
WHERE checkout_session_id = 'SESSION_ID_HERE'
ORDER BY timestamp;
```

### Discovery-to-Checkout Conversion

```sql
WITH discovery AS (
    SELECT merchant_host, DATE(timestamp) AS day, COUNT(*) AS profile_fetches
    FROM `project.ucp_analytics.ucp_events`
    WHERE event_type = 'profile_discovered'
    GROUP BY merchant_host, day
),
checkouts AS (
    SELECT merchant_host, DATE(timestamp) AS day, COUNT(*) AS checkout_starts
    FROM `project.ucp_analytics.ucp_events`
    WHERE event_type = 'checkout_session_created'
    GROUP BY merchant_host, day
)
SELECT
    d.merchant_host,
    d.day,
    d.profile_fetches,
    COALESCE(c.checkout_starts, 0) AS checkout_starts,
    SAFE_DIVIDE(c.checkout_starts, d.profile_fetches) AS conversion_rate
FROM discovery d
LEFT JOIN checkouts c USING (merchant_host, day)
ORDER BY d.day DESC;
```

### Fulfillment Geography

```sql
SELECT
    fulfillment_destination_country,
    fulfillment_type,
    COUNT(*) AS orders,
    SUM(total_amount) / 100.0 AS revenue_dollars,
    SUM(fulfillment_amount) / 100.0 AS total_fulfillment_dollars
FROM `project.ucp_analytics.ucp_events`
WHERE event_type = 'checkout_session_completed'
  AND fulfillment_destination_country IS NOT NULL
GROUP BY fulfillment_destination_country, fulfillment_type
ORDER BY orders DESC;
```

---

## Dashboard Visualization

### Looker Studio

Connect BigQuery directly to Looker Studio for real-time dashboards.
Recommended charts:

| Chart | Data Source | Key Metrics |
|---|---|---|
| Checkout funnel bar chart | Daily funnel query | created → updated → completed |
| Revenue time series | Revenue by merchant query | GMV, AOV |
| Latency heatmap | Latency percentiles query | p50, p95, p99 by event type |
| Payment pie chart | Payment handler mix query | Transactions by handler + brand |
| Error table | Error analysis query | Error code, severity, count |
| Geography map | Fulfillment geography query | Orders by country |

### Pre-built Queries

See [`dashboards/queries.sql`](../dashboards/queries.sql) for 10 ready-to-use
BigQuery queries covering all of the above plus capability adoption, escalation
rate, and session timeline debugging.

---

## Examples

Eight runnable examples are included in the [`examples/`](../examples/) directory,
covering all 27 UCP event types:

| Example | BigQuery? | Transport | Purpose |
|---|---|---|---|
| `e2e_demo.py` | No (SQLite) | REST | Checkout happy path (no GCP needed) |
| `scenarios_demo.py` | Yes (BigQuery) | REST | Errors, cancellation, escalation |
| `cart_demo.py` | Yes (BigQuery) | REST | Cart lifecycle + checkout conversion |
| `order_lifecycle_demo.py` | Yes (BigQuery) | REST | Order delivered/returned/canceled |
| `transport_demo.py` | Yes (BigQuery) | REST/MCP/A2A | All 3 transport comparisons |
| `identity_payment_demo.py` | Yes (BigQuery) | REST | Identity linking + payment flows |
| `bq_demo.py` | Yes | REST/MCP/A2A | Comprehensive — all 27 event types, 3 transports, SDK models, BQ verification |
| `bq_adk_demo.py` | Yes | ADK/MCP/A2A | Comprehensive ADK — all 27 event types, 3 transports, SDK models, BQ verification |

### Quick Start (No GCP)

```bash
pip install fastapi uvicorn httpx
python examples/e2e_demo.py
```

### Quick Start (BigQuery)

```bash
gcloud auth application-default login
uv sync --all-extras
# Edit PROJECT_ID in examples/_demo_utils.py
uv run python examples/scenarios_demo.py         # errors + edge cases
uv run python examples/cart_demo.py              # cart lifecycle
uv run python examples/order_lifecycle_demo.py   # order lifecycle
uv run python examples/transport_demo.py         # REST vs MCP vs A2A
uv run python examples/identity_payment_demo.py  # identity + payment
```

### BigQuery E2E Demo

```bash
gcloud auth application-default login
uv sync --extra fastapi
# Edit PROJECT_ID in examples/bq_demo.py
uv run python examples/bq_demo.py
```

### ADK BigQuery Demo

```bash
gcloud auth application-default login
uv sync --all-extras
# Edit PROJECT_ID in examples/bq_adk_demo.py
uv run python examples/bq_adk_demo.py
```

See [`examples/README.md`](../examples/README.md) for detailed step-by-step
instructions, expected output, and verification queries.

---

## Cleanup

Delete only demo data (preserves production data):

```sql
DELETE FROM `project.ucp_analytics.ucp_events`
WHERE app_name IN ('bq_demo', 'bq_adk_demo');
```

Drop the entire table:

```sql
DROP TABLE IF EXISTS `project.ucp_analytics.ucp_events`;
```

---

## Feedback & Resources

- [UCP Specification](https://github.com/Universal-Commerce-Protocol/ucp)
- [UCP Developer Docs](https://ucp.dev)
- [Design Doc](design_doc.md)
- [Dashboard Queries](../dashboards/queries.sql)
- [GitHub Issues](https://github.com/haiyuan-eng-google/Universal-Commerce-Protocol-Analytics/issues)
