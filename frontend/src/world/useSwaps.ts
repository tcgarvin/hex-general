// Which own units are halves of a relief swap under the plan (shared by the
// desktop shell and the phone layout).

import { useMemo } from "react";

import type { SwapInfo } from "./labels";
import { hostileZoc, swapPartner, swapRole } from "./relief";
import type { WorldView } from "./useWorld";
import type { WorldPlanning } from "./useWorldPlan";

/** The swap each own unit is half of under the plan, by unit id. */
export function useSwaps(view: WorldView, planning: WorldPlanning): ReadonlyMap<number, SwapInfo> {
  const { units } = view.state;
  const slot = view.your_slot;
  const orders = planning.plan.orders;
  return useMemo(() => {
    const own = units.filter((u) => u.owner === slot);
    const zoc = hostileZoc(units, slot);
    const swaps = new Map<number, SwapInfo>();
    for (const u of own) {
      const partner = swapPartner(u, orders, own);
      if (partner !== null) swaps.set(u.id, { partner, role: swapRole(u, partner, zoc) });
    }
    return swaps;
  }, [units, slot, orders]);
}
