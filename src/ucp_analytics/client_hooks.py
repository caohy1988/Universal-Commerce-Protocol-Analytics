"""HTTPX event hook for UCP platform / agent clients.

Attach to an httpx.AsyncClient to automatically capture every outgoing
UCP request and incoming response into BigQuery analytics.

Usage::

    import httpx
    from ucp_analytics import UCPAnalyticsTracker, UCPClientEventHook

    tracker = UCPAnalyticsTracker(project_id="my-proj", app_name="shopping_agent")
    hook = UCPClientEventHook(tracker)

    client = httpx.AsyncClient(
        event_hooks={"response": [hook]},
    )

    # Every call to the UCP merchant is now tracked:
    resp = await client.post(
        "https://merchant.example.com/checkout-sessions",
        json={"line_items": [...]},
    )
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class UCPClientEventHook:
    """HTTPX response event hook that records UCP analytics.

    This hook fires after every HTTP response. It checks whether the
    request path looks like a UCP operation and, if so, records both
    the request and response into BigQuery.
    """

    UCP_PATH_PATTERNS = (
        "/checkout-sessions",
        "/carts",
        "/.well-known/ucp",
        "/orders",
        "/identity",
        "/simulate-shipping",
        "/webhooks",
        "/webhook",
    )

    def __init__(self, tracker: Any) -> None:
        self.tracker = tracker

    async def __call__(self, response: httpx.Response) -> None:
        """Called by HTTPX after each response is received."""
        request = response.request
        path = request.url.path

        # Skip non-UCP requests
        if not any(p in path for p in self.UCP_PATH_PATTERNS):
            return

        # Read response body
        await response.aread()

        response_body = None
        try:
            response_body = response.json()
        except Exception:
            pass

        # Read request body
        request_body = None
        if request.content:
            try:
                request_body = json.loads(request.content)
            except Exception:
                pass

        # Latency (approximate — from request creation to response received)
        latency_ms = None
        elapsed = response.elapsed
        if elapsed:
            latency_ms = round(elapsed.total_seconds() * 1000, 2)

        # Record
        try:
            headers = dict(request.headers)
            await self.tracker.record_http(
                method=request.method,
                url=str(request.url),
                path=path,
                status_code=response.status_code,
                request_body=request_body,
                response_body=response_body,
                latency_ms=latency_ms,
                request_headers=headers,
            )
        except Exception:
            logger.exception("UCP client analytics recording failed")


class UCPClientTransport(httpx.AsyncBaseTransport):
    """Optional: wrapping transport that adds timing to every request.

    For most users, the event hook above is sufficient. This transport
    wrapper adds precise timing that httpx.Response.elapsed might miss
    for streaming responses.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport, tracker: Any):
        self._transport = transport
        self.tracker = tracker

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        start = time.monotonic()
        response = await self._transport.handle_async_request(request)
        latency_ms = round((time.monotonic() - start) * 1000, 2)

        # Attach latency for the event hook to pick up
        response.extensions["ucp_latency_ms"] = latency_ms
        return response
