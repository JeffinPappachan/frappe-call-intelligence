import pytest

from src.pipeline import CallIntelligencePipeline, InMemoryIdempotencyStore
from src.schemas import CallDirection, CallStatus, TelephonyWebhookPayload


@pytest.mark.asyncio
async def test_pipeline_idempotency():
    store = InMemoryIdempotencyStore()
    pipeline = CallIntelligencePipeline(idempotency_store=store)

    event = TelephonyWebhookPayload(
        provider_call_id="call_idempotency_unique_999",
        telephony_provider="exotel",
        from_number="+15550001111",
        to_number="+15550009012",
        direction=CallDirection.OUTBOUND,
        call_status=CallStatus.COMPLETED,
        duration_seconds=95,
        recording_url="https://example.com/recording.wav",
        agent_id="john.parker@example.com",
    )

    # First execution - brand new event
    res1 = await pipeline.process_call(event)
    assert res1.success is True
    assert res1.idempotent_replay is False
    assert res1.provider_call_id == "call_idempotency_unique_999"
    assert res1.frappe_call_log_id is not None

    # Second execution with exact same provider_call_id - must be detected as duplicate
    res2 = await pipeline.process_call(event)
    assert res2.success is True
    assert res2.idempotent_replay is True
    assert res2.provider_call_id == "call_idempotency_unique_999"
    assert res2.frappe_call_log_id == res1.frappe_call_log_id

    # Third execution with a different call ID - must process anew
    event2 = event.model_copy(update={"provider_call_id": "call_idempotency_different_888"})
    res3 = await pipeline.process_call(event2)
    assert res3.success is True
    assert res3.idempotent_replay is False
    assert res3.provider_call_id == "call_idempotency_different_888"
