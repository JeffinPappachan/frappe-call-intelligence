"""Comprehensive automated tests for audio processing, transcript accuracy,
and isolated LLM call intelligence (Task A requirements).
"""

from datetime import datetime, timedelta
import pytest
from unittest.mock import AsyncMock, patch

from src.observability import AppError
from src.pipeline import CallIntelligencePipeline, InMemoryIdempotencyStore
from src.schemas import (
    CallDirection,
    CallIntelligence,
    CallOutcome,
    CallStatus,
    LeadQuality,
    PrimaryObjection,
    TelephonyWebhookPayload,
)
from src.stt_service import MockSTTService, RealSTTService, get_stt_service
from src.ai_service import MockAIService, RealAIService, get_ai_service


COURSE_FEES_TRANSCRIPT = (
    "Hello, are you interested in our course?\n"
    "Yes, I would like to know the fees.\n"
    "I can send you the details tomorrow.\n"
    "Okay, please call me tomorrow."
)

ORDER_STATUS_TRANSCRIPT = (
    "Hello, I am calling regarding my order number 9876.\n"
    "Could you please tell me when it will be delivered?\n"
    "It is out for delivery today and should arrive by 5 PM.\n"
    "Thank you so much!"
)


@pytest.fixture
def mock_pipeline():
    """Create an isolated test pipeline with in-memory stores and mock services."""
    stt_mock = AsyncMock()
    ai_mock = AsyncMock()
    frappe_mock = AsyncMock()
    store = InMemoryIdempotencyStore(ttl_seconds=3600)

    frappe_mock.lookup_lead_by_phone.return_value = {
        "name": "CRM-LEAD-2026-00001",
        "lead_name": "Test Student",
        "assigned_to": "agent@example.com",
    }
    frappe_mock.create_call_log.return_value = {
        "name": "CRM-CALL-LOG-TEST-001",
        "id": "test-call-001",
    }
    frappe_mock.create_followup_task.return_value = {
        "name": "CRM-TASK-TEST-001",
        "title": "Follow-up",
    }
    frappe_mock.update_lead_status.return_value = {"name": "CRM-LEAD-2026-00001"}

    pipeline = CallIntelligencePipeline(
        stt_service=stt_mock,
        ai_service=ai_mock,
        frappe_client=frappe_mock,
        idempotency_store=store,
    )
    return pipeline, stt_mock, ai_mock, frappe_mock


@pytest.mark.asyncio
async def test_1_correct_transcript_is_used(mock_pipeline):
    """Test 1: Given an uploaded audio file or mocked STT result, verify that the final
    call record contains this transcript and not the marketing demo transcript.
    """
    pipeline, stt_mock, ai_mock, frappe_mock = mock_pipeline
    stt_mock.transcribe.return_value = COURSE_FEES_TRANSCRIPT

    ai_mock.analyze_call.return_value = CallIntelligence(
        call_summary="Customer inquired about course fees.",
        call_outcome=CallOutcome.FOLLOW_UP,
        lead_quality=LeadQuality.WARM,
        primary_objection=PrimaryObjection.NONE,
        customer_intent="Know course fees",
        next_action="Call back tomorrow",
        follow_up_required=True,
        agent_quality_notes="Polite and clear.",
    )

    event = TelephonyWebhookPayload(
        provider_call_id="call-test-1",
        telephony_provider="manual_upload",
        from_number="+15551234567",
        to_number="+15559876543",
    )

    result = await pipeline.process_call(
        event=event,
        raw_audio=b"fake-wav-bytes",
        audio_filename="course_inquiry.wav",
    )

    assert result.transcript == COURSE_FEES_TRANSCRIPT
    assert "BrightPath" not in result.transcript
    assert "digital ad campaigns" not in result.transcript


@pytest.mark.asyncio
async def test_2_analysis_uses_actual_transcript(mock_pipeline):
    """Test 2: Verify that the LLM receives the exact transcript returned by STT."""
    pipeline, stt_mock, ai_mock, _ = mock_pipeline
    stt_mock.transcribe.return_value = COURSE_FEES_TRANSCRIPT

    ai_mock.analyze_call.return_value = CallIntelligence(
        call_summary="Customer asked for course fees.",
        call_outcome=CallOutcome.FOLLOW_UP,
        lead_quality=LeadQuality.WARM,
        customer_intent="Course fees inquiry",
        next_action="Send details tomorrow",
        agent_quality_notes="Good.",
    )

    event = TelephonyWebhookPayload(
        provider_call_id="call-test-2",
        from_number="+15551234567",
        to_number="+15559876543",
    )

    await pipeline.process_call(event=event, raw_audio=b"audio-bytes")

    ai_mock.analyze_call.assert_awaited_once()
    called_args, called_kwargs = ai_mock.analyze_call.call_args
    assert called_kwargs.get("transcript") == COURSE_FEES_TRANSCRIPT or called_args[0] == COURSE_FEES_TRANSCRIPT


