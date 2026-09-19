import logging
from abc import ABC, abstractmethod

from src.config import Settings, get_settings
from src.schemas import CallIntelligence, CallOutcome, LeadQuality, PrimaryObjection

logger = logging.getLogger(__name__)


class AIService(ABC):
    """Abstract interface for LLM Structured Call Intelligence extraction."""

    @abstractmethod
    async def analyze_call(self, transcript: str, metadata: dict | None = None) -> CallIntelligence:
        """Analyze a call transcript and extract structured business intelligence."""


class MockAIService(AIService):
    """Mock LLM provider returning realistic structured intelligence validated by Pydantic."""

    async def analyze_call(self, transcript: str, metadata: dict | None = None) -> CallIntelligence:
        logger.info("[MockAIService] Generating mock structured intelligence (Simulation mode active)")

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

        # Check for course fee inquiry in transcript
        if "course" in lower_transcript or "fees" in lower_transcript:
            return CallIntelligence(
                call_summary="Customer inquired about course fees and agreed to receive details tomorrow with a follow-up call.",
                call_outcome=CallOutcome.FOLLOW_UP,
                lead_quality=LeadQuality.WARM,
                primary_objection=PrimaryObjection.NONE,
                customer_intent="Obtain course fee information",
                next_action="Send course fee details and call back tomorrow",
                recommended_action="Send course fee details and call back tomorrow",
                follow_up_required=True,
                follow_up_date=None,
                follow_up_notes="Customer requested callback tomorrow regarding course fees",
                key_points=["Customer interested in course", "Requested fee details", "Agreed to follow-up call tomorrow"],
                objections=[],
                agent_quality_notes="Agent answered politely and scheduled a follow-up callback.",
                review_flag=False,
            )

        # Default standard qualification response
        return CallIntelligence(
            call_summary="Telecaller connected with customer who requested further information via follow-up.",
            call_outcome=CallOutcome.FOLLOW_UP,
            lead_quality=LeadQuality.WARM,
            primary_objection=PrimaryObjection.NONE,
            customer_intent="Obtain further information",
            next_action="Follow up with requested details",
            recommended_action="Follow up with requested details",
            follow_up_required=True,
            follow_up_date=None,
            follow_up_notes="Customer requested follow-up with further details",
            key_points=["Initial inquiry", "Follow-up requested"],
            objections=[],
            agent_quality_notes="Agent engaged professionally and agreed on next steps.",
            review_flag=False,
        )


class RealAIService(AIService):
    """Real LLM provider using OpenAI GPT or Groq via httpx."""

    def __init__(self, provider: str, api_key: str):
        self.provider = provider
        self.api_key = api_key

    async def analyze_call(self, transcript: str, metadata: dict | None = None) -> CallIntelligence:
        import json

        import httpx

        if not self.api_key:
            raise ValueError(
                "AI provider is not configured. Please configure the AI API key or explicitly enable mock mode."
            )

        meta = metadata or {}
        event_time_str = meta.get("event_timestamp") or meta.get("call_time") or "Current Date/Time"

        system_prompt = (
            "You are an expert sales manager and AI call analyzer. Analyze the provided telecaller-customer transcript "
            "and extract structured business intelligence based ONLY on the actual transcript text.\n"
            "CRITICAL INSTRUCTIONS:\n"
            "- Analyze only the supplied transcript.\n"
            "- Do not use examples or context from previous calls or any demo scenarios (such as BrightPath Ltd, digital ad campaigns, Friday 3 PM, pricing sheet).\n"
            "- Do not invent facts, companies, dates, or details not present in the transcript.\n"
            "- Return null, an empty list ([]), or 'Unknown' when information is unavailable or unsupported by the transcript.\n"
            "- If a detail or objection is not mentioned in the transcript, set objections to [] and primary_objection to 'None'.\n"
            "- If follow-up is requested or agreed upon in the transcript (e.g. 'please call me tomorrow' or 'send details tomorrow'), set follow_up_required = true.\n"
            "- If follow_up_required is false, set follow_up_at = null, follow_up_date = null, follow_up_notes = null.\n"
            "- If follow-up is mentioned relatively (e.g. 'tomorrow') without a specific hour, do not invent an arbitrary time. "
            "Leave follow_up_date as null unless an exact date/time can be confidently determined, and describe the callback request in follow_up_notes.\n"
            "- Ensure the following fields are accurately generated from the actual transcript: summary (call_summary), customer_intent, outcome (call_outcome), "
            "lead_quality, key_points, objections, recommended_action, review_flag, follow_up_required, follow_up_date, follow_up_notes, agent_quality_notes.\n"
            "- Output strictly valid JSON matching the exact schema requirements without any markdown wrappers."
        )

        user_content = (
            f"Analyze the following call transcript.\n\n"
            f"Transcript:\n{transcript}\n\n"
            f"Context Timestamp: {event_time_str}\n\n"
            f"Return structured call intelligence based only on this transcript. "
            f"Do not invent facts. Do not use examples from previous calls or demo data. "
            f"If a field is not supported by the transcript, return null, an empty list, or 'Unknown' according to the schema."
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
                {"role": "user", "content": user_content}
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
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ValueError(f"Invalid LLM response format: {content}")

                # Normalize missing or null fields
                if not parsed.get("next_action"):
                    parsed["next_action"] = parsed.get("recommended_action") or "No further action required"
                if not parsed.get("recommended_action"):
                    parsed["recommended_action"] = parsed.get("next_action") or "No further action required"
                if not parsed.get("agent_quality_notes"):
                    parsed["agent_quality_notes"] = "Good call adherence and clear communication."
                if not parsed.get("customer_intent"):
                    parsed["customer_intent"] = "Customer inquiry"
                if not parsed.get("call_summary"):
                    parsed["call_summary"] = "Call completed."

                # Normalize enum string casing if necessary
                if "call_outcome" in parsed and isinstance(parsed["call_outcome"], str):
                    co = parsed["call_outcome"].strip().title()
                    if co in ["Follow-Up", "Followup", "Follow_Up"]:
                        co = "Follow-up"
                    elif co in ["No-Response", "No_Response"]:
                        co = "No Response"
                    elif co in ["No-Answer", "No_Answer"]:
                        co = "No Answer"
                    elif co in ["Not-Interested", "Not_Interested"]:
                        co = "Not Interested"
                    parsed["call_outcome"] = co

                if "lead_quality" in parsed and isinstance(parsed["lead_quality"], str):
                    parsed["lead_quality"] = parsed["lead_quality"].strip().title()

                if "primary_objection" in parsed and isinstance(parsed["primary_objection"], str):
                    po = parsed["primary_objection"].strip().title()
                    if po in ["No-Need", "No_Need", "Noneed"]:
                        po = "No need"
                    parsed["primary_objection"] = po

                return CallIntelligence.model_validate(parsed)

        except httpx.HTTPStatusError as exc:
            logger.error(f"[RealAIService] HTTP Error: {exc.response.text}")
            raise ValueError(f"AI API Error: {exc.response.text}") from exc
        except Exception as exc:
            logger.error(f"[RealAIService] Analysis failed: {exc}")
            raise


def get_ai_service(settings: Settings | None = None) -> AIService:
    """Factory to retrieve configured AI intelligence provider."""
    cfg = settings or get_settings()
    if cfg.mock_mode or cfg.ai_provider == "mock":
        return MockAIService()
    if not cfg.ai_api_key:
        raise ValueError("AI provider is not configured. Configure AI_API_KEY or set MOCK_MODE=true.")
    return RealAIService(provider=cfg.ai_provider, api_key=cfg.ai_api_key)

