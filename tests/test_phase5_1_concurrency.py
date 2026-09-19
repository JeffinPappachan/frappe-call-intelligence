import pytest
from unittest.mock import AsyncMock

from src.pipeline import CallIntelligencePipeline, InMemoryIdempotencyStore
from src.schemas import TelephonyWebhookPayload, ProcessingStatus, PipelineResponse, CallDirection, CallStatus, CallOutcome, LeadQuality, PrimaryObjection, CallIntelligence

@pytest.fixture
def store():
    return InMemoryIdempotencyStore()

@pytest.fixture
def pipeline(store):
    stt_mock = AsyncMock()
    stt_mock.transcribe.return_value = "Test transcript"
    ai_mock = AsyncMock()
    ai_mock.analyze_call.return_value = CallIntelligence(
        call_summary="Test",
        call_outcome=CallOutcome.INTERESTED,
        lead_quality=LeadQuality.WARM,
        primary_objection=PrimaryObjection.NONE,
        customer_intent="Test",
        next_action="Test",
        agent_quality_notes="Good"
    )
    frappe_mock = AsyncMock()
    return CallIntelligencePipeline(
        stt_service=stt_mock,
        ai_service=ai_mock,
        frappe_client=frappe_mock,
        idempotency_store=store,
    )

@pytest.fixture
def sample_event():
    return TelephonyWebhookPayload(
        provider_call_id="TEST-CONCURRENCY-1",
        telephony_provider="twilio",
        from_number="+15551234567",
        to_number="+15559876543",
        direction=CallDirection.INBOUND,
        call_status=CallStatus.COMPLETED,
        duration_seconds=120,
    )

@pytest.mark.asyncio
async def test_claimed_job_same_worker(pipeline, sample_event, store):
    job = PipelineResponse(
        success=True,
        provider_call_id="TEST-CONCURRENCY-1",
        processing_status=ProcessingStatus.PROCESSING,
        worker_id="worker-A",
        message="processing",
    )
    store.set(job.provider_call_id, job)
    await pipeline.process_call_background(sample_event, worker_id="worker-A")
    final_job = store.get(job.provider_call_id)
    assert final_job.processing_status in (ProcessingStatus.COMPLETED, ProcessingStatus.FAILED)

@pytest.mark.asyncio
async def test_claimed_job_different_worker(pipeline, sample_event, store):
    job = PipelineResponse(
        success=True,
        provider_call_id="TEST-CONCURRENCY-1",
        processing_status=ProcessingStatus.PROCESSING,
        worker_id="worker-A",
        message="processing",
    )
    store.set(job.provider_call_id, job)
    await pipeline.process_call_background(sample_event, worker_id="worker-B")
    final_job = store.get(job.provider_call_id)
    assert final_job.processing_status == ProcessingStatus.PROCESSING
    pipeline.stt_service.transcribe.assert_not_called()

@pytest.mark.asyncio
async def test_completed_job_skipped(pipeline, sample_event, store):
    job = PipelineResponse(
        success=True,
        provider_call_id="TEST-CONCURRENCY-1",
        processing_status=ProcessingStatus.COMPLETED,
        worker_id="worker-A",
        message="done",
    )
    store.set(job.provider_call_id, job)
    await pipeline.process_call_background(sample_event, worker_id="worker-A")
    final_job = store.get(job.provider_call_id)
    assert final_job.processing_status == ProcessingStatus.COMPLETED
    pipeline.stt_service.transcribe.assert_not_called()

@pytest.mark.asyncio
async def test_received_job_unit_test_path(pipeline, sample_event, store):
    job = PipelineResponse(
        success=True,
        provider_call_id="TEST-CONCURRENCY-1",
        processing_status=ProcessingStatus.RECEIVED,
        message="received",
    )
    store.set(job.provider_call_id, job)
    await pipeline.process_call_background(sample_event)
    final_job = store.get(job.provider_call_id)
    assert final_job.processing_status in (ProcessingStatus.COMPLETED, ProcessingStatus.FAILED)
    pipeline.stt_service.transcribe.assert_called_once()
