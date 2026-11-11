import { useState } from "react";
import { Navigate, useLocation } from "react-router";

import { useAuth } from "@/auth/useAuth";
import { Emblem } from "@/components/Brand";
import { Button, Card, ErrorBanner } from "@/components/ui";
import { isLocalSupabase } from "@/lib/supabase";

/**
 * Sign-in gate (journeys J1/J2). Already-signed-in users skip straight to where
 * they were headed (`from`, set by ProtectedRoute) or the dispatcher.
 *
 * The deployed app uses Google SSO. Against a local `supabase start` stack
 * (which has no Google OAuth) it offers email/password sign-in for the seeded
 * `@hex.local` dev accounts instead: the laptop playtest entry point
 * (docs/LOCAL_DEV.md). From there the dev-profile switcher drives both sides.
 * The page sits on `.fhq-backdrop`, ready for key art (index.css).
 */
export function Login() {
  const { session, signInWithGoogle, signInWithPassword } = useAuth();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const [error, setError] = useState<string | null>(null);

  if (session !== null) {
    return <Navigate to={from} replace />;
  }

  const onError = (err: unknown) =>
    setError(err instanceof Error ? err.message : "Sign-in failed");

  return (
    <div className="fhq-backdrop flex min-h-dvh flex-col items-center justify-center px-4 py-10 text-ivory-100">
      <Card className="flex max-w-sm flex-col gap-5">
        <header className="text-center">
          <Emblem className="mx-auto mb-2 h-16" />
          <h1 className="font-display text-3xl uppercase tracking-wide">Hex General</h1>
          <p className="mt-2 text-sm text-wood-700">
            A turn-based hex wargame. Plan your orders, then every move resolves at once.
          </p>
        </header>
        {isLocalSupabase ? (
          <DevPasswordSignIn
            signIn={(email, password) => signInWithPassword(email, password).catch(onError)}
          />
        ) : (
          <Button
            className="w-full"
            onClick={() => {
              signInWithGoogle(from).catch(onError);
            }}
          >
            Sign in with Google
          </Button>
        )}
        {error !== null && <ErrorBanner message={error} />}
      </Card>
    </div>
  );
}

/**
 * Email/password form for the seeded local dev accounts. Defaults to
 * `dev1@hex.local`; the matching password is the seed's `DEV_PASSWORD`.
 */
function DevPasswordSignIn({
  signIn,
}: {
  signIn: (email: string, password: string) => void;
}) {
  const [email, setEmail] = useState("dev1@hex.local");
  const [password, setPassword] = useState("");

  const field =
    "w-full rounded-sm border border-wood-500 bg-ivory-100 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-900/40 focus:outline-2 focus:outline-brass-600";
  const label = "font-label text-xs font-semibold uppercase tracking-wider text-wood-700";

  return (
    <form
      className="flex flex-col gap-3 rounded-sm border border-dashed border-wood-500 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        signIn(email, password);
      }}
    >
      <p className="font-label text-xs font-semibold uppercase tracking-wider text-canvas-600">
        Local dev only: seeded test accounts (no Google locally)
      </p>
      <label className="flex flex-col gap-1">
        <span className={label}>Email</span>
        <input
          className={field}
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="dev1@hex.local"
          autoComplete="username"
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className={label}>Password</span>
        <input
          className={field}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="password"
          autoComplete="current-password"
        />
      </label>
      {/* normal-case: other agents find this button by its exact text, and
          innerText reports the uppercase transform. */}
      <Button type="submit" className="w-full normal-case! tracking-normal!">
        Sign in as dev
      </Button>
    </form>
  );
}
