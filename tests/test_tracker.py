"""Tests for UCPAnalyticsTracker."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from ucp_analytics.tracker import UCPAnalyticsTracker


@pytest.fixture
def mock_writer():
    with patch("ucp_analytics.tracker.AsyncBigQueryWriter") as MockWriter:
        instance = MockWriter.return_value
        instance.enqueue = AsyncMock()
        instance.flush = AsyncMock()
        instance.close = AsyncMock()
        yield instance


@pytest.fixture
def tracker(mock_writer):
    return UCPAnalyticsTracker(project_id="test-project", app_name="test_app")


class TestRecordHttp:
    async def test_basic_record(self, tracker, mock_writer):
        event = await tracker.record_http(
            method="POST",
            url="https://merchant.example.com/checkout-sessions",
            status_code=201,
            response_body={"id": "chk_123", "status": "incomplete"},
        )

        assert event.event_type == "checkout_session_created"
        assert event.merchant_host == "merchant.example.com"
        assert event.http_method == "POST"
        assert event.checkout_session_id == "chk_123"
        mock_writer.enqueue.assert_awaited_once()

    async def test_path_from_url(self, tracker, mock_writer):
        event = await tracker.record_http(
            method="GET",
            url="https://shop.example.com/.well-known/ucp",
            status_code=200,
        )

        assert event.event_type == "profile_discovered"
        assert event.http_path == "/.well-known/ucp"

    async def test_explicit_path_overrides_url(self, tracker, mock_writer):
        event = await tracker.record_http(
            method="GET",
            url="https://shop.example.com/other",
            path="/.well-known/ucp",
            status_code=200,
        )

        assert event.event_type == "profile_discovered"

    async def test_latency_recorded(self, tracker, mock_writer):
        event = await tracker.record_http(
            method="POST",
            path="/checkout-sessions",
            status_code=201,
            latency_ms=42.5,
        )

        assert event.latency_ms == 42.5

    async def test_custom_metadata_attached(self, mock_writer):
        tracker = UCPAnalyticsTracker(
            project_id="test",
            custom_metadata={"env": "prod", "region": "us-west"},
        )
        event = await tracker.record_http(
            method="GET",
            path="/.well-known/ucp",
            status_code=200,
        )

        meta = json.loads(event.custom_metadata_json)
        assert meta["env"] == "prod"
        assert meta["region"] == "us-west"

    async def test_headers_extracted(self, tracker, mock_writer):
        event = await tracker.record_http(
            method="POST",
            path="/checkout-sessions",
            status_code=201,
            request_headers={
                "ucp-agent": 'profile="https://agent.example.com"',
                "idempotency-key": "idem_123",
                "request-id": "req_456",
            },
        )

        assert "agent.example.com" in event.platform_profile_url
        assert event.idempotency_key == "idem_123"
        assert event.request_id == "req_456"

    async def test_webhook_uses_request_body(self, tracker, mock_writer):
        """Webhook: order payload in request_body, response is ack."""
        order_payload = {
            "id": "order_xyz",
            "checkout_id": "chk_abc",
            "status": "shipped",
        }
        event = await tracker.record_http(
            method="POST",
            url="https://merchant.example.com/webhooks/partners/p1/events/order",
            status_code=200,
            request_body=order_payload,
            response_body={"status": "ok"},
        )

        assert event.event_type == "order_shipped"
        assert event.order_id == "order_xyz"
        assert event.checkout_session_id == "chk_abc"

    async def test_webhook_falls_back_to_response_body_when_no_request(
        self, tracker, mock_writer
    ):
        """Webhook callers that only have the response side in hand must
        still produce a populated row. Pin this fallback so the order
        payload-extraction doesn't regress when the new request/response
        merge logic short-circuits webhook flows."""
        order_payload = {
            "id": "order_123",
            "checkout_id": "chk_123",
            "status": "delivered",
        }
        event = await tracker.record_http(
            method="POST",
            url="https://merchant.example.com/webhooks/partners/p1/events/order",
            status_code=200,
            request_body=None,
            response_body=order_payload,
        )

        assert event.event_type == "order_delivered"
        assert event.order_id == "order_123"
        assert event.checkout_session_id == "chk_123"

    async def test_singular_webhook_uses_request_body(self, tracker, mock_writer):
        """Legacy /webhook/ (singular) should also use request_body."""
        order_payload = {
            "id": "order_abc",
            "checkout_id": "chk_xyz",
            "status": "delivered",
        }
        event = await tracker.record_http(
            method="POST",
            url="https://merchant.example.com/webhook/order-delivered",
            status_code=200,
            request_body=order_payload,
            response_body={"status": "ok"},
        )

        assert event.order_id == "order_abc"
        assert event.checkout_session_id == "chk_xyz"

    async def test_request_body_context_survives_response_body(
        self, tracker, mock_writer
    ):
        """A checkout-create exchange has Context only on the request side
        (the platform tells the merchant the buyer's intent / locale /
        currency); the response carries the resolved checkout state.
        Both must end up on the same row."""
        request_body = {
            "context": {
                "intent": "buy a birthday gift",
                "language": "en-US",
                "currency": "USD",
                "eligibility": ["dev.example.loyalty_member"],
            },
            "line_items": [{"item": {"id": "sku_rose"}, "quantity": 1}],
        }
        response_body = {
            "id": "chk_123",
            "status": "ready_for_complete",
            "currency": "USD",
        }
        event = await tracker.record_http(
            method="POST",
            url="https://merchant.example.com/checkout-sessions",
            status_code=201,
            request_body=request_body,
            response_body=response_body,
        )

        # Response-only fields land.
        assert event.event_type == "checkout_session_created"
        assert event.checkout_session_id == "chk_123"
        assert event.checkout_status == "ready_for_complete"
        assert event.currency == "USD"
        # Request-only fields survive the response-side extraction.
        assert event.context_intent == "buy a birthday gift"
        assert event.context_language == "en-US"
        assert event.context_currency == "USD"
        assert event.context_eligibility_json is not None
        assert "dev.example.loyalty_member" in event.context_eligibility_json

    # --- HTTP message signing (RFC 9421 / UCP signatures.md) ---

    async def test_unsigned_exchange_marks_both_directions_false(
        self, tracker, mock_writer
    ):
        """An exchange with observed-but-unsigned headers records
        request_signed=False / response_signed=False — distinct from a
        row where headers were never observed at all."""
        event = await tracker.record_http(
            method="POST",
            path="/checkout-sessions",
            status_code=201,
            request_headers={"content-type": "application/json"},
            response_headers={"content-type": "application/json"},
        )
        assert event.request_signed is False
        assert event.response_signed is False
        assert event.request_signature_keyid is None
        assert event.response_signature_keyid is None

    async def test_unobserved_headers_record_none_not_false(self, tracker, mock_writer):
        """Direct callers that don't pass headers at all must record
        request_signed / response_signed as None (unknown), not False
        (observed unsigned). Without this the "% signed traffic" KPI
        is biased downward by every direct-API row."""
        event = await tracker.record_http(
            method="POST",
            path="/checkout-sessions",
            status_code=201,
            # request_headers and response_headers both omitted entirely
        )
        assert event.request_signed is None
        assert event.response_signed is None
        assert event.request_signature_keyid is None
        assert event.response_signature_keyid is None

    async def test_request_side_signing_extracts_keyid(self, tracker, mock_writer):
        """Request signed by the platform: extract request_signed=True and
        the keyid for joining against /.well-known/ucp signing_keys[].
        Both Signature-Input AND Signature must be present together
        per UCP signatures.md."""
        event = await tracker.record_http(
            method="POST",
            path="/checkout-sessions",
            status_code=201,
            request_headers={
                "Signature-Input": (
                    'sig1=("@method" "@path" "host");'
                    'keyid="platform-key-1";created=1770000000'
                ),
                "Signature": "sig1=:abc==:",
            },
            response_headers={"content-type": "application/json"},
        )
        assert event.request_signed is True
        assert event.request_signature_keyid == "platform-key-1"
        assert event.response_signed is False
        assert event.response_signature_keyid is None

    async def test_half_signed_request_is_not_counted_as_signed(
        self, tracker, mock_writer
    ):
        """Signature-Input present but Signature missing → malformed
        half-signature. Must record request_signed=False so the KPI
        doesn't inflate on incomplete senders. We still parse the keyid
        for forensic purposes — useful when debugging which platforms
        are sending half-signed traffic."""
        event = await tracker.record_http(
            method="POST",
            path="/checkout-sessions",
            status_code=201,
            request_headers={
                # Metadata header only, no actual signature value.
                "Signature-Input": 'sig1=();keyid="platform-key-1"',
            },
            response_headers={"content-type": "application/json"},
        )
        assert event.request_signed is False
        assert event.request_signature_keyid == "platform-key-1"

    async def test_response_side_signing_extracts_keyid(self, tracker, mock_writer):
        """Response signed by the merchant: extract response_signed=True
        independently from request side, since request and response can
        be signed by different parties."""
        event = await tracker.record_http(
            method="POST",
            path="/checkout-sessions",
            status_code=201,
            request_headers={"content-type": "application/json"},
            response_headers={
                "signature-input": 'sig1=();keyid="merchant-key-A"',
                "signature": "sig1=:def==:",
            },
        )
        assert event.request_signed is False
        assert event.response_signed is True
        assert event.response_signature_keyid == "merchant-key-A"
        assert event.request_signature_keyid is None

    async def test_both_sides_signed_with_distinct_keyids(self, tracker, mock_writer):
        """Platform-signed request, merchant-signed response. Distinct
        keyids land in their respective columns — pinned because
        conflating them was an explicit issue #8 acceptance concern."""
        event = await tracker.record_http(
            method="POST",
            path="/checkout-sessions",
            status_code=201,
            request_headers={
                "Signature-Input": 'sig1=();keyid="platform-K"',
                "Signature": "sig1=:abc==:",
            },
            response_headers={
                "Signature-Input": 'sig1=();keyid="merchant-K"',
                "Signature": "sig1=:def==:",
            },
        )
        assert event.request_signed is True
        assert event.response_signed is True
        assert event.request_signature_keyid == "platform-K"
        assert event.response_signature_keyid == "merchant-K"

    async def test_signing_lookup_is_case_insensitive(self, tracker, mock_writer):
        """Real middleware hands the headers off in either casing
        depending on the framework. Pin case-insensitive lookup."""
        event = await tracker.record_http(
            method="POST",
            path="/checkout-sessions",
            status_code=201,
            request_headers={
                "SIGNATURE-INPUT": 'sig1=();keyid="upper-K"',
                "SIGNATURE": "sig1=:abc==:",
            },
            response_headers={
                "sIgNaTuRe-InPuT": 'sig1=();keyid="mixed-K"',
                "sIgNaTuRe": "sig1=:def==:",
            },
        )
        assert event.request_signed is True
        assert event.response_signed is True
        assert event.request_signature_keyid == "upper-K"
        assert event.response_signature_keyid == "mixed-K"

    async def test_response_body_overlays_request_body_on_conflict(
        self, tracker, mock_writer
    ):
        """When request and response both carry the same field, response
        wins — it's the merchant-confirmed state. Pinned so the merge
        order doesn't drift."""
        # Request says draft USD; response says authoritative EUR.
        request_body = {"currency": "USD", "context": {"intent": "browsing"}}
        response_body = {
            "id": "chk_456",
            "status": "incomplete",
            "currency": "EUR",
        }
        event = await tracker.record_http(
            method="POST",
            url="https://merchant.example.com/checkout-sessions",
            status_code=201,
            request_body=request_body,
            response_body=response_body,
        )
        # Response wins on the conflicting field.
        assert event.currency == "EUR"
        # Request-only field is preserved.
        assert event.context_intent == "browsing"


