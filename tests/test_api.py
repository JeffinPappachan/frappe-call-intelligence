import io

import httpx
import pytest
from httpx import ASGITransport

from src.server import app


@pytest.mark.asyncio
async def test_health_endpoint():
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "mock_mode" in data
        assert "frappe_crm_url" in data


@pytest.mark.asyncio
async def test_webhook_endpoint_success():
    payload = {
        "provider_call_id": "call_api_test_101",
        "telephony_provider": "exotel",
        "from_number": "+15550001111",
        "to_number": "+15550009012",
        "direction": "outbound",
        "call_status": "completed",
        "duration_seconds": 125,
        "recording_url": "https://example.com/recording.wav",
        "agent_id": "john.parker@example.com",
    }
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/telephony/webhook", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["provider_call_id"] == "call_api_test_101"
        assert data["idempotent_replay"] is False
        assert data["intelligence"] is not None
        assert "call_summary" in data["intelligence"]
        assert "call_outcome" in data["intelligence"]
        assert data["frappe_call_log_id"] is not None


@pytest.mark.asyncio
async def test_webhook_endpoint_invalid_payload():
    invalid_payload = {
        "provider_call_id": "",  # Empty ID should fail validation
        "from_number": "+15550001111",
    }
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/telephony/webhook", json=invalid_payload)
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_process_audio_upload_endpoint():
    fake_wav_content = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    files = {"file": ("sample_call.wav", io.BytesIO(fake_wav_content), "audio/wav")}
    data = {
        "lead_phone": "+15550009012",
        "agent_id": "john.parker@example.com",
        "direction": "outbound",
        "duration_seconds": "60",
    }

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/telephony/process-audio", files=files, data=data)
        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        assert result["matched_lead"] is not None
        assert result["transcript"] is not None
