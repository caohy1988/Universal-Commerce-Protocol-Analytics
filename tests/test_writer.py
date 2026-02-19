"""Tests for AsyncBigQueryWriter."""

from unittest.mock import MagicMock, patch

import pytest

from ucp_analytics.writer import AsyncBigQueryWriter, get_ddl


class TestAsyncBigQueryWriter:
    @pytest.fixture
    def writer(self):
        return AsyncBigQueryWriter(
            project_id="test-project",
            dataset_id="test_dataset",
            table_id="test_table",
            batch_size=3,
            auto_create_table=False,
        )

    async def test_enqueue_buffers(self, writer):
        await writer.enqueue({"event_id": "1", "event_type": "test"})
        assert len(writer._buffer) == 1

    async def test_flush_when_batch_size_reached(self, writer):
        mock_client = MagicMock()
        mock_client.insert_rows_json.return_value = []
        writer._client = mock_client
        writer.auto_create_table = False

        await writer.enqueue({"event_id": "1", "event_type": "test"})
        await writer.enqueue({"event_id": "2", "event_type": "test"})
        # Third enqueue triggers flush (batch_size=3)
        with patch("asyncio.to_thread", side_effect=lambda fn, *a: fn(*a)):
            await writer.enqueue({"event_id": "3", "event_type": "test"})

        mock_client.insert_rows_json.assert_called_once()
        assert len(writer._buffer) == 0

    async def test_flush_empty_noop(self, writer):
        # Should not raise
        await writer.flush()
        assert len(writer._buffer) == 0

    async def test_flush_requeues_on_error(self, writer):
        mock_client = MagicMock()
        mock_client.insert_rows_json.side_effect = Exception("BQ down")
        writer._client = mock_client

        await writer.enqueue({"event_id": "1", "event_type": "test"})

        with patch("asyncio.to_thread", side_effect=lambda fn, *a: fn(*a)):
            await writer.flush()

        # Rows should be re-queued
        assert len(writer._buffer) == 1
        assert writer._buffer[0]["event_id"] == "1"

    async def test_max_buffer_size_drops_oldest(self):
        writer = AsyncBigQueryWriter(
            project_id="test",
            dataset_id="ds",
            batch_size=100,  # high so no auto-flush
            auto_create_table=False,
            max_buffer_size=3,
        )

        await writer.enqueue({"event_id": "1"})
        await writer.enqueue({"event_id": "2"})
        await writer.enqueue({"event_id": "3"})
        # Buffer full, next enqueue should drop oldest
        await writer.enqueue({"event_id": "4"})

        assert len(writer._buffer) == 3
        ids = [r["event_id"] for r in writer._buffer]
        assert ids == ["2", "3", "4"]

    async def test_close_flushes(self, writer):
        mock_client = MagicMock()
        mock_client.insert_rows_json.return_value = []
        mock_client.close = MagicMock()
        writer._client = mock_client

        await writer.enqueue({"event_id": "1", "event_type": "test"})

        with patch("asyncio.to_thread", side_effect=lambda fn, *a: fn(*a)):
            await writer.close()

        mock_client.insert_rows_json.assert_called_once()
        mock_client.close.assert_called_once()

    def test_full_table_id(self, writer):
        assert writer.full_table_id == "test-project.test_dataset.test_table"


class TestGetDDL:
    def test_generates_valid_ddl(self):
        ddl = get_ddl("my-project", "my_dataset", "my_table")
        assert "CREATE TABLE IF NOT EXISTS" in ddl
        assert "`my-project.my_dataset.my_table`" in ddl
        assert "event_id STRING NOT NULL" in ddl
        assert "PARTITION BY DATE(timestamp)" in ddl
        assert "CLUSTER BY event_type" in ddl

    def test_integer_mapped_to_int64(self):
        ddl = get_ddl("p", "d", "t")
        assert "INT64" in ddl
        assert "FLOAT64" in ddl
