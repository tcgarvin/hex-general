// What the world screen needs from a built playback beyond frames: beat titles,
// how long each beat plays, which beats hold fights (for the scrubber), which
// report lines belong to which beat, and whether the turn showed anything at all.
//
// Pacing: beats are never merged (the reconstruction's snapshots are per beat,
// and the report and the "your fights" list point at beat indices), but a beat
// without any action of the viewer's plays at `QUIET_PACE` of its length, and a
// beat with nothing on the board (economy bookkeeping) is a short pause, so
// playback hurries through other players' marching to the viewer's own news.

import type { ReplayEvent } from "@/lib/api/types";

import type { ReportItem } from "../turnReport";
import { beatDurationMs, cameraHint } from "./interpolate";
import type { Playback } from "./reconstruct";
import { actionOf, type Beat } from "./timeline";

/** Share of its normal length a beat without the viewer's action plays for. */
export const QUIET_PACE = 0.45;
/** Length (ms at 1x) of a beat with nothing on the board. */
export const BLANK_BEAT_MS = 250;

const PHASE_TITLE: Record<Exclude<Beat["phase"], "pulse">, string> = {
  barrage: "Barrage",
  capture: "Captures",
  repair: "Repairs",
  economy: "Supply and building",
  recruit: "Recruits",
  settle: "End of turn",
};

/** "Pulse 3 of 8", "Barrage", "Captures", ... */
export function beatTitle(beats: readonly Beat[], k: number): string {
  const beat = beats[k];
  if (beat.phase !== "pulse") return PHASE_TITLE[beat.phase];
  const pulses = beats.filter((b) => b.phase === "pulse");
  return `Pulse ${pulses.indexOf(beat) + 1} of ${pulses.length}`;
}

/** Whether a beat changes anything on the board (not only report lines). */
export function beatHasBoardAction(beat: Beat): boolean {
  return beat.actions.some((a) => a.kind !== "steady" && a.kind !== "correct");
}

/** How long beat `k` plays at 1x, after pacing. */
export function playDurationMs(pb: Playback, k: number): number {
  const beat = pb.beats[k];
  if (!beatHasBoardAction(beat)) return BLANK_BEAT_MS;
  const base = beatDurationMs(beat);
  return cameraHint(pb, k) === null ? Math.round(base * QUIET_PACE) : base;
}

/**
 * Whether a turn's witnessed events put anything in motion on the board
 * (moves, fights, shelling, captures, recruits). Repairs and bookkeeping alone
 * do not: such a turn has nothing to play.
 */
export function turnHasPlayback(events: readonly ReplayEvent[]): boolean {
  return events.some((e, i) => {
    const a = actionOf(e, i);
    return a !== null && a.kind !== "repair";
  });
}

export type BeatMark = "own_fight" | "fight" | null;

/** Per beat: a fight of the viewer's, someone else's, or none (scrubber ticks). */
export function beatMarks(pb: Playback): BeatMark[] {
  return pb.beats.map((beat, k) => {
    let mark: BeatMark = null;
    for (const a of beat.actions) {
      if (a.kind === "combat") {
        if (a.sides.some((s) => s.owner === pb.viewer)) return "own_fight";
        mark = "fight";
      } else if (a.kind === "barrage") {
        const gun = pb.snapshots[k].get(a.gunId);
        if (gun?.owner === pb.viewer || a.strikes.some((s) => s.owner === pb.viewer)) return "own_fight";
        mark = "fight";
      }
    }
    return mark;
  });
}

/** The beat each report line shows in (`-1`: none, e.g. a summary line). */
export function reportItemBeats(items: readonly ReportItem[], beats: readonly Beat[]): number[] {
  const beatOf = new Map<number, number>();
  beats.forEach((b, k) => {
    for (const i of b.eventIndices) beatOf.set(i, k);
  });
  return items.map((item) => (item.eventIndex === undefined ? -1 : (beatOf.get(item.eventIndex) ?? -1)));
}
