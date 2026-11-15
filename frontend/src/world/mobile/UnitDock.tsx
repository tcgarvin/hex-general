// The phone's unit dock (one of your units selected) and its More sheet.
//
// The dock replaces the bottom bar: previous / next idle unit, the unit's name
// plate, close; a readout of its order (or a prompt); and the order keys. Orders
// that need a hex (move, attack, travel, barrage) come from tapping the map
// (`autoOrder.ts`), so the keys are only the stay-put orders, Deploy, Barrage
// (arms the next tap) and More.

import { useMemo } from "react";

import { hexKey } from "@/board/hex";
import { Glyph } from "@/components/Glyph";
import type { components } from "@/lib/api/schema";

import { attackPreview, barragePreview } from "../combatView";
import type { Designate } from "../designations";
import { orderText, UNIT_LABEL, unitTag } from "../labels";
import { withdrawalWarning } from "../moveRules";
import type { OrderKind } from "../plan";
import type { WorldView } from "../useWorld";
import { useSwaps } from "../useSwaps";
import type { WorldPlanning } from "../useWorldPlan";
import { at, MAX_WAYPOINTS } from "../waypoints";
import type { WorldMap } from "../worldMap";
import { Icon } from "./icons";
import { unitReadout } from "./readout";
import { HpBar, Line, OrderKey, Sheet } from "./Sheet";

type Unit = components["schemas"]["Unit"];

/** The unit's posture as the name plate says it: "deployed · in contact". */
function statusText(unit: Unit, zone: ReadonlySet<string>): string {
  const parts = [unit.posture === "column" ? "packed column" : "deployed"];
  if (zone.has(hexKey(unit.coord.q, unit.coord.r))) parts.push("in contact");
  if (unit.disordered) parts.push("disordered");
  return parts.join(" · ");
}

