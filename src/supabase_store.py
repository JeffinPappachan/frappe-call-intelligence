import logging
from src.schemas import PipelineResponse, CallIntelligence, CallDirection
from src.config import get_supabase_client
from src.pipeline import IdempotencyStore
from datetime import datetime

logger = logging.getLogger(__name__)

class SupabaseIdempotencyStore(IdempotencyStore):
    """Supabase persistent idempotency store."""
    def __init__(self):
        self.client = get_supabase_client()
        if not self.client:
            logger.warning("SupabaseIdempotencyStore initialized without Supabase client configuration.")

    def has(self, key: str) -> bool:
        if not self.client:
            return False
        try:
            res = self.client.table("calls").select("id").eq("provider_call_id", key).execute()
            return len(res.data) > 0
        except Exception as exc:
            logger.error(f"[SupabaseIdempotencyStore] has() failed for {key}: {exc}")
            return False

    def get(self, key: str) -> PipelineResponse | None:
        if not self.client:
            return None
        try:
            call_res = self.client.table("calls").select("*").eq("provider_call_id", key).execute()
            if not call_res.data:
                return None
            call = call_res.data[0]

            intel_res = self.client.table("call_intelligence").select("*").eq("call_id", call["id"]).execute()

            intelligence = None
            if intel_res.data:
                intel = intel_res.data[0]
                intelligence = CallIntelligence(
                    call_summary=intel.get("call_summary", ""),
                    call_outcome=intel.get("call_outcome", ""),
                    lead_quality=intel.get("lead_quality", ""),
                    primary_objection=intel.get("primary_objection", ""),
                    customer_intent=intel.get("customer_intent", ""),
                    key_points=intel.get("key_points", []),
                    next_action=intel.get("next_action", ""),
                    follow_up_required=intel.get("follow_up_required", False),
                    follow_up_date=datetime.fromisoformat(intel["follow_up_date"]) if intel.get("follow_up_date") else None,
                    follow_up_notes=intel.get("follow_up_notes"),
                    objections=intel.get("objections", []),
                    recommended_action=intel.get("recommended_action"),
                    agent_quality_notes=intel.get("agent_quality_notes", ""),
                    review_flag=intel.get("review_flag", False),
                    # Ensure backward compatibility with follow_up_at
                    follow_up_at=datetime.fromisoformat(intel["follow_up_at"]) if intel.get("follow_up_at") else None,
                )

            # Fallback for event_timestamp
            ts = call.get("event_timestamp") or call.get("created_at")
            event_ts = datetime.fromisoformat(ts) if ts else None

            # Map the db format to direction Enum safely
            direction_str = call.get("direction", "outbound")
            try:
                direction_enum = CallDirection(direction_str)
            except ValueError:
                direction_enum = CallDirection.OUTBOUND

            # Map processing status
            from src.schemas import ProcessingStatus
            status_str = call.get("processing_status", "completed")
            try:
                processing_status_enum = ProcessingStatus(status_str)
            except ValueError:
                processing_status_enum = ProcessingStatus.COMPLETED

            return PipelineResponse(
                success=True if processing_status_enum == ProcessingStatus.COMPLETED else (False if processing_status_enum == ProcessingStatus.FAILED else True),
                provider_call_id=call["provider_call_id"],
                idempotent_replay=True,
                transcript=call.get("transcript"),
                intelligence=intelligence,
                matched_lead=call.get("matched_lead_id"),
                frappe_call_log_id=call.get("frappe_call_log_id"),
                frappe_task_id=call.get("frappe_task_id"),
                message="Call successfully processed (from Supabase Cache)",
                duration_seconds=call.get("duration_seconds", 0),
                direction=direction_enum,
                event_timestamp=event_ts,
                agent_id=call.get("agent_id"),
                recording_storage_path=call.get("recording_storage_path"),
                processing_status=processing_status_enum,
                error_message=call.get("error_message"),
                worker_id=call.get("worker_id"),
                retry_count=call.get("retry_count", 0),
                next_retry_at=datetime.fromisoformat(call["next_retry_at"]) if call.get("next_retry_at") else None,
                event_payload=call.get("event_payload")
            )
        except Exception as exc:
            logger.error(f"[SupabaseIdempotencyStore] get() failed for {key}: {exc}")
            return None

    def set(self, key: str, value: PipelineResponse) -> None:
        if not self.client:
            return
        try:
            # Upsert into calls table
            call_data = {
                "provider_call_id": value.provider_call_id,
                "direction": value.direction.value if value.direction else None,
                "duration_seconds": value.duration_seconds,
                "agent_id": value.agent_id,
                "matched_lead_id": value.matched_lead,
                "frappe_call_log_id": value.frappe_call_log_id,
                "frappe_task_id": value.frappe_task_id,
                "transcript": value.transcript,
                "event_timestamp": value.event_timestamp.isoformat() if value.event_timestamp else None,
                "recording_storage_path": value.recording_storage_path,
                "processing_status": value.processing_status.value if value.processing_status else "completed",
                "error_message": value.error_message,
                "worker_id": value.worker_id,
                "retry_count": value.retry_count,
                "next_retry_at": value.next_retry_at.isoformat() if value.next_retry_at else None,
                "event_payload": value.event_payload,
            }
            # Instead of standard INSERT, handle potential conflicts if another worker just inserted
            call_res = self.client.table("calls").upsert(call_data, on_conflict="provider_call_id").execute()
            if not call_res.data:
                logger.error(f"[SupabaseIdempotencyStore] Failed to insert call for {key}: {call_res}")
                return

            call_id = call_res.data[0]["id"]

            if value.intelligence:
                intel = value.intelligence
                intel_data = {
                    "call_id": call_id,
                    "call_summary": intel.call_summary,
                    "call_outcome": intel.call_outcome.value if hasattr(intel.call_outcome, "value") else intel.call_outcome,
                    "lead_quality": intel.lead_quality.value if hasattr(intel.lead_quality, "value") else intel.lead_quality,
                    "customer_intent": intel.customer_intent,
                    "primary_objection": intel.primary_objection.value if hasattr(intel.primary_objection, "value") else intel.primary_objection,
                    "objections": intel.objections,
                    "key_points": intel.key_points,
                    "next_action": intel.next_action,
                    "recommended_action": intel.recommended_action,
                    "follow_up_required": intel.follow_up_required,
                    "follow_up_date": intel.follow_up_date.isoformat() if intel.follow_up_date else None,
                    "follow_up_at": intel.follow_up_at.isoformat() if intel.follow_up_at else None,
                    "follow_up_notes": intel.follow_up_notes,
                    "agent_quality_notes": intel.agent_quality_notes,
                    "review_flag": intel.review_flag,
                }
                self.client.table("call_intelligence").upsert(intel_data).execute()
        except Exception as exc:
            logger.error(f"[SupabaseIdempotencyStore] set() failed for {key}: {exc}")

    def get_all(self, limit: int = 50) -> list[PipelineResponse]:
        if not self.client:
            return []
        try:
            res = self.client.table("calls").select("*, call_intelligence(*)").order("event_timestamp", desc=True).limit(limit).execute()
            if not res.data:
                return []

            results = []
            for call in res.data:
                intel_list = call.get("call_intelligence", [])

                # In Supabase 1:1, it can be returned as dict or list depending on the schema relationship
                if isinstance(intel_list, dict):
                    intel_data = intel_list
                elif isinstance(intel_list, list) and len(intel_list) > 0:
                    intel_data = intel_list[0]
                else:
                    intel_data = None

                intelligence = None
                if intel_data:
                    intelligence = CallIntelligence(
                        call_summary=intel_data.get("call_summary", ""),
                        call_outcome=intel_data.get("call_outcome", ""),
                        lead_quality=intel_data.get("lead_quality", ""),
                        primary_objection=intel_data.get("primary_objection", ""),
                        customer_intent=intel_data.get("customer_intent", ""),
                        key_points=intel_data.get("key_points", []),
                        next_action=intel_data.get("next_action", ""),
                        follow_up_required=intel_data.get("follow_up_required", False),
                        follow_up_date=datetime.fromisoformat(intel_data["follow_up_date"]) if intel_data.get("follow_up_date") else None,
                        follow_up_notes=intel_data.get("follow_up_notes"),
                        objections=intel_data.get("objections", []),
                        recommended_action=intel_data.get("recommended_action"),
                        agent_quality_notes=intel_data.get("agent_quality_notes", ""),
                        review_flag=intel_data.get("review_flag", False),
                        follow_up_at=datetime.fromisoformat(intel_data["follow_up_at"]) if intel_data.get("follow_up_at") else None,
                    )

                ts = call.get("event_timestamp") or call.get("created_at")
                event_ts = datetime.fromisoformat(ts) if ts else None

                direction_str = call.get("direction", "outbound")
                try:
                    direction_enum = CallDirection(direction_str)
                except ValueError:
                    direction_enum = CallDirection.OUTBOUND

                # Map processing status
                from src.schemas import ProcessingStatus
                status_str = call.get("processing_status", "completed")
                try:
                    processing_status_enum = ProcessingStatus(status_str)
                except ValueError:
                    processing_status_enum = ProcessingStatus.COMPLETED

                resp = PipelineResponse(
                    success=True if processing_status_enum == ProcessingStatus.COMPLETED else (False if processing_status_enum == ProcessingStatus.FAILED else True),
                    provider_call_id=call["provider_call_id"],
                    idempotent_replay=True,
                    transcript=call.get("transcript"),
                    intelligence=intelligence,
                    matched_lead=call.get("matched_lead_id"),
                    frappe_call_log_id=call.get("frappe_call_log_id"),
                    frappe_task_id=call.get("frappe_task_id"),
                    message="Call fetched from Supabase",
                    duration_seconds=call.get("duration_seconds", 0),
                    direction=direction_enum,
                    event_timestamp=event_ts,
                    agent_id=call.get("agent_id"),
                    recording_storage_path=call.get("recording_storage_path"),
                    processing_status=processing_status_enum,
                    error_message=call.get("error_message"),
                    worker_id=call.get("worker_id"),
                    retry_count=call.get("retry_count", 0),
                    next_retry_at=datetime.fromisoformat(call["next_retry_at"]) if call.get("next_retry_at") else None,
                    event_payload=call.get("event_payload")
                )
                results.append(resp)
            return results
        except Exception as exc:
            logger.error(f"[SupabaseIdempotencyStore] get_all() failed: {exc}")
            return []

    def claim_next_call(self, worker_id: str) -> PipelineResponse | None:
        if not self.client:
            return None
        try:
            res = self.client.rpc("claim_next_call", {"p_worker_id": worker_id}).execute()
            if not res.data:
                return None
            
            # The RPC returns a JSON object. We just extract provider_call_id and use get()
            claimed_data = res.data
            provider_call_id = claimed_data.get("provider_call_id")
            if not provider_call_id:
                return None
                
            return self.get(provider_call_id)
        except Exception as exc:
            logger.error(f"[SupabaseIdempotencyStore] claim_next_call() failed: {exc}")
            return None

    def clear(self) -> None:
        pass

