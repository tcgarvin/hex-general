import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";

import { Button, ButtonLink, Card, ErrorBanner } from "@/components/ui";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { worldName, worldTag } from "@/lib/worldName";

import type { ActiveWorld } from "./useActiveWorld";

type WorldSummary = components["schemas"]["WorldSummary"];

/**
 * The world list page, shared by the start screen (`/`, never seated: a seated
 * player is redirected into their world) and `/worlds` (anyone, seated or not).
 * Key art can go behind it through `.fhq-backdrop` (index.css).
 */
export function WorldsScreen({ seat }: { seat: ActiveWorld | null }) {
  return (
    <div className="fhq-backdrop flex flex-1 flex-col items-center px-4 py-10">
      <Card className="max-w-xl">
        <h1 className="font-display text-2xl uppercase tracking-wide">
          {seat === null ? "Join a world" : "Worlds"}
        </h1>
        <p className="mt-1 text-sm text-wood-700">
          {seat === null
            ? "Pick a running world to take its first open slot."
            : "The running worlds. You play in one world at a time."}
        </p>
        <WorldList seat={seat} />
        <div className="mt-5 border-t border-wood-500/40 pt-3 text-sm">
          <Link to="/account" className="font-label uppercase tracking-wider text-wood-700 hover:text-ink-900">
            Account
          </Link>
        </div>
      </Card>
    </div>
  );
}

/**
 * The running worlds (W6). Unseated, each row joins that world's first open slot.
 * Seated, the player's own row opens it and the others cannot be joined: the API
 * allows one active world per player and answers a second join with a 409.
 */
function WorldList({ seat }: { seat: ActiveWorld | null }) {
  const navigate = useNavigate();
  const [worlds, setWorlds] = useState<WorldSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [joining, setJoining] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .GET("/worlds")
      .then(({ data, error: apiError }) => {
        if (cancelled) return;
        if (apiError) setError(describeApiError(apiError));
        else setWorlds(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(describeApiError(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function join(gameId: string) {
    setJoining(gameId);
    setError(null);
    const { error: apiError } = await api.POST("/worlds/{game_id}/join", {
      params: { path: { game_id: gameId } },
    });
    if (apiError) {
      setError(describeApiError(apiError));
      setJoining(null);
      return;
    }
    navigate(`/world/${gameId}`);
  }

  return (
    <div className="mt-4 flex flex-col gap-3">
      {seat !== null && <SeatNote seat={seat} />}
      {worlds === null && error === null && (
        <p className="font-label text-sm uppercase tracking-wider text-wood-500">Loading worlds…</p>
      )}
      {worlds !== null && worlds.length === 0 && (
        <p className="text-sm">No world is open yet. Check back soon.</p>
      )}
      {worlds !== null && worlds.length > 0 && (
        <ul className="flex flex-col divide-y divide-wood-500/40 border-y border-wood-500/40">
          {worlds.map((w) => (
            <WorldRow
              key={w.game_id}
              world={w}
              seat={seat}
              joining={joining}
              onJoin={() => void join(w.game_id)}
            />
          ))}
        </ul>
      )}
      {error !== null && <ErrorBanner message={error} />}
    </div>
  );
}

/** Where a seated player is, and why the other worlds are closed to them. */
function SeatNote({ seat }: { seat: ActiveWorld }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-brass-600 bg-parchment-400/25 px-3 py-2 text-sm">
      <p>
        You hold a seat in <strong>{worldName(seat.game_id)}</strong>{" "}
        <span className="font-data text-wood-500">{worldTag(seat.game_id)}</span>. You can
        play in one world at a time, so other worlds can't be joined.
      </p>
      <ButtonLink to={`/world/${seat.game_id}`}>Open</ButtonLink>
    </div>
  );
}

/** One world: codename, size, turn, who is in it and its clock. */
function WorldRow({
  world,
  seat,
  joining,
  onJoin,
}: {
  world: WorldSummary;
  seat: ActiveWorld | null;
  joining: string | null;
  onJoin: () => void;
}) {
  const isHere = seat !== null && seat.game_id === world.game_id;
  const full = world.players >= world.slots;
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-display text-base uppercase tracking-wide">{worldName(world.game_id)}</span>
          <span className="font-data text-xs text-wood-500" title={world.game_id}>
            {worldTag(world.game_id)}
          </span>
          {isHere && (
            <span className="rounded-sm border border-brass-600 px-1.5 font-label text-[11px] font-semibold uppercase tracking-wider text-canvas-600">
              You are here
            </span>
          )}
        </div>
        <p className="mt-0.5 text-sm">
          Turn <Num>{world.turn}</Num> · <Num>{world.players}</Num> {plural(world.players, "player")} (
          <Num>{world.humans}</Num> {plural(world.humans, "human")}) · <Num>{world.slots}</Num> slots
        </p>
        <p className="text-xs text-wood-700">
          {world.clock === "manual" ? "Manual clock" : "Twice daily"}
          {world.next_tick_at !== null && (
            <>
              {" · next resolve "}
              <span className="font-data">{formatResolveTime(world.next_tick_at)}</span>
            </>
          )}
        </p>
      </div>
      {isHere ? (
        <ButtonLink to={`/world/${world.game_id}`}>Open</ButtonLink>
      ) : (
        <Button
          onClick={onJoin}
          disabled={seat !== null || full || joining !== null}
          title={seat !== null ? "You already hold a seat in another world" : full ? "Every slot is taken" : undefined}
        >
          {joining === world.game_id ? "Joining…" : full ? "Full" : "Join"}
        </Button>
      )}
    </li>
  );
}

/** A figure in the typewriter data face. */
function Num({ children }: { children: number }) {
  return <span className="font-data">{children}</span>;
}

function plural(count: number, noun: string): string {
  return count === 1 ? noun : `${noun}s`;
}

/** The next resolve in the viewer's local time, e.g. "Tue, Nov 11, 2:00 PM". */
function formatResolveTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
