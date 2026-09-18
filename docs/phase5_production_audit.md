# Phase 5 - Production Hardening: Production Readiness Audit

**Project:** AI Call Intelligence with Frappe CRM  
**Workspace:** E:\\Hash  
**Audit Date:** 2026-09-18  
**Auditor:** Antigravity AI (Read-Only Audit - No code modified)  

---

## 1. Executive Summary

The project is a well-structured MVP. The full test suite passes **20/20 tests** (0 failures, 2 deprecation warnings).

**Overall readiness rating: NOT PRODUCTION READY**

Critical blockers requiring immediate resolution:
- SEC-01: /api/models endpoint exposes Groq API key unauthenticated (server.py L268-276)
- SEC-02: CORS allow_origins=["*"] + allow_credentials=True violates spec (server.py L39-45)
- SEC-03: 7 diagnostic scripts with credential access committed to repo root (b753335)
- SEC-04: SSL verification disabled in check_log*.py (check_log.py L14-17)
- SEC-05: Full transcript embedded in CRM comments with no PII controls (frappe_client.py L336-342)
- REL-01: Task creation has no error handling - pipeline crash after Call Log write (frappe_client.py L423-429)
- REL-02: add_timeline_comment silent failure - except block unreachable (frappe_client.py L354-357)
- REL-05: In-memory idempotency store lost on every server restart (pipeline.py L38-57)
- DI-01: CRM records manual_7732859fc2b1 and Task 14 unverified at code level

---

## 2. Current Architecture

### Code Flow

```
POST /api/v1/telephony/process-audio (or /webhook)
         |
         v
src/server.py [FastAPI - no auth, CORS=*]
         |
         v
src/pipeline.py :: CallIntelligencePipeline.process_call()
    1. In-memory idempotency check (InMemoryIdempotencyStore)
    2. STT: MockSTTService or RealSTTService [Groq whisper-large-v3]
    3. AI:  MockAIService  or RealAIService  [Groq openai/gpt-oss-20b]
    4. FrappeCRMClient.lookup_lead_by_phone()
    5. FrappeCRMClient.create_call_log()
         -> get_call_log_by_provider_id() [CRM idempotency]
         -> add_timeline_comment()        [SILENT FAIL BUG - frappe_client.py L354]
    6. FrappeCRMClient.create_followup_task() [ONLY if follow_up_required=True]
                                               [NO TRY/EXCEPT - frappe_client.py L423]
    7. FrappeCRMClient.update_lead_status()
    8. Store result in InMemoryIdempotencyStore
    Return PipelineResponse (JSON)
```

### Key Files

| File | Role | Size |
|---|---|---|
| src/server.py | FastAPI app, CORS, all endpoints | 277 lines |
| src/config.py | Pydantic settings, .env loader | 44 lines |
| src/pipeline.py | Orchestration, in-memory idempotency | 239 lines |
| src/ai_service.py | Mock + Real AI (Groq openai/gpt-oss-20b) | 133 lines |
| src/stt_service.py | Mock + Real STT (Groq whisper-large-v3) | 106 lines |
| src/frappe_client.py | Frappe REST client | 645 lines |
| src/schemas.py | All Pydantic models and enums | 157 lines |
| src/static/dashboard.html | Manager dashboard UI | 33,400 bytes |

### What Does NOT Exist
- No Docker / Dockerfile / docker-compose.yml
- No pyproject.toml or setup.cfg
- No authentication on any endpoint
- No rate limiting middleware
- No structured JSON logging
- No request/correlation ID tracking
- No persistent storage (pure in-memory idempotency)
- No background task queue
- No audio file type or size validation

---

## 3. Security Findings

### SEC-01 [CRITICAL]: API Key Exposed via Unauthenticated Endpoint
- **File:** src/server.py, Lines 268-276
- **Problem:** /api/models passes settings.ai_api_key as Bearer token to Groq with zero authentication. Any network-accessible caller can trigger this endpoint.
- **Impact:** Full Groq API key compromise; unlimited LLM billing exposure.
- **Fix:** Delete the /api/models endpoint entirely.

