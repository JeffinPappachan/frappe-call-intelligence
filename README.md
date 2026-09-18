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
| `FRAPPE_BASE_URL` | `https://crm-chm-lly.nvi.frappe.cloud` | Target Frappe Cloud C| `AI_API_KEY` | `""` | API key for LLM structured extraction |
| `STT_PROVIDER` | `mock` | `mock`, `groq`, `openai`, or `gemini` |
| `STT_API_KEY` | `""` | API key for Speech-to-Text Whisper transcription |
| `ALLOWED_ORIGINS` | `["http://localhost:8000", ...]` | Explicit CORS allowed origins list |
| `STORE_TRANSCRIPT_IN_CRM` | `truncated` | `full`, `truncated` (500 char cap), or `none` |
| `ADMIN_API_TOKEN` | `""` | Optional admin token to protect `/api/models` |
| `IDEMPOTENCY_TTL_SECONDS` | `86400` | Idempotency record expiration time (24h default) |
| `IDEMPOTENCY_MAX_ITEMS` | `1000` | Maximum capacity for in-memory idempotency cache (LRU) |

---

## 5. Running the Application

### Start FastAPI Server

```powershell
uvicorn src.server:app --reload --host 0.0.0.0 --port 8000
```

- API Documentation (Swagger UI): [http://localhost:8000/docs](http://localhost:8000/docs)
- Manager Dashboard UI: [http://localhost:8000/dashboard](http://localhost:8000/dashboard)
- Health Check: [http://localhost:8000/health](http://localhost:8000/health)

### API Endpoints
- `POST /api/v1/telephony/webhook`: Core entrypoint for Exotel/Twilio call completion webhooks (Idempotent deduplication guard).
- `POST /api/v1/telephony/process-audio`: Direct audio upload (WAV, MP3, M4A) with 25MB streaming cap, MIME validation, and empty-file protection.
- `GET /api/v1/dashboard/metrics`: Analytics endpoint returning aggregated KPIs, telecaller metrics, and follow-ups.
- `GET /api/v1/dashboard/calls`: Returns feed of recent calls processed by the pipeline.
- `GET /api/models`: Public safe model metadata endpoint protected by optional `X-Admin-Token`.

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
.\.venv\Scripts\python.exe -m pytest -v
```

Tests cover:
- Pydantic schema validation & enum constraints
- Webhook payload validation & rejection of malformed inputs
- Audio upload security (empty file rejection, 25MB limit, MIME & extension checks)
- STT transcription behavior (English & Malayalam)
- LLM structured analysis logic
- Idempotency guard, TTL expiration, capacity bounding, and duplicate webhook suppression
- Frappe CRM client error handling (raise_for_status validation on comments, non-fatal task failure)
- FastAPI HTTP endpoint contracts (`/health`, `/webhook`, `/process-audio`, `/api/models`, `/dashboard`)

---

## 7. Mock Mode Usage

`MOCK_MODE=true` is enabled by default. This ensures:
- Full pipeline execution runs offline without paid external API keys.
- Leads from the live Frappe instance (Carol Smith, Bob Martinez, etc.) are pre-seeded in the mock store.
- Telephony webhooks, speech transcription, structured analysis, and follow-up generation are fully testable.
- Once real API credentials (`FRAPPE_API_KEY`, `AI_API_KEY`) are supplied, setting `MOCK_MODE=false` connects the pipeline directly to Frappe Cloud and external AI providers.

---

## 8. Live Frappe CRM Setup

To connect to the live CRM instance at `https://crm-chm-lly.nvi.frappe.cloud`:
1. Generate an API Key and Secret from your Frappe User profile (in Desk -> Settings -> API Access).
2. Add them to `.env` as `FRAPPE_API_KEY` and `FRAPPE_API_SECRET`.
3. Set `MOCK_MODE=false`.
4. Ensure target Leads exist in the CRM (matching phone numbers).
The pipeline will now lookup live leads, create actual CRM Call Logs, update Lead stages, and assign Follow-up Tasks to CRM Users.

---

## 9. Current Status & Verification

- [x] Phase 1: Foundation scaffolded (schemas, STT/AI interfaces, Frappe client, pipeline, FastAPI server).
- [x] Phase 1: Webhook idempotency and deduplication guard implemented.
- [x] Phase 1: Connect live Frappe Cloud credentials and verify live REST write-back.
- [x] Phase 2: Implement Manager Dashboard UI (http://localhost:8000/dashboard).
- [x] Phase 2: Implement Backend Analytics (`/metrics`, `/calls`).
- [x] Phase 4: Full automated test suite passing (20 baseline regression tests).
- [x] Phase 5.1: Production Hardening — Security & Reliability (SEC-01 through SEC-05, REL-01).
- [x] Phase 5.2: Production Hardening — Reliability & Data Integrity (SEC-06 Audio Validation, REL-02 Frappe Comment Status Checks, REL-03 Idempotency TTL & Bounding & SQLite Store).
- [x] Phase 5.3: Observability & Production Readiness (Structured JSON Logging, Request Correlation ID, `/ready` and `/metrics` Endpoints, AppError Classification, Production Docker & Compose).
- [x] Automated test suite passing (52 passing tests, 0 failures).

---

## 10. Observability & Monitoring

### Endpoints
- **Liveness (`GET /health`)**: Returns `200 OK` and active environment metadata when process is alive.
- **Readiness (`GET /ready`)**: Returns `200 OK` when dependencies (CRM credentials, AI/STT keys, storage) are valid. Returns `503 Service Unavailable` with structured diagnostic reasons if unconfigured.
- **Application Metrics (`GET /metrics`)**: Exposes structured operational counters, processing durations, and failure classification metrics without exposing PII or unbounded labels.

### Request Correlation
Every request accepts or generates a validated `X-Request-ID` (`req_<hex16>`). The correlation ID propagates through async contexts and is returned in HTTP response headers and structured JSON logs.

### Production Deployment & Idempotency Store
- **Single-Worker In-Memory (`IDEMPOTENCY_BACKEND=memory`)**: High-speed, TTL-expiring bounded LRU cache for development or single-worker deployments.
- **Single-Instance SQLite (`IDEMPOTENCY_BACKEND=sqlite`)**: Persistent, file-backed idempotency surviving process restarts.
- **Containerized Run**:
  ```powershell
  docker-compose up -d --build
  ```
