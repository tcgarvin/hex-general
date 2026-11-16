import { useState } from "react";

import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";

/**
 * The admin "resolve turn now" control for the world screen's menu: the same
 * `POST /dev/worlds/{id}/tick` the admin page makes. Shown to admins only (the
 * caller decides); the API checks the role again. The first press asks for a
 * confirm in place (no native dialogs); the second resolves the turn, then
 * `onResolved` reloads the world so the new turn and its report come in.
 */
export function ResolveTurnItem({
  gameId,
  turn,
  active,
  onResolved,
  className,
}: {
  gameId: string;
  turn: number;
  /** Only an active world has a turn to resolve. */
  active: boolean;
  onResolved: () => void;
  className: string;
}) {
  const [stage, setStage] = useState<"idle" | "confirm" | "busy">("idle");
  const [error, setError] = useState<string | null>(null);

  async function resolve() {
    setStage("busy");
    setError(null);
    try {
      const { error: apiError } = await api.POST("/dev/worlds/{game_id}/tick", {
        params: { path: { game_id: gameId } },
      });
      if (apiError) {
        setError(describeApiError(apiError));
      } else {
        onResolved();
      }
    } catch (err) {
      setError(describeApiError(err));
    }
    setStage("idle");
  }

  return (
    <div className="border-t border-wood-500/40 pt-1">
      <div className="px-3 pt-1 font-label text-[10px] font-semibold uppercase tracking-wider text-warn">Admin</div>
      {stage === "confirm" ? (
        <div className="flex items-center gap-2 px-3 py-1.5">
          <button type="button" className="fhq-btn px-2 py-1 text-xs" onClick={() => void resolve()}>
            Resolve turn {turn}
          </button>
          <button
            type="button"
            className="text-xs text-ivory-400 hover:text-ivory-100"
            onClick={() => setStage("idle")}
          >
            Cancel
          </button>
        </div>
      ) : (
        <button
          type="button"
          className={`${className} disabled:cursor-not-allowed disabled:text-ivory-400`}
          disabled={!active || stage === "busy"}
          title={active ? undefined : "Only an active world has a turn to resolve"}
          onClick={() => setStage("confirm")}
        >
          {stage === "busy" ? "Resolving…" : "Resolve turn now"}
        </button>
      )}
      {error !== null && <p className="px-3 pb-1.5 text-xs text-invalid">{error}</p>}
    </div>
  );
}
