// The damage preview in words, for the board's hover tip and the unit panel: what
// a Move into a hostile hex or a Barrage on a hex is expected to do, from the units
// the player can see (`combatPreview.ts` holds the maths).

import { hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { NEUTRAL_OWNER } from "./colors";
import {
  attackPhrase,
  clampNote,
  forecastAttack,
  forecastBarrage,
  type AttackForecast,
  type SeenDefender,
} from "./combatPreview";
import type { Designate } from "./designations";
import type { WorldOrder } from "./plan";
import type { WorldMap } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];
type Unit = WorldState["units"][number];

/** A preview: a heading, the numbers, and a note on what could change them. */
export interface CombatPreview {
  title: string;
  lines: string[];
  /** Plain-words caveat, shown muted. */
  note: string | null;
}

/** Whether `hex` is settlement ground (`GameState.settlement_ground`): a town or city, or a founded site. */
export function settlementGroundAt(map: WorldMap, state: WorldState, hex: Axial): boolean {
  const cell = map.hexAt(hex.q, hex.r);
  if (cell === undefined) throw new Error(`hex (${hex.q}, ${hex.r}) is off the map`);
  if (cell.terrain === "town" || cell.terrain === "city") return true;
  return (
    map.siteAt(hex.q, hex.r) !== undefined && state.settlements.some((s) => s.coord.q === hex.q && s.coord.r === hex.r)
  );
}

/** "s004 infantry", "Garrison infantry", "2nd Infantry". */
function unitName(u: Unit, viewer: string, designate: Designate): string {
  if (u.owner === viewer) return designate(u.id) ?? `Your ${u.type}`;
  return u.owner === NEUTRAL_OWNER ? `Garrison ${u.type}` : `${u.owner} ${u.type}`;
}

/** How a unit seen at `hex` would take a blow: its ground, posture, and whether it holds. */
function seen(
  u: Unit,
  map: WorldMap,
  state: WorldState,
  viewer: string,
  orders: Readonly<Record<number, WorldOrder>>,
): Omit<SeenDefender, "hold"> & { hold: "yes" | "maybe" | "no" } {
  const cell = map.hexAt(u.coord.q, u.coord.r);
  if (cell === undefined) throw new Error(`unit ${u.id} stands off the map`);
  // Garrisons always hold; your own units hold if you order it; anyone else might.
  const hold = u.owner === NEUTRAL_OWNER ? "yes" : u.owner === viewer ? (orders[u.id]?.kind === "hold" ? "yes" : "no") : "maybe";
  return {
    type: u.type,
    hp: u.hp,
    terrain: cell.terrain,
    settlementGround: settlementGroundAt(map, state, u.coord),
    disordered: u.disordered,
    column: u.posture === "column",
    hold,
  };
}

/**
 * The defender a Move by `attacker` into `hex` meets, if any: the deployed hostile
 * unit there, else the first hostile column (it deploys disordered first).
 */
function defenderAt(state: WorldState, viewer: string, hex: Axial): Unit | null {
  const here = state.units.filter((u) => u.owner !== viewer && u.coord.q === hex.q && u.coord.r === hex.r);
  return here.find((u) => u.posture === "deployed") ?? [...here].sort((a, b) => a.id - b.id)[0] ?? null;
}

/** What `attacker`'s Move into the hostile `hex` is expected to do (`null`: nobody hostile seen there). */
export function attackForecastAt(
  attacker: Unit,
  hex: Axial,
  map: WorldMap,
  state: WorldState,
  viewer: string,
): { defender: Unit; forecast: AttackForecast } | null {
  const defender = defenderAt(state, viewer, hex);
  if (defender === null) return null;
  const d = seen(defender, map, state, viewer, {});
  return { defender, forecast: forecastAttack(attacker, { ...d, hold: d.hold === "yes" ? "yes" : "maybe" }) };
}

/** The hover/panel preview of an attack. */
export function attackPreview(
  attacker: Unit,
  hex: Axial,
  map: WorldMap,
  state: WorldState,
  viewer: string,
  designate: Designate,
): CombatPreview | null {
  const found = attackForecastAt(attacker, hex, map, state, viewer);
  if (found === null) return null;
  const { defender, forecast: f } = found;
  const lines = [`${attackPhrase(f)}.`];
  if (f.modifiers.length > 0) lines.push(`Defender: ${f.modifiers.join(", ")}.`);
  if (defender.posture === "column") lines.push("It is packed: it deploys, disordered, before you strike.");
  if (f.kills) lines.push(`That destroys it (${defender.hp} HP).`);
  if (f.dies) lines.push(`${unitName(attacker, viewer, designate)} would be destroyed (${attacker.hp} HP).`);
  return {
    title: `Attack ${unitName(defender, viewer, designate).replace(/^Garrison/, "garrison")}, ${defender.hp} HP`,
    lines,
    note: "If it stays put and nothing else joins: more attackers each strike, and it strikes each of them.",
  };
}

/** One line per seen unit a Barrage on `hex` would strike, e.g. "s004 infantry, 8 HP: 3 (2 if it holds) · +1 hills". */
export function barragePreview(
  hex: Axial,
  map: WorldMap,
  state: WorldState,
  viewer: string,
  visible: ReadonlySet<string>,
  orders: Readonly<Record<number, WorldOrder>>,
  designate: Designate,
): CombatPreview {
  const title = `Barrage (${hex.q},${hex.r})`;
  if (!visible.has(hexKey(hex.q, hex.r))) {
    return { title, lines: ["Not in sight: it hits whatever stands there."], note: null };
  }
  const here = state.units.filter((u) => u.coord.q === hex.q && u.coord.r === hex.r);
  if (here.length === 0) return { title, lines: ["No unit seen there."], note: null };
  const byStrike = [...here].sort((a, b) => Number(a.posture === "column") - Number(b.posture === "column") || a.id - b.id);
  const forecasts = forecastBarrage(byStrike.map((u) => ({ ...seen(u, map, state, viewer, orders), owner: u.owner })));
  const lines = forecasts.map((f, k) => {
    const u = byStrike[k];
    const dmg = `${f.damage.damage}${f.ifHolds !== null ? ` (${f.ifHolds.damage} if it holds)` : clampNote(f.damage)}`;
    const mods = f.modifiers.length > 0 ? ` · ${f.modifiers.join(", ")}` : "";
    const fate = f.kills ? ", destroyed" : "";
    const friend = u.owner === viewer ? " (yours!)" : "";
    return `${unitName(u, viewer, designate)}${friend}, ${u.hp} HP: ${dmg}${fate}${mods}`;
  });
  return { title, lines, note: "Lands before anyone moves, on what stands there now." };
}
