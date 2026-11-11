import { Navigate } from "react-router";

import { Centered, ErrorBanner, Loading } from "@/components/ui";

import { useActiveWorld } from "./useActiveWorld";
import { WorldsScreen } from "./WorldList";

/**
 * Post-login dispatcher (journeys J1/J4). Asks the API for the caller's active
 * world; if present, redirects straight into it, otherwise shows the start screen
 * (the list of running worlds to join). `/worlds` shows the same list without the
 * redirect.
 */
export function Dispatcher() {
  const active = useActiveWorld();

  if (active.status === "loading") {
    return <Loading fill label="Finding your game…" />;
  }
  if (active.status === "error") {
    return (
      <Centered fill>
        <ErrorBanner message={active.message} />
      </Centered>
    );
  }
  if (active.world !== null) {
    return <Navigate to={`/world/${active.world.game_id}`} replace />;
  }
  return <WorldsScreen seat={null} />;
}
