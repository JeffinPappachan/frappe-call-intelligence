from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Application Settings
    app_env: str = "development"
    mock_mode: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # Frappe Cloud CRM Configuration
    frappe_base_url: str = "https://crm-chm-lly.nvi.frappe.cloud"
    frappe_api_key: str = ""
    frappe_api_secret: str = ""

    # AI Provider Settings
    # Supports: "mock", "groq", "openai", "gemini"
    ai_provider: Literal["mock", "groq", "openai", "gemini"] = "groq"
    ai_api_key: str = ""

    # STT Provider Settings
    # Supports: "mock", "groq", "openai", "gemini"
    stt_provider: Literal["mock", "groq", "openai", "gemini"] = "groq"
    stt_api_key: str = ""

    # Local Audio Storage
    audio_storage_dir: str = "./samples"

    # Security & Privacy Settings
    allowed_origins: list[str] = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
    ]
    store_transcript_in_crm: Literal["full", "truncated", "none"] = "full"
    admin_api_token: str = ""

    # Reliability & Idempotency Store
    idempotency_backend: Literal["memory", "sqlite", "supabase"] = "memory"
    sqlite_db_path: str = "idempotency.db"
    idempotency_ttl_seconds: int = 86400  # 24 hours
    idempotency_max_items: int = 1000

    # Observability & Logging Settings
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    metrics_enabled: bool = True

    # Upstream Timeouts (Seconds)
    request_timeout_seconds: float = 60.0
    stt_timeout_seconds: float = 60.0
    ai_timeout_seconds: float = 45.0
    frappe_timeout_seconds: float = 15.0

    # Supabase Configuration
    supabase_url: str = ""
    supabase_service_key: str = ""


@lru_cache
def get_settings() -> Settings:
    """Returns a cached singleton instance of Settings."""
    return Settings()

@lru_cache
def get_supabase_client():
    from supabase import create_client
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_key:
        return None
    return create_client(settings.supabase_url, settings.supabase_service_key)
