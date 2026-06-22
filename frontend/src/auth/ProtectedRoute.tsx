import { Navigate, Outlet, useLocation } from "react-router";

import { Loading } from "@/components/ui";

import { useAuth } from "./useAuth";

/**
 * Gate for authenticated routes. While the session resolves, shows a loader; with
 * no session, redirects to `/login`, preserving the intended path in router state
 * so an invite deep link (`/join/:id#invite=…`) survives the login round-trip.
 */
export function ProtectedRoute() {
  const { session, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return <Loading />;
  }
  if (session === null) {
    const from = `${location.pathname}${location.search}${location.hash}`;
    return <Navigate to="/login" replace state={{ from }} />;
  }
  return <Outlet />;
}