def upload_audio_to_supabase(call_id: str, raw_audio: bytes | None, recording_url: str | None, filename: str | None) -> str | None:
    """Uploads audio bytes to Supabase storage or just returns the URL if it's already a URL."""
    from src.config import get_supabase_client
    import httpx

    client = get_supabase_client()
    if not client:
        return None

    audio_bytes = raw_audio
    name = filename or f"{call_id}.wav"

    if not audio_bytes and recording_url:
        try:
            resp = httpx.get(recording_url, timeout=30.0)
            resp.raise_for_status()
            audio_bytes = resp.content
            # Extract extension from URL if possible
            if "." in recording_url.split("/")[-1]:
                name = f"{call_id}_{recording_url.split('/')[-1]}"
        except Exception as exc:
            logger.error(f"[Supabase Storage] Failed to download audio from {recording_url}: {exc}")
            return None

    if not audio_bytes:
        return None

    try:
        path = f"{call_id}/{name}"
        # Determine content type
        ext = name.split(".")[-1].lower() if "." in name else "wav"
        content_type = f"audio/{ext}" if ext in ["wav", "mp3", "m4a", "ogg", "webm", "flac"] else "audio/wav"

        client.storage.from_("recordings").upload(
            file=audio_bytes,
            path=path,
            file_options={"content-type": content_type}
        )
        # Return the path in the bucket
        return path
    except Exception as exc:
        logger.error(f"[Supabase Storage] Failed to upload audio for {call_id}: {exc}")
        return None
