// Short names for your units: "1st Infantry", "2nd Infantry", "1st Armor".
//
// Purely presentational (the API knows units by id only). Numbers are given per
// type in order of seniority (lower unit id first) and then kept: a unit keeps
// its number when others die, and a new recruit takes the next number of its type
// rather than a fallen unit's. The book of numbers lives in `localStorage` per
// world and seat, so it survives reloads; a browser that has never seen the world
// numbers the units it finds by seniority. `#id` stays available in the unit panel.
//
// Storage is a boundary: it may be missing, full or blocked, and what comes back
// may be from an older build; a book that doesn't parse is rebuilt.

import type { components } from "@/lib/api/schema";

type Unit = components["schemas"]["Unit"];
type UnitType = components["schemas"]["UnitType"];

const UNIT_TYPES: readonly UnitType[] = ["infantry", "armor", "artillery"];

/** Every own unit ever numbered, and the next number of each type. */
export interface DesignationBook {
  /** Unit id → its type and number. Fallen units stay, so old reports keep their names. */
  units: Readonly<Record<number, { type: UnitType; n: number }>>;
  next: Readonly<Record<UnitType, number>>;
}

export const EMPTY_BOOK: DesignationBook = { units: {}, next: { infantry: 1, armor: 1, artillery: 1 } };

/**
 * `book` with every unit in `own` numbered: the ones it already has keep their
 * numbers, new ones take the next numbers of their type in id order. Returns
 * `book` itself when nothing is new.
 */
export function updateBook(book: DesignationBook, own: readonly Unit[]): DesignationBook {
  const fresh = own.filter((u) => book.units[u.id] === undefined).sort((a, b) => a.id - b.id);
  if (fresh.length === 0) return book;
  const units = { ...book.units };
  const next = { ...book.next };
  for (const u of fresh) {
    units[u.id] = { type: u.type, n: next[u.type] };
    next[u.type] += 1;
  }
  return { units, next };
}

const TYPE_NAME: Record<UnitType, string> = { infantry: "Infantry", armor: "Armor", artillery: "Artillery" };
const TYPE_SHORT: Record<UnitType, string> = { infantry: "Inf", armor: "Arm", artillery: "Art" };

/** 1 → "1st", 2 → "2nd", 11 → "11th", 22 → "22nd". */
export function ordinal(n: number): string {
  const tens = n % 100;
  if (tens >= 11 && tens <= 13) return `${n}th`;
  switch (n % 10) {
    case 1:
      return `${n}st`;
    case 2:
      return `${n}nd`;
    case 3:
      return `${n}rd`;
    default:
      return `${n}th`;
  }
}

/** A unit's designation: "2nd Infantry", or "2nd Inf" with `short`. */
export function designationOf(entry: { type: UnitType; n: number }, short = false): string {
  return `${ordinal(entry.n)} ${(short ? TYPE_SHORT : TYPE_NAME)[entry.type]}`;
}

/** Looks a unit up by id: its designation, or `null` for a unit not in the book (not yours). */
export type Designate = (unitId: number, short?: boolean) => string | null;

export function designator(book: DesignationBook): Designate {
  return (unitId, short = false) => {
    const entry = book.units[unitId];
    return entry === undefined ? null : designationOf(entry, short);
  };
}

// --- storage ------------------------------------------------------------------------

const PREFIX = "hexgen:designations:";

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null; // access itself can throw when site data is blocked
  }
}

/** The stored book for this world and seat, or the empty book. */
export function loadBook(gameId: string, slot: string): DesignationBook {
  const store = storage();
  if (store === null) return EMPTY_BOOK;
  try {
    const raw = store.getItem(`${PREFIX}${gameId}:${slot}`);
    return raw === null ? EMPTY_BOOK : (parseBook(raw) ?? EMPTY_BOOK);
  } catch {
    return EMPTY_BOOK;
  }
}

export function saveBook(gameId: string, slot: string, book: DesignationBook): void {
  const store = storage();
  if (store === null) return;
  try {
    store.setItem(`${PREFIX}${gameId}:${slot}`, JSON.stringify(book));
  } catch {
    // Full or blocked: names still work for this page, numbering restarts next time.
  }
}

/** A stored book, or `null` if it isn't one. */
export function parseBook(raw: string): DesignationBook | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(data) || !isRecord(data.units) || !isRecord(data.next)) return null;
  const next = { ...EMPTY_BOOK.next };
  for (const t of UNIT_TYPES) {
    const n = data.next[t];
    if (!Number.isInteger(n) || (n as number) < 1) return null;
    next[t] = n as number;
  }
  const units: Record<number, { type: UnitType; n: number }> = {};
  for (const [id, entry] of Object.entries(data.units)) {
    if (!Number.isInteger(Number(id)) || !isRecord(entry)) return null;
    if (!UNIT_TYPES.includes(entry.type as UnitType) || !Number.isInteger(entry.n)) return null;
    units[Number(id)] = { type: entry.type as UnitType, n: entry.n as number };
  }
  return { units, next };
}

function isRecord(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}
