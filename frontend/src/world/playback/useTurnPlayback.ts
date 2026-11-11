// The data side of turn playback for the world screen.
//
// The shell already holds the replay of the last resolved turn N (the report).
// From it this builds, right away, a *provisional* playback that rewinds
// `state_after` (start picture (b) in `reconstruct.ts`): enough for the "your
// fights" list, the report-line ↔ beat map and the "nothing to play" check, since
// the beats themselves do not depend on the start picture. Only when the player
// asks to watch is turn N-1 fetched for the exact start picture (a), and the
// full playback built once per turn. Turn 0 has no earlier replay and plays the
// rewound one.

import { useMemo } from "react";

import { unitBook } from "../turnReport";
import { useTurnReport, type ReportLoad } from "../useTurnReport";
import type { WorldMap } from "../worldMap";
import { buildPlayback, type Playback } from "./reconstruct";
import { turnHasPlayback } from "./session";

export type TurnPlayback =
  /** No resolved turn yet. */
  | { status: "none" }
  /** The turn's replay is still loading (or failed: the report says why). */
  | { status: "waiting" }
  /** The turn resolved but nothing moved in the viewer's sight. */
  | { status: "empty"; turn: number }
  | {
      status: "idle" | "loading" | "ready";
      turn: number;
      /** Rewound playback, for beat indices before the full one exists. */
      provisional: Playback;
      /** The playback to watch: set once `status` is "ready". */
      playback: Playback | null;
      message: string | null;
    };

export function useTurnPlayback({
  gameId,
  report,
  viewer,
  map,
  wanted,
}: {
  gameId: string;
  /** The last resolved turn's replay, as the shell loaded it for the report. */
  report: ReportLoad;
  viewer: string;
  map: WorldMap;
  /** The player has asked to watch: fetch the turn before for the start picture. */
  wanted: boolean;
}): TurnPlayback {
  const turn = report.status === "none" ? null : report.turn;
  const before = useTurnReport(gameId, wanted && turn !== null && turn > 0 ? turn - 1 : null);
  const terrainAt = useMemo(() => (q: number, r: number) => map.hexAt(q, r)?.terrain, [map]);
  const replay = report.status === "ready" ? report.replay : null;

  const provisional = useMemo(() => {
    if (replay === null || !turnHasPlayback(replay.events)) return null;
    const book = unitBook(replay.events, replay.state_after.units, viewer);
    return buildPlayback({ replay, viewer, before: null, terrainAt, book });
  }, [replay, viewer, terrainAt]);

  const beforeState = before.status === "ready" ? before.replay.state_after : null;
  const full = useMemo(() => {
    if (replay === null || provisional === null || !wanted) return null;
    if (replay.turn === 0) return provisional;
    if (beforeState === null) return null;
    const book = unitBook(replay.events, replay.state_after.units, viewer);
    return buildPlayback({ replay, viewer, before: beforeState, terrainAt, book });
  }, [replay, provisional, wanted, beforeState, viewer, terrainAt]);

  if (turn === null) return { status: "none" };
  if (replay === null) return { status: "waiting" };
  if (provisional === null) return { status: "empty", turn };
  if (full !== null) return { status: "ready", turn, provisional, playback: full, message: null };
  // Without the turn before, play the rewound picture rather than nothing.
  if (wanted && before.status === "error") {
    return { status: "ready", turn, provisional, playback: provisional, message: `Start picture unavailable: ${before.message}` };
  }
  return { status: wanted ? "loading" : "idle", turn, provisional, playback: null, message: null };
}
