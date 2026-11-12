import { useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { MeView } from "@/lib/api/types";

/** The caller's own account and roles from `GET /me`, as it loads. */
export type MeState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; me: MeView };

/**
 * Load `GET /me` once for the mounting component: the *real* account (dev
 * impersonation is not applied) with its dev and admin roles. The roles only
 * decide what the UI shows; the API checks them again on every call.
 */
export function useMe(): MeState {
  const [state, setState] = useState<MeState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    api
      .GET("/me")
      .then(({ data, error }) => {
        if (cancelled) return;
        if (error) setState({ status: "error", message: describeApiError(error) });
        else setState({ status: "ready", me: data });
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
