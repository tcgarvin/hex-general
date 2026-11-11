"""World games: the turn lifecycle API and its persistence (WORLD_SPEC §10.1).

Wires the pure execution engine (`app.engine`) to the deployed stack with real
player identity. This package owns the HTTP boundary (request/response schemas,
order validation, auth) and persistence (Supabase), keeping the engine pure.
"""
