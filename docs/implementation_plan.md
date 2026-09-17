# Implementation Plan: AI Call Intelligence with Frappe CRM (Test Work 01)

Build an end-to-end telecaller intelligence automation pipeline connected to Frappe CRM. The pipeline processes call audio recordings via a realistic telephony webhook, performs speech-to-text transcription, runs structured AI business analysis, writes Call Logs and Follow-up Tasks back to Frappe CRM, and provides a manager analytics dashboard.

---

## User Review Required

> [!IMPORTANT]
> **Frappe Cloud API Access:**
> You already have a live Frappe CRM running at `https://crm-chm-lly.nvi.frappe.cloud`. To enable programmatically writing Call Logs, Tasks, and updating Leads, we need an **API Key** and **API Secret** from your Frappe user profile.
> 
> *Steps to generate:*
> 1. In your Frappe instance, go to **Desk** (`/desk` or top right avatar $\to$ Settings).
> 2. Open your User document (**Jeffin Pappachan** or **Administrator**).
> 3. Scroll to the **API Access** section and click **Generate Keys**.
> 4. Copy the generated **API Key** and **API Secret** into our `.env` file.

> [!IMPORTANT]
> **AI Provider Selection for STT & LLM:**
> The assessment strongly prefers English + Malayalam speech transcription and structured JSON extraction.
> We recommend:
> - **Groq** (`whisper-large-v3` for fast, accurate STT + `llama-3.3-70b-versatile` for sub-second structured JSON extraction) OR
> - **OpenAI** (`whisper-1` + `gpt-4o-mini`) OR
> - **Google Gemini** (audio multimodal transcription + structured extraction).
> Please confirm which API key you have available.

---

## Architecture & Data Flow

```
[ Sample Audio File / Telephony Webhook ]
                  │
                  ▼
┌───────────────────────────────────────────────┐
│ FastAPI Telephony Gateway (Webhook Receiver)  │
│ - Validates payload (Exotel / Twilio schema)  │
│ - Idempotency guard (Provider Call ID check)  │
│ - Asynchronous background worker dispatch     │
└───────────────────────┬───────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────┐
│ Speech-to-Text Layer (Whisper STT)            │
│ - Supports English & Malayalam code-switching │
│ - Outputs timestamped transcript              │
└───────────────────────┬───────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────┐
│ Structured LLM Call Intelligence Extractor    │
│ - Pydantic schema validation                  │
│ - Fields: summary, outcome, lead quality,     │
│   intent, primary objection, next action,     │
│   follow_up_at, agent notes, review flag      │
└───────────────────────┬───────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────┐
│ Frappe CRM REST Client                        │
│ - Match Lead by phone (+1 555 000 9012, etc.) │
│ - Create CRM Call Log (linked to Lead & Agent)│
│ - Update Lead status & quality                │
│ - Auto-create scheduled CRM Task / Follow-up  │
└───────────────────────┬───────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────┐
│ Manager Call Intelligence Dashboard           │
│ - Team call volume, completion rates          │
│ - Call outcome & lead quality distributions   │
│ - Overdue / Due follow-ups tracking           │
│ - Direct drill-down to transcripts & analysis │
└───────────────────────────────────────────────┘
```

---

## Proposed Changes

### Core Project Layout (`e:\Hash`)

```
e:\Hash\
├── docs\
│   ├── environment-inspection.md      # Completed initial inspection
│   ├── architecture-diagram.md        # Mermaid diagrams & system design
│   ├── api-telephony-spec.md          # Webhook payloads (Exotel/Twilio) & curl examples
│   ├── technical-questions.md         # Detailed answers to the 7 assessment questions
│   └── production-readiness.md        # Concurrency, retention, costs, privacy notes
├── samples\
│   ├── sample_call_english.wav        # Standard English sales call audio
│   └── sample_call_malayalam.wav      # Malayalam / Manglish inquiry call audio
├── src\
│   ├── __init__.py
│   ├── config.py                      # Pydantic Settings & environment loader
│   ├── schemas.py                     # Webhook payload & AI extraction schemas
│   ├── stt_service.py                 # Whisper transcription client (Groq / OpenAI)
│   ├── ai_service.py                  # Structured extraction & validation logic
│   ├── frappe_client.py               # Frappe CRM REST API client (Call Log, Lead, Task)
│   ├── pipeline.py                    # End-to-end orchestration & idempotency cache
│   ├── server.py                      # FastAPI server (webhooks, manual trigger, health)
│   └── static\
│       └── dashboard.html             # Executive Manager Dashboard (Vanilla JS + Modern CSS)
├── tests\
│   ├── test_schemas.py                # Schema validation unit tests
│   ├── test_idempotency.py            # Duplicate webhook prevention tests
│   └── test_pipeline_e2e.py           # End-to-end simulation test
├── .env.example
├── README.md
└── requirements.txt
```

