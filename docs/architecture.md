# System Architecture — AI Call Intelligence with Frappe CRM

## Overview

The AI Call Intelligence system bridges telephony call events and recordings with Frappe CRM. It ingests call events, executes speech-to-text transcription, runs structured business intelligence extraction with strict Pydantic validation, and updates Frappe CRM records (Call Logs, Leads, and Follow-up Tasks).

---

## Mermaid Architecture Diagram

```mermaid
flowchart TD
    subgraph Ingestion["1. Telephony Ingestion Layer"]
        A1["Telephony Provider<br/>(Exotel / Twilio / Mock Webhook)"] -->|POST /api/v1/telephony/webhook| B1["FastAPI Gateway"]
        A2["Manual Audio Upload<br/>(WAV / MP3 File)"] -->|POST /api/v1/telephony/process-audio| B1
        B1 --> B2["Idempotency Guard<br/>(Provider Call ID Check)"]
    end

    subgraph Intelligence["2. AI Processing Pipeline"]
        B2 -->|New Call Event| C1["Speech-to-Text Layer<br/>(Whisper / MockSTT)"]
        C1 -->|Full Transcript| C2["Structured LLM Extraction<br/>(Groq / OpenAI / MockAI)"]
        C2 -->|Schema Validation| C3["Pydantic CallIntelligence<br/>• Summary<br/>• Outcome<br/>• Lead Quality<br/>• Primary Objection<br/>• Customer Intent<br/>• Next Action<br/>• Follow-up Date/Time<br/>• Agent Quality Notes<br/>• Review Flag"]
    end

    subgraph FrappeCRM["3. Frappe Cloud CRM Write-Back"]
        C3 --> D1["Frappe CRM REST Client<br/>(Token Auth)"]
        D1 -->|1. Lookup Lead by Phone| E1[("CRM Lead<br/>(Carol Smith, Bob Martinez)")]
        D1 -->|2. Create Record| E2[("CRM Call Log<br/>(Metadata, Transcript, AI Fields)")]
        D1 -->|3. If Follow-up required| E3[("CRM Task<br/>(Assigned to Telecaller)")]
        D1 -->|4. Update Stage/Quality| E1
    end

    subgraph ReplayGuard["4. Webhook Idempotency Cache"]
        B2 -.->|Duplicate Call ID| F1["Cached PipelineResponse<br/>(idempotent_replay: true)"]
        C3 -->|Cache Result| F1
    end

    subgraph Reporting["5. Executive Management"]
        E2 --> G1["Manager Call Intelligence Dashboard"]
        E3 --> G1
    end
```

---

## Component Separation & Boundaries

1. **FastAPI Webhook Gateway (`src/server.py`):**
   - Non-blocking entry points for external telephony providers.
   - Idempotency check prevents duplicate Call Log creation on webhook replays.

2. **AI & Speech Providers (`src/stt_service.py`, `src/ai_service.py`):**
   - Pluggable abstract interfaces (`STTService`, `AIService`).
   - Offline `MockSTTService` and `MockAIService` allow instant local testing without external API dependencies.
   - Real implementations (Groq Whisper, OpenAI GPT, or Google Gemini) plug seamlessly into the same interface.

3. **Validation Layer (`src/schemas.py`):**
   - Enforces the 9 assessment-mandated structured business fields via Pydantic v2.
   - Guarantees valid enums and parsed ISO timestamps for follow-up scheduling.

4. **Frappe CRM REST Client (`src/frappe_client.py`):**
   - Communicates with Frappe Cloud (`https://crm-chm-lly.nvi.frappe.cloud`) using HTTP token authentication.
   - Automatically handles Lead phone number normalization, Call Log creation, Follow-up Task assignment, and Lead stage updates.
