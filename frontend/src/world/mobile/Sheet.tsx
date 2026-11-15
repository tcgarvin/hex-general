// Building blocks of the phone layout: the bottom sheet, its parchment rows and
// section captions, the brass keys, and the HP bar.

import { Glyph } from "@/components/Glyph";
import type { GlyphName } from "@/ui-art/glyphs";

import { Icon, type IconName } from "./icons";

/**
 * A bottom sheet over the map: a wood tray with a grab bar, a title row with a
 * close key, a parchment body that scrolls inside, and an optional footer key.
 * It stops short of the top bar and some map, so the board stays in view above.
 */
export function Sheet({
  label,
  title,
  glyph,
  subtitle,
  onClose,
  children,
  footer,
}: {
  /** The accessible name of the sheet. */
  label: string;
  title: string;
  glyph?: GlyphName;
  subtitle?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <section
      aria-label={label}
      className="fhq-wood pointer-events-auto absolute inset-x-0 bottom-0 z-30 flex max-h-[calc(100%-88px)] flex-col rounded-t-xl border-t-2 border-brass-600 px-2 pb-[max(10px,env(safe-area-inset-bottom))] pl-[max(8px,env(safe-area-inset-left))] pr-[max(8px,env(safe-area-inset-right))] shadow-[0_-4px_14px_rgba(0,0,0,0.55)]"
    >
      <div className="relative flex h-12 shrink-0 items-center gap-2">
        <span aria-hidden className="absolute left-1/2 top-1.5 h-1 w-10 -translate-x-1/2 rounded-sm bg-ivory-400" />
        {glyph !== undefined && <Glyph name={glyph} size={20} className="ml-1 text-ivory-100" />}
        <h2 className={`${glyph === undefined ? "ml-1" : ""} min-w-0 truncate font-display text-[17px] uppercase tracking-wide text-ivory-100`}>
          {title}
        </h2>
        {subtitle !== undefined && <span className="min-w-0 truncate font-data text-[13px] text-brass-400">{subtitle}</span>}
        <CloseKey onClick={onClose} className="ml-auto" />
      </div>
      <div className="fhq-parchment min-h-0 overflow-y-auto overscroll-contain [touch-action:pan-y]">{children}</div>
      {footer !== undefined && <div className="shrink-0 pt-2.5">{footer}</div>}
    </section>
  );
}

export function CloseKey({ onClick, className = "", label = "Close" }: { onClick: () => void; className?: string; label?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`flex size-11 shrink-0 items-center justify-center text-ivory-100 ${className}`}
    >
      <Icon name="close" />
    </button>
  );
}

/** A section caption inside a sheet ("No orders · 3"). */
export function Caption({ children, action }: { children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex min-h-[34px] items-center gap-2 pr-2">
      <h3 className="px-3 pt-1.5 font-label text-xs font-bold uppercase tracking-widest text-wood-500">{children}</h3>
      {action !== undefined && <div className="ml-auto pt-1">{action}</div>}
    </div>
  );
}

/** A line of a sheet: a name over a small data-face line, with something on the right. */
export function Line({
  icon,
  name,
  sub,
  subTone = "",
  right,
  onClick,
  title,
}: {
  icon?: React.ReactNode;
  name: React.ReactNode;
  sub?: React.ReactNode;
  /** A colour class for the sub line (a warning, an attack). */
  subTone?: string;
  right?: React.ReactNode;
  onClick?: () => void;
  title?: string;
}) {
  const body = (
    <>
      {icon}
      <span className="flex min-w-0 flex-col gap-px">
        <span className="truncate font-label text-[17px] font-semibold leading-[19px]">{name}</span>
        {sub !== undefined && (
          <span className={`font-data text-xs leading-[14px] ${subTone === "" ? "text-wood-500" : subTone}`}>{sub}</span>
        )}
      </span>
      {right !== undefined && <span className="ml-auto flex shrink-0 items-center gap-2">{right}</span>}
    </>
  );
  const box = "flex min-h-[52px] w-full items-center gap-2.5 border-b border-ink-900/20 px-3 py-1 text-left last:border-b-0";
  return onClick !== undefined ? (
    <button type="button" onClick={onClick} title={title} className={`${box} active:bg-parchment-400/40`}>
      {body}
    </button>
  ) : (
    <div className={box} title={title}>
      {body}
    </div>
  );
}

/**
 * An order key in the dock: an icon over its word label (the label always stays).
 * `icon` is a placeholder line icon or a UI glyph.
 */
export function OrderKey({
  icon,
  glyph,
  label,
  active = false,
  onClick,
  title,
}: {
  icon?: IconName;
  glyph?: GlyphName;
  label: string;
  active?: boolean;
  onClick: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      data-active={active}
      aria-pressed={active}
      className="fhq-btn h-[52px] min-w-0 flex-1 flex-col gap-1 px-1 text-xs"
    >
      {glyph !== undefined ? <Glyph name={glyph} size={20} /> : icon !== undefined ? <Icon name={icon} /> : null}
      {label}
    </button>
  );
}

/** HP as a number and a bar, amber at 6 and below, red at 3 and below. */
/** HP as a bar of the HP the unit starts with (`max`: 10, or a militia unit's starting HP). */
export function HpBar({ hp, max = 10 }: { hp: number; max?: number }) {
  const tone = hp * 10 <= max * 3 ? "bg-invalid" : hp * 10 <= max * 6 ? "bg-warn" : "bg-[#5d7f3a]";
  return (
    <span className="flex shrink-0 items-center gap-1.5" title={`${hp} of ${max} HP`}>
      <span className="font-data text-[15px] tabular-nums">{hp}</span>
      <span className="block h-2 w-9 bg-ink-900/20" aria-hidden>
        <span className={`block h-2 ${tone}`} style={{ width: `${(hp * 100) / max}%` }} />
      </span>
    </span>
  );
}
