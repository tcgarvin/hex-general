import { useState } from "react";
import { Navigate, useLocation } from "react-router";

import { useAuth } from "@/auth/useAuth";
import { Button, Centered, ErrorBanner } from "@/components/ui";
import { isLocalSupabase } from "@/lib/supabase";

/**
 * Sign-in gate (journeys J1/J2). Already-signed-in users skip straight to where
 * they were headed (`from`, set by ProtectedRoute) or the dispatcher.
 *
 * The deployed app uses Google SSO. Against a local `supabase start` stack
 * (which has no Google OAuth) it also offers email/password sign-in for the
 * seeded `@hex.local` dev accounts — the laptop playtest entry point
 * (docs/LOCAL_DEV.md). From there the dev-profile switcher drives both sides.
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
    <Centered>
      <h1 className="text-2xl font-semibold">Hex General</h1>
      <p className="max-w-xs text-center text-sm text-zinc-400">
        A turn-based hex wargame. Sign in to start or rejoin a match.
      </p>
      {isLocalSupabase ? (
        <DevPasswordSignIn
          signIn={(email, password) =>
            signInWithPassword(email, password).catch(onError)
          }
        />
      ) : (
        <Button
          onClick={() => {
            signInWithGoogle(from).catch(onError);
          }}
        >
          Sign in with Google
        </Button>
      )}
      {error !== null && <ErrorBanner message={error} />}
    </Centered>
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
    "w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500";

  return (
    <form
      className="flex w-64 flex-col gap-2 border-t border-zinc-800 pt-4"
      onSubmit={(e) => {
        e.preventDefault();
        signIn(email, password);
      }}
    >
      <p className="text-center text-xs text-zinc-500">
        Local dev sign-in (no Google locally)
      </p>
      <input
        className={field}
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="dev1@hex.local"
        autoComplete="username"
      />
      <input
        className={field}
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="password"
        autoComplete="current-password"
      />
      <Button type="submit" variant="ghost">
        Sign in as dev
      </Button>
    </form>
  );
}
