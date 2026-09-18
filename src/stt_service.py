import logging
from abc import ABC, abstractmethod

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)


class STTService(ABC):
    """Abstract interface for Speech-to-Text transcription services."""

    @abstractmethod
    async def transcribe(self, audio_source: str | bytes, filename: str | None = None) -> str:
        """Transcribe audio from a local file path, URL, or raw bytes."""


class MockSTTService(STTService):
    """Mock STT provider for offline testing and initial scaffolding."""

    DEFAULT_TRANSCRIPT = (
        "Agent: Hello, this is John calling from Hash Adz Creative Solutions. Am I speaking with Carol?\n"
        "Customer: Yes, speaking. What is this regarding?\n"
        "Agent: I'm following up on your inquiry regarding our performance marketing and social media creative packages for BrightPath Ltd.\n"
        "Customer: Oh yes! We are looking to scale our digital ad campaigns next quarter, but our budget is somewhat tight right now. Could you share your pricing and case studies?\n"
        "Agent: Absolutely! I can send over our standard tier breakdown and schedule a detailed walkthrough with our strategy head. Would this Friday at 3 PM work for you?\n"
        "Customer: Friday at 3 PM sounds perfect. Please email the details before then.\n"
        "Agent: Will do, Carol. Thank you for your time, and have a great day!"
    )

    MALAYALAM_TRANSCRIPT = (
        "Agent: Namaskaram, Hash Adz-il ninnum John aanu vilikkunnathu. Carol-nodano samsarikkunnathu?\n"
        "Customer: Athe, parayu. Entha kaaryam?\n"
        "Agent: Njangalude digital marketing packages-ne patti inquiry cheythirunnu. Athine kurichu discuss cheyyaan aanu vilichathu.\n"
        "Customer: Njangalkku marketing campaign thudangaan aagrahomundu. Pakshe budget koncham tight aanu. Details email cheyyaamo? Friday 3 PM-nu vilichaal kollam.\n"
        "Agent: Theerchayaayum, Friday 3 PM-nu follow-up call schedule cheyyaam. Thank you!"
    )

    async def transcribe(self, audio_source: str | bytes, filename: str | None = None) -> str:
        logger.info("[MockSTTService] Generating mock transcription (Simulation mode active)")
        if filename and ("malayalam" in filename.lower() or "manglish" in filename.lower()):
            return self.MALAYALAM_TRANSCRIPT
        return self.DEFAULT_TRANSCRIPT


class RealSTTService(STTService):
    """Real STT provider using OpenAI Whisper API."""

    def __init__(self, provider: str, api_key: str):
        self.provider = provider
        self.api_key = api_key

    async def transcribe(self, audio_source: str | bytes, filename: str | None = None) -> str:
        import os

        import httpx

        if not self.api_key:
            raise ValueError("STT API key is not configured.")

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

        url = "https://api.openai.com/v1/audio/transcriptions"
        model_name = "whisper-1"

        if self.provider == "groq":
            url = "https://api.groq.com/openai/v1/audio/transcriptions"
            model_name = "whisper-large-v3"

        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        files = {
            "file": (name, file_bytes, "audio/mpeg"),
        }
        data = {
            "model": model_name,
        }

        timeout = get_settings().stt_timeout_seconds
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, files=files, data=data)
                response.raise_for_status()
                return response.json().get("text", "")
        except Exception as exc:
            logger.error(f"[RealSTTService] Transcription failed: {exc}")
            raise


def get_stt_service(settings: Settings | None = None) -> STTService:
    """Factory to retrieve configured STT provider."""
    cfg = settings or get_settings()
    if cfg.mock_mode or cfg.stt_provider == "mock" or not cfg.stt_api_key:
        return MockSTTService()
    return RealSTTService(provider=cfg.stt_provider, api_key=cfg.stt_api_key)
