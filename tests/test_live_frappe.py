import os
from datetime import datetime, UTC

import pytest
from dotenv import load_dotenv

from src.frappe_client import FrappeCRMClient
from src.schemas import (
    CallDirection,
    CallIntelligence,
    CallOutcome,
    CallStatus,
    LeadQuality,
    PrimaryObjection,
    TelephonyWebhookPayload,
)

load_dotenv()

HAS_LIVE_CREDS = bool(os.getenv("FRAPPE_API_KEY") and os.getenv("FRAPPE_API_SECRET"))
IS_LIVE_MODE = os.getenv("MOCK_MODE", "true").lower() == "false"

skip_unless_live = pytest.mark.skipif(
    not (HAS_LIVE_CREDS and IS_LIVE_MODE),
    reason="Live Frappe credentials or MOCK_MODE=false not configured; skipping live network test",
)


@pytest.fixture
def live_client():
    return FrappeCRMClient(
        base_url=os.getenv("FRAPPE_BASE_URL"),
        api_key=os.getenv("FRAPPE_API_KEY"),
        api_secret=os.getenv("FRAPPE_API_SECRET"),
        mock_mode=False,
    )


@skip_unless_live
@pytest.mark.asyncio
async def test_live_lead_lookup(live_client):
    # Lookup Carol Smith by phone number
    lead = await live_client.lookup_lead_by_phone("+1 555 000 9012")
    assert lead is not None
    assert "Carol Smith" in lead.get("lead_name", "")
    assert lead.get("name", "").startswith("CRM-LEAD-")


@skip_unless_live
@pytest.mark.asyncio
async def test_live_call_log_and_idempotency(live_client):
    test_call_id = f"test_pytest_{int(datetime.now(UTC).timestamp())}"
    event = TelephonyWebhookPayload(
        provider_call_id=test_call_id,
        telephony_provider="exotel",
        from_number="+15550001111",
        to_number="+1 555 000 9012",
        direction=CallDirection.OUTBOUND,
        call_status=CallStatus.COMPLETED,
        duration_seconds=90,
    )
    intel = CallIntelligence(
        call_summary="Test automated integration call.",
        call_outcome=CallOutcome.FOLLOW_UP,
        lead_quality=LeadQuality.WARM,
        primary_objection=PrimaryObjection.NONE,
        customer_intent="Automated testing",
        next_action="Review automated test run",
        follow_up_at=datetime.now(UTC),
        agent_quality_notes="Automated test note",
    )

    # First creation
    log1 = await live_client.create_call_log(
        call_event=event,
        transcript="Agent: Hello Carol. Customer: Hi John.",
        intelligence=intel,
        lead_id="CRM-LEAD-2026-00003",
    )
    assert log1 is not None
    assert log1.get("name") is not None

    # Repeated creation must reuse existing record (Idempotency)
    log2 = await live_client.create_call_log(
        call_event=event,
        transcript="Agent: Hello Carol. Customer: Hi John.",
        intelligence=intel,
        lead_id="CRM-LEAD-2026-00003",
    )
    assert log2.get("name") == log1.get("name")