@pytest.mark.asyncio
async def test_3_no_hardcoded_demo_analysis(mock_pipeline):
    """Test 3: Verify that the final analysis does not contain unrelated values such as
    BrightPath Ltd, digital ad campaigns, standard tier pricing, Friday 3 PM walkthrough.
    """
    pipeline, stt_mock, ai_mock, _ = mock_pipeline
    stt_mock.transcribe.return_value = COURSE_FEES_TRANSCRIPT

    course_intel = CallIntelligence(
        call_summary="The customer asked about course fees. The agent offered to send the details tomorrow, and the customer requested a callback tomorrow.",
        call_outcome=CallOutcome.FOLLOW_UP,
        lead_quality=LeadQuality.WARM,
        primary_objection=PrimaryObjection.NONE,
        customer_intent="Learn about course fees and receive additional course details.",
        key_points=[
            "Customer asked about course fees",
            "Agent offered to send details tomorrow",
            "Customer requested a callback tomorrow",
        ],
        objections=[],
        recommended_action="Send course details tomorrow and call the customer back tomorrow.",
        review_flag=False,
        follow_up_required=True,
        follow_up_date=None,
        follow_up_notes="Send course details and call the customer tomorrow.",
        agent_quality_notes="The agent responded to the customer's question and agreed to a follow-up.",
        next_action="Send course details tomorrow",
    )
    ai_mock.analyze_call.return_value = course_intel

    event = TelephonyWebhookPayload(
        provider_call_id="call-test-3",
        from_number="+15551234567",
        to_number="+15559876543",
    )

    result = await pipeline.process_call(event=event, raw_audio=b"audio-bytes")

    serialized = result.model_dump_json()
    assert "BrightPath" not in serialized
    assert "digital ad campaigns" not in serialized
    assert "standard tier pricing" not in serialized
    assert "Friday 3 PM" not in serialized


@pytest.mark.asyncio
async def test_4_follow_up_detection(mock_pipeline):
    """Test 4: Verify that a transcript containing 'please call me tomorrow' results in
    follow_up_required = true and creates the correct follow-up task.
    """
    pipeline, stt_mock, ai_mock, frappe_mock = mock_pipeline
    stt_mock.transcribe.return_value = COURSE_FEES_TRANSCRIPT

    ai_mock.analyze_call.return_value = CallIntelligence(
        call_summary="Callback agreed for tomorrow.",
        call_outcome=CallOutcome.FOLLOW_UP,
        lead_quality=LeadQuality.WARM,
        customer_intent="Course fee details",
        next_action="Call customer tomorrow",
        follow_up_required=True,
        follow_up_notes="Customer requested callback tomorrow regarding course fees",
        agent_quality_notes="Agreed to callback promptly.",
    )

    event = TelephonyWebhookPayload(
        provider_call_id="call-test-4",
        from_number="+15551234567",
        to_number="+15559876543",
    )

    result = await pipeline.process_call(event=event, raw_audio=b"audio-bytes")

    assert result.intelligence.follow_up_required is True
    assert result.frappe_task_id == "CRM-TASK-TEST-001"
    frappe_mock.create_followup_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_5_no_false_follow_up_date(mock_pipeline):
    """Test 5: Verify that follow_up_required = false does not display or create a follow-up task/date."""
    pipeline, stt_mock, ai_mock, frappe_mock = mock_pipeline
    stt_mock.transcribe.return_value = "Customer: I am not interested. Please do not call again."

    ai_mock.analyze_call.return_value = CallIntelligence(
        call_summary="Customer declined further contact.",
        call_outcome=CallOutcome.NOT_INTERESTED,
        lead_quality=LeadQuality.COLD,
        customer_intent="Decline services",
        next_action="Close lead",
        follow_up_required=False,
        follow_up_date=None,
        follow_up_at=None,
        agent_quality_notes="Polite termination.",
    )

    event = TelephonyWebhookPayload(
        provider_call_id="call-test-5",
        from_number="+15551234567",
        to_number="+15559876543",
    )

    result = await pipeline.process_call(event=event, raw_audio=b"audio-bytes")

    assert result.intelligence.follow_up_required is False
    assert result.intelligence.follow_up_date is None
    assert result.frappe_task_id is None
    frappe_mock.create_followup_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_6_call_isolation(mock_pipeline):
    """Test 6: Process two different calls and verify that the transcript and intelligence
    from call A never appear in call B.
    """
    pipeline, stt_mock, ai_mock, _ = mock_pipeline

    # Call A: Course fees
    stt_mock.transcribe.return_value = COURSE_FEES_TRANSCRIPT
    ai_mock.analyze_call.return_value = CallIntelligence(
        call_summary="Course fee inquiry callback.",
        call_outcome=CallOutcome.FOLLOW_UP,
        lead_quality=LeadQuality.WARM,
        customer_intent="Course fees",
        next_action="Send details tomorrow",
        agent_quality_notes="Good.",
    )
    event_a = TelephonyWebhookPayload(
        provider_call_id="call-isolation-A",
        from_number="+15551111111",
        to_number="+15552222222",
    )
    res_a = await pipeline.process_call(event=event_a, raw_audio=b"audio-a")

    # Call B: Order status
    stt_mock.transcribe.return_value = ORDER_STATUS_TRANSCRIPT
    ai_mock.analyze_call.return_value = CallIntelligence(
        call_summary="Order status inquiry for order 9876.",
        call_outcome=CallOutcome.INTERESTED,
        lead_quality=LeadQuality.HOT,
        customer_intent="Check order 9876 delivery status",
        next_action="Ensure prompt delivery by 5 PM",
        follow_up_required=False,
        agent_quality_notes="Courteous update.",
    )
    event_b = TelephonyWebhookPayload(
        provider_call_id="call-isolation-B",
        from_number="+15553333333",
        to_number="+15554444444",
    )
    res_b = await pipeline.process_call(event=event_b, raw_audio=b"audio-b")

    assert res_a.transcript == COURSE_FEES_TRANSCRIPT
    assert res_b.transcript == ORDER_STATUS_TRANSCRIPT
    assert "order number 9876" not in res_a.transcript
    assert "course" not in res_b.transcript
    assert "fees" not in res_b.intelligence.call_summary
    assert "9876" in res_b.intelligence.customer_intent


