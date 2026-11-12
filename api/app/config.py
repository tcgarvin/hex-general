from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _id_set(csv: str) -> frozenset[str]:
    """A comma-separated user id list as a set (blank entries dropped)."""
    return frozenset(u.strip() for u in csv.split(",") if u.strip())


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

    # Comma-separated Supabase user ids allowed to use the dev profile switcher
    # (impersonate another user via the X-Dev-Profile header). Empty by default,
    # so impersonation is denied unless an env explicitly names dev accounts.
    dev_user_ids: str = ""

    # Comma-separated Supabase user ids allowed to manage worlds from the admin
    # screen (create, list, tick, run the clock, delete) without being devs: no
    # impersonation or other dev power. Every dev is an admin too, so this only
    # needs the extra accounts. Empty by default.
    admin_user_ids: str = ""

    @property
    def dev_user_id_set(self) -> frozenset[str]:
        """The dev allowlist as a set (empty entries from the CSV are dropped)."""
        return _id_set(self.dev_user_ids)

    @property
    def admin_user_id_set(self) -> frozenset[str]:
        """The admin allowlist as a set, parsed like `dev_user_id_set`."""
        return _id_set(self.admin_user_ids)

    # The world turn clock (app.games.scheduler_auth): the OIDC audience Cloud
    # Scheduler's token names (the API's URL) and the scheduler's service-account
    # email. Both empty by default, so the clock endpoint refuses every call until
    # a deployment configures it.
    clock_audience: str = ""
    clock_invoker_email: str = ""

    # Browser origins allowed to call the API (CORS). Defaults to this
    # project's Vercel deployments (production + preview subdomains) and local
    # development. Override with CORS_ALLOW_ORIGIN_REGEX if the domain changes.
    cors_allow_origin_regex: str = (
        r"^(https://hex-general[a-z0-9-]*\.vercel\.app|http://localhost:\d+)$"
    )


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance (use as a FastAPI dependency)."""
    return Settings()
