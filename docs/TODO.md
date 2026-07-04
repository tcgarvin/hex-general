# TODO

Running list of follow-ups and tech debt that don't belong to a specific roadmap
phase. Newest section on top. See [`ROADMAP.md`](./ROADMAP.md) for planned feature
work.

**When an item is finished, delete it from this file — don't check it off.** This
list tracks only outstanding work; completed work lives in git history.

## Label bot games in history / header

The new-game opponent flow shipped (hub + `Play vs Bot` / `Join` spokes;
vs-bot create routes straight to the board — `Dispatcher`, `BotPicker`,
`JoinForm`). What's left is making a bot game *legible after creation*, which
turned out **not** to be frontend-only: `GameView` and `GameHistoryEntry`
(`api/app/games/schemas.py`) carry no `vs_bot` flag or opponent identity, so the
frontend can't tell a finished bot game from a human one.

- Add `vs_bot` (and ideally an opponent label / `bot_policy`) to `GameView` +
  `GameHistoryEntry`; regen `schema.d.ts` (`npm run gen:api`).
- Surface it: "vs Computer (Blitz/Sage)" in the game header and history rows
  (`Account.tsx` `HistoryRow`), and sanity-pass resign / outcome labels for bot
  games.
