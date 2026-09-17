import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from src.server import app
from src.pipeline import get_pipeline
from src.config import get_settings
from src.schemas import (
    PipelineResponse,
    CallIntelligence,
    CallOutcome,
    LeadQuality,
    PrimaryObjection,
)

client = TestClient(app)

@pytest.fixture(autouse=True)
def reset_pipeline():
    # Force mock_mode for analytics tests so we use the local store
    settings = get_settings()
    original_mode = settings.mock_mode
    settings.mock_mode = True
    
    import src.server
    original_server_mode = src.server.settings.mock_mode
    src.server.settings.mock_mode = True
    
    pipeline = get_pipeline()
    pipeline.idempotency_store.clear()
    yield
    pipeline.idempotency_store.clear()
    settings.mock_mode = original_mode
    src.server.settings.mock_mode = original_server_mode

def test_empty_analytics():
    response = client.get("/api/v1/dashboard/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["total_calls"] == 0
    assert data["completed_calls"] == 0
    
    response2 = client.get("/api/v1/dashboard/calls")
    assert response2.status_code == 200
    assert response2.json()["calls"] == []

def test_populated_analytics():
    pipeline = get_pipeline()
    
    # Mock some data
    intelligence1 = CallIntelligence(
        call_summary="Test 1",
        call_outcome=CallOutcome.FOLLOW_UP,
        lead_quality=LeadQuality.HOT,
        primary_objection=PrimaryObjection.NONE,
        customer_intent="Buy",
        next_action="Call back",
        follow_up_at=datetime.now() + timedelta(days=1),
        agent_quality_notes="Good",
        review_flag=False,
    )
    
    call1 = PipelineResponse(
        success=True,
        provider_call_id="call-1",
        idempotent_replay=False,
        intelligence=intelligence1,
        duration_seconds=120,
        event_timestamp=datetime.now() - timedelta(minutes=5),
        agent_id="Agent Smith",
    )
    
    intelligence2 = CallIntelligence(
        call_summary="Test 2",
        call_outcome=CallOutcome.NOT_INTERESTED,
        lead_quality=LeadQuality.COLD,
        primary_objection=PrimaryObjection.PRICE,
        customer_intent="Complain",
        next_action="None",
        follow_up_at=datetime.now() - timedelta(days=1), # overdue
        agent_quality_notes="Poor",
        review_flag=True,
    )
    
    call2 = PipelineResponse(
        success=False,
        provider_call_id="call-2",
        idempotent_replay=False,
        intelligence=intelligence2,
        duration_seconds=60,
        event_timestamp=datetime.now() - timedelta(minutes=10),
        agent_id="Agent Neo",
    )
    
    pipeline.idempotency_store.set("call-1", call1)
    pipeline.idempotency_store.set("call-2", call2)
    
    response = client.get("/api/v1/dashboard/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["total_calls"] == 2
    assert data["completed_calls"] == 1
    assert data["missed_calls"] == 1
    assert data["average_call_duration_seconds"] == 90.0
    
    assert data["calls_per_telecaller"]["Agent Smith"] == 1
    assert data["calls_per_telecaller"]["Agent Neo"] == 1
    
    assert data["lead_quality_distribution"]["Hot"] == 1
    assert data["lead_quality_distribution"]["Cold"] == 1
    
    assert data["call_outcome_distribution"]["Follow-up"] == 1
    assert data["call_outcome_distribution"]["Not Interested"] == 1
    
    assert data["follow_ups_due"] == 1
    assert data["follow_ups_overdue"] == 1
    
    response2 = client.get("/api/v1/dashboard/calls")
    assert response2.status_code == 200
    calls = response2.json()["calls"]
    assert len(calls) == 2
    # Sort order: descending by timestamp, so call1 (5 mins ago) is before call2 (10 mins ago)
    assert calls[0]["provider_call_id"] == "call-1"
    assert calls[1]["provider_call_id"] == "call-2"

def test_frappe_comment_parsing():
    from src.frappe_client import FrappeCRMClient
    from src.schemas import CallOutcome, LeadQuality, PrimaryObjection
    
    client = FrappeCRMClient(mock_mode=True)
    html = """
    <div><h4>AI Call Intelligence Analysis <span style="color:green;">[Verified]</span></h4><p><b>Summary:</b> Customer was very interested in the product.</p><ul><li><b>Outcome:</b> Follow-up</li><li><b>Lead Quality:</b> Warm</li><li><b>Customer Intent:</b> Buy</li><li><b>Primary Objection:</b> Price</li><li><b>Next Action:</b> Send pricing</li><li><b>Follow-up At:</b> 2026-09-18T10:00:00+00:00</li><li><b>Agent Quality Notes:</b> Good job</li></ul></div>
    """
    
    intel = client._parse_html_comment(html)
    assert intel.call_summary == "Customer was very interested in the product."
    assert intel.call_outcome == CallOutcome.FOLLOW_UP
    assert intel.lead_quality == LeadQuality.WARM
    assert intel.primary_objection == PrimaryObjection.PRICE
    assert intel.customer_intent == "Buy"
    assert intel.next_action == "Send pricing"
    assert intel.agent_quality_notes == "Good job"
    assert intel.follow_up_at is not None
    assert intel.follow_up_at.isoformat() == "2026-09-18T10:00:00+00:00"

