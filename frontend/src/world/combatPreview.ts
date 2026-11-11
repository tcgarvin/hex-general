// What an attack or a barrage is expected to do, before it is ordered.
//
// A port of the engine's damage maths: `compute_damage`, `defender_modifiers` and
// `resolve_contest` in api/app/engine/combat.py, and `_strike` in barrage.py.
// Damage is the striker's attack minus the target's defence minus the target's
// modifiers, clamped to 1..5, and does not depend on HP. Only a stationary
// defender gets modifiers: its ground (terrain, or +2 on settlement ground) and
// +1 for a Hold order, less 2 if it is disordered. Attackers get none. A packed
// column hit by a barrage loses 3 and gets no ground or Hold. `combat_fixture.py`
// runs the real engine over a table of matchups and `combatPreview.test.ts` checks
// every row here.
//
// What the planner can't know is left to the caller to say: whether another
// player's unit Holds (garrisons always do), whether it moves first, and who else
// joins the fight.

import type { components } from "@/lib/api/schema";
import { UNIT_STATS } from "@/planning/rules";

import { MANUAL_RULES } from "./manualRules";

type UnitType = components["schemas"]["UnitType"];
type Terrain = components["schemas"]["Terrain"];

const { damageMin, damageMax, holdBonus, disorderDefensePenalty, columnDefensePenalty } = MANUAL_RULES;

/** One unit's damage to another, with the raw value before the 1..5 clamp. */
export interface Strike {
  damage: number;
  raw: number;
}

/** `compute_damage`: attack − defence − the target's modifiers, clamped to 1..5. */
export function strike(attacker: UnitType, target: UnitType, targetMods: number): Strike {
  const raw = UNIT_STATS[attacker].attack - UNIT_STATS[target].defense - targetMods;
  return { damage: Math.max(damageMin, Math.min(damageMax, raw)), raw };
}

/**
 * The defence a stationary defender's hex gives it (`terrain_defense` in models.py):
 * its terrain's, or the settlement bonus on settlement ground if that is higher.
 */
export function groundDefense(terrain: Terrain, settlementGround: boolean): number {
  const bonus = (MANUAL_RULES.terrainDefense as Partial<Record<Terrain, number>>)[terrain] ?? 0;
  return settlementGround ? Math.max(bonus, MANUAL_RULES.settlementDefense) : bonus;
}

/** A stationary unit being struck, as the striker's side can know it. */
export interface Target {
  type: UnitType;
  /** The ground bonus of its hex (`groundDefense`). */
  ground: number;
  hold: boolean;
  disordered: boolean;
}

/** `defender_modifiers`: ground + Hold − disorder. */
export function defenderMods(t: Target): number {
  return t.ground + (t.hold ? holdBonus : 0) - (t.disordered ? disorderDefensePenalty : 0);
}

/**
 * A melee attack on a deployed defender by one or more units stepping in on the
 * same pulse (`resolve_contest`): each attacker deals its strike to the defender,
 * and the defender strikes every attacker in full. Attackers get no modifiers.
 */
export function melee(attackers: readonly UnitType[], defender: Target): { dealt: Strike[]; taken: Strike[] } {
  const mods = defenderMods(defender);
  return {
    dealt: attackers.map((a) => strike(a, defender.type, mods)),
    taken: attackers.map((a) => strike(defender.type, a, 0)),
  };
}

/** A barrage's strike on one unit in the target hex (`_strike`). */
export function barrageStrike(occupant: Target & { column: boolean }): Strike {
  if (occupant.column) return strike("artillery", occupant.type, -columnDefensePenalty);
  return strike("artillery", occupant.type, defenderMods(occupant));
}

// --- what the planner shows ---------------------------------------------------------

/** "+2 site, +1 hold, −2 disordered": a defender's modifiers in words. */
export function modifierWords(
  t: { ground: number; settlementGround: boolean; terrain: Terrain; disordered: boolean },
  hold: "yes" | "no" | "maybe",
): string[] {
  const words: string[] = [];
  if (t.ground > 0) words.push(`+${t.ground} ${t.settlementGround ? "site" : t.terrain}`);
  if (hold === "yes") words.push(`+${holdBonus} hold`);
  if (hold === "maybe") words.push(`+${holdBonus} if it holds`);
  if (t.disordered) words.push(`−${disorderDefensePenalty} disordered`);
  return words;
}

