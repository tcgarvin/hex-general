// Geometry of the map console (`MapControls`): the brass keys' tray resting on the
// well that frames the renderer's minimap, and how far it reaches up the board, so
// the left panels can stop above it.

import { useEffect, useState } from "react";

import { MINIMAP_MARGIN, type WorldRenderer } from "./WorldRenderer";

/** The well's lip (`.fhq-well` border) around the minimap. */
export const WELL = 8;
/** The key tray: its frame's top rail (`.fhq-tray`), 1 px padding, 28 px keys. */
const TRAY_RAIL = 6;
const KEY = 28;
const TRAY_H = TRAY_RAIL + 1 + KEY + 1;
/** The tray's width for five keys 2 px apart, 1 px padding, and its side rails. */
export const TRAY_MIN_W = 5 * KEY + 4 * 2 + 2 + 2 * TRAY_RAIL;
/** The tray's bottom edge rests this far down over the well's leather, above its brass lip. */
export const TRAY_OVERLAP = 4;

export interface MinimapSize {
  width: number;
  height: number;
}

/**
 * How far the console reaches up from the board's bottom edge, so the left panels
 * can stop above it (they shrink; the console never moves, as the minimap under it
 * is fixed). Before the minimap is known, just the keys.
 */
export function consoleHeight(minimap: MinimapSize | null): number {
  const keys = TRAY_H - TRAY_OVERLAP;
  if (minimap === null) return MINIMAP_MARGIN.y + keys;
  return MINIMAP_MARGIN.y + minimap.height + WELL + keys;
}

/** The minimap's size on the canvas, kept current across resizes. */
export function useMinimapSize(renderer: WorldRenderer | null): MinimapSize | null {
  const [size, setSize] = useState<MinimapSize | null>(null);
  useEffect(() => {
    if (renderer === null) return;
    const place = () => {
      const m = renderer.minimapBox;
      setSize((s) => (s !== null && s.width === m.width && s.height === m.height ? s : { width: m.width, height: m.height }));
    };
    place();
    renderer.app.renderer.on("resize", place);
    return () => {
      if (!renderer.destroyed) renderer.app.renderer.off("resize", place);
    };
  }, [renderer]);
  return size;
}
