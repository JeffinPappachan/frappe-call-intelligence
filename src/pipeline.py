from abc import ABC, abstractmethod
from datetime import datetime
import logging
from typing import Dict, Optional, List

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

    @abstractmethod
    def get_all(self) -> List[PipelineResponse]:
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

    def get_all(self) -> List[PipelineResponse]:
        return list(self._cache.values())

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

        from src.config import get_settings
        if get_settings().mock_mode and len(self.idempotency_store.get_all()) == 0:
            self._seed_mock_data()

    def _seed_mock_data(self):
        """Seed realistic mock calls for the dashboard on startup."""
        from datetime import timedelta
        from src.schemas import CallIntelligence, CallOutcome, LeadQuality, PrimaryObjection, CallDirection

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
            duration_seconds=event.duration_seconds,
            direction=event.direction,
            event_timestamp=event.event_timestamp or datetime.now(),
            agent_id=event.agent_id,
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
