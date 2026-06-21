"""Pure, headless game engine for the hex wargame.

Everything in this package is deterministic and free of I/O: domain models
(`models`), reference stat tables (`stats`), and the hex coordinate library
(`hex_coords`). The turn-resolution function (Phase 2+) will live here too.
Nothing in this package may import from the FastAPI app or touch Supabase.
"""