### SEC-02 [CRITICAL]: CORS Wildcard + Credentials Misconfiguration
- **File:** src/server.py, Lines 39-45
- **Problem:** allow_origins=["*"] with allow_credentials=True violates the CORS specification. Browsers reject this combination. Cross-origin credential theft is enabled.
- **Fix:** Replace allow_origins=["*"] with an explicit domain allowlist.

### SEC-03 [CRITICAL]: Diagnostic Scripts Committed to Repository
- **Files:** check_log.py, check_log2.py, inspect_crm_live.py, inspect_crm_raw.py, inspect_comments.py, inspect_req.py, inspect_url.py (all committed in git commit b753335)
- **Problem:** All scripts access FRAPPE_API_KEY and FRAPPE_API_SECRET. inspect_crm_raw.py calls client._get_client() which does not exist - would crash. SSL disabled in check_log*.py.
- **Fix:** Add check_log*.py and inspect_*.py to .gitignore.

### SEC-04 [CRITICAL]: SSL Certificate Verification Disabled
- **File:** check_log.py L14-17, check_log2.py L14-17
- **Problem:** ctx.check_hostname=False and ctx.verify_mode=ssl.CERT_NONE used with live Frappe endpoint carrying API credentials.
- **Impact:** MITM attack exposes API credentials on any untrusted network.
- **Fix:** Delete these scripts.

### SEC-05 [CRITICAL]: Full Transcript Embedded in CRM Without PII Controls
- **File:** src/frappe_client.py, Lines 336-342
- **Problem:** Full verbatim transcript stored as HTML pre tag in Frappe CRM timeline comment. No length limit, truncation, or PII scrubbing.
- **Impact:** GDPR violation risk; PII leakage into third-party CRM.
- **Fix:** Make configurable via STORE_TRANSCRIPT_IN_CRM env var, default to truncated (500 chars).

### SEC-06 [HIGH]: Exception Messages Returned in API Responses
- **File:** src/server.py, Lines 100, 148
- **Problem:** str(exc) returned in HTTP 500 detail, exposing Groq error bodies, file paths, API URLs.
- **Fix:** Return generic error message; log full exception server-side.

### SEC-07 [MEDIUM]: Health Endpoint Exposes Internal Configuration
- **File:** src/server.py, Lines 63-73
- **Problem:** /health returns frappe_crm_url, mock_mode, ai_provider, stt_provider unauthenticated.
- **Fix:** In production, return only {status: healthy, timestamp: ...}.

### SEC-08 [HIGH]: No Authentication on Any Endpoint
- **File:** src/server.py, Lines 76-265
- **Problem:** Zero authentication on webhook, audio upload, dashboard metrics, call intelligence retrieval.
- **Fix:** Implement X-API-Key header validation for all /api/v1/ endpoints.

### SEC-09 [HIGH]: Missing Audio File Type and Size Validation
- **File:** src/server.py, Lines 110-149
- **Problem:** Any file type accepted; no size limit; await file.read() loads entire file to memory.
- **Fix:** Allowlist content_type; enforce 50 MB max; sanitize filename.

### SEC-10 [LOW]: Personal Developer Email as Default agent_id
- **File:** src/server.py, Line 113
- **Problem:** jeffinpappachan110@gmail.com hardcoded as default, appears in CRM Call Logs.
- **Fix:** Change default to None.

### SEC-11 [PASS]: .env Not Committed to Git
- git log -- .env returned empty. .gitignore correctly excludes .env. No credential exposure.

---

## 4. Reliability Findings

### REL-01 [CRITICAL]: No Error Handling on Task Creation - Pipeline Crash
- **File:** src/frappe_client.py, Lines 423-429
- **Problem:** create_followup_task() calls response.raise_for_status() with no try/except. Any Frappe 4xx/5xx propagates uncaught through pipeline.py. CRM Call Log is ALREADY WRITTEN but idempotency store is NOT SAVED. On retry: re-runs STT+AI and re-attempts all CRM writes.
- **Impact:** Partial write, orphaned Call Log, re-processing risk.
- **Fix:** Wrap step 6 in try/except; task failure should be non-fatal.

