// What the pointer is over: a tooltip for the hovered hex after a short dwell,
// the travel route a click would order (dashed on the board, with its ETA, where
// it would halt on contact and how much of it is unscouted), and the short notice
// a refused click leaves.
//
// It listens to the renderer, which reports only hex changes, and keeps its own
// state, so moving the mouse re-renders this small component and never the shell.

import { useEffect, useMemo, useState } from "react";

import { axialDistance, hexKey } from "@/board/hex";
import { Glyph } from "@/components/Glyph";

import { attackPreview, barragePreview, type CombatPreview } from "./combatView";
import { fogText, haltText } from "./contact";
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

  // Shift held: the travel preview shows the hovered hex as the next waypoint.
  const shift = useShiftHeld();

  // The travel preview, searched once the pointer settles on a hex.
  const unit = planning.selectedUnit;
  const travelling = unit !== null && planning.mode === "travel";
  const { previewTravel } = planning;
  const ownHere =
    hover !== null && view.state.units.some((u) => u.owner === view.your_slot && u.coord.q === hover.hex.q && u.coord.r === hover.hex.r);
  const [computed, setComputed] = useState<{
    hover: Hover;
    search: typeof previewTravel;
    shift: boolean;
    preview: TravelPreview | null;
  } | null>(null);
  useEffect(() => {
    // A click on your own unit selects it, so there is no route to preview there.
    if (!travelling || hover === null || ownHere) return;
    // The search itself runs in slices between frames (`previewTravel`), so a far
    // destination on a big map never stalls the pointer; a newer hover cancels it.
    let cancel = () => {};
    const id = setTimeout(() => {
      cancel = previewTravel(hover.hex, (preview) => setComputed({ hover, search: previewTravel, shift, preview }), shift);
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      clearTimeout(id);
      cancel();
    };
  }, [travelling, hover, ownHere, previewTravel, shift]);
  const preview =
    travelling &&
    !ownHere &&
    computed !== null &&
    computed.hover === hover &&
    computed.search === previewTravel &&
    computed.shift === shift
      ? computed.preview
      : null;
  const fog = preview === null ? null : fogText(preview.fog);
  const hasTravel = unit !== null && planning.plan.orders[unit.id]?.kind === "travel";
  useEffect(() => {
    renderer.setPreview(
      preview === null
        ? null
        : {
            from: preview.from,
            path: preview.path,
            waypoints: preview.waypoints,
            halt: preview.halt,
            unscouted: preview.fog.unscouted,
          },
    );
  }, [renderer, preview]);
  useEffect(() => () => renderer.setPreview(null), [renderer]);

  const visible = useMemo(() => new Set(view.visible.map((c) => hexKey(c.q, c.r))), [view.visible]);
  const info = hover !== null && dweltOn === hover ? hexInfo(map, view.state, visible, view.your_slot, hover.hex, designate) : null;
  const site = hover !== null ? map.siteAt(hover.hex.q, hover.hex.r) : undefined;
  const combat = useMemo((): CombatPreview | null => {
    if (hover === null || unit === null) return null;
    const key = hexKey(hover.hex.q, hover.hex.r);
    if (planning.mode === "move" && planning.moveReach.has(key) && planning.hostile.has(key)) {
      return attackPreview(unit, hover.hex, map, view.state, view.your_slot, designate);
    }
    if (planning.mode === "barrage") {
      const d = axialDistance(unit.coord, hover.hex);
      if (d < 1 || !planning.barrageRange.some((c) => c.q === hover.hex.q && c.r === hover.hex.r)) return null;
      return barragePreview(hover.hex, map, view.state, view.your_slot, visible, planning.plan.orders, designate);
    }
    return null;
  }, [hover, unit, planning.mode, planning.moveReach, planning.hostile, planning.barrageRange, planning.plan.orders, map, view, visible, designate]);

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
      {hover !== null && (preview !== null || info !== null || combat !== null) && (
        <div
          className="fhq-tip pointer-events-none absolute z-10 w-max max-w-72 px-2 py-1 text-xs leading-snug"
          style={place(hover, 288, combat !== null ? 170 : 110)}
        >
          {combat !== null && (
            <div className={preview !== null || info !== null ? "mb-1 border-b border-wood-500/40 pb-1" : ""}>
              <p className="font-label font-semibold uppercase tracking-wide">{combat.title}</p>
              {combat.lines.map((line) => (
                <p key={line}>{line}</p>
              ))}
              {combat.note !== null && <p className="text-wood-500">{combat.note}</p>}
            </div>
          )}
          {preview !== null && (
            <div className={info !== null ? "mb-1 border-b border-wood-500/40 pb-1" : ""}>
              <p className="font-label font-semibold uppercase tracking-wide">
                {preview.asWaypoint ? "Through here" : "Travel"}: {preview.eta <= 1 ? "arrives this turn" : `${preview.eta} turns`}
              </p>
              {preview.asWaypoint ? (
                <p className="text-wood-500">Shift+click to add waypoint {preview.waypoints.length}</p>
              ) : (
                hasTravel && <p className="text-wood-500">Click: new destination · Shift: pass through here</p>
              )}
              {preview.halt !== null && <p className="text-invalid">{capitalize(haltText(preview.halt, preview.from))}</p>}
              {fog !== null && <p className="text-[#9a6510]">{fog}</p>}
            </div>
          )}
          {info !== null && (
            <>
              <p className="font-label font-semibold uppercase tracking-wide">
                {site !== undefined && <Glyph name={site.kind} size={14} className="mr-1" />}
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

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Whether Shift is held (for previews that change with it); false again when the window loses focus. */
function useShiftHeld(): boolean {
  const [held, setHeld] = useState(false);
  useEffect(() => {
    const on = (e: KeyboardEvent) => setHeld(e.shiftKey);
    const off = () => setHeld(false);
    window.addEventListener("keydown", on);
    window.addEventListener("keyup", on);
    window.addEventListener("blur", off);
    return () => {
      window.removeEventListener("keydown", on);
      window.removeEventListener("keyup", on);
      window.removeEventListener("blur", off);
    };
  }, []);
  return held;
}
