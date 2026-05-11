"""UCPAnalyticsTracker — the main entry point for recording UCP events.

Can be used directly (tracker.record()), or indirectly via the FastAPI
middleware or HTTPX event hook.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ucp_analytics._headers import (
    is_signed,
    parse_bearer_challenge,
    signature_keyid,
    ucp_agent_profile_url,
    webhook_id,
    webhook_timestamp_iso,
)
from ucp_analytics._path_match import is_webhook_delivery
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
        webhook_path_prefixes: Optional[List[str]] = None,
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
        # UCP order.md: "The URL format is platform-specific." The
        # default `/webhook(s)` prefix lives inside is_webhook_delivery;
        # operators on platforms that publish `/events`, `/ucp-events`,
        # `/hooks/<id>`, etc. extend that set here. The header-based
        # fallback (Webhook-Id + Webhook-Timestamp) catches deliveries
        # even when the path is unknown, so this list is a
        # noise-suppression knob more than a coverage knob.
        self.webhook_path_prefixes: tuple = tuple(webhook_path_prefixes or ())

        self._writer = AsyncBigQueryWriter(
            project_id=project_id,
            dataset_id=dataset_id,
            table_id=table_id,
            batch_size=batch_size,
            auto_create_table=auto_create_table,
        )
        self._pending_tasks: set[asyncio.Task] = set()

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
        response_headers: Optional[Dict[str, str]] = None,
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

        # Single source of truth for "is this a webhook delivery?" — used
        # both to gate body extraction toward the request side and to
        # scope Webhook-Id / Webhook-Timestamp capture to webhook flows
        # only. Per UCP order.md these headers belong to the Order Event
        # Webhook flow; capturing them off arbitrary requests would let
        # a buggy or malicious sender stamp webhook metadata onto a
        # checkout / cart / catalog row.
        #
        # is_webhook_delivery accepts either a default/configured path
        # prefix OR the Standard Webhooks header pair (Webhook-Id +
        # Webhook-Timestamp). The header pair is the strong signal --
        # UCP order.md requires both on every order-event webhook --
        # so a platform that publishes `/events` instead of `/webhooks`
        # is still detected without the operator having to enumerate
        # every variant.
        is_webhook = is_webhook_delivery(
            path, request_headers, self.webhook_path_prefixes
        )

        # Classify (pass request_body for webhook flows where payload
        # is in the request and response is just an ack). Forward the
        # same webhook-detection signals so the classifier picks the
        # ORDER_* taxonomy rather than falling through to REQUEST.
        event_type = UCPResponseParser.classify(
            method,
            path,
            status_code,
            response_body,
            request_body=request_body,
            request_headers=request_headers,
            webhook_path_prefixes=self.webhook_path_prefixes,
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
            # HTTP message signing per RFC 9421. is_signed / signature_keyid
            # do their own case-insensitive lookup, so we don't need to
            # pre-normalize the header dict here.
            #
            # Distinguish "headers never observed" (None) from "headers
            # observed and unsigned" (False). Middleware and HTTPX hook
            # always pass a dict (possibly empty), so genuinely unsigned
            # traffic records False; direct callers that don't pass the
            # corresponding side record None — without this the
            # "% signed traffic" KPI would treat every direct-API row as
            # observed unsigned.
            request_signed=(
                is_signed(request_headers) if request_headers is not None else None
            ),
            response_signed=(
                is_signed(response_headers) if response_headers is not None else None
            ),
            request_signature_keyid=signature_keyid(request_headers),
            response_signature_keyid=signature_keyid(response_headers),
            # Standard Webhooks metadata (UCP order.md). The Webhook-*
            # headers ride on the inbound webhook *request* and are
            # scoped to the Order Event Webhook flow; gate on is_webhook
            # so a checkout / cart / catalog request that happens to
            # carry these headers (buggy sender, fuzzing, etc.) doesn't
            # stamp webhook metadata onto an unrelated row.
            # webhook_timestamp_iso parses the Unix-seconds value into
            # an ISO 8601 UTC string suitable for the TIMESTAMP column.
            webhook_id=webhook_id(request_headers) if is_webhook else None,
            webhook_timestamp=(
                webhook_timestamp_iso(request_headers) if is_webhook else None
            ),
            # UCP-Agent profile URI (RFC 8941 Dictionary, parsed). Direction-
            # neutral on purpose: on platform → business requests this is the
            # platform's profile, on business → platform webhooks it's the
            # business's. The legacy platform_profile_url field above keeps
            # storing the raw header string for backwards compatibility.
            ucp_agent_profile_url=ucp_agent_profile_url(request_headers),
        )

        # WWW-Authenticate Bearer challenge (RFC 7235 / RFC 6750 / RFC 9728).
        # Lives on the response side — issued by the merchant on 401/403.
        # We parse whenever the challenge is present rather than gating on
        # status_code, which keeps the helper composable; senders that put
        # WWW-Authenticate on a non-failure response are technically out of
        # spec but we record what they sent rather than dropping data.
        challenge = parse_bearer_challenge(response_headers)
        if challenge:
            event.auth_challenge_error = challenge.get("error")
            event.auth_challenge_scope = challenge.get("scope")
            event.auth_challenge_realm = challenge.get("realm")
            event.auth_challenge_resource_metadata = challenge.get("resource_metadata")

        # Extract UCP fields from both request and response bodies.
        # Response takes precedence on conflict (it's the merchant-
        # confirmed state) but request-body-only fields — the new
        # context_intent / context_language / context_currency /
        # context_eligibility_json on a checkout-create or
        # catalog-search request, idempotency-related metadata, etc.
        # — survive even when the response has its own body.
        # For webhooks, the order payload is in the request body and
        # the response is just an ack like {"status": "ok"}, so we
        # extract only from the request body. (is_webhook computed
        # earlier; reused here.)
        if is_webhook:
            # Webhooks normally carry the order payload in the request
            # body, with the response being just an ack. Fall back to
            # response_body when the caller only has the response side
            # — matches the prior behavior so an
            # order_delivered classification doesn't end up with an
            # empty order_id / checkout_session_id.
            bodies_to_parse: List[Optional[dict]] = [request_body or response_body]
        else:
            bodies_to_parse = [request_body, response_body]
        for body in bodies_to_parse:
            if not body or not isinstance(body, dict):
                continue
            if self.redact_pii:
                body = self._redact(body)
            fields = UCPResponseParser.extract(body)
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

    def register_pending_task(self, task: asyncio.Task) -> None:
        """Track a fire-and-forget task (used by middleware).

        Tasks are automatically removed when done.  Call
        :meth:`drain_pending` (or :meth:`close`, which calls it) to
        await all in-flight tasks before shutdown.
        """
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def drain_pending(self) -> None:
        """Await all in-flight recording tasks.

        The FastAPI middleware fires analytics recording as background
        tasks so it doesn't block the HTTP response.  Call this before
        :meth:`close` — or just call :meth:`close`, which drains
        automatically.
        """
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
            self._pending_tasks.clear()

    async def close(self):
        """Drain pending tasks, flush, and release resources."""
        await self.drain_pending()
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
