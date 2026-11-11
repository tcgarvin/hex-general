import { useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";

export type ActiveWorld = components["schemas"]["ActiveWorld"];

/**
 * The caller's seat, from `GET /worlds/active`. A ready `world` of `null` has the
 * API's one meaning: the caller holds no slot in any un-finished world.
 */
export type ActiveWorldState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; world: ActiveWorld | null };

/** Fetch the caller's active world once on mount. */
export function useActiveWorld(): ActiveWorldState {
  const [state, setState] = useState<ActiveWorldState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    api
      .GET("/worlds/active")
      .then(({ data, error }) => {
        if (cancelled) return;
        if (error) setState({ status: "error", message: describeApiError(error) });
        else setState({ status: "ready", world: data.active_world });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ status: "error", message: describeApiError(err) });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
