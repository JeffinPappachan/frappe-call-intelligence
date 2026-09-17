from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator


class CallOutcome(str, Enum):
    INTERESTED = "Interested"
    FOLLOW_UP = "Follow-up"
    NOT_INTERESTED = "Not Interested"
    NO_ANSWER = "No Answer"
    OTHER = "Other"


class LeadQuality(str, Enum):
    HOT = "Hot"
    WARM = "Warm"
    COLD = "Cold"


class PrimaryObjection(str, Enum):
    PRICE = "Price"
    TIMING = "Timing"
    COMPETITOR = "Competitor"
    NO_NEED = "No need"
    OTHER = "Other"
    NONE = "None"


class CallDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallStatus(str, Enum):
    COMPLETED = "completed"
    NO_ANSWER = "no-answer"
    FAILED = "failed"
    BUSY = "busy"
    CANCELED = "canceled"


class TelephonyWebhookPayload(BaseModel):
    """Schema representing incoming webhook payload from a telephony provider (e.g. Exotel / Twilio)."""

    provider_call_id: str = Field(..., description="Unique provider call identifier for deduplication/idempotency")
    telephony_provider: str = Field(default="exotel", description="Telephony service provider name")
    from_number: str = Field(..., description="Caller phone number (E.164 or local format)")
    to_number: str = Field(..., description="Callee phone number (E.164 or local format)")
    direction: CallDirection = Field(default=CallDirection.OUTBOUND, description="Direction of the call")
    call_status: CallStatus = Field(default=CallStatus.COMPLETED, description="Call completion status")
    duration_seconds: int = Field(default=0, ge=0, description="Call duration in seconds")
    recording_url: Optional[str] = Field(default=None, description="Public or presigned URL to the call audio recording")
    agent_id: Optional[str] = Field(default=None, description="Telecaller email or system ID")
    call_type: Optional[str] = Field(default="sales_enquiry", description="Category or purpose of the call")
    event_timestamp: Optional[datetime] = Field(default=None, description="Provider timestamp of the event")

    @field_validator("provider_call_id")
    @classmethod
    def validate_provider_call_id(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("provider_call_id cannot be empty")
        return clean


class AudioProcessRequest(BaseModel):
    """Manual audio upload or processing request payload."""

    provider_call_id: Optional[str] = Field(default=None, description="Optional custom call identifier")
    lead_phone: str = Field(..., description="Phone number associated with the CRM Lead")
    agent_id: Optional[str] = Field(default="agent@example.com", description="Telecaller identifier")
    direction: CallDirection = Field(default=CallDirection.OUTBOUND)
    duration_seconds: int = Field(default=60, ge=0)


class CallIntelligence(BaseModel):
    """Structured AI Call Analysis output as mandated by the technical assessment."""

    call_summary: str = Field(..., description="Concise business summary of the call")
    call_outcome: CallOutcome = Field(..., description="Categorized business outcome of the call")
    lead_quality: LeadQuality = Field(..., description="Assessed lead quality (Hot / Warm / Cold)")
    primary_objection: PrimaryObjection = Field(
        default=PrimaryObjection.NONE,
        description="Main customer objection (Price / Timing / Competitor / No need / Other / None)",
    )
    customer_intent: str = Field(..., description="What the customer was trying to achieve or inquire about")
    next_action: str = Field(..., description="Concrete next sales/operational action")
    follow_up_at: Optional[datetime] = Field(
        default=None,
        description="Validated ISO date and time for follow-up, or null if not applicable",
    )
    agent_quality_notes: str = Field(
        ...,
        description="Observations regarding telecaller adherence, greeting, pacing, and tone",
    )
    review_flag: bool = Field(
        default=False,
        description="Whether this call requires manual supervisor/manager review",
    )


class PipelineResponse(BaseModel):
    """Unified response returned after processing a call through the intelligence pipeline."""

    success: bool
    provider_call_id: str
    idempotent_replay: bool = Field(
        default=False,
        description="Indicates whether this event was already processed and returned from deduplication cache",
    )
    transcript: Optional[str] = None
    intelligence: Optional[CallIntelligence] = None
    matched_lead: Optional[str] = None
    frappe_call_log_id: Optional[str] = None
    frappe_task_id: Optional[str] = None
    message: str = "Call successfully processed"
    
    # Metadata for dashboard
    duration_seconds: Optional[int] = None
    direction: Optional[CallDirection] = None
    event_timestamp: Optional[datetime] = None
    agent_id: Optional[str] = None


class DashboardMetricsResponse(BaseModel):
    total_calls: int = 0
    completed_calls: int = 0
    missed_calls: int = 0
    average_call_duration_seconds: float = 0.0
    calls_per_telecaller: Dict[str, int] = {}
    lead_quality_distribution: Dict[str, int] = {}
    call_outcome_distribution: Dict[str, int] = {}
    follow_ups_due: int = 0
    follow_ups_overdue: int = 0


class DashboardCallFeedResponse(BaseModel):
    calls: List[PipelineResponse] = []

