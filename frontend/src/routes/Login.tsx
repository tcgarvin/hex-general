import { useState } from "react";
import { Navigate, useLocation } from "react-router";

import { useAuth } from "@/auth/useAuth";
import { Button, Centered, ErrorBanner } from "@/components/ui";

/**
 * Google SSO gate (journeys J1/J2). Already-signed-in users skip straight to where
 * they were headed (`from`, set by ProtectedRoute) or the dispatcher.
 */
export function Login() {
  const { session, signInWithGoogle } = useAuth();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const [error, setError] = useState<string | null>(null);

  if (session !== null) {
    return <Navigate to={from} replace />;
  }

  return (
    <Centered>
      <h1 className="text-2xl font-semibold">Hex General</h1>
      <p className="max-w-xs text-center text-sm text-zinc-400">
        A turn-based hex wargame. Sign in to start or rejoin a match.
      </p>
      <Button
        onClick={() => {
          signInWithGoogle(from).catch((err: unknown) =>
            setError(err instanceof Error ? err.message : "Sign-in failed"),
          );
        }}
      >
        Sign in with Google
      </Button>
      {error !== null && <ErrorBanner message={error} />}
    </Centered>
  );
}
