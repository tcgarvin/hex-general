# NBP-era applied tiles (superseded)

The terrain tiles exactly as `frontend/src/board/tiles/` shipped them up to commit
30a0d4c, before the Codex paint-in set (`art/codex/terrain/`, ratified 2026-11-09)
replaced them. They are kept so `scripts/tile-foundry/selftest.py` can keep proving
that the NBP foundry tools (`compose.py`, `vignette.py`, `river.py`, `bridge.py`,
`roadfinish.py`) reproduce their own recorded outputs byte for byte from
`manifest.json`. Do not edit or apply them; they are a reference, not art in use.
The unit sprites are unaffected and still verify against `frontend/src/board/units/`.
