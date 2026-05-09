"""Async batched BigQuery writer for UCP analytics events.

Auto-creates the target table (partitioned + clustered) if it does not
exist.  Buffers rows and flushes when batch_size is reached or on
explicit flush()/close().
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BigQuery schema (aligned with UCPEvent.to_bq_row())
# ---------------------------------------------------------------------------

BQ_SCHEMA_FIELDS = [
    # identity
    ("event_id", "STRING", "REQUIRED"),
    ("event_type", "STRING", "REQUIRED"),
    ("timestamp", "TIMESTAMP", "REQUIRED"),
    # context
    ("app_name", "STRING", "NULLABLE"),
    ("merchant_host", "STRING", "NULLABLE"),
    ("platform_profile_url", "STRING", "NULLABLE"),
    ("transport", "STRING", "NULLABLE"),
    # HTTP
    ("http_method", "STRING", "NULLABLE"),
    ("http_path", "STRING", "NULLABLE"),
    ("http_status_code", "INTEGER", "NULLABLE"),
    ("idempotency_key", "STRING", "NULLABLE"),
    ("request_id", "STRING", "NULLABLE"),
    # checkout
    ("checkout_session_id", "STRING", "NULLABLE"),
    ("checkout_status", "STRING", "NULLABLE"),
    ("order_id", "STRING", "NULLABLE"),
    # context (UCP request-body Context: intent, language, currency, eligibility)
    ("context_intent", "STRING", "NULLABLE"),
    ("context_language", "STRING", "NULLABLE"),
    ("context_currency", "STRING", "NULLABLE"),
    ("context_eligibility_json", "JSON", "NULLABLE"),
    # HTTP message signing (RFC 9421 / UCP signatures.md)
    ("request_signed", "BOOL", "NULLABLE"),
    ("response_signed", "BOOL", "NULLABLE"),
    ("request_signature_keyid", "STRING", "NULLABLE"),
    ("response_signature_keyid", "STRING", "NULLABLE"),
    # Standard Webhooks metadata (UCP order.md)
    ("webhook_id", "STRING", "NULLABLE"),
    ("webhook_timestamp", "TIMESTAMP", "NULLABLE"),
    # UCP-Agent profile URI (parsed; supersedes platform_profile_url)
    ("ucp_agent_profile_url", "STRING", "NULLABLE"),
    # WWW-Authenticate Bearer challenge (RFC 7235 / RFC 6750 / RFC 9728)
    ("auth_challenge_error", "STRING", "NULLABLE"),
    ("auth_challenge_scope", "STRING", "NULLABLE"),
    ("auth_challenge_realm", "STRING", "NULLABLE"),
    ("auth_challenge_resource_metadata", "STRING", "NULLABLE"),
    # financial (spec total types: items_discount, subtotal, discount,
    # fulfillment, tax, fee, total)
    ("currency", "STRING", "NULLABLE"),
    ("items_discount_amount", "INTEGER", "NULLABLE"),
    ("subtotal_amount", "INTEGER", "NULLABLE"),
    ("discount_amount", "INTEGER", "NULLABLE"),
    ("fulfillment_amount", "INTEGER", "NULLABLE"),
    ("tax_amount", "INTEGER", "NULLABLE"),
    ("fee_amount", "INTEGER", "NULLABLE"),
    ("total_amount", "INTEGER", "NULLABLE"),
    # line items
    ("line_items_json", "JSON", "NULLABLE"),
    ("line_item_count", "INTEGER", "NULLABLE"),
    # payment
    ("payment_handler_id", "STRING", "NULLABLE"),
    ("payment_instrument_type", "STRING", "NULLABLE"),
    ("payment_brand", "STRING", "NULLABLE"),
    # body.ucp.payment_handlers[*].available_instruments — per-handler
    # registry, preserved in full so downstream can pivot on handler id
    # or instrument type. JSON, not STRING, so dashboards can query via
    # JSON_QUERY_ARRAY without an additional decode round-trip.
    ("payment_available_instruments_json", "JSON", "NULLABLE"),
    # capabilities
    ("ucp_version", "STRING", "NULLABLE"),
    ("capabilities_json", "JSON", "NULLABLE"),
    ("extensions_json", "JSON", "NULLABLE"),
    # identity linking
    ("identity_provider", "STRING", "NULLABLE"),
    ("identity_scope", "STRING", "NULLABLE"),
    # fulfillment
    ("fulfillment_type", "STRING", "NULLABLE"),
    ("fulfillment_destination_country", "STRING", "NULLABLE"),
    # discount extension
    ("discount_codes_json", "JSON", "NULLABLE"),
    ("discount_applied_json", "JSON", "NULLABLE"),
    # checkout metadata
    ("expires_at", "TIMESTAMP", "NULLABLE"),
    ("continue_url", "STRING", "NULLABLE"),
    # order
    ("permalink_url", "STRING", "NULLABLE"),
    # errors
    ("error_code", "STRING", "NULLABLE"),
    ("error_message", "STRING", "NULLABLE"),
    ("error_severity", "STRING", "NULLABLE"),
    ("messages_json", "JSON", "NULLABLE"),
    # Per-severity code lists pulled from messages[] for dashboard
    # filters. JSON arrays of strings; identity_optional_present is
    # a fast-filter BOOL for the C11 KPI.
    ("message_info_codes_json", "JSON", "NULLABLE"),
    ("message_warning_codes_json", "JSON", "NULLABLE"),
    ("identity_optional_present", "BOOL", "NULLABLE"),
    # performance
    ("latency_ms", "FLOAT", "NULLABLE"),
    # custom
    ("custom_metadata_json", "JSON", "NULLABLE"),
]


# ---------------------------------------------------------------------------
# DDL template (for manual setup)
# ---------------------------------------------------------------------------

DDL_TEMPLATE = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.{table}` (
{columns}
)
PARTITION BY DATE(timestamp)
CLUSTER BY event_type, checkout_session_id, merchant_host
OPTIONS(
  description = 'UCP commerce analytics events',
  labels = [('managed_by', 'ucp_analytics')]
);
"""


