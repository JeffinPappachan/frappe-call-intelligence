from datetime import datetime
import logging
import os
import uuid
from typing import Optional
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from src.config import get_settings
from src.pipeline import get_pipeline
from src.frappe_client import get_frappe_client
from src.schemas import (
    CallDirection,
    CallStatus,
    PipelineResponse,
    TelephonyWebhookPayload,
    DashboardMetricsResponse,
    DashboardCallFeedResponse,
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

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"], summary="Serve Manager Dashboard")
async def serve_dashboard():
    """Serves the Manager Call Intelligence Dashboard UI."""
    dashboard_path = os.path.join(static_dir, "dashboard.html")
    if not os.path.exists(dashboard_path):
        return HTMLResponse(content="<h1>Dashboard UI not found</h1><p>Please create src/static/dashboard.html.</p>", status_code=404)
    with open(dashboard_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


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
    
    if payload.provider_call_id.startswith("MOCK-") and not settings.mock_mode:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot run mock simulations in live MOCK_MODE=false environment to avoid polluting live CRM."
        )

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


@app.get(
    "/api/v1/dashboard/metrics",
    response_model=DashboardMetricsResponse,
    tags=["Analytics"],
)
async def get_dashboard_metrics():
    """Aggregate call metrics from the local idempotency store or live CRM."""
    if not settings.mock_mode:
        frappe = get_frappe_client()
        calls = await frappe.get_recent_call_logs(limit=200)
    else:
        calls = get_pipeline().idempotency_store.get_all()
    
    total = len(calls)
    if total == 0:
        return DashboardMetricsResponse()

    completed = sum(1 for c in calls if c.success)
    missed = total - completed
    total_duration = sum(c.duration_seconds for c in calls if c.duration_seconds)
    avg_duration = total_duration / total if total > 0 else 0.0

    calls_per_telecaller = {}
    lead_quality_distribution = {}
    call_outcome_distribution = {}
    follow_ups_due = 0
    follow_ups_overdue = 0
    
    # We need timezone aware datetime if follow_up_at is timezone aware.
    # The models use standard datetime, which we'll treat naively for simplicity, or use UTC if possible.
    # We will just use timezone-naive datetime.now() for simplicity if the mock follow ups are naive.
    # Wait, the pydantic model schema for datetime might parse it as timezone aware if it has a Z.
    now = datetime.now()

    for c in calls:
        agent = c.agent_id or "Unknown"
        calls_per_telecaller[agent] = calls_per_telecaller.get(agent, 0) + 1
        
        if c.intelligence:
            outcome = c.intelligence.call_outcome.value
            call_outcome_distribution[outcome] = call_outcome_distribution.get(outcome, 0) + 1
            
            quality = c.intelligence.lead_quality.value
            lead_quality_distribution[quality] = lead_quality_distribution.get(quality, 0) + 1
            
            if c.intelligence.follow_up_at:
                # To compare safely, make `now` aware if follow_up_at is aware
                if c.intelligence.follow_up_at.tzinfo:
                    now_tz = datetime.now(c.intelligence.follow_up_at.tzinfo)
                    if c.intelligence.follow_up_at < now_tz:
                        follow_ups_overdue += 1
                    else:
                        follow_ups_due += 1
                else:
                    if c.intelligence.follow_up_at < now:
                        follow_ups_overdue += 1
                    else:
                        follow_ups_due += 1

    return DashboardMetricsResponse(
        total_calls=total,
        completed_calls=completed,
        missed_calls=missed,
        average_call_duration_seconds=round(avg_duration, 2),
        calls_per_telecaller=calls_per_telecaller,
        lead_quality_distribution=lead_quality_distribution,
        call_outcome_distribution=call_outcome_distribution,
        follow_ups_due=follow_ups_due,
        follow_ups_overdue=follow_ups_overdue,
    )


@app.get(
    "/api/v1/dashboard/calls",
    response_model=DashboardCallFeedResponse,
    tags=["Analytics"],
)
async def get_dashboard_calls():
    """Get recent calls feed for the dashboard."""
    if not settings.mock_mode:
        frappe = get_frappe_client()
        calls = await frappe.get_recent_call_logs(limit=50)
    else:
        calls = get_pipeline().idempotency_store.get_all()
        
    # Sort by timestamp descending
    calls.sort(
        key=lambda c: c.event_timestamp.timestamp() if c.event_timestamp else 0,
        reverse=True,
    )
    return DashboardCallFeedResponse(calls=calls)

