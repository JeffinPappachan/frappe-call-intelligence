import io
import time
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport

from src.frappe_client import FrappeCRMClient
from src.pipeline import (
    CallIntelligencePipeline,
    InMemoryIdempotencyStore,
    SQLiteIdempotencyStore,
)
from src.schemas import (
    CallDirection,
    CallIntelligence,
    CallOutcome,
    CallStatus,
    LeadQuality,
    PipelineResponse,
    TelephonyWebhookPayload,
)
from src.server import app

# =====================================================================
# SEC-06 Audio Upload Validation Tests
# =====================================================================

@pytest.mark.asyncio
async def test_audio_upload_empty_file_rejected():
    """SEC-06: Empty audio file must be rejected with HTTP 400."""
    files = {"file": ("empty.wav", io.BytesIO(b""), "audio/wav")}
    data = {"lead_phone": "+15550009012"}

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/telephony/process-audio", files=files, data=data)
        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_audio_upload_unsupported_mime_type_rejected():
    """SEC-06: Unsupported MIME type must be rejected with HTTP 415."""
    fake_content = b"fake audio content"
    files = {"file": ("malicious.wav", io.BytesIO(fake_content), "application/x-executable")}
    data = {"lead_phone": "+15550009012"}

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/telephony/process-audio", files=files, data=data)
        assert response.status_code == 415
        assert "unsupported media type" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_audio_upload_unsupported_extension_rejected():
    """SEC-06: Unsupported file extension must be rejected with HTTP 415."""
    fake_content = b"fake audio content"
    files = {"file": ("payload.exe", io.BytesIO(fake_content), "audio/wav")}
    data = {"lead_phone": "+15550009012"}

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/telephony/process-audio", files=files, data=data)
        assert response.status_code == 415
        assert "unsupported file extension" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_audio_upload_exceeding_25mb_rejected():
    """SEC-06: Audio upload exceeding 25 MB must be rejected with HTTP 413."""
    # Create a 26 MB stream simulator without allocating 26MB RAM at once
    class LargeAudioStream(io.RawIOBase):
        def __init__(self, total_bytes: int):
            self.total_bytes = total_bytes
            self.bytes_read = 0

        def read(self, size=-1):
            if size == -1 or size is None:
                size = self.total_bytes - self.bytes_read
            size = min(size, self.total_bytes - self.bytes_read)
            self.bytes_read += size
            return b"A" * size

    large_stream = LargeAudioStream(26 * 1024 * 1024)
    files = {"file": ("big_recording.wav", large_stream, "audio/wav")}
    data = {"lead_phone": "+15550009012"}

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/telephony/process-audio", files=files, data=data)
        assert response.status_code == 413
        assert "exceeds maximum upload size" in response.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename,mime_type",
    [
        ("sample.wav", "audio/wav"),
        ("sample.mp3", "audio/mpeg"),
        ("sample.m4a", "audio/mp4"),
    ],
)
async def test_audio_upload_supported_formats_success(filename, mime_type):
    """SEC-06: Supported WAV, MP3, and M4A audio uploads must succeed."""
    fake_content = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    files = {"file": (filename, io.BytesIO(fake_content), mime_type)}
    data = {
        "lead_phone": "+15550009012",
        "agent_id": "sarah.demo@example.com",
        "direction": "outbound",
        "duration_seconds": "45",
    }

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/telephony/process-audio", files=files, data=data)
        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        assert result["matched_lead"] is not None
        assert result["transcript"] is not None


# =====================================================================
# REL-02 Timeline Comment Reliability Tests
# =====================================================================

@pytest.mark.asyncio
async def test_add_timeline_comment_http_success_200():
    """REL-02: add_timeline_comment returns True on HTTP 200."""
    frappe = FrappeCRMClient(mock_mode=False)
    dummy_intelligence = CallIntelligence(
        call_outcome=CallOutcome.CALLBACK_REQUESTED,
        lead_quality=LeadQuality.WARM,
        sentiment="Neutral",
        urgency="Medium",
        confidence_score=0.9,
        customer_intent="Demo inquiry",
        summary="Customer asked for callback.",
        call_summary="Customer asked for callback.",
        objections=[],
        next_action="Call tomorrow",
        follow_up_required=True,
        agent_quality_score=9.0,
        agent_quality_notes="Good interaction",
    )

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = httpx.Response(
            200,
            json={"message": "ok"},
            request=httpx.Request("POST", "https://crm.example.com/api/resource/Comment"),
        )
        success = await frappe.add_timeline_comment(
            reference_doctype="CRM Call Log",
            reference_name="TEST-LOG-200",
            title="AI Call Summary",
            intelligence=dummy_intelligence,
        )
        assert success is True