---

## Detailed Implementation Modules

### 1. Telephony Webhook Ingestion & Idempotency Layer
- Endpoints:
  - `POST /api/v1/telephony/webhook`: Accepts realistic provider webhook payloads (CallSid/CallId, From, To, Duration, RecordingUrl, Direction, Status).
  - `POST /api/v1/telephony/process-audio`: Direct multipart file upload for testing arbitrary audio recordings.
- Idempotency:
  - Tracks `provider_call_id` in an in-memory or SQLite deduplication table.
  - Repeated webhook events for the same `provider_call_id` return `200 OK` with cached result without duplicating Frappe CRM records.

### 2. Speech-to-Text & Structured Intelligence Engine
- STT Layer:
  - Transcribes audio file or URL using Whisper API.
  - Tested with English and Malayalam samples.
- LLM Intelligence:
  - Enforces JSON Schema using Pydantic:
    ```python
    class CallIntelligence(BaseModel):
        call_summary: str
        call_outcome: Literal["Interested", "Follow-up", "Not Interested", "No Answer", "Other"]
        lead_quality: Literal["Hot", "Warm", "Cold"]
        primary_objection: Literal["Price", "Timing", "Competitor", "No need", "Other"]
        customer_intent: str
        next_action: str
        follow_up_at: Optional[datetime]
        agent_quality_notes: str
        review_flag: bool
    ```

### 3. Frappe CRM Integration Client
- Authenticates with Frappe Cloud instance (`https://crm-chm-lly.nvi.frappe.cloud`) using `token {api_key}:{api_secret}`.
- Operations:
  1. Look up Lead by phone number (e.g. Carol Smith `+1 555 000 9012`, Bob Martinez `+1 555 000 5678`).
  2. Create `CRM Call Log` with duration, direction, status, recording URL, transcript, and AI summary.
  3. If `follow_up_at` or `next_action` is indicated, automatically create a `CRM Task` assigned to the telecaller linked to the Lead.
  4. Update Lead status and notes with AI insights.

### 4. Executive Manager Dashboard
- Clean, responsive dashboard matching modern dark/glassmorphic aesthetics.
- Key Metrics:
  - Total Calls, Completed vs Missed/Failed, Average Duration.
  - Telecaller Breakdown (calls per agent, e.g., John Parker vs Sarah Connor).
  - Outcome & Lead Quality distribution charts.
  - Follow-up tracker (Due, Overdue, Scheduled).
  - Recent Call Feed with expandable transcript, audio player, and structured AI results.
- Trigger Controls:
  - Interactive "Simulate Inbound/Outbound Call" button and "Upload Audio" button for live evaluator demonstrations.

---

## Verification Plan

### Automated Verification
- Run `pytest tests/` covering:
  1. Payload validation and schema matching.
  2. Webhook idempotency (sending identical webhook twice must produce exactly one CRM call log).
  3. LLM structured JSON output validation.
  4. Frappe CRM client mocking and live API health check.

### Manual End-to-End Verification
1. Start FastAPI server (`uvicorn src.server:app --reload`).
2. Replay sample curl webhook representing an outbound call from telecaller John Parker to lead Carol Smith.
3. Verify Whisper transcription produces the correct transcript.
4. Verify LLM correctly extracts all 9 required fields.
5. Check Frappe Cloud CRM (`https://crm-chm-lly.nvi.frappe.cloud`):
   - Confirm new Call Log appears under Call Logs.
   - Confirm Lead status is updated.
   - Confirm Follow-up Task is created in Tasks view.
6. Open Manager Dashboard in browser and verify metrics, recent call entry, and drill-downs.

---

## Open Questions & Next Steps

1. **Which LLM/STT provider API key do you prefer to use?** (Groq, OpenAI, or Google Gemini?)
2. Once you provide the Frappe API Key/Secret and AI Key (or allow us to set up the `.env` template), we will proceed immediately to build and verify Phase 1 and Phase 2.
