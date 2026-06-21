from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables.

    Field names map case-insensitively to env vars (SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY). Validated at the boundary: constructing
    Settings raises if a required variable is missing, so a misconfigured
    deployment fails fast at startup rather than on the first request that
    needs Supabase.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str
    supabase_service_role_key: str


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance (use as a FastAPI dependency)."""
    return Settings()
