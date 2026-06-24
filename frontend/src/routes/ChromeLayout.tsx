import { Outlet } from "react-router";

import { AppHeader } from "@/components/AppHeader";

/**
 * Shared chrome for the standard authenticated pages (dispatcher, account, join):
 * the site header above the routed page. The game screen is full-bleed with its
 * own header, so it sits outside this layout. Pages rendered here fill the column
 * below the header — use `<Centered fill>` / `<Loading fill>` for centered states.
 */
export function ChromeLayout() {
  return (
    <div className="flex min-h-dvh flex-col bg-zinc-950 text-zinc-100">
      <AppHeader />
      <main className="flex flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  );
}
