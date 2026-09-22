import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, UTC
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.config import get_settings
from src.observability import (
    AppError,
    metrics,
    request_id_ctx,
    setup_structured_logging,
)
from src.pipeline import get_pipeline
from src.schemas import (
    CallDirection,
    CallIntelligence,
    CallStatus,
    DashboardCallFeedResponse,
    DashboardMetricsResponse,
    PipelineResponse,
    ProcessingStatus,
    TelephonyWebhookPayload,
)

settings = get_settings()
setup_structured_logging(log_level=settings.log_level, log_format=settings.log_format)
logger = logging.getLogger("ai_call_intelligence")


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = None
    if settings.idempotency_backend == "supabase":
        import asyncio
        from src.worker import run_worker
        worker_task = asyncio.create_task(run_worker(register_signals=False))
        logger.info("Started in-process queue worker task.")
    yield
    if worker_task:
        worker_task.cancel()
        try:
            await worker_task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(
    title="AI Call Intelligence with Frappe CRM",
    description="Telephony Webhook Ingestion, Speech-to-Text, Structured LLM Extraction, and Frappe CRM Write-Back.",
    version="0.1.0",
    lifespan=lifespan,
)

# Enable CORS with explicit origins from settings and regex for Netlify deployments
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_origin_regex=r"https://.*\.netlify\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_correlation_middleware(request: Request, call_next):
    """Middleware ensuring every request has a validated Request ID propagated via contextvars and headers."""
    incoming_id = request.headers.get("X-Request-ID")
    # Validate client-supplied ID: must be alphanumeric/dashes and length <= 64
    if incoming_id and re.match(r"^[a-zA-Z0-9_\-]{1,64}$", incoming_id):
        req_id = incoming_id
    else:
        req_id = f"req_{uuid.uuid4().hex[:16]}"

    token = request_id_ctx.set(req_id)
    metrics.increment("total_requests")

    try:
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response
    finally:
        request_id_ctx.reset(token)


# Mount static files for the React frontend
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")


@app.get("/dashboard", response_class=HTMLResponse, tags=["UI"])
async def dashboard_ui():
    """Serve the React frontend dashboard."""
    index_file = frontend_dist / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Frontend not built. Please run 'npm run build' in the frontend directory.</h1>", status_code=404)
    return index_file.read_text(encoding="utf-8")


@app.get("/health", tags=["Monitoring"])
async def health_check():
    """Health check endpoint exposing application liveness and non-sensitive operational modes."""
    return {
        "status": "healthy",
        "mock_mode": settings.mock_mode,
        "app_env": settings.app_env,
        "frappe_crm_url": settings.frappe_base_url,
        "ai_provider": settings.ai_provider,
        "stt_provider": settings.stt_provider,
    }


@app.get("/ready", tags=["Monitoring"])
async def readiness_check():
    """Readiness check verifying that application dependencies and configurations are operational."""
    current_settings = get_settings()
    checks = {
        "crm_configured": False,
        "ai_configured": False,
        "stt_configured": False,
        "storage_ready": False,
    }
    is_ready = True
    reasons = []

    # 1. CRM configuration check
    if current_settings.mock_mode or (current_settings.frappe_api_key and current_settings.frappe_api_secret and current_settings.frappe_base_url):
        checks["crm_configured"] = True
    else:
        is_ready = False
        reasons.append("Frappe CRM API credentials missing in live mode")

    # 2. AI provider configuration check
    if current_settings.mock_mode or current_settings.ai_provider == "mock" or (current_settings.ai_api_key and current_settings.ai_provider in ("groq", "openai", "gemini")):
        checks["ai_configured"] = True
    else:
        is_ready = False
        reasons.append(f"AI provider '{current_settings.ai_provider}' lacks configured API key")

    # 3. STT provider configuration check
    if current_settings.mock_mode or current_settings.stt_provider == "mock" or (current_settings.stt_api_key and current_settings.stt_provider in ("groq", "openai", "gemini")):
        checks["stt_configured"] = True
    else:
        is_ready = False
        reasons.append(f"STT provider '{current_settings.stt_provider}' lacks configured API key")

    # 4. Storage / Idempotency check
    if current_settings.idempotency_backend == "sqlite":
        try:
            import sqlite3
            with sqlite3.connect(current_settings.sqlite_db_path) as conn:
                conn.execute("SELECT 1 FROM idempotency_cache LIMIT 1")
            checks["storage_ready"] = True
        except Exception as exc:
            is_ready = False
            reasons.append(f"SQLite idempotency database error: {exc!s}")
    else:
        checks["storage_ready"] = True

    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return Response(
        content=json.dumps({
            "status": "ready" if is_ready else "not_ready",
            "checks": checks,
            "reasons": reasons if not is_ready else [],
            "mock_mode": current_settings.mock_mode,
            "app_env": current_settings.app_env,
        }),
        status_code=status_code,
        media_type="application/json",
    )


