# UCP Analytics — Design Document

**Author:** Haiyuan Cao
**Status:** Draft
**Version:** 0.1
**Date:** February 18, 2026
**Repository:** [haiyuan-eng-google/Universal-Commerce-Protocol-Analytics](https://github.com/haiyuan-eng-google/Universal-Commerce-Protocol-Analytics)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Architecture](#3-architecture)
4. [Event Classification](#4-event-classification)
5. [BigQuery Schema](#5-bigquery-schema)
6. [Response Parser Design](#6-response-parser-design)
7. [Async BigQuery Writer](#7-async-bigquery-writer)
8. [Integration Patterns](#8-integration-patterns)
9. [Analytics Queries](#9-analytics-queries)
10. [Examples](#10-examples)
11. [Deployment & Configuration](#11-deployment--configuration)
12. [Relationship to Existing Work](#12-relationship-to-existing-work)
13. [Future Work](#13-future-work)

---

## 1. Executive Summary

The Universal Commerce Protocol (UCP) defines standardized APIs for agentic commerce — enabling AI agents to discover merchant capabilities, create checkout sessions, process payments, and manage orders. However, UCP ships with **no built-in observability**. Businesses and platforms have no structured way to track checkout conversion funnels, payment success rates, error patterns, or latency across the protocol surface.

**UCP Analytics** is a new open-source package that automatically captures every UCP operation into BigQuery, providing structured commerce event tracking aligned with the UCP specification. It hooks into the HTTP transport layer (the primary UCP binding) via FastAPI middleware (merchant side) and HTTPX event hooks (agent/platform side), requiring **zero changes to existing UCP server or client code**.

**Key outcomes:**
1. Checkout funnel visibility from discovery to completion
2. Revenue attribution by merchant, payment handler, and fulfillment geography
3. Error and escalation debugging with session replay
4. Latency monitoring per UCP operation
5. Capability adoption tracking across the ecosystem

---

## 2. Problem Statement

### 2.1 The Observability Gap

UCP defines the protocol for commerce but leaves observability entirely to implementers. The reference sample server (flower shop) writes to a local SQLite database for transaction state but provides no analytics, no event stream, and no dashboard. As UCP adoption scales, every merchant, platform, and payment handler must independently build:

- Checkout funnel tracking (sessions created vs. completed vs. abandoned)
- Error classification (recoverable vs. escalation vs. fatal)
- Payment handler performance (success rates, latency by handler/brand)
- Fulfillment geography analysis (order destinations, shipping methods)
- Capability adoption metrics (which extensions do merchants implement)

### 2.2 Why This Matters Now

UCP was publicly launched in January 2026, co-developed by Google, Shopify, Etsy, Walmart, Target, Wayfair, and endorsed by 20+ partners including Adyen, Mastercard, Visa, and Stripe. With Google AI Mode in Search and Gemini app providing the first consumer surfaces, UCP transaction volume is growing rapidly. Without standardized analytics, debugging requires ad-hoc log parsing and business intelligence requires custom ETL per merchant.

### 2.3 Non-Goals

- Real-time alerting (use existing GCP monitoring on top of BigQuery)
- PCI-DSS compliant payment storage (only handler IDs and card brands captured, not tokens/credentials)
- Replacing merchant transaction databases (analytics layer, not system of record)
- Non-HTTP transports beyond the current JSON-RPC classification (e.g. native gRPC bindings)

---

## 3. Architecture

### 3.1 System Overview

UCP Analytics hooks into the transport layer as a passive observer: intercepting requests/responses without modifying them, extracting structured UCP fields, and writing batched event rows to BigQuery. It supports three transports:

- **REST** — HTTP method + path + response body classification (primary binding)
- **MCP** — JSON-RPC tool name classification via `record_jsonrpc(transport="mcp")`
- **A2A** — JSON-RPC tool name classification via `record_jsonrpc(transport="a2a")`

```
Platform (Agent)                    Business (Merchant)
+------------------+                +--------------------+
| HTTPX Client     |                | FastAPI Server     |
| + EventHook   -----------REST----------> + Middleware  |
+--------+---------+                +---------+----------+
         |                                    |
         +------------------+-----------------+
                            v
                  UCPAnalyticsTracker
                  +--UCPResponseParser   (classify + extract)
                  +--classify_jsonrpc()  (MCP/A2A tool mapping)
                  +--AsyncBigQueryWriter (batch + flush)
                            |
                            v
                       BigQuery
                  PARTITION BY timestamp
                  CLUSTER BY event_type,
                    checkout_session_id, merchant_host
```

### 3.2 Integration Points

| Integration | Side | Mechanism | Use Case |
|---|---|---|---|
| **FastAPI Middleware** | Merchant server | ASGI middleware on Starlette | Track all inbound UCP requests |
| **HTTPX Event Hook** | Agent / platform | httpx response event hook | Track all outbound UCP calls |
| **ADK Plugin** (optional) | ADK agent | BasePlugin before/after callbacks | For ADK-based commerce agents |
| **JSON-RPC recorder** | MCP / A2A agent | `tracker.record_jsonrpc()` | Track MCP and A2A tool calls |
| **Direct API** | Any | `tracker.record_http()` / `tracker.record_event()` | Custom integrations, testing |

### 3.3 Data Flow

1. The middleware or event hook captures raw HTTP method, path, status code, request body, and response body.
2. `UCPResponseParser.classify()` maps the HTTP operation to a UCP event type using strict regex matching on well-known UCP paths (e.g. `/checkout-sessions`, `/orders`, `/.well-known/ucp`, `/webhooks`). For webhook paths, the classifier accepts an optional `request_body` parameter since the order payload is in the request body (the response is just an ack like `{"status": "ok"}`).
3. `UCPResponseParser.extract()` parses the UCP JSON response to extract structured fields: session ID, status, totals (all 7 spec types), line items, payment instruments/handlers, fulfillment, capabilities, extensions, discount codes/applied, checkout metadata (expires_at, continue_url), order details (permalink_url, fulfillment expectations/events), and errors.
4. The event is serialized into a flat BigQuery row and enqueued in the `AsyncBigQueryWriter` buffer.
5. When buffer reaches `batch_size` (default 50), rows flush to BigQuery via streaming insert (run in a background thread via `asyncio.to_thread()` to avoid blocking the event loop). Remaining rows flush on `tracker.close()`.

### 3.4 Lazy Loading and Optional Dependencies

The core package depends only on `google-cloud-bigquery` and `httpx`. Optional integrations are lazy-loaded to avoid import errors:

- **FastAPI middleware:** `UCPAnalyticsMiddleware` is exposed via `__getattr__` in `__init__.py` and only imported when accessed, so the core package works without `starlette` installed.
- **ADK plugin:** `adk_plugin.py` uses `try/except ImportError` around `google.adk` imports, falling back to `object` as the base class when ADK is not installed.

---

## 4. Event Classification

### 4.1 Event Type Mapping (27 types)

Events are automatically classified from HTTP method + path + response body. Path matching uses strict regex patterns to avoid false positives (e.g. `/orders` matches but `/reorder` does not). For MCP/A2A transports, `classify_jsonrpc()` maps tool names to the same event types.

#### Checkout (6)

| HTTP Operation | Event Type | Trigger |
|---|---|---|
| `POST /checkout-sessions` | `checkout_session_created` | New checkout session initiated |
| `GET /checkout-sessions/{id}` | `checkout_session_get` | Session retrieved for display/validation |
| `PUT /checkout-sessions/{id}` | `checkout_session_updated` | Buyer info, fulfillment, or discount added |
| `PUT /checkout-sessions/{id}` | `checkout_escalation` | Response status = `requires_escalation` |
| `POST /checkout-sessions/{id}/complete` | `checkout_session_completed` | Checkout finalized with payment |
| `POST /checkout-sessions/{id}/cancel` | `checkout_session_canceled` | Session explicitly canceled |

#### Cart (4)

| HTTP Operation | Event Type | Trigger |
|---|---|---|
| `POST /carts` | `cart_created` | New cart created |
| `GET /carts/{id}` | `cart_get` | Cart retrieved |
| `PUT /carts/{id}` | `cart_updated` | Cart updated (items added/removed) |
| `POST /carts/{id}/cancel` | `cart_canceled` | Cart explicitly canceled |

#### Order (6)

| HTTP Operation | Event Type | Trigger |
|---|---|---|
| `POST /orders` | `order_created` | Order webhook from merchant |
| `GET /orders/{id}` *(status=confirmed)* | `order_updated` | Order status retrieval |
| `GET /orders/{id}` *(status=shipped)* | `order_shipped` | Shipment tracking available |
| `GET /orders/{id}` *(status=delivered)* | `order_delivered` | Delivery confirmed |
| `GET /orders/{id}` *(status=returned)* | `order_returned` | Return processed |
| `GET /orders/{id}` *(status=canceled)* | `order_canceled` | Order canceled |
| `POST /webhooks/partners/{id}/events/order` | *(by request body status)* | Upstream partner webhook — classifies as `order_shipped`, `order_delivered`, `order_returned`, `order_canceled`, or `order_updated` based on the order status in the request body |
| `POST /webhooks/order-delivered` | `order_delivered` | Legacy webhook path |
| `POST /webhooks/order-returned` | `order_returned` | Legacy webhook path |
| `POST /webhooks/order-canceled` | `order_canceled` | Legacy webhook path |

**Note:** For webhook paths, the order payload is in the **request** body (the response is typically an ack like `{"status": "ok"}`). The classifier and tracker use `request_body` for both classification and field extraction on webhook paths. Webhook 4xx/5xx responses classify as `error` rather than falling through to `order_updated`.

#### Identity (3)

| HTTP Operation | Event Type | Trigger |
|---|---|---|
| `POST /identity` | `identity_link_initiated` | OAuth identity linking started |
| `GET /identity/callback` | `identity_link_completed` | Identity callback confirmed |
| `POST /identity/revoke` | `identity_link_revoked` | Identity link revoked |

#### Payment (4)

| Event Type | Trigger |
|---|---|
| `payment_handler_negotiated` | Platform + merchant handler intersection computed |
| `payment_instrument_selected` | Buyer selects payment instrument |
| `payment_completed` | Payment succeeds |
| `payment_failed` | Payment fails |

#### Discovery (2)

| HTTP Operation | Event Type | Trigger |
|---|---|---|
| `GET /.well-known/ucp` | `profile_discovered` | Agent fetches merchant discovery profile |
| Capability exchange | `capability_negotiated` | UCP capability negotiation completed |

#### Fallback (2)

| Condition | Event Type | Trigger |
|---|---|---|
| Any unmatched path, status >= 400 | `error` | HTTP error response |
| Any unmatched path, status < 400 | `request` | Unclassified successful request |

**Note:** Path-specific matches take priority over status code. A `POST /checkout-sessions` returning 500 is classified as `checkout_session_created` (not `error`), since the path match is more informative for analytics.

### 4.2 JSON-RPC Classification (MCP/A2A)

For MCP and A2A transports, `classify_jsonrpc()` maps tool names to event types using pattern matching. Examples:

| Tool Name Pattern | Event Type |
|---|---|
| `discover_merchant`, `a2a.ucp.discover` | `profile_discovered` |
| `create_checkout`, `a2a.ucp.checkout.create` | `checkout_session_created` |
| `complete_checkout`, `a2a.ucp.checkout.complete` | `checkout_session_completed` |
| `add_to_checkout`, `remove_from_checkout`, `update_customer_details` | `checkout_session_updated` |
| `start_payment` | `checkout_session_updated` (pre-completion step) |
| `create_cart`, `a2a.ucp.cart.create` | `cart_created` |
| `get_order`, `a2a.ucp.order.get` | `order_updated` (refined by response body status) |
| `order_event_webhook` | *(by request body status)* |
| `link_identity`, `a2a.ucp.identity.link` | `identity_link_initiated` |
| `negotiate_capability`, `a2a.ucp.capability.negotiate` | `capability_negotiated` |

The response body is still parsed for field extraction (totals, status, payment, etc.) regardless of transport.

### 4.3 UCP Checkout State Machine Alignment

```
incomplete --> requires_escalation --> ready_for_complete
     |              |                        |
     v              v                        v
  canceled       canceled             complete_in_progress
                                             |
                                             v
                                         completed
```

Each state transition generates a corresponding analytics event, enabling precise funnel analysis.

---

## 5. BigQuery Schema

**Table:** `{project}.{dataset}.ucp_events`
**Partitioned by:** `timestamp` (daily)
**Clustered by:** `event_type`, `checkout_session_id`, `merchant_host`

### Identity & Context

| Column | Type | Description |
|---|---|---|
| `event_id` | STRING (PK) | UUID v4, unique per event |
| `event_type` | STRING | Classified UCP event type |
| `timestamp` | TIMESTAMP | UTC event time (partition key) |
| `app_name` | STRING | Application name tag |
| `merchant_host` | STRING | Business endpoint hostname |
| `transport` | STRING | `rest` \| `mcp` \| `a2a` \| `embedded` |

### UCP Checkout Fields

| Column | Type | Description |
|---|---|---|
| `checkout_session_id` | STRING | UCP checkout session ID (cluster key) |
| `checkout_status` | STRING | Current status in state machine |
| `order_id` | STRING | Order ID created on completion |
| `currency` | STRING | ISO 4217 currency code |
| `subtotal_amount` | INTEGER | Subtotal in minor units (cents) |
| `items_discount_amount` | INTEGER | Item-level discount in minor units |
| `tax_amount` | INTEGER | Tax in minor units |
| `fulfillment_amount` | INTEGER | Fulfillment cost in minor units |
| `discount_amount` | INTEGER | Discount in minor units |
| `fee_amount` | INTEGER | Fee in minor units |
| `total_amount` | INTEGER | Total in minor units |
| `line_item_count` | INTEGER | Number of items in checkout |
| `line_items_json` | JSON | Full line items array |
| `discount_codes_json` | JSON | Discount codes from discount extension |
| `discount_applied_json` | JSON | Applied discounts from discount extension |
| `expires_at` | STRING | Checkout session expiration timestamp |
| `continue_url` | STRING | URL to continue checkout in browser |
| `permalink_url` | STRING | Permanent link to the order |

### Payment, Capabilities, Fulfillment, & Errors

| Column | Type | Description |
|---|---|---|
| `payment_handler_id` | STRING | Payment handler ID (google_pay, shop_pay, etc.) |
| `payment_instrument_type` | STRING | card, wallet, bank_transfer, etc. |
| `payment_brand` | STRING | Visa, Mastercard, etc. |
| `ucp_version` | STRING | Protocol version from response envelope |
| `capabilities_json` | JSON | Capabilities array from UCP envelope |
| `extensions_json` | JSON | Extensions (capabilities with `extends` field) |
| `fulfillment_type` | STRING | shipping, pickup, digital, etc. |
| `fulfillment_destination_country` | STRING | ISO country code |
| `error_code` | STRING | Error code from messages array |
| `error_message` | STRING | Error content string |
| `error_severity` | STRING | recoverable \| escalation \| fatal |
| `latency_ms` | FLOAT | Request-to-response latency in milliseconds |

---

## 6. Response Parser Design

### 6.1 Field Extraction Logic

`UCPResponseParser.extract()` understands the UCP checkout object schema:

- **Totals array parsing:** UCP represents financial data as a typed `totals` array. Each entry has `type` and `amount` in minor units. The parser handles all 7 spec-defined total types: `items_discount`, `subtotal`, `discount`, `fulfillment`, `tax`, `fee`, and `total`. These map to individual BigQuery columns (e.g. `fulfillment_amount`, `fee_amount`, `items_discount_amount`).
- **Payment extraction:** The SDK `PaymentResponse` contains both `handlers[]` (merchant payment handler configs) and `instruments[]` (buyer payment methods). Instruments are preferred for analytics since they carry `handler_id`, `type`, and `brand`. Also handles `payment_data` from completion requests and discovery-level `payment.handlers` (top-level sibling of `ucp` envelope). Extracts handler_id, instrument type, and brand — never captures credentials/tokens.
- **Capability detection:** Extracts the UCP metadata envelope (`ucp.version`, `ucp.capabilities`). Per the Python SDK and samples, capabilities are arrays of objects with a `name` field (e.g., `[{"name": "dev.ucp.shopping.checkout", "version": "2026-01-11"}]`). For robustness, also handles an object-keyed format where capability names are dict keys. Discovery responses place `payment.handlers` at the top level as a sibling of `ucp`, not nested inside it.
- **Discount extension:** Extracts `discounts.codes` and `discounts.applied` into `discount_codes_json` and `discount_applied_json` BigQuery columns.
- **Checkout metadata:** Extracts `expires_at` and `continue_url` from the checkout session.
- **Order model:** Extracts `checkout.order` as a nested object (not flat `order_id`), including `permalink_url` and fulfillment `expectations[]`/`events[]`.
- **Session-order correlation:** Distinguishes checkout sessions from orders by checking for `checkout_id` (present on orders, absent on checkouts).
- **Checkout status scoping:** The `checkout_status` field is only populated for actual checkout responses, not order or cart responses. This uses two guards: (1) bodies with `checkout_id` are orders (skipped), and (2) the status value must be a known checkout status (`incomplete`, `requires_escalation`, `ready_for_complete`, `complete_in_progress`, `completed`, `canceled`). This prevents order statuses like `shipped` or `delivered` from polluting `checkout_status`.

### 6.2 PII Redaction

Optional PII redaction recursively walks JSON bodies, replacing configured fields (`email`, `phone`, `first_name`, `last_name`, `street_address`, `postal_code`) with `[REDACTED]`. Preserves analytics structure while preventing PII from reaching BigQuery.

---

## 7. Async BigQuery Writer

### 7.1 Batching Strategy

`AsyncBigQueryWriter` buffers events in memory and flushes when:
- `batch_size` threshold reached (default: 50)
- `flush()` called explicitly
- `close()` called (shutdown)

### 7.2 Non-Blocking I/O

All synchronous BigQuery client calls (`create_dataset`, `create_table`, `insert_rows_json`) are dispatched via `asyncio.to_thread()` to avoid blocking the event loop. This is critical when the writer is used inside an async web server (FastAPI/Starlette).

### 7.3 Auto-Table Creation

On first write, the writer lazily initializes the BigQuery client, creates dataset and table with full schema (partitioned + clustered). Uses `exists_ok=True` for idempotent setup across multiple processes.

### 7.4 Buffer Safety

- **Async-safe:** All buffer operations (enqueue, flush, re-queue) are protected by an `asyncio.Lock`.
- **Max buffer size:** The writer caps the in-memory buffer at `max_buffer_size` (default: 10,000). When the buffer is full, the oldest event is dropped and a warning is logged. This prevents unbounded memory growth if BigQuery is persistently unreachable.
- **Retry on failure:** Failed BigQuery inserts are re-queued to the front of the buffer for retry on next flush, also respecting the max buffer size cap.

---

## 8. Integration Patterns

### 8.1 FastAPI Middleware (Merchant Server)

`UCPAnalyticsMiddleware` is a Starlette `BaseHTTPMiddleware` that filters by UCP path prefixes (`/checkout-sessions`, `/.well-known/ucp`, `/orders`, `/carts`, `/identity`, `/testing/simulate`, `/webhooks`). For webhook paths, the tracker uses the request body (which contains the order payload) for both classification and field extraction, since the response is typically just an ack. For matching requests: reads request body, executes handler, captures response, measures latency.

Analytics recording is fire-and-forget: the middleware dispatches `tracker.record_http()` via `asyncio.create_task()` so it does not block the HTTP response. Response headers (including multi-value headers like `set-cookie`) are preserved using raw header passthrough.

The middleware is lazy-loaded in `__init__.py` via `__getattr__`, so importing the core package does not require `starlette` to be installed.

### 8.2 HTTPX Event Hook (Agent Client)

`UCPClientEventHook` is an async response event hook. Fires after every HTTP response, checks path against UCP patterns, reads response body via `aread()`, records event with `Response.elapsed` for latency.

### 8.3 ADK Plugin Adapter (Optional)

`UCPAgentAnalyticsPlugin` is a thin `BasePlugin` adapter with `before_tool_callback` (start timer) and `after_tool_callback` (classify tool call, extract fields, record event). Tool names are matched against UCP patterns. Timing entries are cleaned up for all tools (not just UCP ones) to prevent memory leaks.

### 8.4 Composability

All three integration points share the same `UCPAnalyticsTracker` and `AsyncBigQueryWriter`. Both middleware and event hook can be active simultaneously — they capture different sides of different HTTP connections without duplicating events.

---

## 9. Analytics Queries

10 ready-to-use BigQuery queries in `dashboards/queries.sql`:

| Query | Description | Key Metric |
|---|---|---|
| Checkout Funnel | Daily conversion rates by stage | created → completed % |
| Revenue by Merchant | Daily revenue, AOV per merchant | SUM(total_amount) |
| Payment Handler Mix | Transactions by handler/brand | Count, revenue, latency |
| Capability Adoption | UNNEST capabilities per merchant | Sessions per capability |
| Error Analysis | Error codes with severity breakdown | Affected sessions |
| Escalation Rate | Human handoff rate per merchant | Escalation / created |
| Latency Percentiles | p50/p95/p99 per operation type | APPROX_QUANTILES |
| Fulfillment Geography | Orders by country and method | Revenue per country |
| Session Timeline | Debug a specific checkout | Event sequence |
| Discovery Hit Rate | Profile fetch → checkout rate | Conversion from discovery |

---

## 10. Examples

Eight runnable examples are included (see [`examples/README.md`](../examples/README.md) for full details):

| Example | BigQuery? | Transport | Coverage |
|---|---|---|---|
| `e2e_demo.py` | No (SQLite) | REST | Checkout happy path (5 types) |
| `scenarios_demo.py` | Yes | REST | Errors, cancellation, escalation (7 types) |
| `cart_demo.py` | Yes | REST | Cart CRUD + checkout conversion (6 types) |
| `order_lifecycle_demo.py` | Yes | REST | Order delivered/returned/canceled (8 types) |
| `transport_demo.py` | Yes | REST/MCP/A2A | All 3 transports compared (5 types) |
| `identity_payment_demo.py` | Yes | REST | Identity linking + payment flows (10 types) |
| `bq_demo.py` | Yes | REST/MCP/A2A | All 27 event types, 3 transports, BQ verification |
| `bq_adk_demo.py` | Yes | ADK/MCP/A2A | All 27 event types via ADK plugin, BQ verification |

Shared BigQuery configuration (`PROJECT_ID`, `DATASET_ID`, `TABLE_ID`) lives in `examples/_demo_utils.py` and reads from the `GCP_PROJECT_ID` environment variable.

**Local demo (no GCP):** `e2e_demo.py` starts a mini UCP merchant server (FastAPI, port 8199) with a flower shop catalog, runs a shopping agent through the full happy path (discovery → checkout → payment → shipment), writes 6 events to local SQLite, and prints an analytics report.

**Comprehensive demos:** `bq_demo.py` and `bq_adk_demo.py` each exercise all 27 event types across REST, MCP, and A2A transports, then query BigQuery to verify all events landed correctly.

---

## 11. Deployment & Configuration

### Configuration Options

| Parameter | Default | Description |
|---|---|---|
| `project_id` | (required) | GCP project for BigQuery |
| `dataset_id` | `ucp_analytics` | BigQuery dataset name |
| `table_id` | `ucp_events` | BigQuery table name |
| `app_name` | `""` | Tags every event for multi-app filtering |
| `batch_size` | `50` | Events buffered before flush |
| `auto_create_table` | `True` | Create dataset/table on first write |
| `redact_pii` | `False` | Redact email, phone, address fields |
| `custom_metadata` | `None` | Dict attached as JSON to every event |

The underlying `AsyncBigQueryWriter` also accepts:

| Parameter | Default | Description |
|---|---|---|
| `max_buffer_size` | `10,000` | Maximum in-memory buffer; oldest events dropped when full |

---

## 12. Relationship to Existing Work

### vs. BigQuery Agent Analytics Plugin (ADK)

| Dimension | BQ Agent Analytics (ADK) | UCP Analytics (this) |
|---|---|---|
| Lives in | `google/adk-python` | `haiyuan-eng-google/Universal-Commerce-Protocol-Analytics` |
| Hooks into | ADK Runner callbacks | HTTP layer (FastAPI/HTTPX) |
| Understands | Generic agent/tool/model events | UCP checkout, order, payment, capabilities |
| Schema | Flat event rows | Commerce-aware (totals, line items, fulfillment) |
| Correlation | `session_id` | `session_id` + `checkout_session_id` + `order_id` |
| Financial tracking | No | Yes (minor units, currency, per-item) |
| Use together? | Yes | Yes — complementary layers |

The two plugins are complementary: BQ Agent Analytics provides general agent observability (token usage, model calls, tool latency), while UCP Analytics provides commerce-specific metrics (funnel, revenue, payment mix).

---

## 13. Future Work

- **Streaming analytics:** Real-time dashboards via BigQuery BI Engine or Pub/Sub
- **Cost attribution:** Correlate LLM token costs (from ADK plugin) with revenue per checkout session
- **Conformance testing integration:** Validate captured events against UCP conformance test expectations
- **Multi-merchant aggregation:** Cross-merchant funnel analysis for platform operators
- **Looker Studio template:** Pre-built dashboard deployable via Terraform
