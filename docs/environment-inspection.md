# Environment Inspection Report — Test Work 01: AI Call Intelligence with Frappe CRM

**Date:** 2026-09-17  
**Project:** Hash Adz Creative Consultant — AI Automation Developer Assessment  
**Selected Track:** Test Work 01 — AI Call Intelligence with Frappe CRM  
**Target Workspace:** `e:\Hash`

---

## 1. Environment Details

| Component | Status / Version | Notes |
| :--- | :--- | :--- |
| **Operating System** | Microsoft Windows 11 Home Single Language (10.0.22631 64-bit) | Windows host environment |
| **Python** | Python 3.13.3 (64-bit) | Installed and accessible in PATH |
| **Node.js** | v24.21.0 | Installed and accessible in PATH |
| **npm** | 11.19.0 | Installed and accessible in PATH |
| **Git** | git version 2.36.0.windows.1 | Installed and accessible in PATH |
| **Curl** | curl 8.13.0 (Windows libcurl/8.13.0 Schannel) | Installed (`curl.exe`) |
| **Docker Engine** | Docker CLI 29.1.2 installed; **Daemon is NOT running** | `docker-desktop` WSL distribution is stopped |
| **Frappe Bench (Local)** | Not installed on host | Frappe Bench requires a Linux/Unix environment or Docker container |
| **Frappe Cloud CRM** | **ACTIVE & ONLINE** (`https://crm-chm-lly.nvi.frappe.cloud`) | Live Frappe CRM instance configured with sample leads and users |

---

## 2. Assessment Document Status

- **Status:** Fully accessible and analyzed.
- **Title:** HASH ADZ CREATIVE CONSULTANT • AI AUTOMATION DEVELOPER TECHNICAL TEST (Version 2.0 • September 2026)
- **Scope:** 9 pages covering Test Work 01, Test Work 02, Common Evaluation Framework (100 points), and Technical Questions.
- **Target Selection:** **Test Work 01 — AI Call Intelligence with Frappe CRM**
  - Audio / Mock Telephony Webhook $\to$ Speech-to-Text $\to$ LLM Structured Call Analysis $\to$ Validation $\to$ Frappe CRM Write-Back $\to$ Follow-up Task Automation $\to$ Manager Dashboard.

---

## 3. Existing Project Files

- Workspace directory `e:\Hash` is currently clean and empty.
- Ready for clean project scaffolding following critical-path engineering principles.

---

## 4. Frappe CRM Status & Fastest Reliable Path

### Current State
1. **Local Bench / Docker:**
   - Frappe Bench is not installed locally on Windows.
   - Running Frappe Bench natively on Windows is not supported by Frappe, and setting up Bench via WSL2 / Docker from scratch typically takes several hours of image pulls, MariaDB compilation, Redis configuration, and socket tuning.
2. **Frappe Cloud Instance:**
   - The user has already provisioned and set up a live Frappe CRM instance:
     - **Instance URL:** `https://crm-chm-lly.nvi.frappe.cloud`
     - **CRM App:** Official Frappe CRM app with Leads, Deals, Contacts, Organizations, Notes, Tasks, and Call Logs.
     - **Pre-loaded Demo Data:** 5 sample leads (Carol Smith, Bob Martinez, Grace Park, Frank Turner, Emma Williams) assigned across telecallers (John Parker, Sarah Connor) under manager (Jeffin Pappachan).
     - **Network Connectivity:** Verified responsive (`HTTP 200` on web interface, `HTTP 301` to `/desk`).

### Recommendation (Fastest & Most Reliable)
**Use the Frappe Cloud instance as the central CRM backend.**
- Frappe Framework exposes a robust, complete REST API (`/api/resource/...` and `/api/method/...`).
- Standard Token Authentication (`token {api_key}:{api_secret}`) allows immediate programmatic read/write access for Call Logs, Leads, Tasks, and Notes.
- Eliminates 100% of local infrastructure overhead and matches standard enterprise integration patterns for external telephony / AI pipelines.

---

## 5. Missing Dependencies & Setup Requirements

To implement the automation engine on the Windows host, the following components are needed:

1. **Python Virtual Environment (`.venv` in `e:\Hash`):**
   - Web framework: `fastapi`, `uvicorn` (for mock telephony webhook and API)
   - HTTP client: `httpx` or `requests` (for Frappe CRM REST API interactions)
   - AI & Speech: `groq` or `openai` or `google-genai` (for Whisper transcription + structured LLM analysis)
   - Data validation: `pydantic` (for strict schema validation of AI outputs)
   - Utilities: `python-dotenv`, `pytest` (for automated idempotency and validation tests)
2. **Audio Samples:**
   - High quality English sample audio recording (telecaller sales/support call).
   - Malayalam/Malayalam-English code-switched audio sample (bonus requirement).
3. **Credentials / Configuration:**
   - Frappe Cloud API Key & Secret (generated from user profile in Frappe Desk).
   - LLM / STT API Key (Groq, OpenAI, or Gemini).

---

## 6. Recommended Implementation Architecture

```
                                  [ Telephony Webhook / Audio Input ]
                                                 │
                                                 ▼
                             ┌──────────────────────────────────────┐
                             │  FastAPI Telephony Ingestion Service │
                             │  - Idempotency / Provider Call ID    │
                             │  - Payload validation & audio fetch  │
                             └──────────────────┬───────────────────┘
                                                │
                                                ▼
                             ┌──────────────────────────────────────┐
                             │    Speech-to-Text Layer (Whisper)    │
                             │  - English & Malayalam support       │
                             │  - Transcript generation             │
                             └──────────────────┬───────────────────┘
                                                │
                                                ▼
                             ┌──────────────────────────────────────┐
                             │    Structured LLM Intelligence       │
                             │  - Strict Pydantic schema validation │
                             │  - Outcome, intent, objection, score │
                             │  - Next action & follow-up datetime  │
                             └──────────────────┬───────────────────┘
                                                │
                                                ▼
                             ┌──────────────────────────────────────┐
                             │        Frappe CRM REST Client        │
                             │  - Link Call Log to Lead & Agent     │
                             │  - Update Lead stage/quality         │
                             │  - Auto-create scheduled CRM Task    │
                             └──────────────────┬───────────────────┘
                                                │
                                                ▼
                             ┌──────────────────────────────────────┐
                             │    Frappe Cloud CRM / Manager View   │
                             │  - Call Log with full transcript     │
                             │  - Actionable Task list              │
                             │  - Lightweight Executive Dashboard  │
                             └──────────────────────────────────────┘
```

---

## 7. Current Blockers

1. **Frappe Cloud API Credentials:**
   - Need the `API Key` and `API Secret` from the Frappe Cloud instance (`https://crm-chm-lly.nvi.frappe.cloud`).
   - *How to get:* In Frappe Desk, navigate to **User** $\to$ **Jeffin Pappachan** (or Administrator) $\to$ **API Access** $\to$ **Generate Keys**.
2. **AI Provider API Key:**
   - Need an API Key for STT (Whisper) and LLM inference (e.g., Groq, OpenAI, or Gemini API key).

---

## 8. Immediate Next Action

1. Present the formal Implementation Plan for user approval.
2. Confirm the API credentials for Frappe Cloud and the AI provider.
3. Scaffold the Python automation project in `e:\Hash` following critical-path engineering rules.