@app.get("/metrics", tags=["Monitoring"])
async def get_metrics():
    """Operational metrics endpoint tracking request counts, durations, and error classifications."""
    if not settings.metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Metrics collection disabled")
    return metrics.get_metrics()


@app.get("/api/v1/crm/contacts", tags=["CRM"])
async def get_crm_contacts():
    from src.frappe_client import get_frappe_client
    from src.config import get_supabase_client
    client = get_frappe_client()
    frappe_contacts = await client.get_crm_contacts()

    # Append historical manual upload phones from Supabase
    try:
        supabase = get_supabase_client()
        res = supabase.table("calls").select("event_payload").execute()
        manual_contacts_map = {}
        for call in res.data:
            payload = call.get("event_payload", {})
            if payload and payload.get("telephony_provider") == "manual_upload":
                # For manual uploads, the lead phone is stored in to_number (or from_number)
                phone = payload.get("to_number") or payload.get("from_number")
                name = payload.get("lead_name") or "Custom"
                if phone and phone not in manual_contacts_map:
                    manual_contacts_map[phone] = name

        for phone, name in manual_contacts_map.items():
            if not any(c.get("mobile_no") == phone for c in frappe_contacts):
                frappe_contacts.append({
                    "name": f"saved-{phone}",
                    "lead_name": name,
                    "organization": "",
                    "mobile_no": phone
                })
    except Exception:
        pass

    return {"data": frappe_contacts}


@app.get("/api/v1/crm/agents", tags=["CRM"])
async def get_crm_agents():
    from src.frappe_client import get_frappe_client
    from src.config import get_supabase_client
    client = get_frappe_client()
    frappe_agents = await client.get_crm_agents()

    # Append historical manual upload agents from Supabase
    try:
        supabase = get_supabase_client()
        res = supabase.table("calls").select("agent_id, event_payload").execute()
        manual_agents = set()
        for call in res.data:
            payload = call.get("event_payload", {})
            if payload and payload.get("telephony_provider") == "manual_upload":
                agent = call.get("agent_id")
                if agent:
                    manual_agents.add(agent)

        for agent in manual_agents:
            if not any(a.get("name") == agent or a.get("full_name") == agent for a in frappe_agents):
                frappe_agents.append({
                    "name": f"saved-{agent}",
                    "full_name": agent
                })
    except Exception:
        pass

    return {"data": frappe_agents}


@app.post(
    "/api/v1/telephony/webhook",
    response_model=PipelineResponse,
    status_code=status.HTTP_200_OK,
    tags=["Telephony Webhook"],
)
async def telephony_webhook(payload: TelephonyWebhookPayload, background_tasks: BackgroundTasks):
    """Ingest telephony webhook event (Exotel / Twilio schema) and trigger the AI pipeline."""
    req_id = request_id_ctx.get("-")
    logger.info(
        f"Received webhook for call '{payload.provider_call_id}'",
        extra={"call_id": payload.provider_call_id, "request_id": req_id},
    )

    if payload.provider_call_id.startswith("MOCK-") and not settings.mock_mode:
        metrics.record_error("validation_error")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot run mock simulations in live MOCK_MODE=false environment to avoid polluting live CRM.",
        )

    try:
        pipeline = get_pipeline()
        # Accept call and persist initial state synchronously
        result = pipeline.accept_call(event=payload)

        # Dispatch background processing immediately
        if not result.idempotent_replay:
            background_tasks.add_task(
                pipeline.process_call_background,
                event=payload,
            )
        return result
    except AppError as app_err:
        metrics.increment("pipeline_failed_total")
        metrics.record_error(app_err.error_class.value)
        logger.error(
            f"AppError processing webhook call '{payload.provider_call_id}': {app_err.message}",
            extra={"call_id": payload.provider_call_id, "request_id": req_id, "error_class": app_err.error_class.value},
        )
        raise HTTPException(
            status_code=app_err.status_code,
            detail=app_err.message,
        ) from app_err
    except HTTPException:
        metrics.increment("pipeline_failed_total")
        raise
    except Exception as exc:
        metrics.increment("pipeline_failed_total")
        metrics.record_error("unexpected_error")
        logger.error(
            f"Unexpected error processing webhook call '{payload.provider_call_id}': {exc}",
            extra={"call_id": payload.provider_call_id, "request_id": req_id, "error_class": "unexpected_error"},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline processing failed: {exc!s}",
        ) from exc