@pytest.mark.asyncio
async def test_add_timeline_comment_http_4xx_returns_false():
    """REL-02: add_timeline_comment detects 4xx response via raise_for_status and returns False."""
    frappe = FrappeCRMClient(mock_mode=False)
    dummy_intelligence = CallIntelligence(
        call_outcome=CallOutcome.INTERESTED,
        lead_quality=LeadQuality.HOT,
        sentiment="Positive",
        urgency="High",
        confidence_score=0.95,
        customer_intent="Upgrade subscription",
        summary="Customer ready to buy.",
        call_summary="Customer ready to buy.",
        objections=[],
        next_action="Send invoice",
        follow_up_required=False,
        agent_quality_score=9.5,
        agent_quality_notes="Great pitch",
    )

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = httpx.Response(
            404,
            text="Document not found",
            request=httpx.Request("POST", "https://crm.example.com/api/resource/Comment"),
        )
        success = await frappe.add_timeline_comment(
            reference_doctype="CRM Call Log",
            reference_name="TEST-LOG-NONEXISTENT",
            title="AI Call Summary",
            intelligence=dummy_intelligence,
        )
        assert success is False


@pytest.mark.asyncio
async def test_add_timeline_comment_http_5xx_returns_false():
    """REL-02: add_timeline_comment detects 5xx response via raise_for_status and returns False."""
    frappe = FrappeCRMClient(mock_mode=False)
    dummy_intelligence = CallIntelligence(
        call_outcome=CallOutcome.NOT_INTERESTED,
        lead_quality=LeadQuality.COLD,
        sentiment="Negative",
        urgency="Low",
        confidence_score=0.9,
        customer_intent="Cancel subscription",
        summary="Customer declined.",
        call_summary="Customer declined.",
        objections=[],
        next_action="Close ticket",
        follow_up_required=False,
        agent_quality_score=8.0,
        agent_quality_notes="Handled politely",
    )

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = httpx.Response(
            503,
            text="Service Unavailable",
            request=httpx.Request("POST", "https://crm.example.com/api/resource/Comment"),
        )
        success = await frappe.add_timeline_comment(
            reference_doctype="CRM Call Log",
            reference_name="TEST-LOG-503",
            title="AI Call Summary",
            intelligence=dummy_intelligence,
        )
        assert success is False


# =====================================================================
# REL-03 Idempotency Store (TTL, Bounding, SQLite) Tests
# =====================================================================

def test_inmemory_idempotency_ttl_expiration():
    """REL-03: InMemoryIdempotencyStore honors TTL expiration."""
    store = InMemoryIdempotencyStore(ttl_seconds=1, max_items=10)
    dummy_resp = PipelineResponse(
        success=True,
        provider_call_id="call-ttl-test",
        idempotent_replay=False,
    )

    store.set("call-ttl-test", dummy_resp)
    assert store.has("call-ttl-test") is True
    assert store.get("call-ttl-test") is not None

    # Wait for TTL to expire
    time.sleep(1.1)

    assert store.has("call-ttl-test") is False
    assert store.get("call-ttl-test") is None


def test_inmemory_idempotency_max_items_bounding():
    """REL-03: InMemoryIdempotencyStore bounds size to max_items via LRU eviction."""
    store = InMemoryIdempotencyStore(ttl_seconds=300, max_items=3)

    for i in range(1, 5):
        resp = PipelineResponse(
            success=True,
            provider_call_id=f"call-bound-{i}",
            idempotent_replay=False,
        )
        store.set(f"call-bound-{i}", resp)

    # After inserting 4 items into a store bounded at 3, the first item must be evicted
    assert len(store.get_all()) == 3
    assert store.has("call-bound-1") is False
    assert store.has("call-bound-2") is True
    assert store.has("call-bound-3") is True
    assert store.has("call-bound-4") is True