### REL-02 [HIGH]: add_timeline_comment Silent Failure
- **File:** src/frappe_client.py, Lines 354-357
- **Problem:** Returns res.status_code == 200 without raise_for_status(). On any HTTP error returns False. The except Exception block in create_call_log (L293) is UNREACHABLE because no exception is raised.
- **Impact:** AI intelligence comment silently dropped with no warning.
- **Fix:** Call res.raise_for_status() to make the existing except/warning block reachable.

### REL-03 [HIGH]: No Retry Logic on Groq API Calls
- **File:** src/ai_service.py L111-124, src/stt_service.py L90-97
- **Problem:** Timeouts correct (45s AI, 60s STT) but no retry. Single transient error or HTTP 429 permanently fails the call. logger.error at ai_service.py L120 logs full Groq response body which may contain transcript.
- **Fix:** Exponential backoff retry (2-3 attempts) for 429/500/503. Log status code not body.

### REL-04 [HIGH]: Groq Model Name Hardcoded and Unverified
- **File:** src/ai_service.py, Line 92
- **Problem:** openai/gpt-oss-20b hardcoded. E2E report confirms prior model (llama-3.1-8b-instant) failed with model_not_found. openai/gpt-oss-20b NOT verified against current API key.
- **Fix:** Make configurable via AI_MODEL_NAME env var. Verify access before live run.

### REL-05 [HIGH]: In-Memory Idempotency Store Not Crash-Safe
- **File:** src/pipeline.py, Lines 38-57, 230-238
- **Problem:** Python dict reset on every server restart or uvicorn --reload (server currently running in --reload mode). STT and AI re-executed on every retry, wasting API quota.
- **Fix:** Persist to SQLite or Redis. Add IDEMPOTENCY_BACKEND env var.

### REL-06 [MEDIUM]: Empty Transcript Not Guarded
- **File:** src/pipeline.py, Lines 170-171
- **Problem:** If Groq Whisper returns empty string (silent audio), it flows to AI and then to CRM write. Hallucinated AI intelligence may be written to CRM.
- **Fix:** Raise ValueError if not transcript.strip() after step 2.

### REL-07 [MEDIUM]: Frappe 401 Treated as No-Match
- **File:** src/frappe_client.py, Lines 158-163, 185-187
- **Problem:** 401 (invalid credentials) and network timeout both return None in lookup_lead_by_phone. 401 silently creates an unlinked Call Log.
- **Fix:** Distinguish 401/403 - raise immediately on auth failure.

### REL-08 [MEDIUM]: Task Creation Gated on follow_up_required - Never Set by Mock
- **File:** src/frappe_client.py, Lines 367-368
- **Problem:** MockAIService never sets follow_up_required=True. Tasks never created in mock mode. RealAIService behavior never verified (AI stage failed in only live run). Previously reported Task 14 unexplained.
- **Fix:** Also trigger task on call_outcome=FOLLOW_UP or CALLBACK_REQUESTED.

### REL-09 [HIGH]: No Saga / Partial Failure Recovery
- **File:** src/pipeline.py, Lines 185-227
- **Problem:** Steps 5-8 sequential with no transactional guarantee. Crash between step 5 (Call Log written) and step 8 (idempotency saved) leaves no application record.
- **Fix:** Wrap each CRM step in try/except; always save to idempotency store before returning error.

---

## 5. Observability Findings

### OBS-01 [MEDIUM]: No Structured Logging
- **File:** src/server.py L24-27
- Plain text format; cannot be parsed by log aggregators without regex.
- **Fix:** Use python-json-logger. Log call_id, stage, provider, model, duration_ms, status.

### OBS-02 [MEDIUM]: No Request / Correlation ID
- No request ID threaded through pipeline. Log lines from concurrent calls interleaved.
- **Fix:** Generate request_id = uuid.uuid4().hex at entry; include in all log messages.