# Audio upload security constants (SEC-06)
MAX_AUDIO_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB (Groq Whisper limit)
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac"}
ALLOWED_AUDIO_MIME_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mpeg",
    "audio/mp3",
    "audio/m4a",
    "audio/x-m4a",
    "audio/mp4",
    "audio/ogg",
    "audio/webm",
    "audio/flac",
    "audio/x-flac",
    "application/octet-stream",  # Sometimes supplied by CLI curl/browsers for raw audio
}


@app.post(
    "/api/v1/telephony/process-audio",
    response_model=PipelineResponse,
    status_code=status.HTTP_200_OK,
    tags=["Audio Processing"],
)
async def process_audio(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Audio recording file (WAV, MP3, M4A)"),
    lead_phone: str | None = Form(default=None, description="Lead phone number for CRM linking"),
    agent_phone: str | None = Form(default=None, description="Agent phone number"),
    agent_id: str | None = Form(default="jeffinpappachan110@gmail.com"),
    direction: CallDirection | None = Form(default=CallDirection.OUTBOUND),
    duration_seconds: int | None = Form(default=60),
    provider_call_id: str | None = Form(default=None),
    lead_id: str | None = Form(default=None),
    lead_name: str | None = Form(default=None),
    event_timestamp: str | None = Form(default=None),
):
    """Accept an uploaded audio recording and run speech-to-text, LLM extraction, and CRM sync.

    Enforces maximum 25 MB file size, MIME type and extension validation, and rejects empty files (SEC-06).
    """
    call_id = provider_call_id or f"manual_{uuid.uuid4().hex[:12]}"
    filename = file.filename or "audio.wav"
    ext = os.path.splitext(filename)[1].lower()
    req_id = request_id_ctx.get("-")

    # 1. Extension validation
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        metrics.increment("audio_upload_validation_failures_total")
        metrics.record_error("validation_error")
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file extension '{ext}'. Allowed extensions: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}",
        )

    # 2. Content-Type validation
    content_type = (file.content_type or "").lower().split(";")[0].strip()
    if content_type and content_type not in ALLOWED_AUDIO_MIME_TYPES:
        metrics.increment("audio_upload_validation_failures_total")
        metrics.record_error("validation_error")
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported media type '{file.content_type}'. Allowed MIME types: audio/wav, audio/mpeg, audio/mp4, audio/m4a, audio/ogg, audio/webm, audio/flac",
        )

    logger.info(
        f"Processing uploaded audio '{filename}'",
        extra={"call_id": call_id, "request_id": req_id},
    )

    try:
        # 3. Bounded chunked streaming read to avoid unlimited memory usage
        chunks: list[bytes] = []
        total_read = 0
        chunk_size = 1024 * 1024  # 1 MB chunk

        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            total_read += len(chunk)
            if total_read > MAX_AUDIO_UPLOAD_BYTES:
                metrics.increment("audio_upload_validation_failures_total")
                metrics.record_error("validation_error")
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"File exceeds maximum upload size of {MAX_AUDIO_UPLOAD_BYTES // (1024 * 1024)} MB.",
                )
            chunks.append(chunk)

        # 4. Reject empty files
        if total_read == 0:
            metrics.increment("audio_upload_validation_failures_total")
            metrics.record_error("validation_error")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Empty audio file uploaded. File size must be greater than 0 bytes.",
            )

        audio_bytes = b"".join(chunks)

        # 5. Detect actual audio duration from file content
        detected_duration = duration_seconds  # fall back to user-provided or default
        try:
            import io
            import mutagen
            audio_file = mutagen.File(io.BytesIO(audio_bytes))
            if audio_file is not None and audio_file.info and audio_file.info.length:
                detected_duration = int(audio_file.info.length)
                logger.info(
                    f"Detected audio duration: {detected_duration}s for '{filename}'",
                    extra={"call_id": call_id, "request_id": req_id},
                )
        except Exception as exc:
            logger.warning(
                f"Could not detect audio duration for '{filename}': {exc}",
                extra={"call_id": call_id, "request_id": req_id},
            )

        dt = datetime.now(UTC)
        if event_timestamp:
            try:
                dt = datetime.fromisoformat(event_timestamp)
            except ValueError:
                pass

        # Determine actual from/to numbers based on direction
        call_direction = direction or CallDirection.OUTBOUND
        if call_direction == CallDirection.OUTBOUND:
            actual_from = agent_phone or ""
            actual_to = lead_phone or ""
        else:
            actual_from = lead_phone or ""
            actual_to = agent_phone or ""

        event = TelephonyWebhookPayload(
            provider_call_id=call_id,
            telephony_provider="manual_upload",
            from_number=actual_from,
            to_number=actual_to,
            direction=call_direction,
            call_status=CallStatus.COMPLETED,
            duration_seconds=detected_duration or 60,
            recording_url=None,
            agent_id=agent_id,
            call_type="sales_enquiry",
            event_timestamp=dt,
            lead_id=lead_id,
            lead_name=lead_name,
        )

        def _sync_accept_and_upload():
            """Run blocking Supabase operations in a thread to avoid blocking the event loop."""
            pipeline = get_pipeline()

            # Enqueue background task is handled by external workers (Phase 5)
            # For manual uploads, we MUST upload the audio to storage BEFORE accepting the call
            # to prevent the worker from claiming a job without an audio file path.
            storage_path = None
            from src.config import get_settings
            if get_settings().idempotency_backend == "supabase":
                from src.supabase_store import upload_audio_to_supabase
                storage_path = upload_audio_to_supabase(
                    call_id=call_id,
                    raw_audio=audio_bytes,
                    recording_url=None,
                    filename=filename
                )

            # Fallback to local storage if Supabase upload was not performed or failed
            if not storage_path:
                try:
                    os.makedirs(get_settings().audio_storage_dir, exist_ok=True)
                    local_path = os.path.join(get_settings().audio_storage_dir, f"{call_id}_{filename}")
                    with open(local_path, "wb") as f:
                        f.write(audio_bytes)
                    storage_path = local_path
                except Exception as exc:
                    logger.warning(f"Could not save local copy of audio for {call_id}: {exc}")

            # Accept call and persist initial state synchronously
            result = pipeline.accept_call(event=event, recording_storage_path=storage_path)

            return result

        import asyncio
        result = await asyncio.to_thread(_sync_accept_and_upload)

        pipeline = get_pipeline()
        if not result.idempotent_replay:
            background_tasks.add_task(
                pipeline.process_call_background,
                event=event,
                raw_audio=audio_bytes,
                audio_filename=filename,
            )

        return result
    except AppError as app_err:
        metrics.increment("pipeline_failed_total")
        metrics.record_error(app_err.error_class.value)
        logger.error(
            f"AppError processing audio upload '{filename}': {app_err.message}",
            extra={"call_id": call_id, "request_id": req_id, "error_class": app_err.error_class.value},
        )
        raise HTTPException(
            status_code=app_err.status_code,
            detail=app_err.message,
        ) from app_err
    except HTTPException:
        raise
    except Exception as exc:
        metrics.increment("pipeline_failed_total")
        metrics.record_error("unexpected_error")
        logger.error(
            f"Error processing audio upload '{filename}': {exc}",
            extra={"call_id": call_id, "request_id": req_id, "error_class": "unexpected_error"},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Audio processing failed: {exc!s}",
        ) from exc


