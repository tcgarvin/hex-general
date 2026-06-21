import os

# Settings (app.config) validates Supabase env vars at import time, and
# app.main builds the CORS config from them when imported. Provide dummy
# values so the app imports under test without real credentials. The Supabase
# client itself is never constructed in tests — the /stats dependency is
# overridden with a fake client.
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