@pytest.mark.asyncio
async def test_7_failed_stt_returns_clear_error(mock_pipeline):
    """Test 7: Verify that a failed transcription returns a clear error and does not fall back to demo content."""
    pipeline, stt_mock, _, _ = mock_pipeline
    stt_mock.transcribe.side_effect = ValueError("Groq Whisper connection timed out")

    event = TelephonyWebhookPayload(
        provider_call_id="call-failed-stt",
        from_number="+15551111111",
        to_number="+15552222222",
    )

    with pytest.raises(AppError) as exc_info:
        await pipeline.process_call(event=event, raw_audio=b"corrupted-audio")

    assert "STT transcription failed" in str(exc_info.value.message)
    assert exc_info.value.status_code == 502


def test_stt_unconfigured_error():
    """Verify that unconfigured STT raises explicit message without falling back to mock text."""
    from src.config import Settings

    settings = Settings(
        mock_mode=False,
        stt_provider="groq",
        stt_api_key="",  # Missing key
    )
    with pytest.raises(ValueError) as exc_info:
        get_stt_service(settings)
    assert "Speech-to-text is not configured" in str(exc_info.value)


@pytest.mark.asyncio
async def test_8_crm_writeback_records(mock_pipeline):
    """Test 8: Verify that the transcript, analysis, call ID, and follow-up task are written to the CRM records."""
    pipeline, stt_mock, ai_mock, frappe_mock = mock_pipeline
    stt_mock.transcribe.return_value = COURSE_FEES_TRANSCRIPT

    intel = CallIntelligence(
        call_summary="Course fee inquiry with callback requested for tomorrow.",
        call_outcome=CallOutcome.FOLLOW_UP,
        lead_quality=LeadQuality.WARM,
        customer_intent="Course fees",
        next_action="Send details tomorrow",
        follow_up_required=True,
        agent_quality_notes="Clear response.",
    )
    ai_mock.analyze_call.return_value = intel

    event = TelephonyWebhookPayload(
        provider_call_id="call-crm-writeback",
        from_number="+15550001234",
        to_number="+15559998888",
    )

    result = await pipeline.process_call(event=event, raw_audio=b"audio-bytes")

    # Verify Call Log creation call arguments
    frappe_mock.create_call_log.assert_awaited_once_with(
        call_event=event,
        transcript=COURSE_FEES_TRANSCRIPT,
        intelligence=intel,
        lead_id="CRM-LEAD-2026-00001",
    )

    # Verify Task creation call arguments
    frappe_mock.create_followup_task.assert_awaited_once_with(
        call_log_id="CRM-CALL-LOG-TEST-001",
        lead_id="CRM-LEAD-2026-00001",
        intelligence=intel,
        assigned_to="agent@example.com",
    )


