import { useContext } from "react";

import { GameContext, type GameContextValue } from "./GameContext";

/** Access the live game view. Throws if used outside `<GameProvider>`. */
export function useGame(): GameContextValue {
  const value = useContext(GameContext);
  if (value === null) {
    throw new Error("useGame must be used within <GameProvider>");
  }
  return value;
}
