import pytest

from src.ai_service import MockAIService
from src.schemas import CallOutcome, LeadQuality
from src.stt_service import MockSTTService


@pytest.mark.asyncio
async def test_mock_stt_english():
    stt = MockSTTService()
    transcript = await stt.transcribe("sample_call.wav")
    assert "course" in transcript.lower()
    assert "fees" in transcript.lower()
    assert "BrightPath" not in transcript


@pytest.mark.asyncio
async def test_mock_stt_malayalam():
    stt = MockSTTService()
    transcript = await stt.transcribe("dummy_path", filename="sample_call_malayalam.wav")
    assert "Namaskaram" in transcript
    assert "fees" in transcript.lower()
    assert "BrightPath" not in transcript


@pytest.mark.asyncio
async def test_mock_ai_analysis():
    ai = MockAIService()
    transcript = "Agent: Hello Carol, calling regarding Hash Adz marketing packages. Customer: Yes, let's schedule for Friday 3 PM."
    intelligence = await ai.analyze_call(transcript)

    assert intelligence.call_outcome in [CallOutcome.FOLLOW_UP, CallOutcome.INTERESTED]
    assert intelligence.lead_quality in [LeadQuality.HOT, LeadQuality.WARM]
    assert intelligence.customer_intent != ""
    assert intelligence.next_action != ""
    assert intelligence.agent_quality_notes != ""


@pytest.mark.asyncio
async def test_mock_ai_analysis_not_interested():
    ai = MockAIService()
    transcript = "Customer: I am not interested. Please don't call again."
    intelligence = await ai.analyze_call(transcript)

    assert intelligence.call_outcome == CallOutcome.NOT_INTERESTED
    assert intelligence.lead_quality == LeadQuality.COLD
    assert intelligence.follow_up_at is None
