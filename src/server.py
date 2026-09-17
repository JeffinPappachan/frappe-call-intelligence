import logging
import uuid
from typing import Optional
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware

from src.config import get_settings
from src.pipeline import get_pipeline
from src.schemas import (
    CallDirection,
    CallStatus,
    PipelineResponse,
    TelephonyWebhookPayload,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ai_call_intelligence")

settings = get_settings()

app = FastAPI(
    title="AI Call Intelligence with Frappe CRM",
    description="Telephony Webhook Ingestion, Speech-to-Text, Structured LLM Extraction, and Frappe CRM Write-Back.",
    version="0.1.0",
)

# Enable CORS for local testing / dashboard preview
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["Monitoring"])
async def health_check():
    """Health check endpoint exposing environment and service operational modes."""
    return {
        "status": "healthy",
        "mock_mode": settings.mock_mode,
        "app_env": settings.app_env,
        "frappe_crm_url": settings.frappe_base_url,
        "ai_provider": settings.ai_provider,
        "stt_provider": settings.stt_provider,
    }


@app.post(
    "/api/v1/telephony/webhook",
    response_model=PipelineResponse,
    status_code=status.HTTP_200_OK,
    tags=["Telephony Webhook"],
)
async def telephony_webhook(payload: TelephonyWebhookPayload):
    """Ingest telephony webhook event (Exotel / Twilio schema) and trigger the AI pipeline."""
    logger.info(f"Received webhook for call '{payload.provider_call_id}' from {payload.from_number} to {payload.to_number}")
    try:
        pipeline = get_pipeline()
        result = await pipeline.process_call(event=payload)
        return result
    except Exception as exc:
        logger.error(f"Error processing webhook call '{payload.provider_call_id}': {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline processing failed: {str(exc)}",
        )


@app.post(
    "/api/v1/telephony/process-audio",
    response_model=PipelineResponse,
    status_code=status.HTTP_200_OK,
    tags=["Audio Processing"],
)
async def process_audio(
    file: UploadFile = File(..., description="Audio recording file (WAV, MP3, M4A)"),
    lead_phone: str = Form(..., description="Lead phone number for CRM linking"),
    agent_id: Optional[str] = Form(default="agent@example.com"),
    direction: Optional[CallDirection] = Form(default=CallDirection.OUTBOUND),
    duration_seconds: Optional[int] = Form(default=60),
    provider_call_id: Optional[str] = Form(default=None),
):
    """Accept an uploaded audio recording and run speech-to-text, LLM extraction, and CRM sync."""
    call_id = provider_call_id or f"manual_{uuid.uuid4().hex[:12]}"
    logger.info(f"Processing uploaded audio '{file.filename}' for call ID '{call_id}', lead phone: {lead_phone}")

    try:
        audio_bytes = await file.read()
        event = TelephonyWebhookPayload(
            provider_call_id=call_id,
            telephony_provider="manual_upload",
            from_number=agent_id or "Agent",
            to_number=lead_phone,
            direction=direction or CallDirection.OUTBOUND,
            call_status=CallStatus.COMPLETED,
            duration_seconds=duration_seconds or 60,
            recording_url=None,
            agent_id=agent_id,
            call_type="sales_enquiry",
        )

        pipeline = get_pipeline()
        result = await pipeline.process_call(
            event=event,
            raw_audio=audio_bytes,
            audio_filename=file.filename,
        )
        return result
    except Exception as exc:
        logger.error(f"Error processing audio upload '{file.filename}': {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Audio processing failed: {str(exc)}",
        )
