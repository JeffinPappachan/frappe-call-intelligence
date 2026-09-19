import json
import logging
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from datetime import datetime

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
            self.idempotency_store = SupabaseIdempotencyStore()
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

    def accept_call(self, event: TelephonyWebhookPayload) -> PipelineResponse:
        """Synchronously accept a call webhook, performing idempotency checks and saving initial state."""
        from src.schemas import ProcessingStatus
        
        call_id = event.provider_call_id
        if self.idempotency_store.has(call_id):
            cached = self.idempotency_store.get(call_id)
            if cached:
                replay = cached.model_copy()
                replay.idempotent_replay = True
                replay.message = "Call accepted (from idempotency cache)"
                return replay
                
        # Initialize new call state
        response = PipelineResponse(
            success=True,
            provider_call_id=call_id,
            idempotent_replay=False,
            processing_status=ProcessingStatus.RECEIVED,
            message="Call accepted for processing",
            duration_seconds=event.duration_seconds,
            direction=event.direction,
            event_timestamp=event.event_timestamp or datetime.now(),
            agent_id=event.agent_id,
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
            if "STT transcription failed" in str(final_res.error_message):
                raise AppError(message=final_res.error_message, error_class=ErrorClassification.STT_ERROR, status_code=502)
            if "LLM extraction failed" in str(final_res.error_message):
                raise AppError(message=final_res.error_message.replace("LLM extraction failed", "LLM analysis failed"), error_class=ErrorClassification.LLM_ERROR, status_code=502)
            if "CRM Call Log creation failed" in str(final_res.error_message):
                raise AppError(message=final_res.error_message, error_class=ErrorClassification.CRM_ERROR, status_code=502)
            raise AppError(message=final_res.error_message or "Unknown failure", error_class=ErrorClassification.UNKNOWN_ERROR, status_code=500)
            
        return final_res

    async def process_call_background(
        self,
        event: TelephonyWebhookPayload,
        raw_audio: bytes | None = None,
        audio_filename: str | None = None,
    ) -> None:
        """Process a call event asynchronously in the background."""
        from src.observability import (
            AppError,
            ErrorClassification,
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

        # Double check it isn't already processed
        if cached.processing_status in [ProcessingStatus.COMPLETED, ProcessingStatus.FAILED]:
            logger.info(f"Skipping background processing for {call_id}: status is {cached.processing_status}")
            return

        # Update state to PROCESSING
        cached.processing_status = ProcessingStatus.PROCESSING
        self.idempotency_store.set(call_id, cached)
        
        # 2. Speech-to-Text Transcription
        stt_status = "pending"
        try:
            audio_source = raw_audio or event.recording_url or "mock_call.wav"
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
            cached.processing_status = ProcessingStatus.FAILED
            cached.success = False
            cached.error_message = f"STT transcription failed: {exc}"
            self.idempotency_store.set(call_id, cached)
            return

        # 3. LLM Structured Intelligence Analysis
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
            cached.processing_status = ProcessingStatus.FAILED
            cached.success = False
            cached.error_message = f"LLM extraction failed: {exc}"
            self.idempotency_store.set(call_id, cached)
            return

        # 4. Match Lead in Frappe CRM
        target_phone = event.to_number if event.direction == CallDirection.OUTBOUND else event.from_number
        lead = await self.frappe_client.lookup_lead_by_phone(target_phone)
        lead_id = lead.get("name") if lead else None
        lead_agent = lead.get("assigned_to") if lead else event.agent_id
        
        if lead:
            cached.matched_lead = f"{lead.get('lead_name')} ({lead_id})"

        # 5. Create Frappe CRM Call Log
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
            cached.processing_status = ProcessingStatus.FAILED
            cached.success = False
            cached.error_message = f"CRM Call Log creation failed: {exc}"
            self.idempotency_store.set(call_id, cached)
            return

        # 6. Auto-create Follow-up Task in Frappe CRM if requested
        task_id = None
        task_status = "skipped"
        if intelligence.follow_up_required:
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
        
        recording_storage_path = None
        from src.config import get_settings
        if get_settings().idempotency_backend == "supabase":
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


# Global pipeline singleton
_pipeline_instance: CallIntelligencePipeline | None = None


def get_pipeline() -> CallIntelligencePipeline:
    global _pipeline_instance
    if _pipeline_instance is None:
        _pipeline_instance = CallIntelligencePipeline()
    return _pipeline_instance
