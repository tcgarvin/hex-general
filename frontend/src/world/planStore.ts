// Unsent plan edits kept in `sessionStorage`, so a reload (or a crash) doesn't
// lose a half-made plan. One entry per world and turn; a plan is only ever
// restored into the turn it was made for, and older turns' entries are dropped.
//
// Each draft records the server plan it was forked from (`submissionKey`). If the
// server holds a different plan by the time the draft would come back, a newer
// plan was submitted elsewhere (another device, the API): the draft is set aside
// rather than restored over it, and the player chooses (`draftOnLoad`).
//
// Storage is a boundary: it may be missing, full, or blocked (private windows,
// sandboxed previews), and what comes back may be from an older build. Every
// access is wrapped, and a stored plan that doesn't parse is ignored.

import type { Axial } from "@/board/hex";

import type { StagedRecruit, WorldOrder, WorldPlan } from "./plan";

/** An unsent plan and the `submissionKey` of the server plan it was forked from. */
export interface UnsentDraft {
  plan: WorldPlan;
  /** `""` for a draft stored by an older build, which recorded no base. */
  base: string;
}

/** What to do with a stored draft when the page loads. */
export interface DraftDecision {
  /** Restore this plan as the unsent edits. */
  restore: WorldPlan | null;
  /** Keep the server's plan and offer this draft instead. */
  setAside: WorldPlan | null;
}

/**
 * A stored draft comes back only over the server plan it was forked from; if the
 * server now holds another (or the draft never said), it is set aside.
 */
export function draftOnLoad(draft: UnsentDraft | null, serverKey: string): DraftDecision {
  if (draft === null) return { restore: null, setAside: null };
  if (draft.base === serverKey) return { restore: draft.plan, setAside: null };
  return { restore: null, setAside: draft.plan };
}

/**
 * Whether live, unsent edits should be set aside because the server's plan
 * changed under them (submitted elsewhere while this tab was open). Edits whose
 * own submit just went through (`sent`) never are.
 */
export function supersededBy(local: { base: string; sent: boolean; dirty: boolean }, serverKey: string): boolean {
  return local.dirty && !local.sent && local.base !== serverKey;
}

const PREFIX = "hexgen:plan:";

function keyFor(gameId: string, turn: number): string {
  return `${PREFIX}${gameId}:${turn}`;
}

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null; // access itself can throw when site data is blocked
  }
}

/** Keep `draft` as this turn's unsent edits. */
export function saveUnsentPlan(gameId: string, turn: number, draft: UnsentDraft): void {
  const store = storage();
  if (store === null) return;
  try {
    store.setItem(keyFor(gameId, turn), JSON.stringify(draft));
  } catch {
    // Full or blocked: the plan still lives in memory, only the reload safety net is lost.
  }
}

/** Forget this turn's unsent edits (sent, reverted, or back to the server's plan). */
export function clearUnsentPlan(gameId: string, turn: number): void {
  const store = storage();
  if (store === null) return;
  try {
    store.removeItem(keyFor(gameId, turn));
  } catch {
    // Nothing to do: a store we can't write is one we never wrote to.
  }
}

/**
 * The unsent draft saved for exactly this world and turn, or `null`. Entries for
 * this world's earlier turns are deleted on the way: they can never apply again.
 */
export function loadUnsentPlan(gameId: string, turn: number): UnsentDraft | null {
  const store = storage();
  if (store === null) return null;
  try {
    const stale: string[] = [];
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i);
      if (key === null || !key.startsWith(`${PREFIX}${gameId}:`)) continue;
      if (Number(key.slice(`${PREFIX}${gameId}:`.length)) < turn) stale.push(key);
    }
    for (const key of stale) store.removeItem(key);
    const raw = store.getItem(keyFor(gameId, turn));
    return raw === null ? null : parseDraft(raw);
  } catch {
    return null;
  }
}

/** A stored draft, or `null`; a bare plan from an older build comes back with no base. */
export function parseDraft(raw: string): UnsentDraft | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (isRecord(data) && typeof data.base === "string" && isRecord(data.plan)) {
    const plan = parsePlan(JSON.stringify(data.plan));
    return plan === null ? null : { plan, base: data.base };
  }
  const plan = parsePlan(raw);
  return plan === null ? null : { plan, base: "" };
}

/** A stored plan, or `null` if it isn't one (an older build's format, or tampering). */
export function parsePlan(raw: string): WorldPlan | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(data) || !isRecord(data.orders) || !isRecord(data.recruits) || !Array.isArray(data.disband)) return null;
  const build = data.build;
  if (build !== null && build !== "barracks" && build !== "depot" && build !== "workshop") return null;
  if (!data.disband.every((id) => typeof id === "number")) return null;
  const orders: Record<number, WorldOrder> = {};
  for (const [id, order] of Object.entries(data.orders)) {
    if (!Number.isInteger(Number(id)) || !isOrder(order)) return null;
    orders[Number(id)] = order;
  }
  const recruits: Record<string, StagedRecruit> = {};
  for (const [key, r] of Object.entries(data.recruits)) {
    if (!isRecord(r) || !isAxial(r.coord) || !["infantry", "artillery", "armor"].includes(r.type as string)) return null;
    recruits[key] = r as unknown as StagedRecruit;
  }
  return { orders, recruits, build, disband: data.disband as number[] };
}

function isRecord(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}

function isAxial(x: unknown): x is Axial {
  return isRecord(x) && Number.isInteger(x.q) && Number.isInteger(x.r);
}

function isPath(x: unknown): x is Axial[] {
  return Array.isArray(x) && x.every(isAxial);
}

function isOrder(x: unknown): x is WorldOrder {
  if (!isRecord(x)) return false;
  switch (x.kind) {
    case "travel":
      return isAxial(x.destination) && isPath(x.via) && (x.onArrival === "deployed" || x.onArrival === "column");
    case "move":
      return isPath(x.path);
    case "march":
      return isPath(x.path) && typeof x.deployOnArrival === "boolean";
    case "barrage":
      return isAxial(x.target);
    case "rest":
    case "hold":
    case "deploy":
      return true;
    default:
      return false;
  }
}