### OBS-03 [MEDIUM]: No Timing Metrics
- **File:** src/pipeline.py L149-227
- No STT, AI, or CRM write timing. Critical for an API-billed pipeline.
- **Fix:** Record time.perf_counter() around each external call.

### OBS-04 [MEDIUM]: Transcript May Leak via Groq Error Body in Logs
- **File:** src/ai_service.py L120
- exc.response.text logged at ERROR - Groq may echo request body containing transcript.
- **Fix:** Log exc.response.status_code only.

### OBS-05 [LOW]: No Deep Health Check for Dependencies
- **Fix:** Add /health/ready that probes Frappe and Groq; return 503 if unreachable.

---

## 6. Data Integrity Findings

### DI-01 [HIGH]: Unconfirmed CRM Records from Previous Live Run
- Prior run reported Call Log manual_7732859fc2b1 and Task 14 created.
- Code analysis: Task 14 requires follow_up_required=True. MockAIService NEVER sets this.
- E2E report confirms AI stage FAILED in the only documented live run. No CRM writes should have occurred.
- This is contradictory. Most likely: records created manually via scripts/verify_live_crm.py.
- **Action required:** Verify manual_7732859fc2b1 in Frappe CRM UI. Confirm AI comment presence and Task 14 origin.

### DI-02 [MEDIUM]: Idempotency Check Network Error Causes Duplicate Risk
- **File:** src/frappe_client.py L185-187
- Network error on get_call_log_by_provider_id returns None, proceeds to create duplicate.
- **Fix:** Abort on network error rather than treating as not-found.

### DI-03 [MEDIUM]: Mock Mode Returns Carol Smith for All Unmatched Phones
- **File:** src/frappe_client.py L133
- MOCK_LEADS[0] returned for any unmatched phone. Masks live-mode behavior.
- **Fix:** Return None for unmatched phones in mock mode.

### DI-04 [LOW]: Agent Keyword Whitelist Silently Drops Unknown Agents
- **File:** src/frappe_client.py L233-244, L385-393
- Substring matching on 'john', 'sarah', 'emily', 'jeffin'. Future CRM users silently unlinked.
- **Fix:** Pass agent_id email directly; let Frappe validate.

---

## 7. Test Coverage Findings

### Actual pytest Results (Executed 2026-09-18 03:01 IST)

```
platform win32 -- Python 3.13.3, pytest-9.1.1, pluggy-1.6.0
asyncio: mode=Mode.STRICT
collected 20 items

tests/test_ai_stt.py::test_mock_stt_english PASSED             [  5%]
tests/test_ai_stt.py::test_mock_stt_malayalam PASSED           [ 10%]
tests/test_ai_stt.py::test_mock_ai_analysis PASSED             [ 15%]
tests/test_ai_stt.py::test_mock_ai_analysis_not_interested PASSED [ 20%]
tests/test_analytics.py::test_empty_analytics PASSED           [ 25%]
tests/test_analytics.py::test_populated_analytics PASSED       [ 30%]
tests/test_analytics.py::test_frappe_comment_parsing PASSED    [ 35%]
tests/test_analytics.py::test_get_call_intelligence_endpoint PASSED [ 40%]
tests/test_api.py::test_health_endpoint PASSED                 [ 45%]
tests/test_api.py::test_webhook_endpoint_success PASSED        [ 50%]
tests/test_api.py::test_webhook_endpoint_invalid_payload PASSED [ 55%]
tests/test_api.py::test_process_audio_upload_endpoint PASSED   [ 60%]
tests/test_idempotency.py::test_pipeline_idempotency PASSED    [ 65%]
tests/test_live_frappe.py::test_live_lead_lookup PASSED        [ 70%]
tests/test_live_frappe.py::test_live_call_log_and_idempotency PASSED [ 75%]
tests/test_schemas.py::test_valid_webhook_payload PASSED       [ 80%]
tests/test_schemas.py::test_invalid_webhook_payload_empty_call_id PASSED [ 85%]
tests/test_schemas.py::test_invalid_webhook_payload_missing_required_fields PASSED [ 90%]
tests/test_schemas.py::test_valid_call_intelligence_schema PASSED [ 95%]
tests/test_schemas.py::test_invalid_call_intelligence_enum PASSED [100%]

W1: StarletteDeprecationWarning (httpx -> httpx2)
W2: anyio.abc.BlockingPortal alias deprecated
Result: 20 passed, 2 warnings in 8.76s
```

