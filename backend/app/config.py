"""Application configuration loaded from environment variables with sensible defaults."""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings — all configurable via environment variables."""

    # --- Application ---
    app_name: str = "DocIntel"
    app_version: str = "0.1.0"
    debug: bool = False

    # --- Database ---
    database_url: str = "postgresql+asyncpg://docintel:docintel@localhost:5432/docintel"
    database_echo: bool = False

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- File Storage ---
    upload_dir: Path = Path("uploads")
    max_file_size_mb: int = 50

    # --- LLM ---
    llm_provider: str = "openai"
    openai_api_key: str = ""
    # Fast/cheap model — used by the Classifier Agent (high volume, latency-sensitive)
    openai_model: str = "gpt-4o-mini"
    # Strong reasoning model — used by the Resolver Agent (low volume, quality-critical)
    openai_strong_model: str = "gpt-4o"
    gemini_api_key: str = ""
    # Fast model for classification
    gemini_model: str = "gemini-2.0-flash"
    # Strong model for resolution (Gemini 2.5 Pro or similar)
    gemini_strong_model: str = "gemini-2.5-pro"
    llm_enabled: bool = True  # If False or no API key, falls back to regex extraction

    # --- Elasticsearch ---
    elasticsearch_url: str = "http://localhost:9200"

    # --- CORS ---
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- Celery ---
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # --- Confidence Thresholds ---
    confidence_auto_approve: float = 0.8
    confidence_review_threshold: float = 0.5

    @property
    def llm_available(self) -> bool:
        """Check if LLM extraction is available (API key set and enabled)."""
        if not self.llm_enabled:
            return False
        provider = self.llm_provider.lower()
        if provider == "openai":
            return bool(self.openai_api_key)
        if provider == "gemini":
            return bool(self.gemini_api_key)
        return False

    @property
    def fast_model_name(self) -> str:
        """Fast/cheap model name for the current provider (Classifier Agent)."""
        provider = self.llm_provider.lower()
        if provider == "openai":
            return self.openai_model
        if provider == "gemini":
            return self.gemini_model
        return self.openai_model

    @property
    def strong_model_name(self) -> str:
        """Strong reasoning model name for the current provider (Resolver Agent)."""
        provider = self.llm_provider.lower()
        if provider == "openai":
            return self.openai_strong_model
        if provider == "gemini":
            return self.gemini_strong_model
        return self.openai_strong_model

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}



settings = Settings()
