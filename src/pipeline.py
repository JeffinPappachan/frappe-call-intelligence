import json
import logging
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from datetime import datetime, UTC

from src.ai_service import AIService, get_ai_service
from src.frappe_client import FrappeCRMClient, get_frappe_client
from src.schemas import (
    CallDirection,
    PipelineResponse,
    TelephonyWebhookPayload,
)
from src.stt_service import STTService, get_stt_service

logger = logging.getLogger(__name__)


class IdempotencyStore(ABC):
    """Abstract store for webhook deduplication and replay caching."""

    @abstractmethod
    def has(self, key: str) -> bool:
        pass

    @abstractmethod
    def get(self, key: str) -> PipelineResponse | None:
        pass

    @abstractmethod
    def set(self, key: str, value: PipelineResponse) -> None:
        pass

    @abstractmethod
    def get_all(self, limit: int = 50) -> list[PipelineResponse]:
        pass

    @abstractmethod
    def clear(self) -> None:
        pass


class InMemoryIdempotencyStore(IdempotencyStore):
    """Thread-safe bounded in-memory idempotency cache with TTL expiration (REL-03).

    Features:
    - Bounded capacity with LRU eviction (max_items).
    - Time-To-Live (TTL) expiration per cached item.
    - Thread-safe access via RLock.
    """

    def __init__(self, ttl_seconds: int | None = None, max_items: int | None = None):
        from src.config import get_settings
        settings = get_settings()
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else settings.idempotency_ttl_seconds
        self.max_items = max_items if max_items is not None else settings.idempotency_max_items
        # Store tuples: (PipelineResponse, expiry_timestamp_float)
        self._cache: OrderedDict[str, tuple[PipelineResponse, float]] = OrderedDict()
        self._lock = threading.RLock()

    def _purge_expired(self, now: float | None = None) -> None:
        """Internal helper to purge expired entries."""
        current_time = now if now is not None else time.time()
        expired_keys = [k for k, (_, exp) in self._cache.items() if current_time >= exp]
        for k in expired_keys:
            del self._cache[k]

    def has(self, key: str) -> bool:
        with self._lock:
            if key not in self._cache:
                return False
            _, expiry = self._cache[key]
            if time.time() >= expiry:
                del self._cache[key]
                return False
            return True

    def get(self, key: str) -> PipelineResponse | None:
        with self._lock:
            if key not in self._cache:
                return None
            resp, expiry = self._cache[key]
            if time.time() >= expiry:
                del self._cache[key]
                return None
            # Move to end to maintain LRU access order
            self._cache.move_to_end(key)
            return resp

    def set(self, key: str, value: PipelineResponse) -> None:
        with self._lock:
            self._purge_expired()
            # If at max capacity, evict oldest entry (FIFO / least recently inserted/accessed)
            if key not in self._cache and len(self._cache) >= self.max_items:
                self._cache.popitem(last=False)

            expiry = time.time() + self.ttl_seconds
            self._cache[key] = (value, expiry)
            self._cache.move_to_end(key)

    def get_all(self, limit: int = 50) -> list[PipelineResponse]:
        with self._lock:
            self._purge_expired()
            return [resp for resp, _ in self._cache.values()][:limit]

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