- **Total:** 20 | **Passed:** 20 | **Failed:** 0 | **Skipped:** 0
- **Coverage:** NOT CONFIGURED (pytest-cov not in requirements.txt)
- **IMPORTANT:** test_live_frappe.py tests PASSED (not skipped) = live Frappe credentials in .env right now
- **NOTE:** test_live_call_log_and_idempotency creates REAL CRM Call Logs on every test run

### Coverage Gaps
- No STT error tests (empty transcript, timeout, HTTP errors)
- No AI error tests (JSON parse failure, model_not_found, timeout)
- No Frappe error tests (401, 403, 417, 500 responses)
- No audio upload validation tests (oversized file, wrong MIME type)
- No CORS header tests
- No partial failure tests (crash after Call Log, before Task)
- No concurrent request tests
- Live tests create real CRM records on every test run (data pollution)

---

## 8. Priority List

### CRITICAL - Fix Before Any Public/Production Use

| ID | Finding | File | Lines |
|---|---|---|---|
| SEC-01 | /api/models exposes Groq API key | src/server.py | 268-276 |
| SEC-02 | CORS wildcard + credentials | src/server.py | 39-45 |
| SEC-03 | 7 diagnostic scripts committed | project root | all |
| SEC-04 | SSL verification disabled | check_log.py | 14-17 |
| SEC-05 | Full transcript in CRM no PII control | src/frappe_client.py | 336-342 |
| REL-01 | Task creation no error handling | src/frappe_client.py | 423-429 |

### HIGH - Fix Before Live Demonstration

| ID | Finding | File | Lines |
|---|---|---|---|
| SEC-06 | Exception in API response | src/server.py | 100, 148 |
| SEC-08 | No endpoint authentication | src/server.py | 76-265 |
| SEC-09 | No audio file validation | src/server.py | 110-149 |
| REL-02 | add_timeline_comment silent fail | src/frappe_client.py | 354-357 |
| REL-03 | No Groq retry logic | src/ai_service.py | 111-124 |
| REL-04 | Model name unverified | src/ai_service.py | 92 |
| REL-05 | In-memory idempotency | src/pipeline.py | 38-57 |
| REL-09 | No partial failure recovery | src/pipeline.py | 185-227 |
| DI-01 | CRM records manual_7732859fc2b1 / Task 14 unverified | CRM UI | - |

### MEDIUM - Phase 5 Hardening

| ID | Finding | File | Lines |
|---|---|---|---|
| SEC-07 | Health endpoint info leak | src/server.py | 63-73 |
| REL-06 | Empty transcript not guarded | src/pipeline.py | 170-171 |
| REL-07 | Frappe 401 silent | src/frappe_client.py | 158-163 |
| REL-08 | Task gated on follow_up_required | src/frappe_client.py | 367-368 |
| DI-02 | Idempotency check error = duplicate risk | src/frappe_client.py | 185-187 |
| DI-03 | Mock returns Carol Smith for all misses | src/frappe_client.py | 133 |
| OBS-01 | No structured logging | src/server.py | 24-27 |
| OBS-02 | No correlation ID | all pipeline files | - |
| OBS-03 | No timing metrics | src/pipeline.py | 149-227 |
| OBS-04 | Transcript in Groq error log | src/ai_service.py | 120 |

### LOW - Backlog

| ID | Finding | File | Lines |
|---|---|---|---|
| SEC-10 | Personal email as default agent_id | src/server.py | 113 |
| DI-04 | Agent keyword whitelist | src/frappe_client.py | 233-244, 385-393 |
| OBS-05 | No deep health check | src/server.py | 63-73 |
| TEST-01 | No pytest-cov | requirements.txt | - |
| TEST-02 | Live tests pollute CRM | tests/test_live_frappe.py | 73-89 |
| TEST-03 | No error path tests | tests/ | - |