/** " (floor 1)" / " (cap 5)" when the 1..5 clamp changed it, else "". */
export function clampNote(s: Strike): string {
  if (s.raw < damageMin) return ` (floor ${damageMin})`;
  if (s.raw > damageMax) return ` (cap ${damageMax})`;
  return "";
}

/** A defender as the attacking player sees it on the board. */
export interface SeenDefender {
  type: UnitType;
  hp: number;
  terrain: Terrain;
  settlementGround: boolean;
  disordered: boolean;
  /** A packed column deploys, disordered, before melee reaches it (WORLD_SPEC §4.6). */
  column: boolean;
  /** Garrisons always hold; another player's unit may or may not. */
  hold: "yes" | "maybe";
}

export interface AttackForecast {
  /** What this unit deals if the defender doesn't Hold, and if it does (`null`: it surely does). */
  dealt: Strike;
  dealtIfHolds: Strike | null;
  taken: Strike;
  /** The defender's modifiers in words. */
  modifiers: string[];
  /** This strike alone destroys it (even if it holds). */
  kills: boolean;
  /** The defender's strike back destroys the attacker. */
  dies: boolean;
}

/** One unit's attack on a seen defender, if the defender stays put. */
export function forecastAttack(attacker: { type: UnitType; hp: number }, d: SeenDefender): AttackForecast {
  // Melee always meets a deployed defender: a column deploys disordered first.
  const disordered = d.disordered || d.column;
  const ground = groundDefense(d.terrain, d.settlementGround);
  const base = { type: d.type, ground, disordered };
  const holds = melee([attacker.type], { ...base, hold: true });
  const loose = melee([attacker.type], { ...base, hold: false });
  const surely = d.hold === "yes";
  const dealt = surely ? holds.dealt[0] : loose.dealt[0];
  const dealtIfHolds = surely ? null : holds.dealt[0];
  return {
    dealt,
    dealtIfHolds,
    taken: loose.taken[0],
    modifiers: modifierWords({ ground, settlementGround: d.settlementGround, terrain: d.terrain, disordered }, d.hold),
    kills: (dealtIfHolds ?? dealt).damage >= d.hp,
    dies: loose.taken[0].damage >= attacker.hp,
  };
}

/** "You deal 2 (1 if it holds), take 1": the forecast's numbers in one phrase. */
export function attackPhrase(f: AttackForecast): string {
  const dealt =
    f.dealtIfHolds === null || f.dealtIfHolds.damage === f.dealt.damage
      ? `${(f.dealtIfHolds ?? f.dealt).damage}${clampNote(f.dealtIfHolds ?? f.dealt)}`
      : `${f.dealt.damage} (${f.dealtIfHolds.damage} if it holds)`;
  return `You deal ${dealt}, take ${f.taken.damage}${clampNote(f.taken)}`;
}

export interface BarrageForecast {
  type: UnitType;
  owner: string;
  hp: number;
  damage: Strike;
  /** Damage if it holds; `null` when Hold can't change it (a column, or it surely holds). */
  ifHolds: Strike | null;
  modifiers: string[];
  kills: boolean;
}

/** A barrage on each seen unit in the target hex (`_strike`), deployed occupant first. */
export function forecastBarrage(
  occupants: readonly (Omit<SeenDefender, "hold"> & { owner: string; hold: "yes" | "maybe" | "no" })[],
): BarrageForecast[] {
  const ordered = [...occupants].sort((a, b) => Number(a.column) - Number(b.column));
  return ordered.map((o) => {
    const ground = groundDefense(o.terrain, o.settlementGround);
    const base = { type: o.type, ground, disordered: o.disordered, column: o.column };
    if (o.column) {
      return {
        type: o.type,
        owner: o.owner,
        hp: o.hp,
        damage: barrageStrike({ ...base, hold: false }),
        ifHolds: null,
        modifiers: [`−${columnDefensePenalty} packed column`],
        kills: barrageStrike({ ...base, hold: false }).damage >= o.hp,
      };
    }
    const loose = barrageStrike({ ...base, hold: o.hold === "yes" });
    const held = o.hold === "maybe" ? barrageStrike({ ...base, hold: true }) : null;
    return {
      type: o.type,
      owner: o.owner,
      hp: o.hp,
      damage: loose,
      ifHolds: held !== null && held.damage !== loose.damage ? held : null,
      modifiers: modifierWords(
        { ground, settlementGround: o.settlementGround, terrain: o.terrain, disordered: o.disordered },
        o.hold,
      ),
      kills: (held ?? loose).damage >= o.hp,
    };
  });
}
