import { afterEach, describe, expect, it, vi } from "vitest";

import type { components } from "@/lib/api/schema";

import { designator, EMPTY_BOOK, loadBook, ordinal, parseBook, saveBook, updateBook } from "./designations";

type Unit = components["schemas"]["Unit"];

function unit(id: number, type: Unit["type"]): Unit {
  return { id, type, owner: "p", coord: { q: 0, r: 0 }, hp: 10, facing: "right", posture: "deployed", disordered: false };
}

describe("ordinal", () => {
  it("writes English ordinals", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111].map(ordinal)).toEqual([
      "1st",
      "2nd",
      "3rd",
      "4th",
      "11th",
      "12th",
      "13th",
      "21st",
      "22nd",
      "23rd",
      "101st",
      "111th",
    ]);
  });
});

describe("designations", () => {
  it("numbers each type by seniority", () => {
    const book = updateBook(EMPTY_BOOK, [unit(30, "infantry"), unit(12, "infantry"), unit(20, "armor"), unit(25, "infantry")]);
    const name = designator(book);
    expect([12, 25, 30, 20].map((id) => name(id))).toEqual(["1st Infantry", "2nd Infantry", "3rd Infantry", "1st Armor"]);
    expect(name(12, true)).toBe("1st Inf");
    expect(name(99)).toBeNull();
  });

  it("keeps numbers when a unit dies, and gives a recruit the next one", () => {
    const first = updateBook(EMPTY_BOOK, [unit(1, "infantry"), unit(2, "infantry"), unit(3, "infantry")]);
    // The 2nd falls; a recruit (id 9) joins.
    const later = updateBook(first, [unit(1, "infantry"), unit(3, "infantry"), unit(9, "infantry")]);
    const name = designator(later);
    expect([1, 3, 9].map((id) => name(id))).toEqual(["1st Infantry", "3rd Infantry", "4th Infantry"]);
    // The fallen unit keeps its name for old reports.
    expect(name(2)).toBe("2nd Infantry");
  });

  it("returns the same book when nothing is new", () => {
    const book = updateBook(EMPTY_BOOK, [unit(1, "artillery")]);
    expect(updateBook(book, [unit(1, "artillery")])).toBe(book);
  });

  it("round-trips through storage and ignores a malformed book", () => {
    const data = new Map<string, string>();
    vi.stubGlobal("window", {
      localStorage: { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) },
    });
    const book = updateBook(EMPTY_BOOK, [unit(4, "armor"), unit(5, "infantry")]);
    saveBook("w", "p", book);
    expect(loadBook("w", "p")).toEqual(book);
    expect(loadBook("w", "other")).toEqual(EMPTY_BOOK);
    expect(parseBook("nope")).toBeNull();
    expect(parseBook(JSON.stringify({ units: { 1: { type: "tank", n: 1 } }, next: EMPTY_BOOK.next }))).toBeNull();
  });

  afterEach(() => vi.unstubAllGlobals());
});
