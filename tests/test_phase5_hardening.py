from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from src.config import get_settings
from src.frappe_client import FrappeCRMClient
from src.pipeline import CallIntelligencePipeline
from src.schemas import (
    CallDirection,
    CallIntelligence,
    CallOutcome,
    CallStatus,
    LeadQuality,
    TelephonyWebhookPayload,
)
from src.server import app

client = TestClient(app)


def test_cors_explicit_configuration():
    """SEC-02: Allowed origin receives CORS headers; untrusted origin does not receive access-control-allow-origin."""
    # Test allowed origin from default settings (e.g., http://localhost:8000)
    res_allowed = client.options(
        "/api/v1/telephony/webhook",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert res_allowed.headers.get("access-control-allow-origin") == "http://localhost:8000"
    assert res_allowed.headers.get("access-control-allow-credentials") == "true"

    # Test arbitrary untrusted origin
    res_disallowed = client.options(
        "/api/v1/telephony/webhook",
        headers={
            "Origin": "https://malicious-attacker.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert res_disallowed.headers.get("access-control-allow-origin") != "https://malicious-attacker.com"
    assert res_disallowed.headers.get("access-control-allow-origin") != "*"


def test_api_models_safe_metadata_no_secrets():
    """SEC-01: /api/models returns safe metadata and never exposes API keys or tokens."""
    settings = get_settings()
    secret_key = settings.ai_api_key or "gsk_live_test_secret_12345"

    res = client.get("/api/models")
    assert res.status_code == 200
    data = res.json()
    assert "data" in data
    assert isinstance(data["data"], list)

    raw_response_str = res.text
    if secret_key:
        assert secret_key not in raw_response_str

    for model in data["data"]:
        assert "id" in model
        assert "owned_by" in model
        # Ensure no Authorization header or secret keys are reflected
        assert "authorization" not in model
        assert "api_key" not in model


def test_api_models_admin_token_protection():
    """SEC-01: When admin_api_token is configured, unauthorized requests are blocked."""
    settings = get_settings()
    original_admin_token = settings.admin_api_token
    try:
        settings.admin_api_token = "super_secure_admin_token_999"

        # Request without header fails 401
        res_unauthorized = client.get("/api/models")
        assert res_unauthorized.status_code == 401

        # Request with invalid header fails 401
        res_bad_token = client.get("/api/models", headers={"X-Admin-Token": "wrong_token"})
        assert res_bad_token.status_code == 401

        # Request with correct header succeeds 200
        res_authorized = client.get(
            "/api/models",
            headers={"X-Admin-Token": "super_secure_admin_token_999"},
        )
        assert res_authorized.status_code == 200
    finally:
        settings.admin_api_token = original_admin_token


@pytest.mark.asyncio
async def test_transcript_privacy_modes():
    """SEC-05: add_timeline_comment respects store_transcript_in_crm setting."""
    settings = get_settings()
    original_mode = settings.store_transcript_in_crm
    frappe = FrappeCRMClient(mock_mode=False)

    dummy_intelligence = CallIntelligence(
        call_outcome=CallOutcome.CALLBACK_REQUESTED,
        lead_quality=LeadQuality.WARM,
        sentiment="Neutral",
        urgency="Medium",
        confidence_score=0.9,
        customer_intent="Demo inquiry",
        summary="Customer called to inquire about enterprise demo.",
        call_summary="Customer called to inquire about enterprise demo.",
        objections=[],
        next_action="Schedule meeting",
        follow_up_required=True,
        agent_quality_score=9.0,
        agent_quality_notes="Good interaction",
    )

    long_transcript = "Agent: Hello! Customer: I am interested in your software. " * 30

    try:
        # 1. Test "none": transcript section must be completely excluded
        settings.store_transcript_in_crm = "none"
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = httpx.Response(200, json={"message": "ok"})
            await frappe.add_timeline_comment(
                reference_doctype="CRM Call Log",
                reference_name="TEST-LOG-01",
                title="AI Call Summary",
                intelligence=dummy_intelligence,
                transcript=long_transcript,
            )
            payload = mock_post.call_args[1]["json"]
            assert "View Audio Transcript" not in payload["content"]
            assert long_transcript[:100] not in payload["content"]

        # 2. Test "truncated": transcript should be truncated with privacy disclaimer
        settings.store_transcript_in_crm = "truncated"
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = httpx.Response(200, json={"message": "ok"})
            await frappe.add_timeline_comment(
                reference_doctype="CRM Call Log",
                reference_name="TEST-LOG-01",
                title="AI Call Summary",
                intelligence=dummy_intelligence,
                transcript=long_transcript,
            )
            payload = mock_post.call_args[1]["json"]
            assert "View Audio Transcript (Truncated)" in payload["content"]
            assert "... [Transcript truncated for privacy]" in payload["content"]

        # 3. Test "full": full transcript is retained
        settings.store_transcript_in_crm = "full"
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = httpx.Response(200, json={"message": "ok"})
            await frappe.add_timeline_comment(
                reference_doctype="CRM Call Log",
                reference_name="TEST-LOG-01",
                title="AI Call Summary",
                intelligence=dummy_intelligence,
                transcript=long_transcript,
            )
            payload = mock_post.call_args[1]["json"]
            assert "View Audio Transcript" in payload["content"]
            assert "[Transcript truncated for privacy]" not in payload["content"]
            assert long_transcript in payload["content"]
    finally:
        settings.store_transcript_in_crm = original_mode


@pytest.mark.asyncio
async def test_create_followup_task_failure_non_fatal():
    """REL-01: Frappe CRM task creation failure is handled safely and returns None without crashing."""
    frappe = FrappeCRMClient(mock_mode=False)

    dummy_intelligence = CallIntelligence(
        call_outcome=CallOutcome.CALLBACK_REQUESTED,
        lead_quality=LeadQuality.HOT,
        sentiment="Positive",
        urgency="High",
        confidence_score=0.95,
        customer_intent="Immediate purchase",
        summary="Customer wants to buy today.",
        call_summary="Customer wants to buy today.",
        objections=[],
        next_action="Send invoice",
        follow_up_required=True,
        agent_quality_score=9.5,
        agent_quality_notes="Excellent closing",
    )

    # Simulate HTTP 500 error from upstream Frappe CRM server
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = httpx.Response(
            500,
            text="Internal Server Error: DocType Task validation failed",
            request=httpx.Request("POST", "https://crm.example.com/api/resource/CRM Task"),
        )
        task_result = await frappe.create_followup_task(
            call_log_id="LOG-FAIL-TEST",
            lead_id="LEAD-001",
            intelligence=dummy_intelligence,
        )
        # Should return None safely without raising an exception
        assert task_result is None


@pytest.mark.asyncio
async def test_pipeline_task_failure_preserves_call_log_and_idempotency():
    """REL-01: If Task creation fails, Pipeline completes successfully with task_id=None and preserves Call Log."""
    frappe = FrappeCRMClient(mock_mode=True)
    pipeline = CallIntelligencePipeline(frappe_client=frappe)

    event = TelephonyWebhookPayload(
        provider_call_id="call-hardening-task-fail-001",
        from_number="+15550009012",
        to_number="+15559998888",
        direction=CallDirection.INBOUND,
        call_status=CallStatus.COMPLETED,
        duration_seconds=120,
    )

    # Patch create_followup_task on frappe to raise an exception or return None
    with patch.object(frappe, "create_followup_task", side_effect=RuntimeError("Simulated network drop")):
        response = await pipeline.process_call(event=event, raw_audio=b"fake-audio")

        assert response.success is True
        assert response.provider_call_id == "call-hardening-task-fail-001"
        assert response.frappe_call_log_id is not None
        assert response.frappe_task_id is None
        # Verify idempotency store saved the result
        cached = pipeline.idempotency_store.get("call-hardening-task-fail-001")
        assert cached is not None
        assert cached.frappe_call_log_id == response.frappe_call_log_id
        assert cached.frappe_task_id is None


@pytest.mark.asyncio
async def test_pipeline_task_success_string_id():
    """REL-01: If Task creation succeeds, task ID is properly captured as string."""
    frappe = FrappeCRMClient(mock_mode=True)
    pipeline = CallIntelligencePipeline(frappe_client=frappe)

    event = TelephonyWebhookPayload(
        provider_call_id="call-hardening-task-success-002",
        from_number="+15550009012",
        to_number="+15559998888",
        direction=CallDirection.INBOUND,
        call_status=CallStatus.COMPLETED,
        duration_seconds=95,
    )

    response = await pipeline.process_call(event=event, raw_audio=b"fake-audio")
    assert response.success is True
    # Default mock audio generates follow_up_required=True
    if response.intelligence.follow_up_required:
        assert response.frappe_task_id is not None
        assert isinstance(response.frappe_task_id, str)
        assert response.frappe_task_id.startswith("TASK-")
