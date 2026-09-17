from abc import ABC, abstractmethod
import logging
from typing import Optional

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)


class STTService(ABC):
    """Abstract interface for Speech-to-Text transcription services."""

    @abstractmethod
    async def transcribe(self, audio_source: str | bytes, filename: Optional[str] = None) -> str:
        """Transcribe audio from a local file path, URL, or raw bytes."""
        pass


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

    async def transcribe(self, audio_source: str | bytes, filename: Optional[str] = None) -> str:
        logger.info("[MockSTTService] Generating mock transcription (Simulation mode active)")
        if filename and ("malayalam" in filename.lower() or "manglish" in filename.lower()):
            return self.MALAYALAM_TRANSCRIPT
        return self.DEFAULT_TRANSCRIPT


class RealSTTService(STTService):
    """Real STT provider placeholder (Groq / OpenAI Whisper / Gemini)."""

    def __init__(self, provider: str, api_key: str):
        self.provider = provider
        self.api_key = api_key

    async def transcribe(self, audio_source: str | bytes, filename: Optional[str] = None) -> str:
        # Will be connected to groq / openai client in next phase when credentials are provided
        raise NotImplementedError(
            f"Real STT provider '{self.provider}' configured but client integration pending API key verification. Use MOCK_MODE=true for testing."
        )


def get_stt_service(settings: Optional[Settings] = None) -> STTService:
    """Factory to retrieve configured STT provider."""
    cfg = settings or get_settings()
    if cfg.mock_mode or cfg.stt_provider == "mock" or not cfg.stt_api_key:
        return MockSTTService()
    return RealSTTService(provider=cfg.stt_provider, api_key=cfg.stt_api_key)
