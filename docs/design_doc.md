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
10. [End-to-End Demo](#10-end-to-end-demo)
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
- Non-REST transports in v0.1 (MCP and A2A hooks are future work)

---

## 3. Architecture

### 3.1 System Overview

UCP Analytics hooks into the HTTP transport layer — the primary and most widely-deployed UCP binding. It operates as a passive observer: intercepting requests/responses without modifying them, extracting structured UCP fields, and writing batched event rows to BigQuery.

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
| **Direct API** | Any | `tracker.record_http()` | Custom integrations, testing |

### 3.3 Data Flow

1. The middleware or event hook captures raw HTTP method, path, status code, request body, and response body.
2. `UCPResponseParser.classify()` maps the HTTP operation to a UCP event type using regex matching on well-known UCP paths.
3. `UCPResponseParser.extract()` parses the UCP JSON response to extract structured fields: session ID, status, totals, line items, payment handlers, fulfillment, capabilities, extensions, errors.
4. The event is serialized into a flat BigQuery row and enqueued in the `AsyncBigQueryWriter` buffer.
5. When buffer reaches `batch_size` (default 50), rows flush to BigQuery via streaming insert. Remaining rows flush on `tracker.close()`.

---

## 4. Event Classification

### 4.1 Event Type Mapping

Events are automatically classified from HTTP method + path + response body:

| HTTP Operation | Event Type | Trigger |
|---|---|---|
| `GET /.well-known/ucp` | `profile_discovered` | Agent fetches merchant discovery profile |
| `POST /checkout-sessions` | `checkout_session_created` | New checkout session initiated |
| `GET /checkout-sessions/{id}` | `checkout_session_get` | Session retrieved for display/validation |
| `PUT /checkout-sessions/{id}` | `checkout_session_updated` | Buyer info, fulfillment, or discount added |
| `PUT /checkout-sessions/{id}` | `checkout_escalation` | Response status = `requires_escalation` |
| `POST /checkout-sessions/{id}/complete` | `checkout_session_completed` | Checkout finalized with payment |
| `POST /checkout-sessions/{id}/cancel` | `checkout_session_canceled` | Session explicitly canceled |
| `POST /orders` | `order_created` | Order webhook from merchant |
| `POST /testing/simulate-shipping/{id}` | `order_shipped` | Shipment simulation (testing) |
| Any path, status >= 400 | `error` | HTTP error response |

### 4.2 UCP Checkout State Machine Alignment

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
| `tax_amount` | INTEGER | Tax in minor units |
| `shipping_amount` | INTEGER | Shipping cost in minor units |
| `discount_amount` | INTEGER | Discount in minor units |
| `total_amount` | INTEGER | Total in minor units |
| `line_item_count` | INTEGER | Number of items in checkout |
| `line_items_json` | JSON | Full line items array |

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

- **Totals array parsing:** UCP represents financial data as a typed `totals` array. Each entry has `type` (subtotal, tax, shipping, discount, total) and `amount` in minor units. The parser maps these to individual BigQuery columns.
- **Payment extraction:** Handles both creation responses (`payment.handlers` array) and completion requests (`payment_data` object). Extracts handler_id, instrument type, and brand — never captures credentials/tokens.
- **Capability detection:** Extracts the UCP metadata envelope (`ucp.version`, `ucp.capabilities`) and separates core capabilities from extensions.
- **Session-order correlation:** Distinguishes checkout sessions from orders by checking for `checkout_id` (present on orders, absent on checkouts).

### 6.2 PII Redaction

Optional PII redaction recursively walks JSON bodies, replacing configured fields (`email`, `phone`, `first_name`, `last_name`, `street_address`, `postal_code`) with `[REDACTED]`. Preserves analytics structure while preventing PII from reaching BigQuery.

---

## 7. Async BigQuery Writer

### 7.1 Batching Strategy

`AsyncBigQueryWriter` buffers events in memory and flushes when:
- `batch_size` threshold reached (default: 50)
- `flush()` called explicitly
- `close()` called (shutdown)

### 7.2 Auto-Table Creation

On first write, the writer lazily initializes the BigQuery client, creates dataset and table with full schema (partitioned + clustered). Uses `exists_ok=True` for idempotent setup across multiple processes.

### 7.3 Error Handling

Failed BigQuery inserts are re-queued to the front of the buffer for retry on next flush. Partial insert errors are logged at ERROR level. This prevents data loss without blocking the main application.

---

## 8. Integration Patterns

### 8.1 FastAPI Middleware (Merchant Server)

`UCPAnalyticsMiddleware` is a Starlette `BaseHTTPMiddleware` that filters by UCP path prefixes (`/checkout-sessions`, `/.well-known/ucp`, `/orders`, `/identity`, `/testing/simulate`). For matching requests: reads request body, executes handler, captures response, measures latency, records event.

### 8.2 HTTPX Event Hook (Agent Client)

`UCPClientEventHook` is an async response event hook. Fires after every HTTP response, checks path against UCP patterns, reads response body via `aread()`, records event with `Response.elapsed` for latency.

### 8.3 ADK Plugin Adapter (Optional)

`UCPAdkPlugin` is a thin `BasePlugin` adapter with `before_tool_callback` (start timer) and `after_tool_callback` (classify tool call, extract fields, record event). Tool names matched against UCP patterns.

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

## 10. End-to-End Demo

`examples/e2e_demo.py` (~860 lines) runs without GCP credentials:

1. Starts a mini UCP merchant server (FastAPI, port 8199) with flower shop catalog
2. Runs a shopping agent through full happy path: discovery → create checkout (2 bouquets + 1 sunflower) → add buyer + shipping → apply discount → complete with Visa → simulate shipment
3. Writes 6 events to local SQLite (same schema as BigQuery)
4. Prints analytics report: session timeline, funnel, financial summary, payment, capabilities, latency

**Demo output:** $79.97 subtotal + $7.00 tax + $5.99 shipping - $5.00 discount = **$87.96 total**. 6 events captured in ~35ms total.

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

- **MCP transport hook:** Intercept UCP operations over Model Context Protocol bindings
- **A2A transport hook:** Capture agent-to-agent UCP commerce events
- **Streaming analytics:** Real-time dashboards via BigQuery BI Engine or Pub/Sub
- **Cost attribution:** Correlate LLM token costs (from ADK plugin) with revenue per checkout session
- **Conformance testing integration:** Validate captured events against UCP conformance test expectations
- **Multi-merchant aggregation:** Cross-merchant funnel analysis for platform operators
- **Looker Studio template:** Pre-built dashboard deployable via Terraform

---

## Appendix: Package Statistics

| Component | Lines | Complexity |
|---|---|---|
| `events.py` | 152 | Data classes + enums |
| `parser.py` | 215 | Regex matching + JSON traversal |
| `writer.py` | 205 | Async batch writer + DDL |
| `tracker.py` | 170 | Orchestrator + PII redaction |
| `middleware.py` | 122 | ASGI middleware |
| `client_hooks.py` | 126 | HTTPX event hook |
| `adk_plugin.py` | 153 | Optional ADK adapter |
| `e2e_demo.py` | 860 | Self-contained demo |
| `queries.sql` | 167 | 10 dashboard queries |
| **Total** | **2,170** | |
