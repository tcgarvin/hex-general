import { Outlet } from "react-router";

import { AppHeader } from "@/components/AppHeader";

/**
 * Shared chrome for the standard authenticated pages (dispatcher, world list,
 * account): the site header above the routed page, on the same dark ground the
 * world screen uses. The game screen is full-bleed with its own header, so it
 * sits outside this layout. Pages rendered here fill the column below the header;
 * use `<Centered fill>` / `<Loading fill>` for centered states.
 */
export function ChromeLayout() {
  return (
    <div className="flex min-h-dvh flex-col bg-[#1a1d22] text-ivory-100">
      <AppHeader />
      <main className="flex flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  );
}