class TestPIIRedaction:
    async def test_redacts_configured_fields(self, mock_writer):
        tracker = UCPAnalyticsTracker(
            project_id="test",
            redact_pii=True,
        )
        await tracker.record_http(
            method="PUT",
            path="/checkout-sessions/chk_123",
            status_code=200,
            response_body={
                "id": "chk_123",
                "status": "ready_for_complete",
                "buyer": {
                    "email": "jane@example.com",
                    "phone": "555-1234",
                    "first_name": "Jane",
                    "full_name": "Jane Doe",
                },
            },
        )

        # The event should be recorded (no crash)
        mock_writer.enqueue.assert_awaited_once()

    async def test_redact_nested(self, mock_writer):
        tracker = UCPAnalyticsTracker(
            project_id="test",
            redact_pii=True,
        )
        data = {
            "buyer": {"email": "secret@test.com"},
            "items": [{"email": "also@secret.com"}],
        }
        redacted = tracker._redact(data)

        assert redacted["buyer"]["email"] == "[REDACTED]"
        assert redacted["items"][0]["email"] == "[REDACTED]"

    async def test_redact_preserves_non_pii(self, mock_writer):
        tracker = UCPAnalyticsTracker(
            project_id="test",
            redact_pii=True,
        )
        data = {"id": "chk_123", "status": "incomplete", "email": "secret"}
        redacted = tracker._redact(data)

        assert redacted["id"] == "chk_123"
        assert redacted["status"] == "incomplete"
        assert redacted["email"] == "[REDACTED]"

    async def test_no_redaction_when_disabled(self, mock_writer):
        tracker = UCPAnalyticsTracker(project_id="test", redact_pii=False)
        await tracker.record_http(
            method="PUT",
            path="/checkout-sessions/chk_123",
            status_code=200,
            response_body={
                "id": "chk_123",
                "buyer": {"email": "jane@example.com"},
            },
        )

        mock_writer.enqueue.assert_awaited_once()


class TestFlushAndClose:
    async def test_flush_delegates(self, tracker, mock_writer):
        await tracker.flush()
        mock_writer.flush.assert_awaited_once()

    async def test_close_delegates(self, tracker, mock_writer):
        await tracker.close()
        mock_writer.close.assert_awaited_once()

    async def test_close_drains_pending_tasks(self, tracker, mock_writer):
        """close() should await in-flight tasks before flushing."""
        completed = []

        async def slow_work():
            await asyncio.sleep(0.01)
            completed.append(True)

        task = asyncio.create_task(slow_work())
        tracker.register_pending_task(task)

        await tracker.close()
        assert completed == [True]
        assert len(tracker._pending_tasks) == 0