@app.get(
    "/api/v1/dashboard/metrics",
    response_model=DashboardMetricsResponse,
    tags=["Analytics"],
)
async def get_dashboard_metrics():
    """Aggregate call metrics from the local idempotency store or live CRM."""
    calls = get_pipeline().idempotency_store.get_all(limit=100)


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
async def get_dashboard_calls(background_tasks: BackgroundTasks):
    """Get recent calls feed for the dashboard."""
    calls = get_pipeline().idempotency_store.get_all(limit=50)

    # Background heal any calls that still have mock data, are unanalyzed, failed, or missing CRM logs
    for c in calls:
        needs_heal = (
            (c.transcript and "are you interested in our course" in c.transcript.lower())
            or (c.intelligence and "course fee" in c.intelligence.call_summary.lower())
            or c.intelligence is None
            or c.processing_status != ProcessingStatus.COMPLETED
            or not c.frappe_call_log_id
        )
        if needs_heal:
            background_tasks.add_task(auto_heal_call, c)

    # Sort by timestamp descending
    calls.sort(
        key=lambda c: c.event_timestamp.timestamp() if c.event_timestamp else 0,
        reverse=True,
    )
    return DashboardCallFeedResponse(calls=calls)


async def auto_heal_call(cached: PipelineResponse) -> PipelineResponse:
    """If a call has hardcoded mock data, failed STT/AI, or missing Frappe CRM / Supabase sync,
    re-run real speech-to-text, AI intelligence, and sync with Frappe CRM.
    """
    if not cached:
        return cached

    has_mock_transcript = bool(cached.transcript and "are you interested in our course" in cached.transcript.lower())
    has_mock_intel = bool(cached.intelligence and "course fee" in cached.intelligence.call_summary.lower())
    missing_intel = cached.intelligence is None
    is_failed = cached.processing_status == ProcessingStatus.FAILED
    missing_crm = not cached.frappe_call_log_id

    # If completely analyzed and synchronized, nothing to heal
    if not (has_mock_transcript or has_mock_intel or missing_intel or is_failed or missing_crm):
        return cached

    pipeline = get_pipeline()

    # 1. Re-run STT and AI if missing, mock, or failed
    if has_mock_transcript or has_mock_intel or missing_intel or is_failed or not cached.transcript:
        logger.info(f"Auto-healing STT and AI for call '{cached.provider_call_id}'...")
        audio_bytes = None
        filename = "recording.mp3"

        storage_path = cached.recording_storage_path
        if storage_path:
            filename = os.path.basename(storage_path)
            if os.path.exists(storage_path):
                try:
                    with open(storage_path, "rb") as f:
                        audio_bytes = f.read()
                except Exception as e:
                    logger.error(f"Failed to read audio from {storage_path}: {e}")
            else:
                alt_local = os.path.join(settings.audio_storage_dir, filename)
                if os.path.exists(alt_local):
                    try:
                        with open(alt_local, "rb") as f:
                            audio_bytes = f.read()
                    except Exception as e:
                        logger.error(f"Failed to read audio from {alt_local}: {e}")
                elif settings.idempotency_backend == "supabase":
                    from src.supabase_store import download_audio_from_supabase
                    audio_bytes = download_audio_from_supabase(storage_path)

        if audio_bytes:
            try:
                # Transcribe with real Groq Whisper
                stt_meta = {
                    "call_id": cached.provider_call_id,
                    "audio_filename": filename,
                }
                real_transcript = await pipeline.stt_service.transcribe(
                    audio_source=audio_bytes,
                    filename=filename,
                    metadata=stt_meta,
                )
                if real_transcript and real_transcript.strip():
                    cached.transcript = real_transcript

                    # Analyze with real Groq AI
                    ai_meta = {
                        "call_id": cached.provider_call_id,
                        "event_timestamp": cached.event_timestamp.isoformat() if cached.event_timestamp else None,
                    }
                    real_intel = await pipeline.ai_service.analyze_call(
                        transcript=real_transcript,
                        metadata=ai_meta,
                    )
                    cached.intelligence = real_intel
                    cached.processing_status = ProcessingStatus.COMPLETED
                    cached.error_message = None
                    logger.info(f"Auto-healed STT & AI for '{cached.provider_call_id}'!")
            except Exception as exc:
                logger.error(f"Auto-heal STT/AI failed for '{cached.provider_call_id}': {exc}", exc_info=True)

    # 2. Sync to Frappe CRM if Call Log is missing and intelligence is available
    if cached.intelligence and not cached.frappe_call_log_id:
        try:
            logger.info(f"Synchronizing missing Frappe CRM Call Log for '{cached.provider_call_id}'...")
            payload_data = cached.event_payload or {}
            event = TelephonyWebhookPayload(
                provider_call_id=cached.provider_call_id,
                telephony_provider=payload_data.get("telephony_provider", "manual_upload"),
                from_number=payload_data.get("from_number", "+15550003333"),
                to_number=payload_data.get("to_number", "+15551234567"),
                direction=cached.direction or CallDirection.OUTBOUND,
                call_status=CallStatus.COMPLETED,
                duration_seconds=cached.duration_seconds or 60,
                agent_id=cached.agent_id or payload_data.get("agent_id"),
                recording_url=payload_data.get("recording_url"),
                event_timestamp=cached.event_timestamp,
                lead_id=payload_data.get("lead_id"),
                lead_name=cached.matched_lead or payload_data.get("lead_name"),
            )
            # Lookup lead in Frappe CRM
            phone_to_check = event.to_number if event.direction == CallDirection.OUTBOUND else event.from_number
            lead = await pipeline.frappe_client.lookup_lead_by_phone(phone_to_check)
            lead_id = lead.get("name") if lead else event.lead_id
            call_log = await pipeline.frappe_client.create_call_log(
                call_event=event,
                transcript=cached.transcript or "",
                intelligence=cached.intelligence,
                lead_id=lead_id,
            )
            if call_log and call_log.get("name"):
                cached.frappe_call_log_id = call_log.get("name")
                logger.info(f"Successfully created Frappe CRM Call Log: {cached.frappe_call_log_id}")
        except Exception as crm_exc:
            logger.error(f"Frappe CRM sync failed in auto-heal for '{cached.provider_call_id}': {crm_exc}")

    # 3. Persist updated record to current store and Supabase
    try:
        pipeline.idempotency_store.set(cached.provider_call_id, cached)
        from src.config import get_supabase_client
        if get_supabase_client():
            from src.supabase_store import SupabaseIdempotencyStore
            sb_store = SupabaseIdempotencyStore()
            sb_store.set(cached.provider_call_id, cached)
    except Exception as save_exc:
        logger.error(f"Failed to persist auto-healed call '{cached.provider_call_id}': {save_exc}")

    return cached