class SQLiteIdempotencyStore(IdempotencyStore):
    """Local SQLite persistent idempotency store with TTL expiration (REL-03).

    Provides persistent local disk storage across server restarts while honoring TTL.
    Note: For multi-worker, horizontally-scaled cloud deployments, Redis is recommended.
    """

    def __init__(self, db_path: str = "idempotency.db", ttl_seconds: int | None = None, max_items: int | None = None):
        from src.config import get_settings
        settings = get_settings()
        self.db_path = db_path
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else settings.idempotency_ttl_seconds
        self.max_items = max_items if max_items is not None else settings.idempotency_max_items
        self._lock = threading.RLock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS idempotency_cache (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_expires_at ON idempotency_cache(expires_at)")
            conn.commit()

    def _purge_expired(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM idempotency_cache WHERE expires_at <= ?", (time.time(),))

    def has(self, key: str) -> bool:
        with self._lock, self._get_conn() as conn:
            self._purge_expired(conn)
            cur = conn.execute("SELECT 1 FROM idempotency_cache WHERE key = ?", (key,))
            return cur.fetchone() is not None

    def get(self, key: str) -> PipelineResponse | None:
        with self._lock, self._get_conn() as conn:
            self._purge_expired(conn)
            cur = conn.execute("SELECT payload FROM idempotency_cache WHERE key = ?", (key,))
            row = cur.fetchone()
            if not row:
                return None
            try:
                data = json.loads(row["payload"])
                return PipelineResponse.model_validate(data)
            except Exception as exc:
                logger.error(f"[SQLiteIdempotencyStore] Failed to deserialize payload for {key}: {exc}")
                return None

    def set(self, key: str, value: PipelineResponse) -> None:
        with self._lock, self._get_conn() as conn:
            self._purge_expired(conn)
            # Enforce max items bound: delete oldest entries if at or over max_items
            cur = conn.execute("SELECT COUNT(*) as cnt FROM idempotency_cache")
            count = cur.fetchone()["cnt"]
            if count >= self.max_items:
                conn.execute(
                    """
                    DELETE FROM idempotency_cache WHERE key IN (
                        SELECT key FROM idempotency_cache ORDER BY created_at ASC LIMIT ?
                    )
                    """,
                    (count - self.max_items + 1,),
                )

            now = time.time()
            expires_at = now + self.ttl_seconds
            payload_json = value.model_dump_json()
            conn.execute(
                """
                INSERT INTO idempotency_cache (key, payload, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    payload = excluded.payload,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (key, payload_json, now, expires_at),
            )
            conn.commit()

    def get_all(self, limit: int = 50) -> list[PipelineResponse]:
        with self._lock, self._get_conn() as conn:
            self._purge_expired(conn)
            cur = conn.execute("SELECT payload FROM idempotency_cache ORDER BY created_at DESC LIMIT ?", (limit,))
            results = []
            for row in cur.fetchall():
                try:
                    results.append(PipelineResponse.model_validate_json(row["payload"]))
                except Exception:
                    continue
            return results

    def clear(self) -> None:
        with self._lock, self._get_conn() as conn:
            conn.execute("DELETE FROM idempotency_cache")
            conn.commit()


class CallIntelligencePipeline:
    """Core orchestration pipeline for Test Work 01.

    Workflow:
    Webhook / Audio Input -> Idempotency Check -> STT -> Structured LLM Extraction ->
    Frappe CRM Call Log -> Frappe Follow-up Task -> Lead Update -> Result Cache.
    """

    def __init__(
        self,
        stt_service: STTService | None = None,
        ai_service: AIService | None = None,
        frappe_client: FrappeCRMClient | None = None,
        idempotency_store: IdempotencyStore | None = None,
    ):
        from src.config import get_settings
        settings = get_settings()

        self.stt_service = stt_service or get_stt_service()
        self.ai_service = ai_service or get_ai_service()
        self.frappe_client = frappe_client or get_frappe_client()

        if idempotency_store:
            self.idempotency_store = idempotency_store
        elif settings.idempotency_backend == "supabase":
            from src.supabase_store import SupabaseIdempotencyStore
            sb_store = SupabaseIdempotencyStore()
            if sb_store.client is not None:
                self.idempotency_store = sb_store
            else:
                logger.warning("Supabase client not configured; falling back to SQLite idempotency store.")
                self.idempotency_store = SQLiteIdempotencyStore(
                    db_path=settings.sqlite_db_path,
                    ttl_seconds=settings.idempotency_ttl_seconds,
                    max_items=settings.idempotency_max_items,
                )
        elif settings.idempotency_backend == "sqlite":
            self.idempotency_store = SQLiteIdempotencyStore(
                db_path=settings.sqlite_db_path,
                ttl_seconds=settings.idempotency_ttl_seconds,
                max_items=settings.idempotency_max_items,
            )
        else:
            self.idempotency_store = InMemoryIdempotencyStore(
                ttl_seconds=settings.idempotency_ttl_seconds,
                max_items=settings.idempotency_max_items,
            )

        if settings.mock_mode and settings.idempotency_backend != "supabase" and len(self.idempotency_store.get_all()) == 0:
            self._seed_mock_data()

    def _seed_mock_data(self):
        """Seed realistic mock calls for the dashboard on startup."""
        from datetime import timedelta

        from src.schemas import (
            CallDirection,
            CallIntelligence,
            CallOutcome,
            LeadQuality,
            PrimaryObjection,
        )

        now = datetime.now()

        c1 = PipelineResponse(
            success=True, provider_call_id="SEED-001", idempotent_replay=False,
            intelligence=CallIntelligence(
                call_summary="Client inquired about enterprise pricing.",
                call_outcome=CallOutcome.FOLLOW_UP,
                lead_quality=LeadQuality.HOT,
                primary_objection=PrimaryObjection.PRICE,
                customer_intent="Pricing inquiry",
                next_action="Send proposal",
                follow_up_at=now + timedelta(days=1),
                agent_quality_notes="Great tone, slightly rushed.",
                review_flag=False,
            ),
            duration_seconds=145, direction=CallDirection.OUTBOUND, event_timestamp=now - timedelta(hours=2),
            agent_id="sarah.demo@example.com", matched_lead="Alice Johnson",
            frappe_call_log_id="CALL-LOG-MOCK1", frappe_task_id="TASK-MOCK1"
        )

        c2 = PipelineResponse(
            success=True, provider_call_id="SEED-002", idempotent_replay=False,
            intelligence=CallIntelligence(
                call_summary="Client not interested in our services at this time.",
                call_outcome=CallOutcome.NOT_INTERESTED,
                lead_quality=LeadQuality.COLD,
                primary_objection=PrimaryObjection.TIMING,
                customer_intent="Just browsing",
                next_action="None",
                follow_up_at=None,
                agent_quality_notes="Agent sounded unenthusiastic.",
                review_flag=True,
            ),
            duration_seconds=65, direction=CallDirection.OUTBOUND, event_timestamp=now - timedelta(hours=5),
            agent_id="john.demo@example.com", matched_lead="Bob Martinez",
            frappe_call_log_id="CALL-LOG-MOCK2"
        )

        c3 = PipelineResponse(
            success=True, provider_call_id="SEED-003", idempotent_replay=False,
            intelligence=CallIntelligence(
                call_summary="Product demo went well, client wants to move forward.",
                call_outcome=CallOutcome.INTERESTED,
                lead_quality=LeadQuality.WARM,
                primary_objection=PrimaryObjection.COMPETITOR,
                customer_intent="Demo feedback",
                next_action="Schedule onboarding",
                follow_up_at=now - timedelta(hours=1),
                agent_quality_notes="Perfect pacing and objection handling.",
                review_flag=False,
            ),
            duration_seconds=320, direction=CallDirection.INBOUND, event_timestamp=now - timedelta(days=1),
            agent_id="sarah.demo@example.com", matched_lead="Carol Smith",
            frappe_call_log_id="CALL-LOG-MOCK3"
        )

        self.idempotency_store.set("SEED-001", c1)
        self.idempotency_store.set("SEED-002", c2)
        self.idempotency_store.set("SEED-003", c3)

    def accept_call(self, event: TelephonyWebhookPayload, recording_storage_path: str | None = None) -> PipelineResponse:
        """Synchronously accept a call webhook, performing idempotency checks and saving initial state."""
        from src.schemas import ProcessingStatus

        call_id = event.provider_call_id
        if self.idempotency_store.has(call_id):
            cached = self.idempotency_store.get(call_id)
            if cached:
                if cached.processing_status == ProcessingStatus.FAILED:
                    logger.info(f"Retrying previously failed call {call_id}")
                    # Allow retry: we will return idempotent_replay=False
                    # The background task will skip completed steps.
                    response = cached.model_copy()
                    response.processing_status = ProcessingStatus.RECEIVED
                    response.error_message = None
                    response.idempotent_replay = False
                    self.idempotency_store.set(call_id, response)
                    return response
                else:
                    replay = cached.model_copy()
                    replay.idempotent_replay = True
                    replay.message = "Call accepted (from idempotency cache)"
                    return replay

        # Initialize new call state
        response = PipelineResponse(
            success=True,
            provider_call_id=call_id,
            direction=event.direction,
            duration_seconds=event.duration_seconds,
            event_timestamp=event.event_timestamp or datetime.now(UTC),
            agent_id=event.agent_id,
            processing_status=ProcessingStatus.RECEIVED,
            message="Call accepted",
            event_payload=event.model_dump(mode="json"),
            recording_storage_path=recording_storage_path,
            matched_lead=event.lead_name or (f"Contact ({event.lead_id})" if event.lead_id else None),
        )
        self.idempotency_store.set(call_id, response)
        return response

    async def process_call(
        self,
        event: TelephonyWebhookPayload,
        raw_audio: bytes | None = None,
        audio_filename: str | None = None,
    ) -> PipelineResponse:
        """Helper for tests and backwards compatibility to run the full pipeline synchronously."""
        from src.observability import AppError, ErrorClassification
        from src.schemas import ProcessingStatus

        res = self.accept_call(event)
        if res.idempotent_replay:
            return res

        await self.process_call_background(event, raw_audio, audio_filename)
        final_res = self.idempotency_store.get(event.provider_call_id)

        if final_res and final_res.processing_status == ProcessingStatus.FAILED:
            error_msg = final_res.error_message or "Unknown failure"
            if "STT transcription failed" in error_msg:
                raise AppError(message=error_msg, error_class=ErrorClassification.STT_ERROR, status_code=502)
            if "LLM extraction failed" in error_msg:
                raise AppError(message=error_msg.replace("LLM extraction failed", "LLM analysis failed"), error_class=ErrorClassification.LLM_ERROR, status_code=502)
            if "CRM Call Log creation failed" in error_msg:
                raise AppError(message=error_msg, error_class=ErrorClassification.CRM_ERROR, status_code=502)
            raise AppError(message=error_msg, error_class=ErrorClassification.UNEXPECTED_ERROR, status_code=500)

        assert final_res is not None
        return final_res

    def _handle_background_failure(self, call_id: str, cached, exc: Exception, step_name: str) -> None:
        from datetime import datetime, timedelta
        from src.schemas import ProcessingStatus
        import logging
        logger = logging.getLogger('CallIntelligencePipeline')

        error_msg = f"{step_name} failed: {exc}"
        cached.success = False
        cached.error_message = error_msg

        from src.config import get_settings
        max_retries = 3 if get_settings().idempotency_backend == "supabase" else 0
        if cached.retry_count < max_retries:
            cached.retry_count += 1
            cached.processing_status = ProcessingStatus.RECEIVED
            delay_minutes = 1 if cached.retry_count == 1 else (5 if cached.retry_count == 2 else 15)
            cached.next_retry_at = datetime.now() + timedelta(minutes=delay_minutes)
            logger.warning(f"[{call_id}] {error_msg}. Will retry {cached.retry_count}/{max_retries} at {cached.next_retry_at}")
        else:
            cached.processing_status = ProcessingStatus.FAILED
            logger.error(f"[{call_id}] {error_msg}. Max retries exceeded.")

        self.idempotency_store.set(call_id, cached)

    async def process_call_background(
        self,
        event: TelephonyWebhookPayload,
        raw_audio: bytes | None = None,
        audio_filename: str | None = None,
        worker_id: str | None = None,
        force_reprocess: bool = False,
    ) -> None:
        """Process a call event asynchronously in the background."""
        from src.observability import (
            metrics,
            request_id_ctx,
        )
        from src.schemas import ProcessingStatus

        start_time = time.time()
        call_id = event.provider_call_id
        req_id = request_id_ctx.get("-")

        cached = self.idempotency_store.get(call_id)
        if not cached:
            logger.error(f"Cannot process call {call_id}: not found in idempotency store.")
            return

        # Double check it isn't already processed or processing (Concurrency protection)
        is_mock_data = bool(cached.transcript and "are you interested in our course" in cached.transcript.lower())
        if cached.processing_status == ProcessingStatus.COMPLETED and not is_mock_data and not force_reprocess:
            logger.info(f"Skipping background processing for {call_id}: status is {cached.processing_status}")
            return

        if cached.processing_status == ProcessingStatus.PROCESSING and not is_mock_data and not force_reprocess:
            if worker_id and cached.worker_id == worker_id:
                # The current worker owns the job, we can proceed
                pass
            elif not worker_id and not cached.worker_id:
                # Direct unit test path where no worker is involved
                pass
            else:
                logger.info(f"Skipping background processing for {call_id}: status is {cached.processing_status} owned by {cached.worker_id}")
                return

        # Update state to PROCESSING
        cached.processing_status = ProcessingStatus.PROCESSING
        self.idempotency_store.set(call_id, cached)

        try:
            # 2. Speech-to-Text Transcription
            stt_status = "skipped"
            if not cached.transcript or is_mock_data or force_reprocess:
                stt_status = "pending"
                try:
                    # If we don't have raw_audio but we have a storage path in Supabase, fetch it.
                    if not raw_audio and cached.recording_storage_path:
                        from src.config import get_settings
                        if get_settings().idempotency_backend == "supabase":
                            from src.supabase_store import download_audio_from_supabase
                            fetched_audio = download_audio_from_supabase(cached.recording_storage_path)
                            if fetched_audio:
                                raw_audio = fetched_audio

                    audio_source = raw_audio or event.recording_url or "mock_call.wav"

                    if not audio_filename and cached.recording_storage_path:
                        audio_filename = cached.recording_storage_path.split("/")[-1]

                    stt_meta = {
                        "call_id": call_id,
                        "audio_filename": audio_filename,
                        "telephony_provider": event.telephony_provider,
                    }
                    transcript = await self.stt_service.transcribe(
                        audio_source=audio_source,
                        filename=audio_filename,
                        metadata=stt_meta,
                    )
                    if not transcript or not transcript.strip():
                        raise ValueError("Transcription result is empty.")
                    stt_status = "success"
                    cached.transcript = transcript
                except Exception as exc:
                    stt_status = "failed"
                    metrics.increment("stt_failures_total")
                    metrics.record_error("stt_error")
                    logger.error(
                        f"STT transcription failed for call '{call_id}': {exc}",
                        extra={"call_id": call_id, "request_id": req_id, "error_class": "stt_error"},
                    )
                    self._handle_background_failure(call_id, cached, exc, "STT transcription")
                    return
            else:
                transcript = cached.transcript

            # 3. LLM Structured Intelligence Analysis
            llm_status = "skipped"
            if not cached.intelligence or is_mock_data or force_reprocess:
                llm_status = "pending"
                try:
                    ai_meta = {
                        "call_id": call_id,
                        "provider": event.telephony_provider,
                        "event_timestamp": event.event_timestamp.isoformat() if event.event_timestamp else datetime.now().isoformat(),
                    }
                    intelligence = await self.ai_service.analyze_call(
                        transcript=transcript,
                        metadata=ai_meta,
                    )
                    llm_status = "success"
                    cached.intelligence = intelligence
                except Exception as exc:
                    llm_status = "failed"
                    metrics.increment("llm_failures_total")
                    metrics.record_error("llm_error")
                    logger.error(
                        f"LLM extraction failed for call '{call_id}': {exc}",
                        extra={"call_id": call_id, "request_id": req_id, "error_class": "llm_error"},
                    )
                    self._handle_background_failure(call_id, cached, exc, "LLM extraction")
                    return
            else:
                intelligence = cached.intelligence

            # 4. Match Lead in Frappe CRM
            target_phone = event.to_number if event.direction == CallDirection.OUTBOUND else event.from_number
            lead_id = None
            lead_agent = event.agent_id

            if not cached.matched_lead:
                lead = await self.frappe_client.lookup_lead_by_phone(target_phone)
                lead_id = lead.get("name") if lead else None
                lead_agent = lead.get("assigned_to") if lead else event.agent_id

                if lead:
                    cached.matched_lead = f"{lead.get('lead_name')} ({lead_id})"
            else:
                # Extract lead ID from cached string like "John Doe (CRM-LEAD-123)"
                import re
                match = re.search(r"\((CRM-LEAD-[\w-]+)\)", cached.matched_lead)
                if match:
                    lead_id = match.group(1)

            # 5. Create Frappe CRM Call Log
            crm_status = "skipped"
            call_log_id = cached.frappe_call_log_id
            if not call_log_id:
                crm_status = "pending"
                try:
                    call_log = await self.frappe_client.create_call_log(
                        call_event=event,
                        transcript=transcript,
                        intelligence=intelligence,
                        lead_id=lead_id,
                    )
                    call_log_id = call_log.get("name", "CALL-LOG-UNKNOWN")
                    crm_status = "success"
                    cached.frappe_call_log_id = call_log_id
                except Exception as exc:
                    crm_status = "failed"
                    metrics.increment("crm_failures_total")
                    metrics.record_error("crm_error")
                    logger.error(
                        f"CRM Call Log creation failed for call '{call_id}': {exc}",
                        extra={"call_id": call_id, "lead_id": lead_id, "request_id": req_id, "error_class": "crm_error"},
                    )
                    self._handle_background_failure(call_id, cached, exc, "CRM Call Log creation")
                    return

            # 6. Auto-create Follow-up Task in Frappe CRM if requested
            task_id = cached.frappe_task_id
            task_status = "skipped"
            if not task_id and intelligence.follow_up_required:
                try:
                    task = await self.frappe_client.create_followup_task(
                        call_log_id=call_log_id,
                        lead_id=lead_id,
                        intelligence=intelligence,
                        assigned_to=lead_agent,
                    )
                    if task and task.get("name") is not None:
                        task_id = str(task.get("name"))
                        task_status = "success"
                        cached.frappe_task_id = task_id
                    else:
                        task_status = "failed"
                        metrics.increment("followup_task_failures_total")
                except Exception as exc:
                    task_status = "failed"
                    metrics.increment("followup_task_failures_total")
                    logger.error(
                        f"Unexpected error during task creation for call '{call_id}': {exc}",
                        extra={"call_id": call_id, "lead_id": lead_id, "request_id": req_id},
                    )
                    task_id = None

            # 7. Update Lead stage & quality
            if lead_id:
                try:
                    await self.frappe_client.update_lead_status(lead_id=lead_id, intelligence=intelligence)
                except Exception as exc:
                    logger.warning(
                        f"Non-fatal error updating Lead stage for '{lead_id}': {exc}",
                        extra={"call_id": call_id, "lead_id": lead_id, "request_id": req_id},
                    )

            # 8. Assemble response and save to Idempotency Store
            duration_ms = round((time.time() - start_time) * 1000, 2)
            metrics.record_duration(duration_ms)
            metrics.increment("pipeline_success_total")

            from src.config import get_settings
            if not cached.recording_storage_path and get_settings().idempotency_backend == "supabase":
                from src.supabase_store import upload_audio_to_supabase
                recording_storage_path = upload_audio_to_supabase(
                    call_id=call_id,
                    raw_audio=raw_audio,
                    recording_url=event.recording_url,
                    filename=audio_filename
                )
                cached.recording_storage_path = recording_storage_path

            cached.processing_status = ProcessingStatus.COMPLETED
            cached.message = "Call processed, analyzed, and synchronized with Frappe CRM"
            self.idempotency_store.set(call_id, cached)

            logger.info(
                f"Successfully processed call '{call_id}' in {duration_ms}ms",
                extra={
                    "call_id": call_id,
                    "lead_id": lead_id,
                    "frappe_call_log_id": call_log_id,
                    "frappe_task_id": task_id,
                    "duration_ms": duration_ms,
                    "stt_status": stt_status,
                    "llm_status": llm_status,
                    "crm_status": crm_status,
                    "task_status": task_status,
                    "pipeline_status": "success",
                },
            )

        except Exception as global_exc:
            # Catch-all to prevent calls getting stuck in PROCESSING state forever
            logger.error(
                f"Global unexpected failure during background processing for '{call_id}': {global_exc}",
                extra={"call_id": call_id, "request_id": req_id, "error_class": "unexpected_error"},
                exc_info=True
            )
            self._handle_background_failure(call_id, cached, global_exc, "Unexpected")


# Global pipeline singleton
_pipeline_instance: CallIntelligencePipeline | None = None


def get_pipeline() -> CallIntelligencePipeline:
    global _pipeline_instance
    if _pipeline_instance is None:
        _pipeline_instance = CallIntelligencePipeline()
    return _pipeline_instance
