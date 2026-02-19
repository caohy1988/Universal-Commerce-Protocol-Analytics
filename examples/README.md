# UCP Analytics — Examples

This directory contains four runnable examples that demonstrate UCP Analytics
from local-only quick starts to full BigQuery integration with and without
the Google ADK plugin.

| Example | BigQuery? | ADK? | Purpose |
|---|---|---|---|
| [`e2e_demo.py`](#e2e-demo-local-sqlite) | No (SQLite) | No | Quick local demo, no GCP needed |
| [`flower_shop_analytics.py`](#flower-shop-integration) | Yes | No | Add analytics to the official UCP samples server |
| [`bq_demo.py`](#bigquery-demo) | Yes | No | Full BigQuery E2E with verification |
| [`bq_adk_demo.py`](#adk-bigquery-demo) | Yes | Yes | ADK plugin E2E with verification |

---

## BigQuery Demo

**`bq_demo.py`** — Full end-to-end demo that writes real UCP analytics events
to BigQuery using `UCPAnalyticsTracker`, the FastAPI middleware, and the HTTPX
client hook, then queries BigQuery to verify the data landed correctly.

### What It Does

The demo spins up a mini UCP merchant server and a shopping agent client in a
single process. Both sides are instrumented with analytics:

- **Server side:** `UCPAnalyticsMiddleware` captures every inbound UCP request
- **Client side:** `UCPClientEventHook` captures every outbound UCP call

The agent walks through a full checkout lifecycle:

```
Discovery  -->  Create Checkout  -->  Update (buyer + fulfillment)
     -->  Apply Discount  -->  Complete Checkout  -->  Simulate Shipping
```

Each step generates spec-aligned analytics events that stream into BigQuery.
After the flow completes, the demo queries BigQuery and prints a verification
report.

### Prerequisites

1. **Google Cloud project** with BigQuery API enabled:

   ```bash
   gcloud services enable bigquery.googleapis.com
   ```

2. **Application Default Credentials** (ADC):

   ```bash
   gcloud auth application-default login
   ```

3. **Python dependencies:**

   ```bash
   # From the repo root
   uv sync                       # core deps
   # or with FastAPI middleware support
   uv sync --extra fastapi
   ```

### Configuration

Open `bq_demo.py` and update the project ID at the top:

```python
PROJECT_ID = "your-gcp-project-id"    # <-- change this
DATASET_ID = "ucp_analytics"          # dataset (auto-created)
TABLE_ID   = "ucp_events"             # table   (auto-created)
```

The tracker will create the dataset and table automatically on first write.

### Run

```bash
uv run python examples/bq_demo.py
```

### Expected Output

```
======================================================================
  UCP ANALYTICS -- BigQuery E2E Demo
======================================================================

-- Step 1: Discover Merchant --
   UCP version: 2026-01-11
   Capabilities: ['dev.ucp.shopping.checkout', ...]

-- Step 2: Create Checkout --
   Session: chk_dc597aa65654
   Status: incomplete
   Total: $86.97

-- Step 3: Update (buyer + fulfillment) --
   Status: ready_for_complete
   Total (with fulfillment): $92.96

-- Step 4: Apply Discount --
   Discount applied, new total: $87.96

-- Step 5: Complete Checkout --
   Status: completed
   Order: order_9781e7c356
   Permalink: https://shop.example.com/orders/order_9781e7c356

-- Step 6: Simulate Shipping --
   Order status: shipped

   Flushing events to BigQuery...

======================================================================
  BIGQUERY VERIFICATION
======================================================================

   Waiting 10s for BigQuery streaming buffer...
   Querying BigQuery for session chk_dc597aa65654...

   Found 10 events in BigQuery:
   #   Event Type                     Status                 Total   Latency
   -------------------------------------------------------------------------
   1   checkout_session_created       incomplete            $86.97       1ms
   2   checkout_session_created       incomplete            $86.97       3ms
   ...

   Verification:
     [PASS] checkout_session_created
     [PASS] checkout_session_updated
     [PASS] checkout_session_completed
     [PASS] fulfillment_amount extracted
     [PASS] payment_handler_id extracted
     [PASS] ucp_version extracted

   All BigQuery verifications passed!
```

Each operation appears twice (once from the server middleware, once from the
client hook) — this is expected when both integration points are active.

### What Gets Written to BigQuery

Every event row includes:

| Field | Example Value | Source |
|---|---|---|
| `event_type` | `checkout_session_completed` | Auto-classified from HTTP method + path |
| `checkout_session_id` | `chk_dc597aa65654` | Extracted from response body `id` |
| `checkout_status` | `completed` | Extracted from response body `status` |
| `currency` | `USD` | Extracted from response body |
| `subtotal_amount` | `7997` | From `totals[]` where `type=subtotal` |
| `fulfillment_amount` | `599` | From `totals[]` where `type=fulfillment` |
| `discount_amount` | `500` | From `totals[]` where `type=discount` |
| `total_amount` | `8796` | From `totals[]` where `type=total` |
| `payment_handler_id` | `com.mock.payment` | From `payment.instruments[0].handler_id` |
| `payment_instrument_type` | `card` | From `payment.instruments[0].type` |
| `ucp_version` | `2026-01-11` | From `ucp.version` |
| `capabilities_json` | `[{"name":"dev.ucp.shopping.checkout",...}]` | From `ucp.capabilities` array (per SDK: array of `{name, version}` objects) |
| `discount_codes_json` | `["FLOWERS10"]` | From `discounts.codes` |
| `discount_applied_json` | `[{"code":"FLOWERS10","amount":500,...}]` | From `discounts.applied` |
| `permalink_url` | `https://shop.example.com/orders/...` | From `order.permalink_url` |
| `order_id` | `order_9781e7c356` | From `order.id` (nested in checkout) |
| `fulfillment_type` | `shipping` | From `fulfillment.methods[0].type` |
| `fulfillment_destination_country` | `US` | From `fulfillment.methods[0].destinations[0]` |
| `latency_ms` | `3.14` | Measured end-to-end per request |
| `idempotency_key` | `46994281-a318-...` | From request `Idempotency-Key` header |
| `request_id` | `670cf848-070c-...` | From request `Request-Id` header |

### Verify Manually

After the demo completes you can query BigQuery directly:

```sql
SELECT event_type, checkout_status, total_amount, fulfillment_amount,
       payment_handler_id, discount_codes_json, permalink_url, latency_ms
FROM `YOUR_PROJECT.ucp_analytics.ucp_events`
WHERE app_name = 'bq_demo'
ORDER BY timestamp;
```

---

## ADK BigQuery Demo

**`bq_adk_demo.py`** — Demonstrates the `UCPAgentAnalyticsPlugin` adapter that
integrates UCP analytics into a Google ADK agent via tool callbacks. No LLM
calls are needed — the demo simulates the ADK tool lifecycle directly.

### What It Does

Instead of HTTP requests, this demo exercises the ADK plugin's
`before_tool_callback` / `after_tool_callback` flow. It simulates an ADK agent
calling UCP tools in sequence:

```
discover_merchant  -->  create_checkout  -->  update_checkout
     -->  complete_checkout  -->  get_weather (non-UCP, should be skipped)
```

The plugin:

1. Records a start timestamp in `before_tool_callback`
2. Classifies the tool name into a UCP event type via a tool-name-to-HTTP
   mapping (e.g. `create_checkout` maps to `POST /checkout-sessions`)
3. Extracts spec-aligned fields from the tool result
4. Computes latency from the before/after timing gap
5. Writes the event to BigQuery
6. Skips non-UCP tools (like `get_weather`) unless `track_all_tools=True`

### Prerequisites

Same as the BigQuery demo above, plus the ADK extra:

```bash
# Install with ADK support
uv sync --all-extras
# or just the ADK extra
uv sync --extra adk
```

Verify the ADK plugin is importable:

```bash
uv run python -c "from ucp_analytics.adk_plugin import UCPAgentAnalyticsPlugin; print('OK')"
```

### Configuration

Open `bq_adk_demo.py` and update the project ID:

```python
PROJECT_ID = "your-gcp-project-id"    # <-- change this
DATASET_ID = "ucp_analytics"          # dataset (auto-created)
TABLE_ID   = "ucp_events"             # table   (auto-created)
```

### Run

```bash
uv run python examples/bq_adk_demo.py
```

### Expected Output

```
======================================================================
  UCP ANALYTICS -- ADK Plugin BigQuery Demo
======================================================================

-- Step 1: discover_merchant --
   Captured discovery event

-- Step 2: create_checkout --
   Session: chk_adk_dbd9d0bf
   Status: incomplete

-- Step 3: update_checkout --
   Status: ready_for_complete

-- Step 4: complete_checkout --
   Status: completed
   Order: order_adk_fc9baece

-- Step 5: get_weather (non-UCP, should be skipped) --
   Skipped (not a UCP tool)

   Flushing events to BigQuery...

======================================================================
  ADK BIGQUERY VERIFICATION
======================================================================

   Waiting 10s for BigQuery streaming buffer...
   Querying BigQuery for ADK events...

   Found 4 ADK events in BigQuery:
   #   Event Type                     Status                 Total   Latency
   -------------------------------------------------------------------------
   1   profile_discovered                                               31ms
   2   checkout_session_created       incomplete            $32.61      81ms
   3   checkout_session_updated       ready_for_complete    $38.60      61ms
   4   checkout_session_completed     completed             $38.60     121ms

   Event types: ['checkout_session_completed', 'checkout_session_created',
                 'checkout_session_updated', 'profile_discovered']

   Verification:
     [PASS] Discovery event captured
     [PASS] Checkout created captured
     [PASS] Checkout lifecycle captured
     [PASS] Non-UCP tool correctly skipped
     [PASS] Latency captured from tool timing

   All ADK BigQuery verifications passed!
```

Note that unlike the non-ADK demo, each operation produces exactly one event
(not two) because there is only a single integration point (the plugin).

### How the ADK Plugin Classifies Tools

The plugin maps ADK tool names to equivalent UCP HTTP operations so the
existing classifier can determine the correct event type:

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
| `get_weather` | *(skipped)* | *(not recorded)* |

Tools whose names don't match any UCP pattern are silently skipped (or recorded
as generic `request` events if `track_all_tools=True`).

### Verify Manually

```sql
SELECT event_type, checkout_status, total_amount, fulfillment_amount,
       payment_handler_id, permalink_url, latency_ms
FROM `YOUR_PROJECT.ucp_analytics.ucp_events`
WHERE app_name = 'bq_adk_demo'
ORDER BY timestamp;
```

---

## E2E Demo (Local SQLite)

**`e2e_demo.py`** — A fully self-contained demo that requires no GCP
credentials. Uses SQLite instead of BigQuery so you can try UCP Analytics
in seconds.

```bash
pip install fastapi uvicorn httpx
python examples/e2e_demo.py
```

Runs the same checkout flow (discovery, create, update, discount, complete,
ship) and prints a local analytics report with funnel, financials, payment,
capabilities, and latency stats.

---

## Flower Shop Integration

**`flower_shop_analytics.py`** — Shows how to add UCP analytics to the
[official UCP samples server](https://github.com/Universal-Commerce-Protocol/samples)
with just two lines of code. Not a standalone demo — requires the samples
server to be running.

---

## Cleanup

To delete the demo data from BigQuery after testing:

```sql
-- Delete only demo rows (preserves production data)
DELETE FROM `YOUR_PROJECT.ucp_analytics.ucp_events`
WHERE app_name IN ('bq_demo', 'bq_adk_demo');
```

Or drop the entire table:

```sql
DROP TABLE IF EXISTS `YOUR_PROJECT.ucp_analytics.ucp_events`;
```
