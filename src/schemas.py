from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class CallOutcome(str, Enum):
    INTERESTED = "Interested"
    FOLLOW_UP = "Follow-up"
    NOT_INTERESTED = "Not Interested"
    CONVERTED = "Converted"
    CALLBACK_REQUESTED = "Callback Requested"
    NO_RESPONSE = "No Response"
    INVALID = "Invalid"
    NO_ANSWER = "No Answer"
    OTHER = "Other"


class LeadQuality(str, Enum):
    HOT = "Hot"
    WARM = "Warm"
    COLD = "Cold"
    UNKNOWN = "Unknown"


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


class ProcessingStatus(str, Enum):
    RECEIVED = "received"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TelephonyWebhookPayload(BaseModel):
    """Schema representing incoming webhook payload from a telephony provider (e.g. Exotel / Twilio)."""

    provider_call_id: str = Field(..., description="Unique provider call identifier for deduplication/idempotency")
    telephony_provider: str = Field(default="exotel", description="Telephony service provider name")
    from_number: str = Field(..., description="Caller phone number (E.164 or local format)")
    to_number: str = Field(..., description="Callee phone number (E.164 or local format)")
    direction: CallDirection = Field(default=CallDirection.OUTBOUND, description="Direction of the call")
    call_status: CallStatus = Field(default=CallStatus.COMPLETED, description="Call completion status")
    duration_seconds: int = Field(default=0, ge=0, description="Call duration in seconds")
    recording_url: str | None = Field(default=None, description="Public or presigned URL to the call audio recording")
    agent_id: str | None = Field(default=None, description="Telecaller email or system ID")
    call_type: str | None = Field(default="sales_enquiry", description="Category or purpose of the call")
    event_timestamp: datetime | None = Field(default=None, description="Provider timestamp of the event")
    lead_id: str | None = Field(default=None, description="Explicit CRM Lead / Contact ID if known")
    lead_name: str | None = Field(default=None, description="Explicit CRM Contact or Lead name")

    @field_validator("provider_call_id")
    @classmethod
    def validate_provider_call_id(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("provider_call_id cannot be empty")
        return clean


class AudioProcessRequest(BaseModel):
    """Manual audio upload or processing request payload."""

    provider_call_id: str | None = Field(default=None, description="Optional custom call identifier")
    lead_phone: str = Field(..., description="Phone number associated with the CRM Lead")
    agent_id: str | None = Field(default="agent@example.com", description="Telecaller identifier")
    direction: CallDirection = Field(default=CallDirection.OUTBOUND)
    duration_seconds: int = Field(default=60, ge=0)
    lead_id: str | None = Field(default=None, description="Explicit CRM Lead / Contact ID")
    lead_name: str | None = Field(default=None, description="Explicit CRM Contact or Lead name")


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
    key_points: list[str] = Field(default_factory=list, description="Key discussion points")

    # Existing compatible fields
    next_action: str = Field(..., description="Concrete next sales/operational action")
    follow_up_at: datetime | None = Field(
        default=None,
        description="Validated ISO date and time for follow-up, or null if not applicable",
    )

    # New Phase 3 AI requested fields
    follow_up_required: bool = Field(default=False, description="Is a follow up required?")
    follow_up_date: datetime | None = Field(default=None, description="Follow up date if explicitly available")
    follow_up_notes: str | None = Field(default=None, description="Notes for the follow up")
    objections: list[str] = Field(default_factory=list, description="List of all objections raised")
    recommended_action: str | None = Field(default=None, description="Recommended next action")

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
    transcript: str | None = None
    intelligence: CallIntelligence | None = None
    matched_lead: str | None = None
    frappe_call_log_id: str | None = None
    frappe_task_id: str | None = None
    message: str = "Call successfully processed"

    # Metadata for dashboard
    duration_seconds: int | None = None
    direction: CallDirection | None = None
    event_timestamp: datetime | None = None
    agent_id: str | None = None
    recording_storage_path: str | None = None

    # State tracking
    processing_status: ProcessingStatus = Field(default=ProcessingStatus.COMPLETED)
    error_message: str | None = None

    # Job queue tracking (Phase 5)
    worker_id: str | None = None
    retry_count: int = Field(default=0)
    next_retry_at: datetime | None = None
    event_payload: dict | None = None


class DashboardMetricsResponse(BaseModel):
    total_calls: int = 0
    completed_calls: int = 0
    missed_calls: int = 0
    average_call_duration_seconds: float = 0.0
    calls_per_telecaller: dict[str, int] = {}
    lead_quality_distribution: dict[str, int] = {}
    call_outcome_distribution: dict[str, int] = {}
    follow_ups_due: int = 0
    follow_ups_overdue: int = 0


class DashboardCallFeedResponse(BaseModel):
    calls: list[PipelineResponse] = []

