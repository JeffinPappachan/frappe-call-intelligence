from abc import ABC, abstractmethod
import logging
from typing import Dict, Optional

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
    def get(self, key: str) -> Optional[PipelineResponse]:
        pass

    @abstractmethod
    def set(self, key: str, value: PipelineResponse) -> None:
        pass


class InMemoryIdempotencyStore(IdempotencyStore):
    """In-memory idempotency cache designed to be easily swapped with SQLite or Redis."""

    def __init__(self):
        self._cache: Dict[str, PipelineResponse] = {}

    def has(self, key: str) -> bool:
        return key in self._cache

    def get(self, key: str) -> Optional[PipelineResponse]:
        return self._cache.get(key)

    def set(self, key: str, value: PipelineResponse) -> None:
        self._cache[key] = value

    def clear(self) -> None:
        self._cache.clear()


class CallIntelligencePipeline:
    """Core orchestration pipeline for Test Work 01.

    Workflow:
    Webhook / Audio Input -> Idempotency Check -> STT -> Structured LLM Extraction ->
    Frappe CRM Call Log -> Frappe Follow-up Task -> Lead Update -> Result Cache.
    """

    def __init__(
        self,
        stt_service: Optional[STTService] = None,
        ai_service: Optional[AIService] = None,
        frappe_client: Optional[FrappeCRMClient] = None,
        idempotency_store: Optional[IdempotencyStore] = None,
    ):
        self.stt_service = stt_service or get_stt_service()
        self.ai_service = ai_service or get_ai_service()
        self.frappe_client = frappe_client or get_frappe_client()
        self.idempotency_store = idempotency_store or InMemoryIdempotencyStore()

    async def process_call(
        self,
        event: TelephonyWebhookPayload,
        raw_audio: Optional[bytes] = None,
        audio_filename: Optional[str] = None,
    ) -> PipelineResponse:
        """Process a call event end-to-end with idempotency guarantees."""
        call_id = event.provider_call_id

        # 1. Idempotency Check
        if self.idempotency_store.has(call_id):
            logger.info(f"[Pipeline] Duplicate webhook received for call '{call_id}'. Returning cached result.")
            cached = self.idempotency_store.get(call_id)
            if cached:
                # Return a copy marked as an idempotent replay
                replay_response = cached.model_copy()
                replay_response.idempotent_replay = True
                replay_response.message = "Call already processed; returned from idempotency cache"
                return replay_response

        # 2. Speech-to-Text Transcription
        audio_source = raw_audio or event.recording_url or "mock_call.wav"
        transcript = await self.stt_service.transcribe(audio_source=audio_source, filename=audio_filename)

        # 3. LLM Structured Intelligence Analysis
        intelligence = await self.ai_service.analyze_call(
            transcript=transcript,
            metadata={"call_id": call_id, "provider": event.telephony_provider},
        )

        # 4. Match Lead in Frappe CRM
        target_phone = event.to_number if event.direction == CallDirection.OUTBOUND else event.from_number
        lead = await self.frappe_client.lookup_lead_by_phone(target_phone)
        lead_id = lead.get("name") if lead else None
        lead_agent = lead.get("assigned_to") if lead else event.agent_id

        # 5. Create Frappe CRM Call Log
        call_log = await self.frappe_client.create_call_log(
            call_event=event,
            transcript=transcript,
            intelligence=intelligence,
            lead_id=lead_id,
        )
        call_log_id = call_log.get("name", "CALL-LOG-UNKNOWN")

        # 6. Auto-create Follow-up Task in Frappe CRM if requested
        task_id = None
        task = await self.frappe_client.create_followup_task(
            call_log_id=call_log_id,
            lead_id=lead_id,
            intelligence=intelligence,
            assigned_to=lead_agent,
        )
        if task:
            task_id = task.get("name")

        # 7. Update Lead stage & quality
        if lead_id:
            await self.frappe_client.update_lead_status(lead_id=lead_id, intelligence=intelligence)

        # 8. Assemble response and save to Idempotency Store
        response = PipelineResponse(
            success=True,
            provider_call_id=call_id,
            idempotent_replay=False,
            transcript=transcript,
            intelligence=intelligence,
            matched_lead=f"{lead.get('lead_name')} ({lead_id})" if lead else None,
            frappe_call_log_id=call_log_id,
            frappe_task_id=task_id,
            message="Call processed, analyzed, and synchronized with Frappe CRM",
        )

        self.idempotency_store.set(call_id, response)
        return response


# Global pipeline singleton
_pipeline_instance: Optional[CallIntelligencePipeline] = None


def get_pipeline() -> CallIntelligencePipeline:
    global _pipeline_instance
    if _pipeline_instance is None:
        _pipeline_instance = CallIntelligencePipeline()
    return _pipeline_instance
