import { createContext } from "react";

import type { Session, User } from "@supabase/supabase-js";

/** The authenticated session state exposed to the app via `useAuth`. */
export interface AuthState {
  /** The current Supabase session, or null when signed out. */
  session: Session | null;
  /** Convenience accessor for `session.user`. */
  user: User | null;
  /** True until the initial session lookup resolves (avoids a login flash). */
  loading: boolean;
  /** Begin the Google OAuth flow; returns to `redirectPath` after sign-in. */
  signInWithGoogle: (redirectPath?: string) => Promise<void>;
  /**
   * Sign in with email + password against local Auth (dev playtesting only —
   * the deployed app uses Google SSO). See docs/LOCAL_DEV.md.
   */
  signInWithPassword: (email: string, password: string) => Promise<void>;
  /** Sign out and clear the local session. */
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<AuthState | null>(null);