@app.get(
    "/api/v1/dashboard/calls/{call_id}/intelligence",
    response_model=CallIntelligence,
    tags=["Analytics"],
)
async def get_call_intelligence(call_id: str):
    """Retrieve detailed AI analysis for a specific call."""
    pipeline = get_pipeline()
    cached = pipeline.idempotency_store.get(call_id)
    if cached:
        cached = await auto_heal_call(cached)
        if cached.intelligence:
            return cached.intelligence

    raise HTTPException(status_code=404, detail="Intelligence not found for this call")


@app.get(
    "/api/v1/dashboard/calls/{call_id}/details",
    response_model=PipelineResponse,
    tags=["Analytics"],
)
async def get_call_details(call_id: str):
    """Retrieve full call details including transcript, recording storage path, and intelligence."""
    pipeline = get_pipeline()
    cached = pipeline.idempotency_store.get(call_id)
    if cached:
        cached = await auto_heal_call(cached)
        return cached

    raise HTTPException(status_code=404, detail="Call not found")


@app.post(
    "/api/v1/dashboard/calls/{call_id}/reprocess",
    response_model=PipelineResponse,
    tags=["Analytics"],
)
async def reprocess_call(call_id: str):
    """Force re-processing of a call's transcription, AI analysis, and CRM sync from its recording."""
    pipeline = get_pipeline()
    cached = pipeline.idempotency_store.get(call_id)
    if not cached:
        raise HTTPException(status_code=404, detail="Call not found")

    cached.transcript = None
    cached.intelligence = None
    cached.frappe_call_log_id = None
    cached.processing_status = ProcessingStatus.PROCESSING
    cached = await auto_heal_call(cached)
    return cached


