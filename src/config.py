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
    mock_mode: bool = True
    host: str = "0.0.0.0"
    port: int = 8000

    # Frappe Cloud CRM Configuration
    frappe_base_url: str = "https://crm-chm-lly.nvi.frappe.cloud"
    frappe_api_key: str = ""
    frappe_api_secret: str = ""

    # AI Provider Settings
    # Supports: "mock", "groq", "openai", "gemini"
    ai_provider: Literal["mock", "groq", "openai", "gemini"] = "mock"
    ai_api_key: str = ""

    # STT Provider Settings
    # Supports: "mock", "groq", "openai", "gemini"
    stt_provider: Literal["mock", "groq", "openai", "gemini"] = "mock"
    stt_api_key: str = ""

    # Local Audio Storage
    audio_storage_dir: str = "./samples"


@lru_cache
def get_settings() -> Settings:
    """Returns a cached singleton instance of Settings."""
    return Settings()
