// Your units' designations ("2nd Infantry") for the world screen: the stored book
// (`designations.ts`), extended with any unit not yet numbered and saved back.

import { useEffect, useMemo, useState } from "react";

import type { components } from "@/lib/api/schema";

import { designator, loadBook, saveBook, updateBook, type Designate } from "./designations";

type Unit = components["schemas"]["Unit"];

export function useDesignations(gameId: string, slot: string, units: readonly Unit[]): Designate {
  const [book, setBook] = useState(() => loadBook(gameId, slot));
  const own = useMemo(() => units.filter((u) => u.owner === slot), [units, slot]);
  const current = updateBook(book, own);
  // New units are numbered as they appear (state adjusted during render), so a
  // number once given is never given again.
  if (current !== book) setBook(current);
  useEffect(() => saveBook(gameId, slot, book), [gameId, slot, book]);
  return useMemo(() => designator(current), [current]);
}