@app.get(
    "/api/v1/dashboard/calls/{call_id}/recording",
    tags=["Analytics"],
)
async def get_call_recording(call_id: str):
    """Retrieve or stream the audio recording for a call directly to prevent CORS/redirect player errors."""
    pipeline = get_pipeline()
    cached = pipeline.idempotency_store.get(call_id)
    if not cached:
        raise HTTPException(status_code=404, detail="Call not found")

    storage_path = cached.recording_storage_path
    if not storage_path:
        raise HTTPException(status_code=404, detail="Recording not found")

    # 1. Local file path check
    if os.path.exists(storage_path):
        from fastapi.responses import FileResponse
        return FileResponse(storage_path)

    local_path = os.path.join(settings.audio_storage_dir, os.path.basename(storage_path))
    if os.path.exists(local_path):
        from fastapi.responses import FileResponse
        return FileResponse(local_path)

    # 2. Supabase storage check - stream bytes directly to client with CORS and range support
    from src.config import get_supabase_client

    client = get_supabase_client()
    if client:
        candidates = [
            storage_path,
            f"{call_id}/{os.path.basename(storage_path)}",
            f"{call_id}/{os.path.basename(storage_path).replace(call_id + '_', '')}",
        ]
        for cand in candidates:
            try:
                audio_bytes = client.storage.from_("recordings").download(cand)
                if audio_bytes:
                    ext = os.path.splitext(cand)[1].lower()
                    mime_map = {
                        ".wav": "audio/wav",
                        ".mp3": "audio/mpeg",
                        ".m4a": "audio/m4a",
                        ".mp4": "audio/mp4",
                        ".ogg": "audio/ogg",
                        ".webm": "audio/webm",
                        ".flac": "audio/flac",
                    }
                    media_type = mime_map.get(ext, "audio/mpeg")
                    return Response(
                        content=audio_bytes,
                        media_type=media_type,
                        headers={
                            "Accept-Ranges": "bytes",
                            "Content-Length": str(len(audio_bytes)),
                            "Content-Disposition": f'inline; filename="{os.path.basename(storage_path)}"',
                        },
                    )
            except Exception as exc:
                logger.debug(f"Candidate {cand} not found in Supabase storage: {exc}")

    raise HTTPException(status_code=404, detail="Recording not found")



