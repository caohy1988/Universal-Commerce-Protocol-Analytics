"""Tests for UCPEvent data model."""

from ucp_analytics.events import CheckoutStatus, UCPEvent, UCPEventType


class TestUCPEvent:
    def test_to_bq_row_drops_none_fields(self):
        event = UCPEvent(event_type="checkout_session_created")
        row = event.to_bq_row()

        # None fields should be absent
        assert "currency" not in row
        assert "checkout_session_id" not in row
        assert "latency_ms" not in row

        # Set fields should be present
        assert row["event_type"] == "checkout_session_created"
        assert "event_id" in row
        assert "timestamp" in row

    def test_to_bq_row_includes_set_fields(self):
        event = UCPEvent(
            event_type="checkout_session_completed",
            checkout_session_id="chk_123",
            total_amount=5999,
            currency="USD",
        )
        row = event.to_bq_row()

        assert row["checkout_session_id"] == "chk_123"
        assert row["total_amount"] == 5999
        assert row["currency"] == "USD"

    def test_defaults(self):
        event = UCPEvent()
        assert event.event_type == ""
        assert event.transport == "rest"
        assert event.app_name == ""
        assert event.currency is None

    def test_event_id_is_unique(self):
        e1 = UCPEvent()
        e2 = UCPEvent()
        assert e1.event_id != e2.event_id


class TestEnums:
    def test_event_type_values(self):
        assert UCPEventType.CHECKOUT_SESSION_CREATED.value == "checkout_session_created"
        assert UCPEventType.ERROR.value == "error"

    def test_checkout_status_values(self):
        assert CheckoutStatus.INCOMPLETE.value == "incomplete"
        assert CheckoutStatus.COMPLETED.value == "completed"
        assert CheckoutStatus.REQUIRES_ESCALATION.value == "requires_escalation"