def get_ddl(project: str, dataset: str, table: str) -> str:
    """Return the CREATE TABLE DDL for manual execution."""
    col_lines = []
    for name, bq_type, mode in BQ_SCHEMA_FIELDS:
        not_null = " NOT NULL" if mode == "REQUIRED" else ""
        # Map our shorthand to BQ SQL types
        sql_type = {"INTEGER": "INT64", "FLOAT": "FLOAT64"}.get(bq_type, bq_type)
        col_lines.append(f"  {name} {sql_type}{not_null}")
    columns = ",\n".join(col_lines)
    return DDL_TEMPLATE.format(
        project=project, dataset=dataset, table=table, columns=columns
    )


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class AsyncBigQueryWriter:
    """Batched, async-safe BigQuery streaming-insert writer."""

    def __init__(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str = "ucp_events",
        batch_size: int = 50,
        auto_create_table: bool = True,
        max_buffer_size: int = 10_000,
    ):
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.table_id = table_id
        self.batch_size = batch_size
        self.auto_create_table = auto_create_table
        self.max_buffer_size = max_buffer_size

        self._buffer: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._client = None
        self._table_ensured = False

    @property
    def full_table_id(self) -> str:
        return f"{self.project_id}.{self.dataset_id}.{self.table_id}"

    # -- lazy init --

    def _get_client(self):
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client(project=self.project_id)
        return self._client

    def _ensure_table_sync(self):
        """Synchronous table creation — meant to be called via asyncio.to_thread."""
        from google.cloud import bigquery

        client = self._get_client()
        ds_ref = bigquery.DatasetReference(self.project_id, self.dataset_id)
        client.create_dataset(bigquery.Dataset(ds_ref), exists_ok=True)

        schema = [
            bigquery.SchemaField(name, bq_type, mode=mode)
            for name, bq_type, mode in BQ_SCHEMA_FIELDS
        ]
        tbl_ref = bigquery.TableReference(ds_ref, self.table_id)
        table = bigquery.Table(tbl_ref, schema=schema)
        table.time_partitioning = bigquery.TimePartitioning(field="timestamp")
        table.clustering_fields = [
            "event_type",
            "checkout_session_id",
            "merchant_host",
        ]
        client.create_table(table, exists_ok=True)
        logger.info("Ensured table %s", self.full_table_id)

    async def _ensure_table(self):
        if self._table_ensured or not self.auto_create_table:
            return
        try:
            await asyncio.to_thread(self._ensure_table_sync)
            self._table_ensured = True
        except Exception:
            logger.exception("Failed to ensure BQ table")

    # -- public API --

    async def enqueue(self, row: Dict[str, Any]):
        should_flush = False
        async with self._lock:
            if len(self._buffer) >= self.max_buffer_size:
                dropped = self._buffer.pop(0)
                logger.warning(
                    "Buffer full (%d); dropping oldest event %s",
                    self.max_buffer_size,
                    dropped.get("event_id", "?"),
                )
            self._buffer.append(row)
            should_flush = len(self._buffer) >= self.batch_size
        if should_flush:
            await self.flush()

    async def flush(self):
        async with self._lock:
            if not self._buffer:
                return
            batch = self._buffer.copy()
            self._buffer.clear()

        try:
            await self._ensure_table()
            client = self._get_client()
            errors = await asyncio.to_thread(
                client.insert_rows_json, self.full_table_id, batch
            )
            if errors:
                # Extract only the failed rows by index
                failed_indices = {
                    e["index"] for e in errors if isinstance(e, dict) and "index" in e
                }
                failed_rows = [
                    batch[i] for i in sorted(failed_indices) if i < len(batch)
                ]
                logger.error(
                    "BQ insert errors (%d/%d rows failed): %s",
                    len(failed_rows),
                    len(batch),
                    errors[:3],
                )
                if failed_rows:
                    async with self._lock:
                        requeued = failed_rows + self._buffer
                        if len(requeued) > self.max_buffer_size:
                            requeued = requeued[: self.max_buffer_size]
                        self._buffer = requeued
            else:
                logger.debug("Flushed %d UCP events", len(batch))
        except Exception:
            logger.exception("BQ flush failed; re-queuing %d rows", len(batch))
            async with self._lock:
                # Re-queue but respect max buffer size
                requeued = batch + self._buffer
                if len(requeued) > self.max_buffer_size:
                    requeued = requeued[: self.max_buffer_size]
                self._buffer = requeued

    async def close(self):
        await self.flush()
        if self._client:
            self._client.close()
            self._client = None
