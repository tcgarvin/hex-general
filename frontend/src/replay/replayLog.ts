// Build the display model for the replay event log (UI-SPEC §13.3): turn the
// ordered beats into log beats whose entries are plain-language lines. `unit_moved`
// events are collapsed into a single "N units moved" summary per beat — they are
// rarely interesting individually and would flood the log. Pure and testable.

import type { components } from "@/lib/api/schema";

import type { Beat } from "./beats";
import { eventLines, unitName, type LogLine, type NameOf } from "./eventText";

type UnitType = components["schemas"]["UnitType"];

/** One log entry: the lines for a single event, or the collapsed-moves summary. */
export interface LogEntry {
  lines: LogLine[];
}

/** A beat's header plus its entries, carrying the beat index for click-to-jump. */
export interface LogBeat {
  /** Index into the `Beat[]` array (matches `currentBeat`). */
  beatIndex: number;
  label: string;
  entries: LogEntry[];
}

/** Minimal unit identity the log needs to name a unit referenced only by id. */
export interface UnitRef {
  owner: string;
  type: UnitType;
}

/** A name lookup from a units-by-id map, falling back to a generic label. */
export function nameLookup(unitsById: ReadonlyMap<number, UnitRef>): NameOf {
  return (id: number): string => {
    const ref = unitsById.get(id);
    return ref === undefined ? "a unit" : unitName(ref.owner, ref.type);
  };
}

/**
 * Build the log model from grouped beats. Empty beats are dropped already by
 * {@link groupBeats}, but a beat holding only `unit_moved` events still yields a
 * single summary entry.
 */
export function buildReplayLog(beats: Beat[], nameOf: NameOf): LogBeat[] {
  return beats.map((beat, beatIndex): LogBeat => {
    const entries: LogEntry[] = [];
    let movedCount = 0;

    for (const event of beat.events) {
      if (event.kind === "unit_moved") {
        movedCount += 1;
        continue;
      }
      entries.push({ lines: eventLines(event, nameOf) });
    }

    if (movedCount > 0) {
      const label = movedCount === 1 ? "1 unit moved" : `${movedCount} units moved`;
      entries.push({ lines: [{ indent: 0, text: `[${label}]` }] });
    }

    return { beatIndex, label: beat.label, entries };
  });
}
