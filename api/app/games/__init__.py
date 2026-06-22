"""Phase 5: turn lifecycle API & persistence (async two-player).

Wires the pure execution engine (`app.engine`) to the deployed stack with real
player identity. This package owns the HTTP boundary (request/response schemas,
order validation, auth) and persistence (Supabase), keeping the engine pure.
"""
