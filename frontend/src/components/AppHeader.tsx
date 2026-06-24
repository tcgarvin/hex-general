import { Link } from "react-router";

import { AccountMenu } from "./AccountMenu";

/**
 * Thin site header for the standard (non-game) pages: brand on the left, account
 * menu on the right. The game screen has its own status-heavy header (GameHeader)
 * that hosts the same `AccountMenu`, so it does not use this one.
 */
export function AppHeader() {
  return (
    <header className="flex h-12 shrink-0 items-center gap-5 border-b border-zinc-800 bg-zinc-950 px-4 text-sm">
      <Link to="/" className="font-semibold tracking-tight text-zinc-100">
        hex-general
      </Link>
      <div className="ml-auto">
        <AccountMenu />
      </div>
    </header>
  );
}
