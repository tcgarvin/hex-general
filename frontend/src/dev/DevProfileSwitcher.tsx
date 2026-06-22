import { useEffect, useState } from "react";

import { useAuth } from "@/auth/useAuth";
import { Button } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { DevProfile } from "@/lib/api/types";

import {
  clearImpersonatedUserId,
  getImpersonatedUserId,
  IMPERSONATION_CHANGED,
  setImpersonatedUserId,
} from "./impersonation";

/**
 * Corner overlay for solo two-sided playtesting (journey J6). Visible only to devs
 * (server-decided via `GET /me`, which reports the *real* account so the switcher
 * stays up even while impersonating). It lists the picks the dev can act as —
 * their own account plus the `@hex.local` test accounts (`GET /dev/profiles`) — so
 * a dev selects who to be from a menu instead of pasting a raw user id. Choosing an
 * account makes every API request carry `X-Dev-Profile`; choosing oneself clears it.
 *
 * Changing identity reloads to the dispatcher so the (now different) user's active
 * game is re-resolved cleanly rather than leaving stale, possibly-forbidden state.
 */
export function DevProfileSwitcher() {
  const { session } = useAuth();
  const [isDev, setIsDev] = useState(false);
  const [profiles, setProfiles] = useState<DevProfile[]>([]);
  const [impersonating, setImpersonating] = useState(getImpersonatedUserId());

  useEffect(() => {
    if (session === null) {
      return;
    }
    let cancelled = false;
    api.GET("/me").then(({ data, error }) => {
      if (!cancelled && !error && data) {
        setIsDev(data.is_dev);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [session]);

  // Load the selectable accounts once we know the caller is a dev (the endpoint
  // 403s for everyone else, so there is no point asking before /me confirms).
  useEffect(() => {
    if (!isDev) {
      return;
    }
    let cancelled = false;
    api.GET("/dev/profiles").then(({ data, error }) => {
      if (!cancelled && !error && data) {
        setProfiles(data);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [isDev]);

  useEffect(() => {
    const handler = () => setImpersonating(getImpersonatedUserId());
    window.addEventListener(IMPERSONATION_CHANGED, handler);
    return () => window.removeEventListener(IMPERSONATION_CHANGED, handler);
  }, []);

  if (session === null || !isDev) {
    return null;
  }

  function actAs(profile: DevProfile) {
    // Acting as oneself means *not* impersonating; clear the header instead of
    // sending X-Dev-Profile for the dev's own id.
    if (profile.is_self) {
      clearImpersonatedUserId();
    } else {
      setImpersonatedUserId(profile.user_id);
    }
    window.location.assign("/"); // re-dispatch as the chosen user
  }

  // An id we are impersonating that is not in the menu (e.g. set before this list
  // existed); surface it so the dev can see and clear it.
  const offListId =
    impersonating !== null &&
    !profiles.some((p) => p.user_id === impersonating)
      ? impersonating
      : null;

  return (
    <div className="fixed bottom-4 right-4 z-50 w-72 rounded-lg border border-amber-700 bg-zinc-950/95 p-3 text-xs shadow-lg">
      <div className="mb-2 font-semibold text-amber-400">Dev: act as user</div>
      <div className="flex flex-col gap-1">
        {profiles.map((profile) => {
          const active = profile.is_self
            ? impersonating === null
            : impersonating === profile.user_id;
          return (
            <button
              key={profile.user_id}
              onClick={() => actAs(profile)}
              disabled={active}
              className={
                "flex items-center justify-between gap-2 rounded border px-2 py-1 text-left transition-colors " +
                (active
                  ? "border-amber-600 bg-amber-950/40 text-amber-200"
                  : "border-zinc-700 text-zinc-200 hover:bg-zinc-800")
              }
            >
              <span className="truncate">
                {profile.email || profile.user_id}
                {profile.is_self ? " (you)" : ""}
              </span>
              {active ? <span className="text-amber-400">●</span> : null}
            </button>
          );
        })}
        {offListId !== null ? (
          <div className="mt-1 flex flex-col gap-1 border-t border-zinc-800 pt-2">
            <p className="text-zinc-400">
              Acting as <code className="text-amber-300">{offListId}</code> (not
              in list)
            </p>
            <Button
              variant="ghost"
              onClick={() => {
                clearImpersonatedUserId();
                window.location.assign("/");
              }}
            >
              Stop impersonating
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
