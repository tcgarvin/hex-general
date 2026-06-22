import { useEffect, useState } from "react";

import { useAuth } from "@/auth/useAuth";
import { Button } from "@/components/ui";
import { api } from "@/lib/api/client";

import {
  clearImpersonatedUserId,
  getImpersonatedUserId,
  IMPERSONATION_CHANGED,
  setImpersonatedUserId,
} from "./impersonation";

/**
 * Corner overlay for solo two-sided playtesting (journey J6). Visible only to devs
 * (server-decided via `GET /me`, which reports the *real* account so the switcher
 * stays up even while impersonating). Entering a user id makes every API request
 * carry `X-Dev-Profile`, so the dev acts as that user; clearing reverts.
 *
 * Changing identity reloads to the dispatcher so the (now different) user's active
 * game is re-resolved cleanly rather than leaving stale, possibly-forbidden state.
 */
export function DevProfileSwitcher() {
  const { session } = useAuth();
  const [isDev, setIsDev] = useState(false);
  const [impersonating, setImpersonating] = useState(getImpersonatedUserId());
  const [input, setInput] = useState("");

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

  useEffect(() => {
    const handler = () => setImpersonating(getImpersonatedUserId());
    window.addEventListener(IMPERSONATION_CHANGED, handler);
    return () => window.removeEventListener(IMPERSONATION_CHANGED, handler);
  }, []);

  if (session === null || !isDev) {
    return null;
  }

  function actAs(userId: string) {
    setImpersonatedUserId(userId);
    window.location.assign("/"); // re-dispatch as the impersonated user
  }

  function stopImpersonating() {
    clearImpersonatedUserId();
    window.location.assign("/");
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 w-72 rounded-lg border border-amber-700 bg-zinc-950/95 p-3 text-xs shadow-lg">
      <div className="mb-2 font-semibold text-amber-400">Dev: act as user</div>
      {impersonating !== null ? (
        <div className="flex flex-col gap-2">
          <p className="text-zinc-300">
            Acting as <code className="text-amber-300">{impersonating}</code>
          </p>
          <Button variant="ghost" onClick={stopImpersonating}>
            Stop impersonating
          </Button>
        </div>
      ) : (
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const id = input.trim();
            if (id !== "") {
              actAs(id);
            }
          }}
        >
          <input
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1"
            placeholder="user_id to impersonate"
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
          <Button variant="ghost" type="submit">
            Act as this user
          </Button>
        </form>
      )}
    </div>
  );
}
