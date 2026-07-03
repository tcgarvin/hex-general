# TODO

Running list of follow-ups and tech debt that don't belong to a specific roadmap
phase. Newest section on top. See [`ROADMAP.md`](./ROADMAP.md) for planned feature
work.

**When an item is finished, delete it from this file — don't check it off.** This
list tracks only outstanding work; completed work lives in git history.

## Frontend: new game vs. human *or* bot

The API side shipped (AI_SPEC §9): `POST /games` accepts `vs_bot: bool` and
`bot_policy` (`"scripted"` / `"search"`, registry in `app/ai/arena.py`), fills
the open slot with a bot, and the game starts immediately — the bot plans
synchronously when the human submits. Types are already regenerated in
`schema.d.ts`. Remaining work is all frontend UX:

- New-game flow offers an opponent choice: invite a human (current flow) or
  play the computer (pick a difficulty/policy — scripted is the more aggressive
  opponent until the search bot's tuning lands).
- Vs-bot games skip the invite/lobby wait state — the game is active from
  creation, so route straight to planning.
- Sanity-pass the dispatch/history screens for bot games (opponent display
  name, resign, outcome labels).
