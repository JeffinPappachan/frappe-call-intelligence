from datetime import datetime, UTC

import pytest
from pydantic import ValidationError

from src.schemas import (
    CallDirection,
    CallIntelligence,
    CallOutcome,
    CallStatus,
    LeadQuality,
    PrimaryObjection,
    TelephonyWebhookPayload,
)


def test_valid_webhook_payload():
    payload = TelephonyWebhookPayload(
        provider_call_id="call_test_001",
        telephony_provider="exotel",
        from_number="+15550001111",
        to_number="+15550009012",
        direction=CallDirection.OUTBOUND,
        call_status=CallStatus.COMPLETED,
        duration_seconds=120,
        recording_url="https://example.com/recording.wav",
        agent_id="agent@example.com",
    )
    assert payload.provider_call_id == "call_test_001"
    assert payload.duration_seconds == 120
    assert payload.direction == CallDirection.OUTBOUND


def test_invalid_webhook_payload_empty_call_id():
    with pytest.raises(ValidationError):
        TelephonyWebhookPayload(
            provider_call_id="   ",
            from_number="+15550001111",
            to_number="+15550009012",
        )


def test_invalid_webhook_payload_missing_required_fields():
    with pytest.raises(ValidationError):
        TelephonyWebhookPayload(
            provider_call_id="call_missing_fields",
            # missing from_number and to_number
        )


def test_valid_call_intelligence_schema():
    intelligence = CallIntelligence(
        call_summary="Customer requested product catalog and pricing sheet.",
        call_outcome=CallOutcome.FOLLOW_UP,
        lead_quality=LeadQuality.HOT,
        primary_objection=PrimaryObjection.PRICE,
        customer_intent="Evaluate enterprise pricing and feature set",
        next_action="Send revised pricing quotation by end of day",
        follow_up_at=datetime.now(UTC),
        agent_quality_notes="Agent conducted clear discovery questioning and proposed timely follow-up.",
        review_flag=False,
    )
    assert intelligence.lead_quality == LeadQuality.HOT
    assert intelligence.call_outcome == CallOutcome.FOLLOW_UP
    assert intelligence.primary_objection == PrimaryObjection.PRICE
    assert intelligence.review_flag is False


def test_invalid_call_intelligence_enum():
    with pytest.raises(ValidationError):
        CallIntelligence(
            call_summary="Summary",
            call_outcome="NonExistentOutcome",  # Invalid enum value
            lead_quality=LeadQuality.WARM,
            primary_objection=PrimaryObjection.NONE,
            customer_intent="Intent",
            next_action="Action",
            agent_quality_notes="Good",
        )