export function UnitDock({
  unit,
  view,
  map,
  planning,
  designate,
  onCycle,
  onMore,
}: {
  unit: Unit;
  view: WorldView;
  map: WorldMap;
  planning: WorldPlanning;
  designate: Designate;
  /** Step to the previous / next unit with nothing to do (and bring it into view). */
  onCycle: (dir: 1 | -1) => void;
  onMore: () => void;
}) {
  const order = planning.plan.orders[unit.id];
  const route = planning.routes.find((r) => r.unitId === unit.id);
  const swap = useSwaps(view, planning).get(unit.id) ?? null;
  const visible = useMemo(() => new Set(view.visible.map((c) => hexKey(c.q, c.r))), [view.visible]);
  const expected =
    order?.kind === "move" && route?.attack
      ? attackPreview(unit, route.attack, map, view.state, view.your_slot, designate)
      : order?.kind === "barrage"
        ? barragePreview(order.target, map, view.state, view.your_slot, visible, planning.plan.orders, designate)
        : null;
  const readout = unitReadout({
    unit,
    order,
    route,
    swap,
    partnerName: swap === null ? null : unitTag(swap.partner, designate),
    expected,
    blocked: planning.conflicts.get(unit.id) ?? null,
    warning: withdrawalWarning(unit, order, view.state.units, view.your_slot, planning.hostile),
    zone: planning.contactZone,
    armed: planning.armed,
  });
  const name = designate(unit.id) ?? UNIT_LABEL[unit.type];
  const column = unit.posture === "column";
  const guns = unit.type === "artillery" && !column;
  const kind: OrderKind | null = order?.kind ?? null;
  const stay = (k: "hold" | "rest" | "deploy") => planning.setOrder(unit.id, kind === k ? null : { kind: k });
  const titleTone = readout.kind === "attack" ? "text-[#ffb4a6]" : readout.kind === "order" ? "text-brass-400" : "text-ivory-100";

  return (
    <section
      aria-label="Selected unit"
      className="fhq-wood pointer-events-auto flex flex-col gap-2 border-t-2 border-brass-600 px-2 pb-[max(10px,env(safe-area-inset-bottom))] pl-[max(8px,env(safe-area-inset-left))] pr-[max(8px,env(safe-area-inset-right))] pt-2"
    >
      <div className="flex items-center gap-1.5">
        <button type="button" className="fhq-btn h-12 w-11 shrink-0 px-0" onClick={() => onCycle(-1)} aria-label="Previous unit without orders" title="Previous unit without orders">
          <Icon name="prev" />
        </button>
        <div className="fhq-parchment flex h-12 min-w-0 flex-1 items-center gap-2 px-2.5">
          <Glyph name={column ? "column" : unit.type} size={24} label={column ? `${UNIT_LABEL[unit.type]}, packed column` : UNIT_LABEL[unit.type]} />
          {/* HP sits on the name's line, so the status line has the plate's full width. */}
          <div className="flex min-w-0 flex-1 flex-col gap-px">
            <span className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate font-label text-base font-bold uppercase leading-[18px] tracking-wide">{name}</span>
              <HpBar hp={unit.hp} />
            </span>
            <span className="truncate font-data text-xs leading-[14px] text-wood-500">{statusText(unit, planning.contactZone)}</span>
          </div>
        </div>
        <button type="button" className="fhq-btn h-12 w-11 shrink-0 px-0" onClick={() => onCycle(1)} aria-label="Next unit without orders" title="Next unit without orders">
          <Icon name="next" />
        </button>
        <button
          type="button"
          onClick={() => planning.selectUnit(null)}
          aria-label="Close"
          title="Close"
          className="flex h-12 w-10 shrink-0 items-center justify-center text-ivory-100"
        >
          <Icon name="close" />
        </button>
      </div>
      <div className="flex min-h-[38px] flex-col gap-px text-ivory-100" aria-live="polite">
        {readout.kind === "prompt" && readout.title === "No orders yet" ? (
          <p className="text-[15px] leading-[19px]">No orders yet. {readout.lines[0]?.text}</p>
        ) : (
          <>
            <span className={`font-display text-[15px] uppercase leading-[19px] tracking-wide ${titleTone}`}>{readout.title}</span>
            {readout.lines.slice(0, 4).map((line) => (
              <span key={line.text} className={`text-sm leading-[18px] ${LINE_TONE[line.tone]}`}>
                {line.text}
              </span>
            ))}
          </>
        )}
      </div>
      <div className="flex gap-1.5">
        {order !== undefined && (
          <OrderKey icon="clear" label="Clear" title="Drop this turn's order" onClick={() => planning.setOrder(unit.id, null)} />
        )}
        {column && <OrderKey glyph="deploy" label="Deploy" active={kind === "deploy"} onClick={() => stay("deploy")} title="Unpack where it stands, ready to fight next turn" />}
        {guns && (
          <OrderKey
            icon="barrage"
            label="Barrage"
            active={planning.armed === "barrage" || kind === "barrage"}
            onClick={() => planning.arm(planning.armed === "barrage" ? null : "barrage")}
            title="Then tap a hex within range to shell it"
          />
        )}
        <OrderKey icon="hold" label="Hold" active={kind === "hold"} onClick={() => stay("hold")} title={column ? "Stop here (a column gets no Hold bonus)" : "Stay put and dig in"} />
        <OrderKey icon="rest" label="Rest" active={kind === "rest"} onClick={() => stay("rest")} title="Stay put and repair" />
        <OrderKey icon="more" label="More" onClick={onMore} title="Stops on the way, on arrival, disband" />
      </div>
    </section>
  );
}

const LINE_TONE = { plain: "text-ivory-100", quiet: "text-ivory-400", warn: "text-warn", bad: "text-[#ff8a78]" } as const;

