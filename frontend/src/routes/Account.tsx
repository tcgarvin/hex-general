import { useEffect, useState } from "react";
import { Link } from "react-router";

import { useAuth } from "@/auth/useAuth";
import { Centered, ErrorBanner, Loading } from "@/components/ui";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { MeView } from "@/lib/api/types";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; me: MeView };

/**
 * Account screen (journey J4 extension). Shows the caller's identity (email and
 * dev role, from `GET /me`).
 */
export function Account() {
  const { user } = useAuth();
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    api
      .GET("/me")
      .then((me) => {
        if (cancelled) {
          return;
        }
        if (me.error) {
          setState({ status: "error", message: describeApiError(me.error) });
        } else {
          setState({ status: "ready", me: me.data });
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ status: "error", message: describeApiError(err) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") {
    return <Loading fill label="Loading your account…" />;
  }
  if (state.status === "error") {
    return (
      <Centered fill>
        <ErrorBanner message={state.message} />
        <Link to="/" className="text-sm text-indigo-400 hover:underline">
          ← Back
        </Link>
      </Centered>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-6">
      <header>
        <h1 className="text-lg font-semibold">Account</h1>
        <p className="text-sm text-zinc-400">
          {user?.email ?? "Signed in"}
          {state.me.is_dev && (
            <span className="ml-2 rounded bg-indigo-950 px-1.5 py-0.5 text-xs text-indigo-300">
              dev
            </span>
          )}
        </p>
      </header>
    </div>
  );
}
