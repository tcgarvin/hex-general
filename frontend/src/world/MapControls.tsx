// On-screen camera buttons (zoom, centre on base, whole world) and the keyboard
// shortcut list, in a wood console in the board's bottom-left corner, under the
// left panels: a row of brass keys over the recessed well that frames the minimap
// (the minimap itself is drawn by the renderer; the well is see-through, so clicks
// reach it). The list opens beside the console, where it has the height it needs
// without covering a panel.

import type { Axial } from "@/board/hex";

import { TRAY_MIN_W, TRAY_OVERLAP, WELL, type MinimapSize } from "./minimapConsole";
import { SHORTCUTS, ZOOM_STEP } from "./useWorldHotkeys";
import { MINIMAP_MARGIN, TACTICAL_SCALE, type WorldRenderer } from "./WorldRenderer";

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}

export function MapControls({
  renderer,
  minimap,
  home,
  helpOpen,
  setHelpOpen,
}: {
  renderer: WorldRenderer | null;
  /** From `useMinimapSize`. */
  minimap: MinimapSize | null;
  home: Axial | null;
  helpOpen: boolean;
  setHelpOpen: (open: boolean) => void;
}) {
  const off = renderer === null;
  // The well's outer edge, and the tray over it: as wide as the well, or wider
  // (hanging off its right side) when a narrow minimap leaves no room for the keys.
  const left = MINIMAP_MARGIN.x - WELL;
  const wellW = minimap === null ? TRAY_MIN_W : minimap.width + 2 * WELL;
  return (
    <div className="pointer-events-none absolute flex flex-col items-start" style={{ left, bottom: MINIMAP_MARGIN.y - WELL }}>
      {helpOpen && (
        <div
          role="dialog"
          aria-label="Keyboard shortcuts"
          className="fhq-frame pointer-events-auto absolute bottom-0 left-full z-20 ml-4 w-72 px-1.5 pb-1.5 pt-1 text-xs"
        >
          <div className="mb-1 flex items-center justify-between">
            <h2 className="fhq-plate font-display text-sm uppercase tracking-wide">Keys</h2>
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
      <div
        className="fhq-tray fhq-tray-open fhq-on-wood pointer-events-auto relative z-[1] flex justify-center gap-0.5 py-px"
        style={{ width: Math.max(TRAY_MIN_W, wellW - 6), marginLeft: 3, marginBottom: -TRAY_OVERLAP }}
        role="toolbar"
        aria-label="Map controls"
      >
        <button type="button" className="fhq-key size-7" disabled={off} onClick={() => renderer?.zoomBy(ZOOM_STEP)} title="Zoom in (+)" aria-label="Zoom in">
          <Icon d="M8 3v10M3 8h10" />
        </button>
        <button type="button" className="fhq-key size-7" disabled={off} onClick={() => renderer?.zoomBy(1 / ZOOM_STEP)} title="Zoom out (−)" aria-label="Zoom out">
          <Icon d="M3 8h10" />
        </button>
        <button
          type="button"
          className="fhq-key size-7"
          disabled={off || home === null}
          onClick={() => home !== null && renderer?.centerOn(home, TACTICAL_SCALE)}
          title="Centre on your base (Home)"
          aria-label="Centre on your base"
        >
          <Icon d="M2.5 8 8 3l5.5 5M4.5 6.5V13h7V6.5" />
        </button>
        <button type="button" className="fhq-key size-7" disabled={off} onClick={() => renderer?.fitAll()} title="Show the whole world (F)" aria-label="Show the whole world">
          <Icon d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10" />
        </button>
        <button
          type="button"
          className="fhq-key size-7 font-label text-sm font-bold"
          onClick={() => setHelpOpen(!helpOpen)}
          title="Keyboard shortcuts (?)"
          aria-label="Keyboard shortcuts"
          aria-expanded={helpOpen}
        >
          ?
        </button>
      </div>
      {minimap !== null && <div className="fhq-well" style={{ width: wellW, height: minimap.height + 2 * WELL }} />}
    </div>
  );
}
