import { useContext } from "react";

import { AuthContext, type AuthState } from "./AuthContext";

/** Access the authenticated session. Throws if used outside `<AuthProvider>`. */
export function useAuth(): AuthState {
  const state = useContext(AuthContext);
  if (state === null) {
    throw new Error("useAuth must be used within <AuthProvider>");
  }
  return state;
}
