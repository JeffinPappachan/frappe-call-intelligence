import asyncio
import logging
import signal
import uuid
from datetime import datetime

from src.observability import request_id_ctx
from src.pipeline import get_pipeline
from src.schemas import TelephonyWebhookPayload, CallDirection, CallStatus
from src.config import get_settings

logger = logging.getLogger("worker")

# Global flag for graceful shutdown
_shutdown_requested = False


def _signal_handler(signum, frame):
    global _shutdown_requested
    logger.info("Shutdown signal received. Finishing current job...")
    _shutdown_requested = True


async def requeue_stuck_jobs(pipeline):
    """Periodically resets jobs stuck in PROCESSING back to RECEIVED after a timeout."""
    if not pipeline.idempotency_store.client:
        return

    try:
        # Calls stuck in PROCESSING for more than 15 minutes
        pipeline.idempotency_store.client.table("calls")\
            .update({"processing_status": "received", "worker_id": None})\
            .eq("processing_status", "processing")\
            .lt("updated_at", (datetime.now().timestamp() - 900) * 1000)\
            .execute()

        # Note: Supabase doesn't support easy complex time filters in REST sometimes,
        # so this is a simplified logic. In a real system, a separate heartbeat or
        # an RPC is better. We'll skip complex date filters here since it's just an example.
    except Exception as exc:
        logger.warning(f"Failed to requeue stuck jobs: {exc}")


async def run_worker(register_signals: bool = True):
    settings = get_settings()

    if settings.idempotency_backend != "supabase":
        logger.info("Worker skipped: IDEMPOTENCY_BACKEND is not supabase")
        return

    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    logger.info(f"Starting job queue worker: {worker_id}")

    # Register signal handlers for graceful shutdown if requested and in main thread
    if register_signals:
        try:
            signal.signal(signal.SIGINT, _signal_handler)
            signal.signal(signal.SIGTERM, _signal_handler)
        except Exception:
            pass

    pipeline = get_pipeline()

    # Wait briefly for DB to be available
    await asyncio.sleep(2)

    empty_polls = 0

    while not _shutdown_requested:
        try:
            # 1. Claim a job
            cached_job = pipeline.idempotency_store.claim_next_call(worker_id)

            if not cached_job:
                # Exponential backoff for polling if queue is empty (max 10s)
                empty_polls = min(empty_polls + 1, 10)
                await asyncio.sleep(min(empty_polls, 10))
                continue

            # We found a job! Reset poll counter
            empty_polls = 0

            call_id = cached_job.provider_call_id
            request_id_ctx.set(f"job-{call_id}")
            logger.info(f"[{worker_id}] Claimed job {call_id}")

            # 2. Reconstruct event payload
            event_payload = cached_job.event_payload or {}

            # Reconstruct fallback if payload is missing
            try:
                event = TelephonyWebhookPayload(**event_payload)
            except Exception:
                # Provide a synthetic event for legacy rows if payload wasn't saved
                event = TelephonyWebhookPayload(
                    provider_call_id=call_id,
                    telephony_provider="unknown",
                    from_number="+00000000000",
                    to_number="+00000000000",
                    direction=cached_job.direction or CallDirection.OUTBOUND,
                    call_status=CallStatus.COMPLETED,
                    duration_seconds=cached_job.duration_seconds or 60,
                    agent_id=cached_job.agent_id,
                    event_timestamp=cached_job.event_timestamp or datetime.now()
                )

            # 3. Process the job
            # We don't need to pass raw_audio/filename for standard webhooks
            # If it's a manual upload, audio is in Supabase storage and handled by pipeline
            await pipeline.process_call_background(event=event, worker_id=worker_id)

            # The pipeline internally sets COMPLETED or FAILED, but if it crashes
            # our global try/catch in process_call_background handles it.

        except Exception as exc:
            logger.error(f"[{worker_id}] Critical error in polling loop: {exc}", exc_info=True)
            await asyncio.sleep(5)

    logger.info(f"[{worker_id}] Shutting down gracefully.")


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
