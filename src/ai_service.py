from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from src.config import Settings, get_settings
from src.schemas import CallIntelligence, CallOutcome, LeadQuality, PrimaryObjection

logger = logging.getLogger(__name__)


class AIService(ABC):
    """Abstract interface for LLM Structured Call Intelligence extraction."""

    @abstractmethod
    async def analyze_call(self, transcript: str, metadata: Optional[dict] = None) -> CallIntelligence:
        """Analyze a call transcript and extract structured business intelligence."""
        pass


class MockAIService(AIService):
    """Mock LLM provider returning realistic structured intelligence validated by Pydantic."""

    async def analyze_call(self, transcript: str, metadata: Optional[dict] = None) -> CallIntelligence:
        logger.info("[MockAIService] Generating mock structured intelligence (Simulation mode active)")

        # Target a realistic follow-up time (e.g. upcoming Friday at 15:00 UTC)
        now = datetime.now(timezone.utc)
        days_ahead = (4 - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_follow_up = (now + timedelta(days=days_ahead)).replace(hour=15, minute=0, second=0, microsecond=0)

        # Basic context-sensitive heuristic for mock mode
        lower_transcript = transcript.lower()
        if "not interested" in lower_transcript or "don't call" in lower_transcript:
            return CallIntelligence(
                call_summary="Customer declined services stating no requirement.",
                call_outcome=CallOutcome.NOT_INTERESTED,
                lead_quality=LeadQuality.COLD,
                primary_objection=PrimaryObjection.NO_NEED,
                customer_intent="Politely refuse further contact",
                next_action="Mark lead as unqualified/closed",
                follow_up_at=None,
                agent_quality_notes="Agent acknowledged objection politely and concluded call.",
                review_flag=False,
            )

        # Default standard sales qualification response
        return CallIntelligence(
            call_summary=(
                "Telecaller followed up on marketing inquiry. Customer interested in scaling digital ad campaigns "
                "next quarter but raised budget constraints. Requested pricing breakdown and case studies with a scheduled "
                "walkthrough this Friday at 3 PM."
            ),
            call_outcome=CallOutcome.FOLLOW_UP,
            lead_quality=LeadQuality.WARM,
            primary_objection=PrimaryObjection.PRICE,
            customer_intent="Explore digital ad packages and evaluate pricing relative to available budget",
            next_action="Send standard tier pricing sheet and prepare for Friday 3 PM strategy walkthrough",
            follow_up_at=target_follow_up,
            agent_quality_notes="Clear pitch and good objection handling. Proactively proposed and locked in a firm follow-up slot.",
            review_flag=False,
        )


class RealAIService(AIService):
    """Real LLM provider using OpenAI GPT via httpx."""

    def __init__(self, provider: str, api_key: str):
        self.provider = provider
        self.api_key = api_key

    async def analyze_call(self, transcript: str, metadata: Optional[dict] = None) -> CallIntelligence:
        import httpx
        import json

        if not self.api_key:
            raise ValueError("AI API key is not configured.")

        system_prompt = (
            "You are an expert sales manager and AI call analyzer. Analyze the provided telecaller-customer transcript "
            "and extract structured business intelligence. "
            "You MUST output ONLY valid JSON matching the exact schema requirements without any markdown wrappers."
        )

        url = "https://api.openai.com/v1/chat/completions"
        model_name = "gpt-4o-mini"
        
        if self.provider == "groq":
            url = "https://api.groq.com/openai/v1/chat/completions"
            model_name = "openai/gpt-oss-20b"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        schema_info = CallIntelligence.model_json_schema()
        
        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt + f"\n\nJSON Schema:\n{json.dumps(schema_info)}"},
                {"role": "user", "content": f"Transcript:\n{transcript}"}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1
        }

        timeout = get_settings().ai_timeout_seconds
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=data)
                response.raise_for_status()
                
                content = response.json()["choices"][0]["message"]["content"]
                return CallIntelligence.model_validate_json(content)
                
        except httpx.HTTPStatusError as exc:
            logger.error(f"[RealAIService] HTTP Error: {exc.response.text}")
            raise ValueError(f"Groq API Error: {exc.response.text}") from exc
        except Exception as exc:
            logger.error(f"[RealAIService] Analysis failed: {exc}")
            raise


def get_ai_service(settings: Optional[Settings] = None) -> AIService:
    """Factory to retrieve configured AI intelligence provider."""
    cfg = settings or get_settings()
    if cfg.mock_mode or cfg.ai_provider == "mock" or not cfg.ai_api_key:
        return MockAIService()
    return RealAIService(provider=cfg.ai_provider, api_key=cfg.ai_api_key)