/** The selected unit's less common orders: stops on the way, on arrival, the disband pick. */
export function MoreSheet({
  unit,
  view,
  planning,
  designate,
  onClose,
}: {
  unit: Unit;
  view: WorldView;
  planning: WorldPlanning;
  designate: Designate;
  onClose: () => void;
}) {
  const order = planning.plan.orders[unit.id];
  const route = planning.routes.find((r) => r.unitId === unit.id);
  const swap = useSwaps(view, planning).get(unit.id) ?? null;
  const travel = order?.kind === "travel" ? order : null;
  const canStop = travel !== null || (order === undefined && route?.kind === "standing");
  const used = travel?.via.length ?? 0;
  const disbandRank = planning.plan.disband.indexOf(unit.id);
  const name = designate(unit.id) ?? UNIT_LABEL[unit.type];
  return (
    <Sheet label={`More orders for ${name}`} title={name} subtitle={orderText(order, route, swap, designate)} onClose={onClose}>
      <Line
        icon={<Icon name="waypoint" size={24} />}
        name="Add a stop on the way"
        sub={
          !canStop
            ? "give it a Travel order first: tap a far hex"
            : used >= MAX_WAYPOINTS
              ? `all ${MAX_WAYPOINTS} stops used`
              : `then tap the map · ${used} of ${MAX_WAYPOINTS} used`
        }
        right={canStop && used < MAX_WAYPOINTS ? <Icon name="chevron" size={18} /> : undefined}
        onClick={
          canStop && used < MAX_WAYPOINTS
            ? () => {
                planning.arm("waypoint");
                onClose();
              }
            : undefined
        }
      />
      {travel !== null && travel.via.length > 0 && (
        <div className="border-b border-ink-900/20 px-3 py-2">
          <div className="flex items-center justify-between">
            <span className="font-label text-sm">Through</span>
            <button type="button" onClick={() => planning.clearWaypoints(unit.id)} className="fhq-btn h-9 text-xs">
              Clear stops
            </button>
          </div>
          <ol className="mt-1 flex flex-wrap gap-1.5">
            {travel.via.map((w, k) => (
              <li key={`${w.q},${w.r}`} className="flex items-center gap-1 rounded-sm bg-parchment-400/50 pl-2 font-data text-sm">
                <span aria-hidden>◆</span>
                {k + 1}: {at(w)}
                <button
                  type="button"
                  onClick={() => planning.removeWaypoint(unit.id, k)}
                  aria-label={`Remove stop ${k + 1} at ${at(w)}`}
                  className="flex size-11 items-center justify-center text-wood-500"
                >
                  <Icon name="close" size={16} />
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}
      {travel !== null && (
        <div className="flex min-h-16 items-center gap-3 border-b border-ink-900/20 px-3">
          <span className="font-label text-[17px] font-semibold">On arrival</span>
          <div className="ml-auto flex gap-1.5">
            <button
              type="button"
              data-active={travel.onArrival === "deployed"}
              aria-pressed={travel.onArrival === "deployed"}
              onClick={() => planning.setOnArrival(unit.id, "deployed")}
              className="fhq-btn h-11 gap-1.5 px-3 text-[13px]"
            >
              <Glyph name="deploy" size={18} />
              Deploy
            </button>
            <button
              type="button"
              data-active={travel.onArrival === "column"}
              aria-pressed={travel.onArrival === "column"}
              onClick={() => planning.setOnArrival(unit.id, "column")}
              className="fhq-btn h-11 gap-1.5 px-3 text-[13px]"
            >
              <Glyph name="column" size={18} />
              Stay packed
            </button>
          </div>
        </div>
      )}
      <label className="flex min-h-[60px] items-center gap-3 px-3">
        <Glyph name="disband" size={24} className={disbandRank >= 0 ? "text-invalid" : ""} />
        <span className="flex flex-col gap-px">
          <span className="font-label text-[17px] font-semibold leading-[19px]">
            Disband first in a shortage{disbandRank >= 0 ? ` (#${disbandRank + 1})` : ""}
          </span>
          <span className="font-data text-xs leading-[14px] text-wood-500">only matters if supply runs out</span>
        </span>
        <input
          type="checkbox"
          checked={disbandRank >= 0}
          onChange={() => planning.toggleDisband(unit.id)}
          className="ml-auto size-6 accent-[#6f4f24] [color-scheme:light]"
        />
      </label>
    </Sheet>
  );
}
