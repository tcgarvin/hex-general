// "Your fights": the viewer's own engagements in a turn, for a jump list
// (WORLD_SPEC §11 asks for a list of your engagements to jump between).
//
// One entry per combat with one of the viewer's units in it, barrage fired by
// or landing on one of theirs, and capture of or from them, in playback order,
// each with the beat to jump to and a one-line result.

import type { Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import type { UnitBook } from "../turnReport";
import type { Playback } from "./reconstruct";

type UnitType = components["schemas"]["UnitType"];

export type EngagementResult =
  /** The viewer's side destroyed the opposition and lost nothing. */
  | "won"
  /** The viewer lost units and destroyed none. */
  | "lost"
  /** Both sides lost units. */
  | "traded"
  /** The viewer's defender stood its ground. */
  | "held"
  /** The viewer's attack went in and failed: no one destroyed, nobody moved (nobody retreats). */
  | "repulsed"
  /** The viewer's barrage struck (or shelled an empty hex: `missed`). */
  | "shelled"
  | "missed"
  /** The viewer's units were shelled. */
  | "under_fire"
  /** A site changed hands: `took` for the viewer, `lost_site` from them. */
  | "took"
  | "lost_site";

export interface EngagementUnit {
  unitId: number;
  owner: string;
  type: UnitType;
  damageTaken: number;
  destroyed: boolean;
}

export interface Engagement {
  beat: number;
  eventIndex: number;
  kind: "combat" | "barrage" | "capture";
  at: Axial;
  result: EngagementResult;
  ours: EngagementUnit[];
  theirs: EngagementUnit[];
  /** "Your armor and infantry took (25,32) from Garrison infantry." */
  summary: string;
}

const coordText = (a: Axial) => `(${a.q},${a.r})`;

/**
 * "Your armor and infantry": names that share an owner word ("Your", "Garrison",
 * a slot) carry it once, at the front.
 */
export function joinNames(names: string[]): string {
  if (names.length <= 1) return names.join("");
  const owner = names[0].split(" ")[0];
  // "Your infantry and armor", but never "1st Infantry and Armor": an ordinal is a name.
  const shared = !/^\d+(st|nd|rd|th)$/.test(owner) && names.every((n) => n.startsWith(`${owner} `));
  const parts = shared ? [names[0], ...names.slice(1).map((n) => n.slice(owner.length + 1))] : names;
  return `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;
}

/** The viewer's engagements in playback order. */
export function engagements(pb: Playback, book: UnitBook): Engagement[] {
  const out: Engagement[] = [];
  const names = (units: EngagementUnit[]) => joinNames(units.map((u) => book.nameOf(u.unitId)));
  pb.beats.forEach((beat, k) => {
    for (const a of beat.actions) {
      if (a.kind === "combat") {
        const sides = a.sides.map((s) => ({
          unitId: s.unitId,
          owner: s.owner,
          type: s.type,
          damageTaken: s.damageTaken,
          destroyed: s.destroyed,
          role: s.role,
        }));
        const ours = sides.filter((s) => s.owner === pb.viewer);
        if (ours.length === 0) continue;
        const theirs = sides.filter((s) => s.owner !== pb.viewer);
        const lost = ours.some((s) => s.destroyed);
        const killed = theirs.some((s) => s.destroyed);
        const entered = a.entrantId !== null && ours.some((s) => s.unitId === a.entrantId);
        const defending = ours.some((s) => s.role === "defender");
        const result: EngagementResult =
          lost && killed ? "traded" : lost ? "lost" : killed || entered ? "won" : defending ? "held" : "repulsed";
        const verb = {
          traded: "traded losses with",
          lost: "lost to",
          won: entered ? `took ${coordText(a.at)} from` : "beat",
          held: "held off",
          repulsed: "could not dislodge",
        }[result as "traded" | "lost" | "won" | "held" | "repulsed"];
        const strip = (u: (typeof sides)[number]): EngagementUnit => ({
          unitId: u.unitId,
          owner: u.owner,
          type: u.type,
          damageTaken: u.damageTaken,
          destroyed: u.destroyed,
        });
        out.push({
          beat: k,
          eventIndex: a.eventIndex,
          kind: "combat",
          at: a.at,
          result,
          ours: ours.map(strip),
          theirs: theirs.map(strip),
          summary: `${names(ours)} ${verb} ${names(theirs)}${entered ? "" : ` at ${coordText(a.at)}`}.`,
        });
      } else if (a.kind === "barrage") {
        const gun = a.gunId === null ? undefined : pb.snapshots[k].get(a.gunId);
        const gunName = a.gunId === null ? "An unseen gun" : book.nameOf(a.gunId);
        const firedByUs = gun?.owner === pb.viewer;
        const strikes = a.strikes.map((s) => ({
          unitId: s.unitId,
          owner: s.owner,
          type: s.type,
          damageTaken: s.damage,
          destroyed: s.destroyed,
        }));
        const ours = strikes.filter((s) => s.owner === pb.viewer);
        if (!firedByUs && ours.length === 0) continue;
        const theirs = strikes.filter((s) => s.owner !== pb.viewer);
        const result: EngagementResult = ours.length > 0 ? "under_fire" : strikes.length > 0 ? "shelled" : "missed";
        const summary =
          result === "under_fire"
            ? `${names(ours)} came under fire at ${coordText(a.target)}.`
            : result === "shelled"
              ? `${gunName} shelled ${names(theirs)} at ${coordText(a.target)}.`
              : `${gunName} shelled an empty ${coordText(a.target)}.`;
        out.push({ beat: k, eventIndex: a.eventIndex, kind: "barrage", at: a.target, result, ours, theirs, summary });
      } else if (a.kind === "capture") {
        if (a.owner !== pb.viewer && a.previousOwner !== pb.viewer) continue;
        const took = a.owner === pb.viewer;
        const site = a.site === null ? "a settlement" : `a ${a.site.replace("_", " ")}`;
        out.push({
          beat: k,
          eventIndex: a.eventIndex,
          kind: "capture",
          at: a.at,
          result: took ? "took" : "lost_site",
          ours: [],
          theirs: [],
          summary: took
            ? `You took ${site} at ${coordText(a.at)}.`
            : `${book.naming.player(a.owner)} took your ${site.replace(/^an? /, "")} at ${coordText(a.at)}.`,
        });
      }
    }
  });
  return out;
}
