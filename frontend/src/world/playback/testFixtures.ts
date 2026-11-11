// Recorded turns for the playback tests (`__fixtures__/playback.json`, written by
// `__fixtures__/playback_fixture.py` from a real headless bot world).

import type { components } from "@/lib/api/schema";

import raw from "../__fixtures__/playback.json";
import { unitBook } from "../turnReport";
import type { WorldReplay } from "../useTurnReport";
import { decodeWorldMap } from "../worldMap";
import { buildPlayback, type Playback } from "./reconstruct";
import type { TerrainAt } from "./visibility";

type WorldState = components["schemas"]["WorldState"];
type CompactMap = components["schemas"]["CompactMap"];

export interface PlaybackCase {
  viewer: string;
  turn: number;
  before: WorldState | null;
  replay: WorldReplay;
  visible_before: [number, number][];
  visible_after: [number, number][];
}

const fixture = raw as unknown as { map: CompactMap; cases: PlaybackCase[] };

export const FIXTURE_MAP = decodeWorldMap(fixture.map);
export const FIXTURE_CASES: readonly PlaybackCase[] = fixture.cases;
export const fixtureTerrain: TerrainAt = (q, r) => FIXTURE_MAP.hexAt(q, r)?.terrain;

export function casePlayback(c: PlaybackCase, opts: { rewind?: boolean } = {}): Playback {
  const book = unitBook(c.replay.events, c.replay.state_after.units, c.viewer);
  return buildPlayback({
    replay: c.replay,
    viewer: c.viewer,
    before: opts.rewind ? null : c.before,
    terrainAt: fixtureTerrain,
    book,
  });
}