---

## 9. Exact File and Line References

| Finding | File | Lines |
|---|---|---|
| SEC-01 | src/server.py | 268-276 |
| SEC-02 | src/server.py | 39-45 |
| SEC-03 | check_log.py, inspect_*.py (root) | all |
| SEC-04 | check_log.py | 14-17 |
| SEC-05 | src/frappe_client.py | 336-342 |
| SEC-06 | src/server.py | 100, 148 |
| SEC-07 | src/server.py | 63-73 |
| SEC-08 | src/server.py | 76-265 |
| SEC-09 | src/server.py | 110-149 |
| SEC-10 | src/server.py | 113 |
| REL-01 | src/frappe_client.py | 423-429 |
| REL-02 | src/frappe_client.py | 354-357 |
| REL-03 | src/ai_service.py | 111-124 |
| REL-04 | src/ai_service.py | 92 |
| REL-05 | src/pipeline.py | 38-57 |
| REL-06 | src/pipeline.py | 170-171 |
| REL-07 | src/frappe_client.py | 158-163 |
| REL-08 | src/frappe_client.py | 367-368 |
| REL-09 | src/pipeline.py | 185-227 |
| DI-01 | CRM UI / docs/live_groq_e2e_report.md | - |
| DI-02 | src/frappe_client.py | 185-187 |
| DI-03 | src/frappe_client.py | 133 |
| DI-04 | src/frappe_client.py | 233-244, 385-393 |
| OBS-01 | src/server.py | 24-27 |
| OBS-03 | src/pipeline.py | 149-227 |
| OBS-04 | src/ai_service.py | 120 |

---

## 10. Recommended Implementation Order

All changes must be verified against the 20 existing tests before merging.

### Batch 1 - Immediate Security (No Logic Change)
1. Remove /api/models endpoint (server.py L268-276)
2. Fix CORS - replace allow_origins=[*] with explicit list (server.py L39-45)
3. Add check_log*.py and inspect_*.py to .gitignore
4. Return generic error message in API 500 responses (server.py L100, L148)
5. Change default agent_id from personal email to None (server.py L113)

### Batch 2 - Reliability (Non-Breaking Pipeline Fixes)
6. Wrap task creation in try/except - non-fatal (frappe_client.py L423-429)
7. Call raise_for_status() in add_timeline_comment (frappe_client.py L354-357)
8. Guard against empty transcript (pipeline.py L170-171)
9. Wrap each CRM step - always save to idempotency store (pipeline.py L185-227)
10. Distinguish 401 from network timeout in lead lookup (frappe_client.py L158-163)

### Batch 3 - Configuration and Observability
11. Add AI_MODEL_NAME env var to config.py; use in ai_service.py L92
12. Verify openai/gpt-oss-20b access against current Groq API key
13. Add JSON structured logging (server.py L24-27)
14. Add request_id to all log messages and error responses
15. Add timing metrics for STT, AI, and CRM write durations

### Batch 4 - CRM Verification and Data Integrity
16. Manually verify Call Log manual_7732859fc2b1 in Frappe CRM UI
17. Return None for unmatched phones in mock mode (frappe_client.py L133)
18. Remove agent keyword whitelist; pass email directly (frappe_client.py L233-244)

### Batch 5 - Authentication and Test Hardening
19. Add X-API-Key middleware for /api/v1/ endpoints
20. Add audio file type and size validation (server.py L110-149)
21. Add pytest-cov to requirements.txt
22. Add error path tests for STT, AI, and Frappe failure cases
23. Add CRM record cleanup fixture to test_live_frappe.py

### Batch 6 - Long-Term Infrastructure
24. Persistent idempotency store (SQLite minimum, Redis for scale)
25. Retry logic with exponential backoff for Groq and Frappe calls
26. Deep health check endpoint (/health/ready)
27. Docker containerization with .env secret injection
28. Transcript PII controls - configurable truncation and opt-out

---

*End of Phase 5 Production Readiness Audit - Read-Only. No production code was modified during this audit.*
