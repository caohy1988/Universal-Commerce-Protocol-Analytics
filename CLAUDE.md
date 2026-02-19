# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`ucp-analytics` — a BigQuery-backed commerce observability library for the Universal Commerce Protocol (UCP). It passively captures UCP HTTP traffic (checkout sessions, orders, payments, capabilities) into BigQuery via ASGI middleware (server-side), HTTPX event hooks (client-side), or a Google ADK plugin (agent-side).

## Commands

```bash
# Install (core + dev deps)
uv sync --extra dev

# Install with optional integration deps
uv sync --extra fastapi   # FastAPI/Starlette middleware
uv sync --extra adk       # Google ADK plugin

# Run tests
uv run pytest tests/ -v

# Run a single test
uv run pytest tests/test_parser.py::TestClassify::test_discovery -v

# Lint
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Auto-fix lint issues
uv run ruff check --fix src/ tests/
uv run ruff format src/ tests/

# Run E2E demo (no GCP credentials needed, uses SQLite stub)
pip install fastapi uvicorn httpx && python examples/e2e_demo.py
```

## Architecture

The library is a passive HTTP observer — it never modifies requests or responses.

**Data flow:** HTTP capture → `UCPResponseParser` (classify + extract) → `UCPAnalyticsTracker` (orchestrator) → `AsyncBigQueryWriter` (buffered batch insert) → BigQuery

**Three integration points feed into a shared `UCPAnalyticsTracker`:**
- `middleware.py` — Starlette `BaseHTTPMiddleware` for server-side capture (filters by UCP path prefixes including `/carts`). Analytics recording dispatched via `asyncio.create_task()`. Lazy-loaded in `__init__.py` via `__getattr__`.
- `client_hooks.py` — HTTPX async response event hook + optional wrapping transport for client-side capture
- `adk_plugin.py` — Google ADK `BasePlugin` using before/after tool callbacks (optional dep, graceful `ImportError` fallback)

**Core modules:**
- `events.py` — `UCPEvent` dataclass (~45 fields including `fulfillment_amount`, `items_discount_amount`, `fee_amount`, `discount_codes_json`, `discount_applied_json`, `expires_at`, `continue_url`, `permalink_url`), `UCPEventType` enum (24+ types including cart events), `CheckoutStatus` enum (6-state machine). `UCPEvent.to_bq_row()` serializes to dict, dropping None fields.
- `parser.py` — `UCPResponseParser.classify()` maps HTTP method+path+status to event type via regex (including `/carts` endpoints). `.extract()` parses UCP JSON for structured fields: totals (all 7 spec types: items_discount, subtotal, discount, fulfillment, tax, fee, total), payment instruments (spec format with `handler_id`, fallback to legacy `handlers[]`), capabilities (object-keyed by reverse-domain name with array fallback), discount extension (codes + applied), checkout metadata (expires_at, continue_url), order model (nested object with permalink_url, fulfillment expectations/events), fulfillment, and errors.
- `writer.py` — `AsyncBigQueryWriter` with `asyncio.Lock`-protected buffer, lazy BigQuery client init, auto dataset+table DDL creation, failed-batch retry, and max buffer size (10k default). All BigQuery I/O runs via `asyncio.to_thread()` to avoid blocking the event loop.
- `tracker.py` — `UCPAnalyticsTracker` orchestrates parser→writer. Provides `record_http()`, `record_event()`, `flush()`, `close()`. Has recursive PII redaction (`_redact()`).

## Key Design Decisions

- **Heuristic classification**: event types are inferred from HTTP method + URL path regex + response body — no UCP spec metadata required
- **Lazy init**: BigQuery client and table creation are deferred until the first write
- **Fire-and-forget**: analytics exceptions are caught and logged, never surfacing to the application
- **Async-safe**: all buffer operations use `asyncio.Lock`; failed batches are re-queued for retry
- **Optional deps**: ADK and FastAPI integrations are lazy-loaded (`__getattr__` in `__init__.py`, `try/except ImportError` in `adk_plugin.py`) so the core package has no hard dependency on starlette or google-adk
- **Middleware is fire-and-forget**: analytics recording uses `asyncio.create_task()` so it doesn't block the HTTP response

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on push/PR to `main`:
- **lint**: `ruff check` + `ruff format --check`
- **test**: `pytest` across Python 3.10, 3.11, 3.12

## Tests

58 tests across 5 files (all use pytest-asyncio `auto` mode):
- `test_parser.py` — classify (event type mapping including cart endpoints) + extract (field parsing with spec-aligned test data: instruments, object-keyed capabilities, all 7 total types, discount extension, order confirmation)
- `test_events.py` — `UCPEvent.to_bq_row()`, defaults, enum values
- `test_tracker.py` — `record_http()`, PII redaction, flush/close delegation (mocks `AsyncBigQueryWriter`)
- `test_writer.py` — enqueue buffering, batch flush, retry on error, max buffer eviction, DDL generation (mocks BQ client)
- `test_client_hooks.py` — HTTPX hook capture, skip non-UCP, latency, non-JSON responses

## Tooling Config

- Build backend: `hatchling` (PEP 517)
- Core deps: `google-cloud-bigquery`, `httpx` (no pydantic)
- Ruff: line-length 88, target py310, rules E/F/I/W
- pytest-asyncio: `asyncio_mode = "auto"`
