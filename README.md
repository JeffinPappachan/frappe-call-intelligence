# AI Call Intelligence with Frappe CRM

> **Assessment Track:** Test Work 01 — Hash Adz Creative Consultant AI Automation Developer Technical Test  
> **Version:** 2.0 (September 2026)  
> **Target CRM:** Frappe Cloud CRM (`https://crm-chm-lly.nvi.frappe.cloud`)  
> **Core Stack:** Python 3.13, FastAPI, Pydantic v2, HTTPX, Pytest

---

## 1. Project Purpose

This system implements an automated telecaller intelligence pipeline that bridges telephony audio/webhooks with Frappe CRM:
1. Ingests completed telephony call events (Exotel / Twilio compatible) or audio recordings.
2. Transcribes voice audio to text (supporting English & Malayalam).
3. Extracts structured business intelligence via LLM with strict validation.
4. Synchronizes records into Frappe CRM (linking Call Logs to Leads, updating Lead status, and scheduling follow-up Tasks for telecallers).
5. Provides an executive Manager Dashboard summarizing call volume, outcomes, objection trends, and follow-ups.

---

## 2. Architecture & Pipeline Flow

```
Telephony Webhook / Audio Input
              │
              ▼
FastAPI Webhook Gateway (Idempotency Guard)
              │
              ▼
Speech-to-Text Layer (Whisper / MockSTT)
              │
              ▼
Structured LLM Extraction (Pydantic Schema)
              │
              ▼
Frappe Cloud CRM REST Client (Token Auth)
    ├── Lookup Lead by Phone Number
    ├── Create CRM Call Log (Transcript + Structured AI Fields)
    ├── Auto-schedule Follow-up CRM Task (Assigned to Telecaller)
    └── Update Lead Stage & Quality
              │
              ▼
Manager Dashboard
```

For the complete Mermaid system diagram, see [docs/architecture.md](docs/architecture.md).

---

## 3. Project Structure

```
e:\Hash
├── docs/
│   ├── environment-inspection.md   # System inspection & tool verification
│   └── architecture.md             # Mermaid architecture diagrams
├── samples/
│   ├── mock_webhook_payload.json   # Sample Exotel-format outbound call payload
│   └── mock_webhook_malayalam.json # Sample Malayalam call payload
├── src/
│   ├── __init__.py
│   ├── config.py                   # Pydantic Settings & environment loader
│   ├── schemas.py                  # Pydantic schemas (Webhook, CallIntelligence, PipelineResponse)
│   ├── stt_service.py              # STT abstraction (MockSTT + real provider ready)
│   ├── ai_service.py               # AI extraction abstraction (MockAI + real provider ready)
│   ├── frappe_client.py            # Frappe CRM HTTPX client with Token Auth & simulation
│   ├── pipeline.py                 # Core orchestration & idempotency cache
│   └── server.py                   # FastAPI REST API
├── tests/
│   ├── __init__.py
│   ├── test_schemas.py             # Schema & field validation tests
│   ├── test_ai_stt.py              # STT and AI service unit tests
│   ├── test_idempotency.py         # Webhook deduplication tests
│   └── test_api.py                 # FastAPI endpoint integration tests
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 4. Environment Setup

### Prerequisites
- Windows 11 / Linux / macOS
- Python 3.13+
- Git

### 1. Create and Activate Virtual Environment

```powershell
# In e:\Hash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install Dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Copy `.env.example` to `.env`:

```powershell
cp .env.example .env
```

Key environment variables in `.env`:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `MOCK_MODE` | `true` | When `true`, runs offline without external API keys |
| `FRAPPE_BASE_URL` | `https://crm-chm-lly.nvi.frappe.cloud` | Target Frappe Cloud CRM base URL |
| `FRAPPE_API_KEY` | `""` | Frappe User API Key (from `/desk` $\to$ User $\to$ API Access) |
| `FRAPPE_API_SECRET` | `""` | Frappe User API Secret |
| `AI_PROVIDER` | `mock` | `mock`, `groq`, `openai`, or `gemini` |
| `AI_API_KEY` | `""` | API key for LLM structured extraction |
| `STT_PROVIDER` | `mock` | `mock`, `groq`, `openai`, or `gemini` |
| `STT_API_KEY` | `""` | API key for Speech-to-Text Whisper transcription |

---

## 5. Running the Application

### Start FastAPI Server

```powershell
uvicorn src.server:app --reload --host 0.0.0.0 --port 8000
```

- API Documentation (Swagger UI): [http://localhost:8000/docs](http://localhost:8000/docs)
- Health Check: [http://localhost:8000/health](http://localhost:8000/health)

### Test the Webhook with Sample Data

Using PowerShell or `curl.exe`:

```powershell
curl.exe -X POST http://localhost:8000/api/v1/telephony/webhook `
  -H "Content-Type: application/json" `
  -d "@samples/mock_webhook_payload.json"
```

---

## 6. Running the Test Suite

Run the automated test suite with pytest:

```powershell
pytest -v
```

Tests cover:
- Pydantic schema validation & enum constraints
- Webhook payload validation & rejection of malformed inputs
- STT transcription behavior (English & Malayalam)
- LLM structured analysis logic
- Idempotency guard and duplicate webhook suppression
- FastAPI HTTP endpoint contracts (`/health`, `/webhook`, `/process-audio`)

---

## 7. Mock Mode Usage

`MOCK_MODE=true` is enabled by default. This ensures:
- Full pipeline execution runs offline without paid external API keys.
- Leads from the live Frappe instance (Carol Smith, Bob Martinez, etc.) are pre-seeded in the mock store.
- Telephony webhooks, speech transcription, structured analysis, and follow-up generation are fully testable.
- Once real API credentials (`FRAPPE_API_KEY`, `AI_API_KEY`) are supplied, setting `MOCK_MODE=false` connects the pipeline directly to Frappe Cloud and external AI providers.

---

## 8. Current Foundation & Next Implementation Steps

- [x] Foundation scaffolded (schemas, STT/AI interfaces, Frappe client, pipeline, FastAPI server).
- [x] Webhook idempotency and deduplication guard implemented.
- [x] Automated test suite passing.
- [ ] Connect live Frappe Cloud credentials (API Key & Secret) and verify live REST write-back.
- [ ] Connect live STT/LLM provider (Groq / OpenAI Whisper & LLaMA/GPT-4o).
- [ ] Implement Manager Dashboard UI.
- [ ] Record end-to-end demonstration and complete documentation deliverables.
