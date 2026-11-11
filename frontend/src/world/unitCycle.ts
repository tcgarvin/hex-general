// Tab / Shift+Tab through your own units, idle ones first.

/**
 * The unit after (`dir` 1) or before (`dir` -1) `current` in id order, among
 * units with nothing to do this turn; when none is idle (or only `current`
 * is), among all of them. `null` only when there are no units.
 */
export function cycleUnit<U extends { id: number }>(
  units: readonly U[],
  current: number | null,
  dir: 1 | -1,
  busy: (unit: U) => boolean,
): U | null {
  const idle = units.filter((u) => u.id !== current && !busy(u));
  const pool = [...(idle.length > 0 ? idle : units)].sort((a, b) => a.id - b.id);
  if (pool.length === 0) return null;
  if (current === null) return dir === 1 ? pool[0] : pool[pool.length - 1];
  if (dir === 1) return pool.find((u) => u.id > current) ?? pool[0];
  return [...pool].reverse().find((u) => u.id < current) ?? pool[pool.length - 1];
}
