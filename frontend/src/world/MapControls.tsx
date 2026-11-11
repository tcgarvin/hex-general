// On-screen camera buttons (zoom, centre on base, whole world) and the keyboard
// shortcut list. The buttons sit just above the minimap in the board's
// bottom-left corner, under the left panels; the list opens beside the minimap,
// where it has the height it needs without covering a panel.

import { useEffect, useState } from "react";

import type { Axial } from "@/board/hex";

import { SHORTCUTS, ZOOM_STEP } from "./useWorldHotkeys";
import { TACTICAL_SCALE, type WorldRenderer } from "./WorldRenderer";

const BUTTON =
  "flex h-8 w-8 items-center justify-center rounded-sm border border-brass-600/70 bg-wood-700/90 text-ivory-100 shadow transition-colors hover:bg-wood-500 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brass-400 disabled:cursor-not-allowed disabled:opacity-40";

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}

export function MapControls({
  renderer,
  home,
  helpOpen,
  setHelpOpen,
}: {
  renderer: WorldRenderer | null;
  home: Axial | null;
  helpOpen: boolean;
  setHelpOpen: (open: boolean) => void;
}) {
  // The minimap's size depends on the map's shape; sit just above it and open
  // the list just right of it.
  const [box, setBox] = useState({ bottom: 244, right: 244 });
  useEffect(() => {
    if (renderer === null) return;
    const place = () => {
      const m = renderer.minimapBox;
      setBox({ bottom: renderer.app.screen.height - m.y + 8, right: m.x + m.width + 12 });
    };
    place();
    renderer.app.renderer.on("resize", place);
    return () => {
      renderer.app.renderer.off("resize", place);
    };
  }, [renderer]);

  const off = renderer === null;
  return (
    <>
      {helpOpen && (
        <div
          role="dialog"
          aria-label="Keyboard shortcuts"
          className="absolute bottom-3 z-20 w-72 rounded-sm border border-wood-700 bg-parchment-200 p-3 text-xs text-ink-900 shadow-lg"
          style={{ left: box.right }}
        >
          <div className="mb-1.5 flex items-center justify-between">
            <h2 className="font-display text-sm uppercase tracking-wide">Keys</h2>
            <button
              type="button"
              onClick={() => setHelpOpen(false)}
              className="font-label text-xs uppercase text-wood-500 hover:text-ink-900 focus-visible:outline-2 focus-visible:outline-brass-600"
            >
              Close
            </button>
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
            {SHORTCUTS.map((s) => (
              <div key={s.keys} className="contents">
                <dt className="font-label font-semibold whitespace-nowrap">{s.keys}</dt>
                <dd>{s.does}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-1.5 text-wood-500">Drag or use the minimap to move; scroll or pinch to zoom.</p>
        </div>
      )}
      <div className="absolute left-3 flex gap-1" role="toolbar" aria-label="Map controls" style={{ bottom: box.bottom }}>
        <button type="button" className={BUTTON} disabled={off} onClick={() => renderer?.zoomBy(ZOOM_STEP)} title="Zoom in (+)" aria-label="Zoom in">
          <Icon d="M8 3v10M3 8h10" />
        </button>
        <button type="button" className={BUTTON} disabled={off} onClick={() => renderer?.zoomBy(1 / ZOOM_STEP)} title="Zoom out (−)" aria-label="Zoom out">
          <Icon d="M3 8h10" />
        </button>
        <button
          type="button"
          className={BUTTON}
          disabled={off || home === null}
          onClick={() => home !== null && renderer?.centerOn(home, TACTICAL_SCALE)}
          title="Centre on your base (Home)"
          aria-label="Centre on your base"
        >
          <Icon d="M2.5 8 8 3l5.5 5M4.5 6.5V13h7V6.5" />
        </button>
        <button type="button" className={BUTTON} disabled={off} onClick={() => renderer?.fitAll()} title="Show the whole world (F)" aria-label="Show the whole world">
          <Icon d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10" />
        </button>
        <button
          type="button"
          className={`${BUTTON} font-label text-sm font-semibold`}
          onClick={() => setHelpOpen(!helpOpen)}
          title="Keyboard shortcuts (?)"
          aria-label="Keyboard shortcuts"
          aria-expanded={helpOpen}
        >
          ?
        </button>
      </div>
    </>
  );
}
