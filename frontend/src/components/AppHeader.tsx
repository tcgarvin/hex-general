import { Link } from "react-router";

import { AccountMenu } from "./AccountMenu";

/**
 * Thin site header for the standard (non-game) pages: brand on the left, account
 * menu on the right. The game screen has its own status-heavy header (GameHeader)
 * that hosts the same `AccountMenu`, so it does not use this one.
 *
 * Field-HQ chrome (docs/UI-STYLE.md §4): a dark wood strip — the same kit
 * texture as the Pixi dock — with a brass seam along the map-facing edge.
 */
export function AppHeader() {
  return (
    <header className="fhq-wood flex h-12 shrink-0 items-center gap-5 border-b-2 border-brass-600/70 px-4 text-sm">
      <Link
        to="/"
        className="font-display text-base uppercase tracking-wide text-ivory-100"
      >
        hex-general
      </Link>
      <div className="ml-auto">
        <AccountMenu />
      </div>
    </header>
  );
}
