"""Environment-based application settings. No secrets are hardcoded here."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DOCINT_", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"

    # Database / queue
    database_url: str = "postgresql+psycopg://docint:docint@localhost:5432/docint"
    redis_url: str = "redis://localhost:6379/0"
    db_pool_size: int = 10
    db_max_overflow: int = 10

    # Security
    secret_key: SecretStr = SecretStr("")
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    login_rate_limit_per_min: int = 10  # per client IP + email
    upload_rate_limit_per_min: int = 60  # per user
    cors_origins: str = ""  # comma-separated; empty = no cross-origin access

    # Storage / uploads
    storage_root: str = "/data/storage"
    max_upload_bytes: int = 100 * 1024 * 1024
    pdf_batch_pages: int = Field(default=20, ge=1, le=200)

    # Jobs
    pipeline_version: str = "p1"  # part of idempotency keys; bump to force reprocessing
    job_max_attempts: int = 3
    retry_backoff_base_s: int = 5
    retry_backoff_max_s: int = 300
    task_soft_time_limit_s: int = 900
    ocr_languages: str = "eng"  # e.g. "eng+ben" once the Bengali pack is installed

    # AI
    llm_provider: Literal["mock", "openai_compatible"] = "mock"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_fallback_model: str = ""  # same endpoint, different model, tried if the primary output stays invalid
    llm_json_mode: bool = True  # send response_format=json_object (disable for servers that reject it)
    llm_timeout_s: int = 120
    extraction_max_chars: int = 30000  # per LLM call; documents are processed in page windows under this budget
    extraction_window_pages: int = 8
    extraction_min_confidence: float = 0.7  # fields below this send the invoice to review
    extraction_review_non_invoices: bool = True  # documents not recognised as invoices go to a human, not silently done
    prompt_version: str = "invoice-v1"
    embedding_provider: Literal["mock", "sentence_transformers"] = "mock"
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dim: int = 384

    # Financial validation
    money_decimal_places: int = 2
    money_tolerance: str = "0.01"

    @model_validator(mode="after")
    def _require_secrets_in_production(self) -> "Settings":
        if self.environment == "production" and len(self.secret_key.get_secret_value()) < 32:
            raise ValueError("DOCINT_SECRET_KEY must be at least 32 characters in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
