"""Tests for UCPClientEventHook."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ucp_analytics.client_hooks import UCPClientEventHook


@pytest.fixture
def mock_tracker():
    tracker = MagicMock()
    tracker.record_http = AsyncMock()
    return tracker


@pytest.fixture
def hook(mock_tracker):
    return UCPClientEventHook(mock_tracker)


def _make_response(
    url: str = "https://shop.example.com/checkout-sessions",
    method: str = "POST",
    status_code: int = 201,
    json_body: dict | None = None,
    request_content: bytes = b"",
) -> httpx.Response:
    """Build a mock httpx.Response."""
    request = httpx.Request(method, url, content=request_content)
    response = httpx.Response(
        status_code=status_code,
        request=request,
        json=json_body,
    )
    response._elapsed = timedelta(milliseconds=42)
    return response


class TestUCPClientEventHook:
    async def test_records_ucp_request(self, hook, mock_tracker):
        resp = _make_response(
            url="https://shop.example.com/checkout-sessions",
            method="POST",
            status_code=201,
            json_body={"id": "chk_123"},
        )

        await hook(resp)

        mock_tracker.record_http.assert_awaited_once()
        call_kwargs = mock_tracker.record_http.call_args.kwargs
        assert call_kwargs["method"] == "POST"
        assert call_kwargs["status_code"] == 201
        assert call_kwargs["response_body"] == {"id": "chk_123"}

    async def test_skips_non_ucp_request(self, hook, mock_tracker):
        resp = _make_response(
            url="https://shop.example.com/api/health",
            method="GET",
            status_code=200,
        )

        await hook(resp)

        mock_tracker.record_http.assert_not_awaited()

    async def test_captures_discovery(self, hook, mock_tracker):
        resp = _make_response(
            url="https://shop.example.com/.well-known/ucp",
            method="GET",
            status_code=200,
            json_body={"ucp": {"version": "2026-01-11"}},
        )

        await hook(resp)

        mock_tracker.record_http.assert_awaited_once()

    async def test_captures_latency(self, hook, mock_tracker):
        resp = _make_response()

        await hook(resp)

        call_kwargs = mock_tracker.record_http.call_args.kwargs
        assert call_kwargs["latency_ms"] == pytest.approx(42.0, abs=1)

    async def test_handles_non_json_response(self, hook, mock_tracker):
        request = httpx.Request("POST", "https://shop.example.com/checkout-sessions")
        response = httpx.Response(
            status_code=500,
            request=request,
            text="Internal Server Error",
        )
        response._elapsed = timedelta(milliseconds=10)

        await hook(response)

        call_kwargs = mock_tracker.record_http.call_args.kwargs
        assert call_kwargs["response_body"] is None
