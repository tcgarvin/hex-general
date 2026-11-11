// What the pointer is over: a tooltip for the hovered hex after a short dwell,
// the travel route a click would order (dashed on the board, with its ETA and a
// contact warning), and the short notice a refused click leaves.
//
// It listens to the renderer, which reports only hex changes, and keeps its own
// state, so moving the mouse re-renders this small component and never the shell.

import { useEffect, useMemo, useState } from "react";

import { hexKey } from "@/board/hex";

import { DEPLOY_DISTANCE } from "./contact";
import type { Designate } from "./designations";
import { hexInfo } from "./hexInfo";
import type { WorldView } from "./useWorld";
import type { TravelPreview, WorldPlanning } from "./useWorldPlan";
import type { Hover, WorldRenderer } from "./WorldRenderer";
import type { WorldMap } from "./worldMap";

/** How long the pointer rests on a hex before its tooltip shows. */
const DWELL_MS = 450;
/** Route searches wait for the pointer to settle this long before starting. */
const PREVIEW_DEBOUNCE_MS = 90;
const NOTICE_MS = 3000;

export function HoverInfo({
  renderer,
  map,
  view,
  planning,
  designate,
}: {
  renderer: WorldRenderer;
  map: WorldMap;
  view: WorldView;
  planning: WorldPlanning;
  designate: Designate;
}) {
  const [hover, setHover] = useState<Hover | null>(null);
  // Where the pointer last was over the map, for placing a notice.
  const [lastAt, setLastAt] = useState<{ x: number; y: number } | null>(null);
  const [dweltOn, setDweltOn] = useState<Hover | null>(null);
  useEffect(
    () =>
      renderer.onHover((h) => {
        setHover(h);
        if (h !== null) setLastAt({ x: h.x, y: h.y });
      }),
    [renderer],
  );
  useEffect(() => {
    if (hover === null) return;
    const id = setTimeout(() => setDweltOn(hover), DWELL_MS);
    return () => clearTimeout(id);
  }, [hover]);

  // The travel preview, searched once the pointer settles on a hex.
  const unit = planning.selectedUnit;
  const travelling = unit !== null && planning.mode === "travel";
  const { previewTravel } = planning;
  const ownHere =
    hover !== null && view.state.units.some((u) => u.owner === view.your_slot && u.coord.q === hover.hex.q && u.coord.r === hover.hex.r);
  const [computed, setComputed] = useState<{ hover: Hover; search: typeof previewTravel; preview: TravelPreview | null } | null>(
    null,
  );
  useEffect(() => {
    // A click on your own unit selects it, so there is no route to preview there.
    if (!travelling || hover === null || ownHere) return;
    // The search itself runs in slices between frames (`previewTravel`), so a far
    // destination on a big map never stalls the pointer; a newer hover cancels it.
    let cancel = () => {};
    const id = setTimeout(() => {
      cancel = previewTravel(hover.hex, (preview) => setComputed({ hover, search: previewTravel, preview }));
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      clearTimeout(id);
      cancel();
    };
  }, [travelling, hover, ownHere, previewTravel]);
  const preview =
    travelling && !ownHere && computed !== null && computed.hover === hover && computed.search === previewTravel ? computed.preview : null;
  useEffect(() => {
    renderer.setPreview(preview === null ? null : { from: preview.from, path: preview.path, caution: preview.caution });
  }, [renderer, preview]);
  useEffect(() => () => renderer.setPreview(null), [renderer]);

  const visible = useMemo(() => new Set(view.visible.map((c) => hexKey(c.q, c.r))), [view.visible]);
  const info = hover !== null && dweltOn === hover ? hexInfo(map, view.state, visible, view.your_slot, hover.hex, designate) : null;

  const notice = planning.notice;
  const [expired, setExpired] = useState<number | null>(null);
  useEffect(() => {
    if (notice === null) return;
    const id = setTimeout(() => setExpired(notice.seq), NOTICE_MS);
    return () => clearTimeout(id);
  }, [notice]);
  const showNotice = notice !== null && notice.seq !== expired && lastAt !== null;

  const { width, height } = renderer.app.screen;
  const place = (at: { x: number; y: number }, w: number, h: number) => ({
    left: at.x + 18 + w > width ? Math.max(4, at.x - 18 - w) : at.x + 18,
    top: at.y + 18 + h > height ? Math.max(4, at.y - 18 - h) : at.y + 18,
  });

  return (
    <>
      {hover !== null && (preview !== null || info !== null) && (
        <div
          className="fhq-tip pointer-events-none absolute z-10 w-max max-w-64 px-2 py-1 text-xs leading-snug"
          style={place(hover, 256, 110)}
        >
          {preview !== null && (
            <div className={info !== null ? "mb-1 border-b border-wood-500/40 pb-1" : ""}>
              <p className="font-label font-semibold uppercase tracking-wide">
                Travel: {preview.eta <= 1 ? "arrives this turn" : `${preview.eta} turns`}
              </p>
              {preview.caution && (
                <p className="text-invalid">Columns deploy on contact about {DEPLOY_DISTANCE} hexes out</p>
              )}
            </div>
          )}
          {info !== null && (
            <>
              <p className="font-label font-semibold uppercase tracking-wide">
                {info.title} <span className="font-data font-normal normal-case text-wood-500">({hover.hex.q}, {hover.hex.r})</span>
              </p>
              {info.lines.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </>
          )}
        </div>
      )}
      {showNotice && (
        <div
          role="status"
          className="fhq-tip pointer-events-none absolute z-20 w-max max-w-72 border-invalid! px-2 py-1 text-xs font-semibold text-invalid"
          style={place({ x: lastAt.x, y: lastAt.y - 44 }, 288, 28)}
        >
          {notice.text}
        </div>
      )}
    </>
  );
}
