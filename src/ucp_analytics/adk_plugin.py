"""Optional ADK BasePlugin adapter.

If the user is running an ADK-based commerce agent, this thin adapter
wraps UCPAnalyticsTracker into ADK's BasePlugin interface so it can
be registered on a Runner alongside the BigQuery Agent Analytics Plugin.

Install with: pip install ucp-analytics[adk]

Usage::

    from ucp_analytics.adk_plugin import UCPAgentAnalyticsPlugin

    plugin = UCPAgentAnalyticsPlugin(
        project_id="my-proj",
        dataset_id="ucp_analytics",
    )
    runner = InMemoryRunner(agent=agent, plugins=[plugin])
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from google.adk.plugins.base_plugin import BasePlugin
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse
    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.tool_context import ToolContext
    from google.genai import types

    _ADK_AVAILABLE = True
except ImportError:
    _ADK_AVAILABLE = False
    BasePlugin = object  # type: ignore

from ucp_analytics.events import UCPEvent, UCPEventType
from ucp_analytics.parser import UCPResponseParser
from ucp_analytics.tracker import UCPAnalyticsTracker


class UCPAgentAnalyticsPlugin(BasePlugin):  # type: ignore[misc]
    """ADK plugin that delegates to UCPAnalyticsTracker.

    Intercepts tool calls, detects UCP operations, and records
    structured commerce events. Non-UCP tool calls are optionally
    recorded as generic events.
    """

    # Tool name patterns that indicate UCP operations
    _UCP_PATTERNS = [
        "checkout", "create_checkout", "update_checkout", "complete_checkout",
        "cancel_checkout", "discover", "order", "identity", "payment",
        "ucp_", "negotiate",
    ]

    def __init__(
        self,
        project_id: str,
        dataset_id: str = "ucp_analytics",
        table_id: str = "ucp_events",
        *,
        app_name: str = "",
        batch_size: int = 50,
        track_all_tools: bool = False,
        redact_pii: bool = False,
        custom_metadata: Optional[Dict[str, str]] = None,
    ):
        if not _ADK_AVAILABLE:
            raise ImportError(
                "google-adk is not installed. "
                "Install with: pip install ucp-analytics[adk]"
            )
        super().__init__(name="ucp_agent_analytics")

        self._tracker = UCPAnalyticsTracker(
            project_id=project_id,
            dataset_id=dataset_id,
            table_id=table_id,
            app_name=app_name,
            batch_size=batch_size,
            redact_pii=redact_pii,
            custom_metadata=custom_metadata,
        )
        self._track_all = track_all_tools
        self._timings: Dict[str, float] = {}

    def _is_ucp_tool(self, name: str) -> bool:
        lower = name.lower()
        return any(p in lower for p in self._UCP_PATTERNS)

    async def before_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict,
        tool_context: Any,
    ) -> Optional[dict]:
        key = f"{id(tool_context)}:{tool.name}"
        self._timings[key] = time.monotonic()
        return None

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict,
        tool_context: Any,
        result: dict,
    ) -> Optional[dict]:
        is_ucp = self._is_ucp_tool(tool.name)
        if not is_ucp and not self._track_all:
            return None

        # Latency
        key = f"{id(tool_context)}:{tool.name}"
        latency_ms = None
        if key in self._timings:
            latency_ms = round(
                (time.monotonic() - self._timings.pop(key)) * 1000, 2
            )

        # Build event
        event = UCPEvent(
            event_type=(
                UCPResponseParser.classify(
                    "POST", f"/{tool.name}", 200, result
                ).value
                if is_ucp and isinstance(result, dict)
                else UCPEventType.REQUEST.value
            ),
            app_name=getattr(tool_context, "app_name", ""),
            latency_ms=latency_ms,
        )

        # Extract UCP fields
        if is_ucp and isinstance(result, dict):
            fields = UCPResponseParser.extract(result)
            for k, v in fields.items():
                if hasattr(event, k):
                    setattr(event, k, v)

        await self._tracker.record_event(event)
        return None

    async def close(self):
        await self._tracker.close()
