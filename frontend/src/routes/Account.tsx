import { useEffect, useState } from "react";
import { Link } from "react-router";

import { useAuth } from "@/auth/useAuth";
import { Button, ButtonLink, Card, Centered, ErrorBanner, Loading } from "@/components/ui";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { MeView } from "@/lib/api/types";
import { worldName, worldTag } from "@/lib/worldName";

import { useActiveWorld } from "./useActiveWorld";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; me: MeView };

/**
 * Account screen (journey J4 extension): the caller's identity (email and dev
 * role, from `GET /me`), the world they hold a seat in, and sign-out.
 */
export function Account() {
  const { user, signOut } = useAuth();
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
        <Link to="/worlds" className="text-sm text-brass-400 hover:underline">
          ← Back
        </Link>
      </Centered>
    );
  }

  const term = "font-label text-xs font-semibold uppercase tracking-wider text-wood-700";
  return (
    <div className="flex flex-1 flex-col items-center px-4 py-10">
      <Card className="max-w-md">
        <h1 className="font-display text-2xl uppercase tracking-wide">Account</h1>
        <dl className="mt-4 flex flex-col gap-4">
          <div>
            <dt className={term}>Signed in as</dt>
            <dd className="mt-1 flex flex-wrap items-center gap-2 text-sm">
              <span className="break-all">{user?.email ?? "Signed in"}</span>
              {state.me.is_dev && (
                <span className="rounded-sm border border-wood-500 px-1.5 font-label text-[11px] font-semibold uppercase tracking-wider text-canvas-600">
                  dev
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt className={term}>World</dt>
            <dd className="mt-1">
              <SeatSummary />
            </dd>
          </div>
        </dl>
        <div className="mt-6 border-t border-wood-500/40 pt-4">
          <Button variant="ghost" onClick={() => void signOut()}>
            Sign out
          </Button>
        </div>
      </Card>
    </div>
  );
}

/** The world the caller holds a seat in, with a way into it (or to the list). */
function SeatSummary() {
  const active = useActiveWorld();

  if (active.status === "loading") {
    return <p className="text-sm text-wood-500">Checking…</p>;
  }
  if (active.status === "error") {
    return <ErrorBanner message={active.message} />;
  }
  if (active.world === null) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <span>Not in a world</span>
        <ButtonLink to="/worlds" variant="ghost">
          Find a world
        </ButtonLink>
      </div>
    );
  }
  const { game_id: gameId } = active.world;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
      <span>
        <span className="font-display uppercase tracking-wide">{worldName(gameId)}</span>{" "}
        <span className="font-data text-xs text-wood-500" title={gameId}>
          {worldTag(gameId)}
        </span>
      </span>
      <ButtonLink to={`/world/${gameId}`}>Open</ButtonLink>
    </div>
  );
}
