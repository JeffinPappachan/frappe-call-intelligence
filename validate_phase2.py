import asyncio
import uuid
import sys
import logging

# Set up logging for validation script
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("validator")

async def main():
    try:
        from src.config import get_settings, get_supabase_client
        from src.pipeline import CallIntelligencePipeline
        from src.schemas import TelephonyWebhookPayload, CallDirection, CallStatus
    except Exception as e:
        logger.error(f"Failed to import modules: {e}")
        sys.exit(1)

    settings = get_settings()

    logger.info("--- 1. Validating Configuration ---")
    assert settings.mock_mode == False, "MOCK_MODE should be false"
    assert settings.idempotency_backend == "supabase", "IDEMPOTENCY_BACKEND should be supabase"
    assert settings.stt_provider == "mock", "STT_PROVIDER should be mock"
    assert settings.ai_provider == "mock", "AI_PROVIDER should be mock"
    assert settings.supabase_url != "", "SUPABASE_URL should be present"
    assert settings.supabase_service_key != "", "SUPABASE_SERVICE_KEY should be present"
    logger.info("Configuration validated.")

    logger.info("--- 2. Validating Supabase Connectivity ---")
    sb_client = get_supabase_client()
    assert sb_client is not None, "Failed to create Supabase client"
    try:
        sb_client.table("calls").select("id").limit(1).execute()
        logger.info("Supabase connectivity validated.")
    except Exception as e:
        logger.error(f"Supabase connection failed: {e}")
        sys.exit(1)

    logger.info("--- 5. Running complete sample audio workflow ---")
    pipeline = CallIntelligencePipeline()

    unique_call_id = f"VALIDATION-CALL-{uuid.uuid4().hex[:8]}"
    event = TelephonyWebhookPayload(
        provider_call_id=unique_call_id,
        telephony_provider="exotel",
        from_number="+1234567890",
        to_number="+0987654321",
        direction=CallDirection.INBOUND,
        call_status=CallStatus.COMPLETED,
        duration_seconds=120,
        agent_id="test_agent",
        call_type="test"
    )

    # Mock audio bytes
    raw_audio = b"mock audio content"

    logger.info(f"Processing call: {unique_call_id}")
    response = await pipeline.process_call(event=event, raw_audio=raw_audio, audio_filename="test_audio.wav")

    assert response.success == True, "Pipeline failed"
    assert response.idempotent_replay == False, "Should not be a replay on first pass"
    logger.info("Initial processing successful.")

    logger.info("--- 6. Verifying resulting Supabase data ---")
    call_res = sb_client.table("calls").select("*").eq("provider_call_id", unique_call_id).execute()
    assert len(call_res.data) == 1, f"Expected 1 call record, got {len(call_res.data)}"
    call_row = call_res.data[0]

    intel_res = sb_client.table("call_intelligence").select("*").eq("call_id", call_row["id"]).execute()
    assert len(intel_res.data) == 1, f"Expected 1 intelligence record, got {len(intel_res.data)}"
    intel_row = intel_res.data[0]

    assert call_row["transcript"] is not None and len(call_row["transcript"]) > 0, "Transcript is missing"
    assert intel_row["call_summary"] is not None, "AI analysis (summary) is missing"
    assert call_row["recording_storage_path"] is not None, "Recording storage path is missing"

    # Storage check
    logger.info("--- 4. Validating recording storage ---")
    storage_path = call_row["recording_storage_path"]
    try:
        # Just check if we can generate a signed url to verify existence
        signed = sb_client.storage.from_("recordings").create_signed_url(storage_path, 60)
        assert signed is not None, "Failed to verify storage"
    except Exception as e:
        logger.error(f"Recording storage validation failed: {e}")
        sys.exit(1)

    logger.info("Supabase data and storage verified.")

    logger.info("--- 7. Testing Idempotency ---")
    response_replay = await pipeline.process_call(event=event, raw_audio=raw_audio, audio_filename="test_audio.wav")

    assert response_replay.success == True, "Idempotent replay failed"
    assert response_replay.idempotent_replay == True, "Should be flagged as idempotent replay"

    call_res2 = sb_client.table("calls").select("*").eq("provider_call_id", unique_call_id).execute()
    assert len(call_res2.data) == 1, "Duplicate call records found"

    logger.info("Idempotency verified.")

    logger.info("--- 8. Frappe CRM Integration ---")
    assert response.frappe_call_log_id is not None, "Frappe call log ID is missing"
    # Wait to clean up if necessary, but skipping for simplicity

    logger.info("ALL END-TO-END TESTS PASSED.")

if __name__ == "__main__":
    asyncio.run(main())