def test_sqlite_idempotency_store_persistence_and_ttl(tmp_path):
    """REL-03: SQLiteIdempotencyStore persists to disk, bounds items, and honors TTL."""
    db_file = str(tmp_path / "test_idempotency.db")
    store = SQLiteIdempotencyStore(db_path=db_file, ttl_seconds=1, max_items=2)

    resp1 = PipelineResponse(success=True, provider_call_id="call-sql-1", idempotent_replay=False)
    resp2 = PipelineResponse(success=True, provider_call_id="call-sql-2", idempotent_replay=False)
    resp3 = PipelineResponse(success=True, provider_call_id="call-sql-3", idempotent_replay=False)

    store.set("call-sql-1", resp1)
    store.set("call-sql-2", resp2)
    assert store.has("call-sql-1") is True
    assert store.has("call-sql-2") is True

    # Insert 3rd item: capacity 2 must evict oldest (call-sql-1)
    store.set("call-sql-3", resp3)
    assert store.has("call-sql-1") is False
    assert store.has("call-sql-2") is True
    assert store.has("call-sql-3") is True

    # Re-instantiate SQLite store pointing to the same file to verify persistence
    store_reopened = SQLiteIdempotencyStore(db_path=db_file, ttl_seconds=1, max_items=2)
    cached = store_reopened.get("call-sql-3")
    assert cached is not None
    assert cached.provider_call_id == "call-sql-3"

    # Wait for TTL to expire
    time.sleep(1.1)
    assert store_reopened.has("call-sql-3") is False
    assert store_reopened.get("call-sql-3") is None


# =====================================================================
# End-to-End Pipeline & CRM Partial Failure Scenarios
# =====================================================================

@pytest.mark.asyncio
async def test_pipeline_prevents_duplicate_call_logs_and_tasks():
    """REL-03: Idempotent replay returns cached result and prevents duplicate CRM calls."""
    frappe = FrappeCRMClient(mock_mode=True)
    store = InMemoryIdempotencyStore()
    pipeline = CallIntelligencePipeline(frappe_client=frappe, idempotency_store=store)

    event = TelephonyWebhookPayload(
        provider_call_id="call_duplicate_prevention_001",
        telephony_provider="exotel",
        from_number="+15550001111",
        to_number="+15550009012",
        direction=CallDirection.OUTBOUND,
        call_status=CallStatus.COMPLETED,
        duration_seconds=75,
        recording_url="https://example.com/recording.wav",
        agent_id="sarah.demo@example.com",
    )

    # First run
    res1 = await pipeline.process_call(event)
    assert res1.success is True
    assert res1.idempotent_replay is False
    call_log_id_1 = res1.frappe_call_log_id
    task_id_1 = res1.frappe_task_id

    # Second duplicate run: must return cached result without generating a new call log ID
    res2 = await pipeline.process_call(event)
    assert res2.success is True
    assert res2.idempotent_replay is True
    assert res2.frappe_call_log_id == call_log_id_1
    assert res2.frappe_task_id == task_id_1


@pytest.mark.asyncio
async def test_crm_partial_failure_comment_failure_does_not_crash_pipeline():
    """REL-02 / REL-03: If timeline comment fails, Call Log and Pipeline still succeed."""
    frappe = FrappeCRMClient(mock_mode=False)
    store = InMemoryIdempotencyStore()
    pipeline = CallIntelligencePipeline(frappe_client=frappe, idempotency_store=store)

    event = TelephonyWebhookPayload(
        provider_call_id="call_partial_fail_comment_001",
        from_number="+15550001111",
        to_number="+15550009012",
        direction=CallDirection.OUTBOUND,
        call_status=CallStatus.COMPLETED,
        duration_seconds=60,
    )

    # Patch create_call_log HTTP post to succeed, but comment POST to fail 500
    dummy_req = httpx.Request("POST", "https://crm.example.com/api")
    with patch("httpx.AsyncClient.post") as mock_post:
        def side_effect(url, **kwargs):
            if "CRM%20Call%20Log" in url or "CRM Call Log" in url:
                return httpx.Response(200, json={"data": {"name": "CALL-LOG-PARTIAL-01"}}, request=dummy_req)
            if "Comment" in url:
                return httpx.Response(500, text="Internal Server Error on Comment", request=dummy_req)
            if "CRM%20Task" in url or "CRM Task" in url:
                return httpx.Response(200, json={"data": {"name": "TASK-PARTIAL-01"}}, request=dummy_req)
            return httpx.Response(200, json={"data": {}}, request=dummy_req)

        mock_post.side_effect = side_effect

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value = httpx.Response(200, json={"data": []}, request=httpx.Request("GET", "https://crm.example.com/api"))
            with patch("httpx.AsyncClient.put") as mock_put:
                mock_put.return_value = httpx.Response(200, json={"data": {}}, request=httpx.Request("PUT", "https://crm.example.com/api"))

                res = await pipeline.process_call(event)
                assert res.success is True
                assert res.frappe_call_log_id == "CALL-LOG-PARTIAL-01"
                # Verified stored in idempotency store
                assert store.has("call_partial_fail_comment_001") is True
