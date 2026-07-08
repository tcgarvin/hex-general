import { useEffect, useState } from "react";
import { Link } from "react-router";

import { useAuth } from "@/auth/useAuth";
import {
  clearImpersonatedUserId,
  getImpersonatedUserId,
  IMPERSONATION_CHANGED,
  setImpersonatedUserId,
} from "@/dev/impersonation";
import { api } from "@/lib/api/client";
import type { DevProfile } from "@/lib/api/types";

/**
 * Account dropdown shown in every page header. The button is labelled with the
 * signed-in email; the menu links to the account screen and signs out.
 *
 * For devs (server-decided via `GET /me`, which reports the *real* account so the
 * control stays visible even while impersonating) it also embeds the act-as-user
 * switcher for solo two-sided playtesting (journey J6): the dev picks who to be
 * from their own account plus the `@hex.local` test accounts (`GET /dev/profiles`),
 * and the choice makes every API request carry `X-Dev-Profile` (clearing it when
 * acting as oneself). Switching identity reloads to the dispatcher so the chosen
 * user's active game is re-resolved cleanly rather than leaving stale state.
 */
export function AccountMenu() {
  const { user, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [isDev, setIsDev] = useState(false);
  const [profiles, setProfiles] = useState<DevProfile[]>([]);
  const [impersonating, setImpersonating] = useState(getImpersonatedUserId());

  useEffect(() => {
    let cancelled = false;
    api.GET("/me").then(({ data, error }) => {
      if (!cancelled && !error && data) {
        setIsDev(data.is_dev);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

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
    impersonating !== null && !profiles.some((p) => p.user_id === impersonating)
      ? impersonating
      : null;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex max-w-[12rem] items-center gap-1.5 rounded-sm border border-brass-600/50 bg-ink-900/30 px-3 py-1.5 font-label text-sm font-medium text-ivory-100 hover:bg-ink-900/60"
      >
        <span className="truncate">{user?.email ?? "Account"}</span>
        <span className="text-brass-400">▾</span>
      </button>
      {open && (
        <>
          <button
            className="fixed inset-0 z-10 cursor-default"
            aria-hidden
            tabIndex={-1}
            onClick={() => setOpen(false)}
          />
          <div className="fhq-wood-deep absolute right-0 z-20 mt-1 flex w-64 flex-col rounded-sm border border-brass-600/50 py-1 text-sm shadow-lg shadow-black/50">
            <div className="truncate px-3 py-1.5 text-xs text-ivory-400">
              {user?.email ?? "Signed in"}
              {isDev && (
                <span className="ml-2 rounded-sm border border-brass-600/50 px-1.5 py-0.5 font-label uppercase text-brass-400">
                  dev
                </span>
              )}
            </div>

            <Link
              to="/account"
              className="px-3 py-1.5 text-left text-ivory-100 hover:bg-ink-900/60"
              onClick={() => setOpen(false)}
            >
              Account
            </Link>

            {isDev && profiles.length > 0 && (
              <div className="mt-1 border-t border-wood-500/40 pt-1">
                <div className="px-3 py-1 font-label text-[10px] font-semibold uppercase tracking-wider text-warn">
                  Act as user
                </div>
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
                        "flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left transition-colors " +
                        (active
                          ? "text-warn"
                          : "text-ivory-100 hover:bg-ink-900/60")
                      }
                    >
                      <span className="truncate">
                        {profile.email || profile.user_id}
                        {profile.is_self ? " (you)" : ""}
                      </span>
                      {active && <span className="text-warn">●</span>}
                    </button>
                  );
                })}
                {offListId !== null && (
                  <button
                    onClick={() => {
                      clearImpersonatedUserId();
                      window.location.assign("/");
                    }}
                    className="w-full truncate px-3 py-1.5 text-left text-ivory-400 hover:bg-ink-900/60"
                  >
                    Stop impersonating {offListId}
                  </button>
                )}
              </div>
            )}

            <div className="mt-1 border-t border-wood-500/40 pt-1">
              <button
                className="w-full px-3 py-1.5 text-left text-ivory-100 hover:bg-ink-900/60"
                onClick={() => {
                  setOpen(false);
                  void signOut();
                }}
              >
                Sign out
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
