from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings


@lru_cache
def get_supabase_client() -> Client:
    """Return a process-wide Supabase client built from settings.

    Uses the service-role key, so this client bypasses row-level security and
    must only ever run server-side. Cached so the connection setup happens once
    per process; suitable as a FastAPI dependency.
    """
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
