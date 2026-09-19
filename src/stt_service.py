import logging
import os
from abc import ABC, abstractmethod
from typing import Any

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)


class STTService(ABC):
    """Abstract interface for Speech-to-Text transcription services."""

    @abstractmethod
    async def transcribe(
        self,
        audio_source: str | bytes,
        filename: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Transcribe audio from a local file path, URL, or raw bytes."""


class MockSTTService(STTService):
    """Mock STT provider for offline testing and initial scaffolding."""

    DEFAULT_TRANSCRIPT = (
        "Agent: Hello, are you interested in our course?\n"
        "Customer: Yes, I would like to know the fees.\n"
        "Agent: I can send you the details tomorrow.\n"
        "Customer: Okay, please call me tomorrow."
    )

    MALAYALAM_TRANSCRIPT = (
        "Agent: Namaskaram, course-ne patti ariyamo?\n"
        "Customer: Athe, fees details ariyarnnu.\n"
        "Agent: Naale details ayachu tharam.\n"
        "Customer: Serry, naale vilikku."
    )

    async def transcribe(
        self,
        audio_source: str | bytes,
        filename: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        logger.info("[MockSTTService] Generating mock transcription (Simulation mode active)")
        if filename and ("malayalam" in filename.lower() or "manglish" in filename.lower()):
            return self.MALAYALAM_TRANSCRIPT
        return self.DEFAULT_TRANSCRIPT


class RealSTTService(STTService):
    """Real STT provider using OpenAI Whisper API or Groq Whisper API."""

    def __init__(self, provider: str, api_key: str):
        self.provider = provider
        self.api_key = api_key

    async def transcribe(
        self,
        audio_source: str | bytes,
        filename: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        import httpx

        if not self.api_key:
            raise ValueError(
                "Speech-to-text is not configured. Please configure the STT provider or explicitly enable mock mode."
            )

        meta = metadata or {}
        call_id = meta.get("call_id", "unknown")

        # Determine if we have a file path or raw bytes
        if isinstance(audio_source, str):
            if not os.path.exists(audio_source):
                raise FileNotFoundError(f"Audio file not found: {audio_source}")
            with open(audio_source, "rb") as f:
                file_bytes = f.read()
            name = filename or os.path.basename(audio_source)
        else:
            file_bytes = audio_source
            name = filename or "audio.wav"

        audio_size = len(file_bytes)
        ext = os.path.splitext(name)[1].lower()
        mime_map = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/m4a",
            ".mp4": "audio/mp4",
            ".ogg": "audio/ogg",
            ".webm": "audio/webm",
            ".flac": "audio/flac",
        }
        content_type = mime_map.get(ext, "audio/wav")

        logger.info(
            f"Initiating STT transcription for call '{call_id}'",
            extra={
                "call_id": call_id,
                "uploaded_filename": name,
                "audio_content_type": content_type,
                "audio_size": audio_size,
                "stt_provider": self.provider,
                "transcription_status": "in_progress",
            },
        )

        url = "https://api.openai.com/v1/audio/transcriptions"
        model_name = "whisper-1"

        if self.provider == "groq":
            url = "https://api.groq.com/openai/v1/audio/transcriptions"
            model_name = "whisper-large-v3"

        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        files = {
            "file": (name, file_bytes, content_type),
        }
        data = {
            "model": model_name,
        }

        timeout = get_settings().stt_timeout_seconds
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, files=files, data=data)
                response.raise_for_status()
                text = (response.json().get("text") or "").strip()

                if not text:
                    raise ValueError("Speech-to-text provider returned an empty transcript.")

                logger.info(
                    f"STT transcription successful for call '{call_id}'",
                    extra={
                        "call_id": call_id,
                        "uploaded_filename": name,
                        "audio_content_type": content_type,
                        "audio_size": audio_size,
                        "stt_provider": self.provider,
                        "transcription_status": "success",
                        "transcript_length": len(text),
                    },
                )
                return text
        except Exception as exc:
            logger.error(
                f"STT transcription failed for call '{call_id}': {exc}",
                extra={
                    "call_id": call_id,
                    "uploaded_filename": name,
                    "audio_content_type": content_type,
                    "audio_size": audio_size,
                    "stt_provider": self.provider,
                    "transcription_status": "failed",
                },
            )
            raise


def get_stt_service(settings: Settings | None = None) -> STTService:
    """Factory to retrieve configured STT provider."""
    cfg = settings or get_settings()
    if cfg.mock_mode or cfg.stt_provider == "mock" or not cfg.stt_api_key:
        return MockSTTService()
    return RealSTTService(provider=cfg.stt_provider, api_key=cfg.stt_api_key)

