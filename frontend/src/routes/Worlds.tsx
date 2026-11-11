import { Centered, ErrorBanner, Loading } from "@/components/ui";

import { useActiveWorld } from "./useActiveWorld";
import { WorldsScreen } from "./WorldList";

/**
 * The world list at `/worlds`: like the start screen, but it never redirects, so
 * a seated player can see the running worlds and find their own marked there.
 */
export function Worlds() {
  const active = useActiveWorld();

  if (active.status === "loading") {
    return <Loading fill label="Loading worlds…" />;
  }
  if (active.status === "error") {
    return (
      <Centered fill>
        <ErrorBanner message={active.message} />
      </Centered>
    );
  }
  return <WorldsScreen seat={active.world} />;
}
