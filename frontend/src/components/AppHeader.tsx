import { Link } from "react-router";

import { useAuth } from "@/auth/useAuth";

import { AccountMenu } from "./AccountMenu";

/**
 * Thin site header for the standard (non-game) pages: brand on the left, account
 * menu on the right. The game screen has its own status-heavy header that hosts
 * the same `AccountMenu`, so it does not use this one.
 *
 * The wordmark goes to `homeTo` (the world list by default, which unlike `/`
 * does not bounce a seated player into their world). On a public page with no
 * session it offers a "Sign in" link in place of the account menu.
 *
 * Field-HQ chrome (docs/UI-STYLE.md §4): a dark wood strip, the same kit
 * texture as the Pixi dock, with a brass seam along the map-facing edge.
 */
export function AppHeader({ homeTo = "/worlds" }: { homeTo?: string }) {
  const { session } = useAuth();
  return (
    <header className="fhq-wood flex h-12 shrink-0 items-center gap-5 border-b-2 border-brass-600/70 px-4 text-sm">
      <Link to={homeTo} className="font-display text-base uppercase tracking-wide text-ivory-100">
        hex-general
      </Link>
      <div className="ml-auto">
        {session === null ? (
          <Link
            to="/login"
            className="rounded-sm border border-brass-600/50 bg-ink-900/30 px-3 py-1.5 font-label text-sm font-medium uppercase tracking-wider text-ivory-100 hover:bg-ink-900/60"
          >
            Sign in
          </Link>
        ) : (
          <AccountMenu />
        )}
      </div>
    </header>
  );
}