@app.get("/api/models", tags=["Configuration"], summary="List Safe Supported AI Models")
async def get_groq_models(x_admin_token: str | None = Header(None, alias="X-Admin-Token")):
    """List available AI models with safe public metadata only.

    Secrets, API tokens, and internal provider headers are never returned.
    If ADMIN_API_TOKEN is set in configuration, valid X-Admin-Token is required.
    """
    curr_settings = get_settings()
    if curr_settings.admin_api_token and x_admin_token != curr_settings.admin_api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid or missing admin token",
        )

    # In mock mode or when no external key is configured, return safe static list
    if curr_settings.mock_mode or not curr_settings.ai_api_key or curr_settings.ai_provider != "groq":
        return {
            "object": "list",
            "data": [
                {"id": "openai/gpt-oss-20b", "owned_by": "groq", "active": True},
                {"id": "llama-3.3-70b-versatile", "owned_by": "groq", "active": True},
                {"id": "whisper-large-v3", "owned_by": "groq", "active": True},
            ],
        }

    import httpx
    url = "https://api.groq.com/openai/v1/models"
    headers = {"Authorization": f"Bearer {curr_settings.ai_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url, headers=headers)
            if res.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Upstream model service unavailable",
                )
            raw_data = res.json()
    except httpx.RequestError as exc:
        logger.error(f"Failed to fetch upstream models: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Error communicating with upstream AI model provider",
        ) from exc

    # SEC-01: Filter strictly to safe fields; never expose headers or raw payload
    safe_models = []
    for model in raw_data.get("data", []):
        safe_models.append({
            "id": model.get("id"),
            "owned_by": model.get("owned_by"),
            "active": model.get("active", True),
        })

    return {"object": "list", "data": safe_models}
