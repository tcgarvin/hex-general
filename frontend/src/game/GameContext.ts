import { createContext } from "react";

import type { GameView } from "@/lib/api/types";

/** Live game context: the latest fog-filtered view plus a manual refresh trigger. */
export interface GameContextValue {
  view: GameView;
  /** Force an immediate poll (e.g. right after locking orders). */
  refresh: () => void;
}

export const GameContext = createContext<GameContextValue | null>(null);
