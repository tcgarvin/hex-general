"""AI opponent package (AI_SPEC.md).

A pure library that plans a full planning-phase turn for one player slot. It
imports only from ``app.engine.*`` (and ``app.games.validation`` for the
legality boundary) — no HTTP, no DB. The public entry point is the `Policy`
protocol in `policy`; concrete policies live in `baselines`, `scripted`, and
`planner`, and the headless match runner is in `arena`.
"""
