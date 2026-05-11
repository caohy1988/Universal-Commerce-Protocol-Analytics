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

    def test_eligibility_outcome_fields_default_none(self):
        """A5 — three-state nullable BOOL trio. Defaults to None so
        rows that don't observe an eligibility outcome code don't
        contribute a denominator (NULL ≠ FALSE in BigQuery COUNT)."""
        event = UCPEvent()
        assert event.eligibility_accepted_present is None
        assert event.eligibility_not_accepted_present is None
        assert event.eligibility_invalid_present is None
        # And NULL fields stay out of the row entirely.
        row = event.to_bq_row()
        assert "eligibility_accepted_present" not in row
        assert "eligibility_not_accepted_present" not in row
        assert "eligibility_invalid_present" not in row

    def test_eligibility_outcome_fields_serialize_when_set(self):
        event = UCPEvent(
            eligibility_accepted_present=True,
            eligibility_not_accepted_present=False,
            eligibility_invalid_present=False,
        )
        row = event.to_bq_row()
        assert row["eligibility_accepted_present"] is True
        assert row["eligibility_not_accepted_present"] is False
        assert row["eligibility_invalid_present"] is False

    def test_signature_alg_fields_default_none(self):
        """C5c — per-direction algorithm columns default to None so
        rows without a configured jwk_lookup don't pollute the
        "signed with alg X" dashboards."""
        event = UCPEvent()
        assert event.request_signature_alg is None
        assert event.response_signature_alg is None
        row = event.to_bq_row()
        assert "request_signature_alg" not in row
        assert "response_signature_alg" not in row

    def test_signature_alg_fields_serialize_when_set(self):
        event = UCPEvent(
            request_signature_alg="ES256",
            response_signature_alg="ES384",
        )
        row = event.to_bq_row()
        assert row["request_signature_alg"] == "ES256"
        assert row["response_signature_alg"] == "ES384"

    def test_order_lifecycle_fields_default_none(self):
        """B8 — fulfillment.events[] / adjustments[] columns default
        to None so rows without lifecycle data don't appear in
        COUNT(latest_*) denominators."""
        event = UCPEvent()
        assert event.fulfillment_events_json is None
        assert event.adjustments_json is None
        assert event.latest_fulfillment_event_type is None
        assert event.latest_fulfillment_event_at is None
        assert event.latest_adjustment_type is None
        assert event.latest_adjustment_status is None
        assert event.latest_adjustment_at is None
        row = event.to_bq_row()
        assert "fulfillment_events_json" not in row
        assert "adjustments_json" not in row
        assert "latest_fulfillment_event_type" not in row

    def test_order_lifecycle_fields_serialize_when_set(self):
        event = UCPEvent(
            fulfillment_events_json='[{"id": "fe_1"}]',
            latest_fulfillment_event_type="delivered",
            latest_fulfillment_event_at="2026-05-09T17:00:00Z",
            latest_adjustment_type="refund",
            latest_adjustment_status="completed",
            latest_adjustment_at="2026-05-09T20:00:00Z",
        )
        row = event.to_bq_row()
        assert row["latest_fulfillment_event_type"] == "delivered"
        assert row["latest_fulfillment_event_at"] == "2026-05-09T17:00:00Z"
        assert row["latest_adjustment_type"] == "refund"
        assert row["latest_adjustment_status"] == "completed"
        assert row["latest_adjustment_at"] == "2026-05-09T20:00:00Z"


class TestEnums:
    def test_event_type_values(self):
        assert UCPEventType.CHECKOUT_SESSION_CREATED.value == "checkout_session_created"
        assert UCPEventType.ERROR.value == "error"

    def test_checkout_status_values(self):
        assert CheckoutStatus.INCOMPLETE.value == "incomplete"
        assert CheckoutStatus.COMPLETED.value == "completed"
        assert CheckoutStatus.REQUIRES_ESCALATION.value == "requires_escalation"
