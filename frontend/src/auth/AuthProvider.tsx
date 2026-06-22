import { useEffect, useMemo, useState } from "react";

import type { Session } from "@supabase/supabase-js";

import { supabase } from "@/lib/supabase";

import { AuthContext, type AuthState } from "./AuthContext";

/**
 * Holds the Supabase Auth session and exposes it to the tree.
 *
 * On mount it reads the existing session (so a returning user is dispatched
 * straight to their game — journey J4) and subscribes to auth changes, which fire
 * after the Google OAuth redirect completes and on token refresh/sign-out.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });

    const { data: subscription } = supabase.auth.onAuthStateChange(
      (_event, nextSession) => {
        setSession(nextSession);
        setLoading(false);
      },
    );

    return () => subscription.subscription.unsubscribe();
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      session,
      user: session?.user ?? null,
      loading,
      signInWithGoogle: async (redirectPath = "/") => {
        const redirectTo = `${window.location.origin}${redirectPath}`;
        const { error } = await supabase.auth.signInWithOAuth({
          provider: "google",
          options: { redirectTo },
        });
        if (error) {
          throw error;
        }
      },
      signOut: async () => {
        const { error } = await supabase.auth.signOut();
        if (error) {
          throw error;
        }
      },
    }),
    [session, loading],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}
