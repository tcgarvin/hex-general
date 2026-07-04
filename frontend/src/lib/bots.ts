// Shared metadata for the AI opponents (AI_SPEC §9). `policy` is the `bot_policy`
// the API speaks (sent on `POST /games`, echoed back on `GameView`/`GameHistoryEntry`
// so a bot game is legible after creation); the rest is player-facing presentation.
// Owned here so the opponent picker, the game header, and history rows all render
// the same names rather than each mapping policy → label on their own.

/** A selectable AI opponent. */
export type Bot = {
  policy: string;
  name: string;
  tagline: string;
  blurb: string;
};

// Blurbs describe playstyle rather than a strict easy/hard ladder — the search
// bot's tuning isn't finished, so we don't promise a difficulty ranking yet.
export const BOTS: readonly Bot[] = [
  {
    policy: "scripted",
    name: "Blitz",
    tagline: "Aggressive · no lookahead",
    blurb:
      "Charges straight at you. Blitz picks the best move for each unit on its own and commits — relentless and punchy, but it doesn't plan around your response.",
  },
  {
    policy: "search",
    name: "Sage",
    tagline: "Measured · thinks ahead",
    blurb:
      "Simulates a turn before committing, refining its plan to avoid traffic jams and bad trades. Sage plays more cautiously and coordinates its units.",
  },
];

const BY_POLICY: ReadonlyMap<string, Bot> = new Map(
  BOTS.map((bot) => [bot.policy, bot]),
);

/**
 * Display name for a bot policy (e.g. "search" → "Sage"). Falls back to the raw
 * policy string for a bot the picker doesn't list (a test-only or newly-added
 * policy), so an unknown opponent is still labelled rather than blank.
 */
export function botName(policy: string): string {
  return BY_POLICY.get(policy)?.name ?? policy;
}

/** Opponent label for a bot game, e.g. "Computer (Sage)". */
export function opponentLabel(policy: string): string {
  return `Computer (${botName(policy)})`;
}
