"""Tests for UCPAnalyticsTracker."""

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
