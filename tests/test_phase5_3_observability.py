import json
import logging
import pytest
import httpx
from httpx import ASGITransport

from src.server import app
from src.config import get_settings
from src.observability import (
    metrics,
    RedactingJsonFormatter,
    AppError,
    ErrorClassification,
    request_id_ctx,
)
from src.pipeline import CallIntelligencePipeline, SQLiteIdempotencyStore
from src.schemas import (
    TelephonyWebhookPayload,
    CallDirection,
    CallStatus,
)


# =====================================================================
# 1. Request ID & Correlation Tests
# =====================================================================

@pytest.mark.asyncio
async def test_request_id_generated_when_not_provided():
    """OBS-01: When X-Request-ID is not provided, server generates a secure unique ID in response headers."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        req_id = response.headers.get("X-Request-ID")
        assert req_id is not None
        assert req_id.startswith("req_")
        assert len(req_id) >= 10


@pytest.mark.asyncio
async def test_request_id_preserved_when_valid():
    """OBS-01: Valid client-supplied X-Request-ID is preserved and reflected in response headers."""
    custom_id = "client-trace-123456"
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health", headers={"X-Request-ID": custom_id})
        assert response.status_code == 200
        assert response.headers.get("X-Request-ID") == custom_id


@pytest.mark.asyncio
async def test_request_id_sanitized_when_malicious_or_invalid():
    """OBS-01: Client IDs with invalid characters or excessive length are rejected in favor of generated IDs."""
    malicious_id = "<script>alert(1)</script>" * 5
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health", headers={"X-Request-ID": malicious_id})
        assert response.status_code == 200
        req_id = response.headers.get("X-Request-ID")
        assert req_id != malicious_id
        assert req_id.startswith("req_")


# =====================================================================
# 2. Health & Readiness Endpoint Tests
# =====================================================================

@pytest.mark.asyncio
async def test_health_liveness():
    """OBS-02: /health reports service liveness without exposing sensitive tokens."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "mock_mode" in data
        assert "app_env" in data
        assert "frappe_crm_url" in data
        assert "api_key" not in data
        assert "api_secret" not in data


@pytest.mark.asyncio
async def test_readiness_healthy_in_mock_mode():
    """OBS-02: /ready reports 200 OK when in mock_mode."""
    settings = get_settings()
    original_mock = settings.mock_mode
    try:
        settings.mock_mode = True
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/ready")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ready"
            assert data["checks"]["crm_configured"] is True
            assert data["checks"]["ai_configured"] is True
            assert data["checks"]["stt_configured"] is True
            assert data["checks"]["storage_ready"] is True
    finally:
        settings.mock_mode = original_mock


@pytest.mark.asyncio
async def test_readiness_unhealthy_in_live_mode_missing_keys():
    """OBS-02: /ready reports 503 Service Unavailable in live mode when external keys are missing."""
    settings = get_settings()
    original_mock = settings.mock_mode
    original_frappe_key = settings.frappe_api_key
    original_frappe_secret = settings.frappe_api_secret
    try:
        settings.mock_mode = False
        settings.frappe_api_key = ""
        settings.frappe_api_secret = ""

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/ready")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "not_ready"
            assert data["checks"]["crm_configured"] is False
            assert len(data["reasons"]) > 0
    finally:
        settings.mock_mode = original_mock
        settings.frappe_api_key = original_frappe_key
        settings.frappe_api_secret = original_frappe_secret


# =====================================================================
# 3. Metrics Tracking Tests
# =====================================================================

@pytest.mark.asyncio
async def test_metrics_endpoint_increments():
    """OBS-03: /metrics exposes operational counters, durations, and error classifications."""
    metrics.reset()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Initial request
        res1 = await client.get("/metrics")
        assert res1.status_code == 200
        initial_data = res1.json()
        assert "total_requests" in initial_data
        assert "pipeline_success_total" in initial_data
        assert "average_processing_duration_ms" in initial_data
        assert "errors_by_class" in initial_data

        # Trigger a bad audio upload to increment error metrics
        files = {"file": ("empty.wav", b"", "audio/wav")}
        res2 = await client.post("/api/v1/telephony/process-audio", files=files, data={"lead_phone": "+123456789"})
        assert res2.status_code == 400

        # Check updated metrics
        res3 = await client.get("/metrics")
        updated_data = res3.json()
        assert updated_data["audio_upload_validation_failures_total"] >= 1
        assert updated_data["errors_by_class"].get("validation_error", 0) >= 1


# =====================================================================
# 4. Structured Logging & Sensitive Data Redaction Tests
# =====================================================================

def test_structured_logging_redaction():
    """OBS-04: RedactingJsonFormatter sanitizes secrets, authorization headers, and sensitive keys."""
    formatter = RedactingJsonFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Processing call with sensitive token abc:xyz123 and Bearer secret_groq_key_999",
        args=(),
        exc_info=None,
    )
    # Attach sensitive attributes
    setattr(record, "api_key", "gsk_sensitive_key_value")
    setattr(record, "call_id", "call-obs-999")

    formatted_json = formatter.format(record)
    parsed = json.loads(formatted_json)

    assert parsed["logger"] == "test_logger"
    assert parsed["call_id"] == "call-obs-999"
    assert parsed["api_key"] == "[REDACTED]"
    assert "gsk_sensitive_key_value" not in formatted_json
    assert "[REDACTED]" in parsed["message"]
    assert "secret_groq_key_999" not in parsed["message"]


# =====================================================================
# 5. Error Classification & Upstream Failures Tests
# =====================================================================

def test_error_classification_properties():
    """OBS-05: AppError correctly classifies error types and retryability."""
    err = AppError(
        message="Upstream Groq rate limit exceeded",
        error_class=ErrorClassification.UPSTREAM_ERROR,
        status_code=502,
        retryable=True,
    )
    assert err.error_class == ErrorClassification.UPSTREAM_ERROR
    assert err.status_code == 502
    assert err.retryable is True


@pytest.mark.asyncio
async def test_pipeline_catches_app_error_with_classification():
    """OBS-05: Pipeline gracefully translates unexpected failures into categorized AppErrors."""
    pipeline = CallIntelligencePipeline()

    class FailingAI:
        async def analyze_call(self, transcript, metadata=None):
            raise ConnectionError("Groq timeout")

    pipeline.ai_service = FailingAI()

    event = TelephonyWebhookPayload(
        provider_call_id="call-fail-obs-001",
        from_number="+15550001111",
        to_number="+15550009012",
        direction=CallDirection.OUTBOUND,
        call_status=CallStatus.COMPLETED,
        duration_seconds=60,
    )

    with pytest.raises(AppError) as exc_info:
        await pipeline.process_call(event)

    assert exc_info.value.error_class == ErrorClassification.LLM_ERROR
    assert exc_info.value.status_code == 502
    assert "LLM analysis failed" in str(exc_info.value)
