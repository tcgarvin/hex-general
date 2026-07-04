import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { Button, Centered, ErrorBanner } from "@/components/ui";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import { DEFAULT_SCENARIO } from "@/lib/api/types";

/**
 * A selectable AI opponent. `policy` is the `bot_policy` sent to `POST /games`
 * (resolved server-side through `app.ai.arena`'s registry); the rest is
 * player-facing presentation. Blurbs describe playstyle rather than a strict
 * easy/hard ladder — the search bot's tuning isn't finished, so we don't promise
 * a difficulty ranking yet.
 */
type Bot = {
  policy: string;
  name: string;
  tagline: string;
  blurb: string;
};

const BOTS: readonly Bot[] = [
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

/**
 * "Play vs Bot" spoke (hub → this). Two panes: pick an opponent on the left, read
 * about the hovered/selected one on the right, then Confirm to create the game.
 * A vs-bot game is active from creation (the bot fills its slot immediately), so
 * confirming routes straight into the board.
 */
export function BotPicker() {
  const navigate = useNavigate();
  const [selected, setSelected] = useState<Bot | null>(null);
  const [hovered, setHovered] = useState<Bot | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Hover previews without changing the selection; fall back to the selected bot
  // (then a prompt) when nothing is hovered.
  const detail = hovered ?? selected;

  async function confirm() {
    if (selected === null) {
      return;
    }
    setCreating(true);
    setError(null);
    const { data, error: apiError } = await api.POST("/games", {
      body: {
        scenario: DEFAULT_SCENARIO,
        vs_bot: true,
        bot_policy: selected.policy,
      },
    });
    if (apiError) {
      setError(describeApiError(apiError));
      setCreating(false);
      return;
    }
    navigate(`/game/${data.game_id}`);
  }

  return (
    <Centered fill>
      <h1 className="text-2xl font-semibold">Choose your opponent</h1>

      <div className="flex w-full max-w-2xl flex-col gap-4 rounded-lg border border-zinc-800 p-5">
        <div className="flex flex-col gap-4 sm:flex-row">
          <ul className="flex shrink-0 flex-col gap-2 sm:w-40" role="listbox">
            {BOTS.map((bot) => {
              const isSelected = selected?.policy === bot.policy;
              return (
                <li key={bot.policy}>
                  <button
                    role="option"
                    aria-selected={isSelected}
                    className={`w-full rounded-md border px-3 py-2 text-left text-sm font-medium transition-colors ${
                      isSelected
                        ? "border-indigo-500 bg-indigo-950/40 text-indigo-200"
                        : "border-zinc-700 text-zinc-200 hover:bg-zinc-800"
                    }`}
                    onClick={() => setSelected(bot)}
                    onMouseEnter={() => setHovered(bot)}
                    onMouseLeave={() => setHovered(null)}
                    onFocus={() => setHovered(bot)}
                    onBlur={() => setHovered(null)}
                  >
                    {bot.name}
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="min-h-32 flex-1 rounded-md border border-zinc-800 bg-zinc-900/40 p-4">
            {detail === null ? (
              <p className="text-sm text-zinc-500">
                Hover an opponent to read about it, then pick one to play.
              </p>
            ) : (
              <div className="flex flex-col gap-2">
                <div>
                  <h2 className="text-lg font-semibold text-zinc-100">
                    {detail.name}
                  </h2>
                  <p className="text-xs uppercase tracking-wide text-zinc-500">
                    {detail.tagline}
                  </p>
                </div>
                <p className="text-sm text-zinc-300">{detail.blurb}</p>
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between">
          <Link to="/" className="text-sm text-zinc-400 hover:underline">
            ← Back
          </Link>
          <Button
            onClick={() => void confirm()}
            disabled={selected === null || creating}
          >
            {creating ? "Starting…" : "Confirm"}
          </Button>
        </div>
      </div>

      {error !== null && <ErrorBanner message={error} />}
    </Centered>
  );
}
