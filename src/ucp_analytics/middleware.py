"""FastAPI / Starlette ASGI middleware for UCP merchant servers.

Drop this onto the UCP samples server (or any FastAPI-based UCP business
server) to automatically capture every checkout-session, order, and
discovery request into BigQuery.

Usage::

    from fastapi import FastAPI
    from ucp_analytics import UCPAnalyticsTracker, UCPAnalyticsMiddleware

    app = FastAPI()
    tracker = UCPAnalyticsTracker(project_id="my-proj", app_name="flower_shop")
    app.add_middleware(UCPAnalyticsMiddleware, tracker=tracker)

    @app.on_event("shutdown")
    async def shutdown():
        await tracker.close()  # drains in-flight tasks, then flushes
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class UCPAnalyticsMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that intercepts UCP HTTP traffic on the server side.

    For every request whose path looks like a UCP operation
    (/checkout-sessions, /.well-known/ucp, /orders, etc.) the middleware:

    1. Reads the request body
    2. Lets the handler execute normally
    3. Reads the response body
    4. Passes both to UCPAnalyticsTracker.record_http()

    Non-UCP paths are passed through with zero overhead.
    """

    # Paths that indicate UCP traffic
    UCP_PATH_PREFIXES = (
        "/checkout-sessions",
        "/carts",
        "/.well-known/ucp",
        "/orders",
        "/identity",
        "/testing/simulate",
        "/webhooks",
        "/webhook",
    )

    def __init__(self, app: Any, tracker: Any) -> None:
        super().__init__(app)
        self.tracker = tracker

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Fast path: skip non-UCP requests
        if not any(path.startswith(p) for p in self.UCP_PATH_PREFIXES):
            return await call_next(request)

        # Read request body (for POST/PUT)
        request_body = None
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                raw = await request.body()
                if raw:
                    request_body = json.loads(raw)
            except Exception:
                pass

        # Execute the actual handler
        start = time.monotonic()
        response = await call_next(request)
        latency_ms = round((time.monotonic() - start) * 1000, 2)

        # Read response body
        response_body = None
        body_bytes = b""
        async for chunk in response.body_iterator:
            if isinstance(chunk, str):
                body_bytes += chunk.encode("utf-8")
            else:
                body_bytes += chunk

        try:
            if body_bytes:
                response_body = json.loads(body_bytes)
        except Exception:
            pass

        # Record the event (fire-and-forget; don't block the response).
        # Tasks are tracked on the tracker so tracker.close() drains them.
        try:
            headers = dict(request.headers)
            task = asyncio.create_task(
                self.tracker.record_http(
                    method=request.method,
                    url=str(request.url),
                    path=path,
                    status_code=response.status_code,
                    request_body=request_body,
                    response_body=response_body,
                    latency_ms=latency_ms,
                    request_headers=headers,
                )
            )
            self.tracker.register_pending_task(task)
        except Exception:
            logger.exception("UCP analytics recording failed")

        # Re-create the response with the consumed body, preserving raw headers
        from starlette.responses import Response as StarletteResponse

        new_response = StarletteResponse(
            content=body_bytes,
            status_code=response.status_code,
            media_type=response.media_type,
        )
        # Preserve all original headers including multi-value ones (e.g. set-cookie)
        new_response.raw_headers = response.raw_headers
        return new_response

    async def drain_pending(self) -> None:
        """Await all in-flight recording tasks.

        .. deprecated::
            Pending tasks are now tracked on the tracker itself.
            ``tracker.close()`` drains automatically.  This method
            delegates to ``self.tracker.drain_pending()`` for
            backwards compatibility.
        """
        await self.tracker.drain_pending()
