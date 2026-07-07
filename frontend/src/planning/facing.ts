// Client-side mirror of the engine's cosmetic facing rules (ROADMAP Phase 8
// U0/U1; `default_facing` and `_complete_move` in the api engine). The server is
// authoritative — `Unit.facing` arrives in every view/state payload. These
// helpers exist only where the frontend renders units the server hasn't stamped
// yet: replay beat reconstruction (`applyBeats`) and the planning recruit ghost.

import { axialDistance } from "@/board/hex";
import type { components } from "@/lib/api/schema";

type Axial = components["schemas"]["Axial"];
type Facing = components["schemas"]["Facing"];
type Unit = components["schemas"]["Unit"];

/** Mirror of the engine's `DEFAULT_FACING` (models.py). */
export const DEFAULT_FACING: Facing = "right";

/**
 * Facing after a completed step `origin` → `dest`: the board is flat-top, so
 * the step's net `q` sign decides — rightward faces `right`, leftward `left`,
 * and a purely vertical step keeps `prior`.
 */
export function facingAfterStep(prior: Facing, origin: Axial, dest: Axial): Facing {
  if (dest.q > origin.q) return "right";
  if (dest.q < origin.q) return "left";
  return prior;
}

/**
 * Facing for a freshly placed unit: face the nearest enemy (least hex distance,
 * ties broken by lowest unit id); no enemies or a same-column enemy falls back
 * to {@link DEFAULT_FACING}. Mirror of the engine's `default_facing`.
 */
export function defaultFacing(coord: Axial, owner: string, units: readonly Unit[]): Facing {
  let nearest: Unit | null = null;
  let nearestDistance = Infinity;
  for (const u of units) {
    if (u.owner === owner) continue;
    const d = axialDistance(coord, u.coord);
    if (d < nearestDistance || (d === nearestDistance && nearest !== null && u.id < nearest.id)) {
      nearest = u;
      nearestDistance = d;
    }
  }
  if (nearest === null) return DEFAULT_FACING;
  if (nearest.coord.q > coord.q) return "right";
  if (nearest.coord.q < coord.q) return "left";
  return DEFAULT_FACING;
}
