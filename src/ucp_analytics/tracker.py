"""UCPAnalyticsTracker — the main entry point for recording UCP events.

Can be used directly (tracker.record()), or indirectly via the FastAPI
middleware or HTTPX event hook.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ucp_analytics.events import UCPEvent
from ucp_analytics.parser import UCPResponseParser
from ucp_analytics.writer import AsyncBigQueryWriter

logger = logging.getLogger(__name__)


class UCPAnalyticsTracker:
    """Records UCP commerce events into BigQuery.

    Usage — direct::

        tracker = UCPAnalyticsTracker(project_id="my-proj")
        await tracker.record_http(
            method="POST",
            path="/checkout-sessions",
            status_code=201,
            request_body={...},
            response_body={...},
            latency_ms=142.5,
        )
        await tracker.close()

    Usage — FastAPI middleware::

        from ucp_analytics import UCPAnalyticsMiddleware
        app.add_middleware(UCPAnalyticsMiddleware, tracker=tracker)

    Usage — HTTPX client hook::

        from ucp_analytics import UCPClientEventHook
        client = httpx.AsyncClient(
            event_hooks={"response": [UCPClientEventHook(tracker)]}
        )
    """

    def __init__(
        self,
        project_id: str,
        dataset_id: str = "ucp_analytics",
        table_id: str = "ucp_events",
        *,
        app_name: str = "",
        batch_size: int = 50,
        auto_create_table: bool = True,
        redact_pii: bool = False,
        pii_fields: Optional[List[str]] = None,
        custom_metadata: Optional[Dict[str, str]] = None,
    ):
        self.app_name = app_name
        self.redact_pii = redact_pii
        self.pii_fields = set(
            pii_fields
            or [
                "email",
                "phone",
                "first_name",
                "last_name",
                "phone_number",
                "street_address",
                "postal_code",
            ]
        )
        self.custom_metadata = custom_metadata

        self._writer = AsyncBigQueryWriter(
            project_id=project_id,
            dataset_id=dataset_id,
            table_id=table_id,
            batch_size=batch_size,
            auto_create_table=auto_create_table,
        )

    # ------------------------------------------------------------------ #
    # Primary API
    # ------------------------------------------------------------------ #

    async def record_http(
        self,
        *,
        method: str,
        url: str = "",
        path: str = "",
        status_code: int = 0,
        request_body: Optional[dict] = None,
        response_body: Optional[dict] = None,
        latency_ms: Optional[float] = None,
        request_headers: Optional[Dict[str, str]] = None,
    ) -> UCPEvent:
        """Record a single UCP HTTP request/response pair.

        This is the core method called by the middleware / hooks.
        """
        headers = request_headers or {}

        # Resolve path and host from url
        parsed_url = urlparse(url) if url else None
        if not path and parsed_url:
            path = parsed_url.path

        merchant_host = (parsed_url.hostname or "") if parsed_url else ""

        # Classify (pass request_body for webhook flows where payload
        # is in the request and response is just an ack)
        event_type = UCPResponseParser.classify(
            method, path, status_code, response_body,
            request_body=request_body,
        )

        # Build event
        event = UCPEvent(
            event_type=event_type.value,
            app_name=self.app_name,
            merchant_host=merchant_host,
            http_method=method.upper(),
            http_path=path,
            http_status_code=status_code if status_code else None,
            latency_ms=latency_ms,
            platform_profile_url=headers.get("ucp-agent", ""),
            idempotency_key=headers.get("idempotency-key", ""),
            request_id=headers.get("request-id", ""),
        )

        # Extract UCP fields from response (preferred) or request.
        # For webhooks, the order payload is in the request body and
        # the response is just an ack like {"status": "ok"}.
        is_webhook = "/webhook" in path
        if is_webhook and request_body:
            body_to_parse = request_body
        else:
            body_to_parse = response_body or request_body
        if body_to_parse and isinstance(body_to_parse, dict):
            if self.redact_pii:
                body_to_parse = self._redact(body_to_parse)
            fields = UCPResponseParser.extract(body_to_parse)
            for key, val in fields.items():
                if hasattr(event, key):
                    setattr(event, key, val)

        # Attach custom metadata
        if self.custom_metadata:
            event.custom_metadata_json = json.dumps(self.custom_metadata)

        await self._writer.enqueue(event.to_bq_row())
        return event

    async def record_jsonrpc(
        self,
        *,
        tool_name: str,
        transport: str = "mcp",
        status_code: int = 200,
        response_body: Optional[dict] = None,
        latency_ms: Optional[float] = None,
        merchant_host: str = "",
    ) -> UCPEvent:
        """Record a JSON-RPC (MCP or A2A) event.

        Maps tool/action names to UCP event types via classify_jsonrpc(),
        then extracts fields from the response body.
        """
        event_type = UCPResponseParser.classify_jsonrpc(
            tool_name, status_code, response_body
        )

        # Look up HTTP equivalent for metadata
        http_mapping = UCPResponseParser._TOOL_TO_HTTP.get(tool_name, ("", ""))
        method, path = http_mapping

        event = UCPEvent(
            event_type=event_type.value,
            app_name=self.app_name,
            merchant_host=merchant_host,
            transport=transport,
            http_method=method.upper() if method else "",
            http_path=path,
            http_status_code=status_code if status_code else None,
            latency_ms=latency_ms,
        )

        if response_body and isinstance(response_body, dict):
            if self.redact_pii:
                response_body = self._redact(response_body)
            fields = UCPResponseParser.extract(response_body)
            for key, val in fields.items():
                if hasattr(event, key):
                    setattr(event, key, val)

        if self.custom_metadata:
            event.custom_metadata_json = json.dumps(self.custom_metadata)

        await self._writer.enqueue(event.to_bq_row())
        return event

    async def record_event(self, event: UCPEvent) -> None:
        """Record a manually constructed event."""
        await self._writer.enqueue(event.to_bq_row())

    async def flush(self):
        """Force flush buffered events to BigQuery."""
        await self._writer.flush()

    async def close(self):
        """Flush and release resources."""
        await self._writer.close()
        logger.info("UCPAnalyticsTracker closed")

    # ------------------------------------------------------------------ #
    # PII redaction
    # ------------------------------------------------------------------ #

    def _redact(self, data: Any) -> Any:
        if isinstance(data, dict):
            return {
                k: "[REDACTED]" if k.lower() in self.pii_fields else self._redact(v)
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [self._redact(item) for item in data]
        return data
