import { Link } from "react-router";

import emblemUrl from "@/ui-art/emblem/emblem-1.webp";

/**
 * The brand emblem (emblem-1, the winged hex crest; ratified 2026-11-11, provenance
 * in art/codex/ui/APPLIED.md). Decorative: the wordmark beside or below it names
 * the game. Drawn by height; the art is 240x159.
 */
export function Emblem({ className = "" }: { className?: string }) {
  return <img src={emblemUrl} alt="" draggable={false} className={`w-auto select-none ${className}`} />;
}

/** The header's emblem and HEX-GENERAL wordmark, linking home. Every page header uses it. */
export function HeaderBrand({ to, title }: { to: string; title?: string }) {
  return (
    <Link
      to={to}
      title={title}
      className="flex shrink-0 items-center gap-2 whitespace-nowrap font-display text-base uppercase tracking-wide text-ivory-100"
    >
      <Emblem className="h-7 drop-shadow-[0_1px_1px_rgb(0_0_0/0.5)]" />
      hex-general
    </Link>
  );
}