@pytest.mark.asyncio
async def test_9_dashboard_comment_and_transcript_extraction():
    """Test 9: Verify Frappe comment HTML parsing correctly recovers both transcript and intelligence."""
    from src.frappe_client import FrappeCRMClient

    client = FrappeCRMClient(mock_mode=True)
    intel = CallIntelligence(
        call_summary="Customer asked about course fees.",
        call_outcome=CallOutcome.FOLLOW_UP,
        lead_quality=LeadQuality.WARM,
        primary_objection=PrimaryObjection.NONE,
        customer_intent="Know course fees",
        next_action="Send details tomorrow",
        follow_up_required=True,
        agent_quality_notes="Helpful and clear.",
    )

    # Construct HTML comment with transcript block
    html_content = (
        "<div>"
        "<h4>AI Call Intelligence Analysis <span style='color:green;'>[Verified]</span></h4>"
        "<p><b>Summary:</b> Customer asked about course fees.</p>"
        "<ul>"
        "<li><b>Outcome:</b> Follow-up</li>"
        "<li><b>Lead Quality:</b> Warm</li>"
        "<li><b>Customer Intent:</b> Know course fees</li>"
        "<li><b>Primary Objection:</b> None</li>"
        "<li><b>Objections:</b> None</li>"
        "<li><b>Next Action:</b> Send details tomorrow</li>"
        "<li><b>Recommended Action:</b> Send details tomorrow</li>"
        "<li><b>Follow-up Required:</b> True</li>"
        "<li><b>Follow-up At:</b> None</li>"
        "<li><b>Follow-up Date:</b> None</li>"
        "<li><b>Follow-up Notes:</b> None</li>"
        "<li><b>Key Points:</b> Course fees, Details tomorrow</li>"
        "<li><b>Agent Quality Notes:</b> Helpful and clear.</li>"
        "</ul>"
        "<details><summary><b>View Audio Transcript</b></summary>"
        f"<pre style='white-space: pre-wrap;'>{COURSE_FEES_TRANSCRIPT}</pre>"
        "</details></div>"
    )

    parsed_intel = client._parse_html_comment(html_content)
    parsed_transcript = client._parse_html_transcript(html_content)

    assert parsed_intel.call_summary == "Customer asked about course fees."
    assert parsed_intel.follow_up_required is True
    assert parsed_intel.call_outcome == CallOutcome.FOLLOW_UP
    assert parsed_transcript == COURSE_FEES_TRANSCRIPT


def test_ai_unconfigured_error():
    """Verify that unconfigured AI provider raises explicit error without falling back to mock text."""
    from src.config import Settings

    settings = Settings(
        mock_mode=False,
        ai_provider="groq",
        ai_api_key="",  # Missing key
    )
    with pytest.raises(ValueError) as exc_info:
        get_ai_service(settings)
    assert "AI provider is not configured" in str(exc_info.value)


@pytest.mark.asyncio
async def test_10_failed_ai_returns_clear_error(mock_pipeline):
    """Verify that a failed AI extraction returns a 502 AppError and never falls back to demo data."""
    pipeline, stt_mock, ai_mock, _ = mock_pipeline
    stt_mock.transcribe.return_value = COURSE_FEES_TRANSCRIPT
    ai_mock.analyze_call.side_effect = ValueError("Groq LLM rate limit exceeded")

    event = TelephonyWebhookPayload(
        provider_call_id="call-failed-ai",
        from_number="+15551111111",
        to_number="+15552222222",
    )

    with pytest.raises(AppError) as exc_info:
        await pipeline.process_call(event=event, raw_audio=b"audio-bytes")

    assert "LLM analysis failed" in str(exc_info.value.message)
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_11_mock_services_no_demo_marketing_data():
    """Verify that even in Mock mode, MockAIService and MockSTTService do not produce BrightPath / digital ad marketing text."""
    stt = MockSTTService()
    transcript = await stt.transcribe("test.wav")
    assert "BrightPath" not in transcript
    assert "digital ad campaigns" not in transcript
    assert "Friday 3 PM" not in transcript

    ai = MockAIService()
    intel = await ai.analyze_call(COURSE_FEES_TRANSCRIPT)
    serialized = intel.model_dump_json()
    assert "BrightPath" not in serialized
    assert "digital ad campaigns" not in serialized
    assert "standard tier pricing" not in serialized
    assert "Friday 3 PM" not in serialized
    assert intel.follow_up_required is True
    assert "course" in intel.call_summary.lower()

