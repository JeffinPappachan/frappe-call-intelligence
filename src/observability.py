# Standard context variable to hold active request ID per async execution context
import contextvars
from enum import Enum
import json
import logging
import sys
import threading
from typing import Any

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RedactingJsonFormatter(logging.Formatter):
    """Structured JSON formatter with automated redaction of sensitive credentials.

    Ensures that secrets, authorization tokens, full transcripts, and sensitive
    payloads are never leaked into log aggregators.
    """

    SENSITIVE_KEYS = {
        "api_key",
        "api_secret",
        "token",
        "authorization",
        "password",
        "secret",
        "frappe_api_secret",
        "ai_api_key",
        "stt_api_key",
        "admin_api_token",
    }

    def format(self, record: logging.LogRecord) -> str:
        req_id = getattr(record, "request_id", None) or request_id_ctx.get("-")
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": req_id,
        }

        # Standard LogRecord internal attributes to ignore
        standard_attrs = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "asctime"
        }
        for k, v in record.__dict__.items():
            if k not in standard_attrs and k not in log_entry:
                log_entry[k] = v

        # Handle exception tracebacks safely
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Sanitize sensitive terms if accidentally embedded
        return json.dumps(self._sanitize(log_entry), default=str)

    def _sanitize(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            sanitized = {}
            for k, v in obj.items():
                if any(s in k.lower() for s in self.SENSITIVE_KEYS):
                    sanitized[k] = "[REDACTED]"
                else:
                    sanitized[k] = self._sanitize(v)
            return sanitized
        elif isinstance(obj, list):
            return [self._sanitize(item) for item in obj]
        elif isinstance(obj, str):
            # Scrub common token patterns like "token abc:123" or "Bearer xyz"
            import re
            scrubbed = re.sub(r"(token\s+[a-zA-Z0-9_-]+:)[a-zA-Z0-9_-]+", r"\1[REDACTED]", obj, flags=re.IGNORECASE)
            scrubbed = re.sub(r"(Bearer\s+)[a-zA-Z0-9_\-\.]{8,}", r"\1[REDACTED]", scrubbed, flags=re.IGNORECASE)
            return scrubbed
        return obj


def setup_structured_logging(log_level: str = "INFO", log_format: str = "json") -> None:
    """Configures root application logging to use structured output."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicate log entries
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    formatter: logging.Formatter
    if log_format.lower() == "json":
        formatter = RedactingJsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S%z")
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] [req:%(request_id)s] %(message)s",
            defaults={"request_id": "-"},
        )

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)


class MetricsCollector:
    """Thread-safe in-memory application metrics tracker.

    Tracks pipeline operational health, status rates, durations, and error classifications
    without unbounded label cardinality or PII exposure.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._counters: dict[str, int] = {
            "total_requests": 0,
            "pipeline_success_total": 0,
            "pipeline_failed_total": 0,
            "stt_failures_total": 0,
            "llm_failures_total": 0,
            "crm_failures_total": 0,
            "timeline_comment_failures_total": 0,
            "followup_task_failures_total": 0,
            "idempotent_duplicate_total": 0,
            "audio_upload_validation_failures_total": 0,
        }
        self._errors_by_class: dict[str, int] = {}
        self._total_duration_ms: float = 0.0
        self._duration_count: int = 0

    def increment(self, metric: str, amount: int = 1) -> None:
        with self._lock:
            if metric in self._counters:
                self._counters[metric] += amount

    def record_error(self, error_class: str) -> None:
        with self._lock:
            safe_class = error_class.strip().lower()
            self._errors_by_class[safe_class] = self._errors_by_class.get(safe_class, 0) + 1

    def record_duration(self, duration_ms: float) -> None:
        with self._lock:
            self._total_duration_ms += duration_ms
            self._duration_count += 1

    def get_metrics(self) -> dict[str, Any]:
        with self._lock:
            avg_duration = (
                round(self._total_duration_ms / self._duration_count, 2)
                if self._duration_count > 0
                else 0.0
            )
            return {
                **self._counters,
                "average_processing_duration_ms": avg_duration,
                "errors_by_class": dict(self._errors_by_class),
            }

    def reset(self) -> None:
        with self._lock:
            for k in self._counters:
                self._counters[k] = 0
            self._errors_by_class.clear()
            self._total_duration_ms = 0.0
            self._duration_count = 0


# Global singleton metrics collector
metrics = MetricsCollector()


class ErrorClassification(str, Enum):
    VALIDATION_ERROR = "validation_error"
    AUTHENTICATION_ERROR = "authentication_error"
    AUTHORIZATION_ERROR = "authorization_error"
    TIMEOUT_ERROR = "timeout_error"
    UPSTREAM_ERROR = "upstream_error"
    CRM_ERROR = "crm_error"
    STT_ERROR = "stt_error"
    LLM_ERROR = "llm_error"
    PERSISTENCE_ERROR = "persistence_error"
    UNEXPECTED_ERROR = "unexpected_error"


class AppError(Exception):
    """Base application exception with error classification and retryable indicator."""

    def __init__(
        self,
        message: str,
        error_class: ErrorClassification = ErrorClassification.UNEXPECTED_ERROR,
        status_code: int = 500,
        retryable: bool = False,
        internal_detail: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_class = error_class
        self.status_code = status_code
        self.retryable = retryable
        self.internal_detail = internal_detail
