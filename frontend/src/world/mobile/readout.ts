// What the phone dock says about the selected unit: its current order in plain
// words, or a one-line prompt when it has none. It stands in for the desktop hover
// tip and the unit panel's text, so it carries the same cues: arrival time, the
// stop-short and halt notes, unscouted route hexes, the expected result of an
// attack or barrage, the warning for a Move your own units will block, and the
// withdrawal warning.

import { axialNeighbors, hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import type { Armed } from "../autoOrder";
import type { CombatPreview } from "../combatView";
import { fogText, haltText } from "../contact";
import type { SwapInfo } from "../labels";
import { inBarrageRange, type WorldOrder } from "../plan";
import type { PlannedRoute } from "../useWorldPlan";
import { at, MAX_WAYPOINTS } from "../waypoints";

type Unit = components["schemas"]["Unit"];

export type ReadoutTone = "plain" | "quiet" | "warn" | "bad";

export interface Readout {
  /** The headline: the order and where, or "No orders". */
  title: string;
  /** `attack`: the headline is an attack or barrage (drawn red). */
  kind: "order" | "attack" | "prompt";
  lines: { text: string; tone: ReadoutTone }[];
}

export interface ReadoutInput {
  unit: Unit;
  order: WorldOrder | undefined;
  route: PlannedRoute | undefined;
  swap: SwapInfo | null;
  /** The partner's name in prose, for a swap. */
  partnerName: string | null;
  /** The staged attack's or barrage's expected result (`attackPreview` / `barragePreview`). */
  expected: CombatPreview | null;
  /** Why your own units will block the staged Move (`planConflicts`), else `null`. */
  blocked: string | null;
  /** `withdrawalWarning` for the order. */
  warning: string | null;
  /** The known contact zone (`contactZone`). */
  zone: ReadonlySet<string>;
  armed: Armed | null;
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** "arrives this turn" / "arrives in 3 turns". */
export function arrivalText(eta: number | null): string {
  if (eta === null) return "arrival unknown";
  return eta <= 1 ? "arrives this turn" : `arrives in ${eta} turns`;
}

/** The hex sits just outside the contact zone: a travel ending there stopped short of contact. */
export function edgeOfContact(hex: Axial, zone: ReadonlySet<string>): boolean {
  return !zone.has(hexKey(hex.q, hex.r)) && axialNeighbors(hex).some((n) => zone.has(hexKey(n.q, n.r)));
}

export function unitReadout(input: ReadoutInput): Readout {
  const { unit, order, route, armed } = input;
  if (armed === "waypoint") {
    const used = order?.kind === "travel" ? order.via.length : 0;
    return {
      title: "Add a stop",
      kind: "prompt",
      lines: [{ text: `Tap a hex for the route to pass through (${used} of ${MAX_WAYPOINTS} used).`, tone: "plain" }],
    };
  }
  if (armed === "barrage") {
    return { title: "Barrage", kind: "prompt", lines: [{ text: "Tap a red hex in range to shell it.", tone: "plain" }] };
  }
  const lines: Readout["lines"] = [];
  const add = (text: string | null, tone: ReadoutTone = "plain") => {
    if (text !== null) lines.push({ text, tone });
  };
  const travelCues = () => {
    if (route === undefined) return;
    if (route.halt !== null) add(`${capitalize(haltText(route.halt, route.from))}.`, "bad");
    add(fogText(route.fog), "warn");
  };
  if (order === undefined) {
    if (route?.kind === "standing") {
      travelCues();
      add("Carrying on from an earlier turn. Tap a hex to send it elsewhere.", "quiet");
      return { title: `Travelling · ${arrivalText(route.eta)}`, kind: "order", lines };
    }
    return { title: "No orders yet", kind: "prompt", lines: [{ text: promptFor(unit, input.zone), tone: "plain" }] };
  }
  switch (order.kind) {
    case "travel": {
      if (route !== undefined && route.halt === null && edgeOfContact(order.destination, input.zone)) {
        add(`Stops at ${at(order.destination)}, one hex short of contact, and deploys in order.`, "warn");
      }
      travelCues();
      if (order.via.length > 0) add(`Through ${order.via.length} stop${order.via.length === 1 ? "" : "s"}.`, "quiet");
      if (order.onArrival === "column") add("Stays packed on arrival.", "quiet");
      add(input.warning === null ? null : `${input.warning}.`, "bad");
      return { title: `Travel to ${at(order.destination)} · ${arrivalText(route?.eta ?? null)}`, kind: "order", lines };
    }
    case "move": {
      const swap = input.swap;
      const partner = input.partnerName ?? "a neighbour";
      if (swap !== null) {
        const title =
          swap.role === "relieves"
            ? `Relieve ${partner} at ${at(swap.partner.coord)}`
            : swap.role === "relieved"
              ? `Relieved by ${partner}`
              : `Swap with ${partner}`;
        add(input.blocked === null ? null : `${input.blocked}.`, "bad");
        add("Both step on the first pulse.", "quiet");
        return { title, kind: "order", lines };
      }
      const end = order.path[order.path.length - 1];
      if (route?.attack) {
        add(input.blocked === null ? null : `${input.blocked}.`, "bad");
        for (const line of input.expected?.lines ?? []) add(line);
        add(input.expected?.note ?? null, "quiet");
        add(input.warning === null ? null : `${input.warning}.`, "bad");
        return { title: `Attack ${at(route.attack)}`, kind: "attack", lines };
      }
      add(input.blocked === null ? null : `${input.blocked}.`, "bad");
      add(`${order.path.length} hex${order.path.length === 1 ? "" : "es"}, this turn.`, "quiet");
      add(input.warning === null ? null : `${input.warning}.`, "bad");
      return { title: `Move to ${at(end)}`, kind: "order", lines };
    }
    case "barrage": {
      for (const line of input.expected?.lines ?? []) add(line);
      add(input.expected?.note ?? null, "quiet");
      if (!inBarrageRange(unit, order.target)) add("Target out of range.", "bad");
      return { title: `Barrage ${at(order.target)}`, kind: "attack", lines };
    }
    case "rest":
      return { title: "Rest", kind: "order", lines: [{ text: "Stays put and repairs.", tone: "plain" }] };
    case "hold":
      return {
        title: "Hold",
        kind: "order",
        lines: [
          {
            text: unit.posture === "column" ? "Stops here. A column gets no Hold bonus." : "Stays put and digs in.",
            tone: "plain",
          },
        ],
      };
    case "deploy":
      return { title: "Deploy", kind: "order", lines: [{ text: "Unpacks here, ready to fight next turn.", tone: "plain" }] };
    case "march":
      return { title: "March", kind: "order", lines: [{ text: `${order.path.length} hexes, this turn.`, tone: "plain" }] };
  }
}

/** The one-line prompt for a unit with no orders. */
function promptFor(unit: Unit, zone: ReadonlySet<string>): string {
  if (unit.posture === "column") return "Tap a hex to travel there, or Deploy to unpack here.";
  const near = zone.has(hexKey(unit.coord.q, unit.coord.r));
  if (unit.type === "artillery") {
    return near
      ? "Tap a red hex to shell it, or a lit one to move. Close to contact, it moves a step at a time."
      : "Tap a red hex to shell it, a lit one to move, or anywhere further off to travel there.";
  }
  return near
    ? "Tap a lit hex to move, a red one to attack. Close to contact, it moves a step at a time."
    : "Tap a lit hex to move, a red one to attack, or anywhere further off to travel there.";
}
