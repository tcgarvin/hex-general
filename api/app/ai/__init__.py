"""AI opponent package (AI_SPEC.md).

A pure library that plans a full planning-phase turn for one player slot. It
imports only from ``app.engine.*`` (and ``app.games.validation`` for the
legality boundary) — no HTTP, no DB. The public entry point is the `Policy`
protocol in `policy`; the world bot (`world.WorldPolicy`) is the concrete
policy, built from the `objectives`, `candidates`, `recruit` and `base_plan`
layers over a per-turn `board` index.
"""
