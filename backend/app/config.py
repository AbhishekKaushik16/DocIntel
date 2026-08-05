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
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    llm_enabled: bool = True  # If False or no API key, falls back to regex extraction

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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
