// Line icons for the phone layout's keys that have no UI glyph yet (Hold, Rest,
// Barrage, Clear, Report, More, menu, close, chevrons and a few more).
//
// PLACEHOLDERS until a proper glyph round (docs/UI-STYLE.md §4 "Icons"): the paths
// are copied from the approved phone mock-ups (2026-11-12). Where a glyph exists
// (unit types, resources, army, base, travel, deploy, column, disband), use
// `components/Glyph.tsx` instead. An order key always keeps its word label under
// the icon. Drawn in `currentColor`, 16-unit box.

const PATHS = {
  menu: { d: "M2.5 4.5h11M2.5 8h11M2.5 11.5h11", width: 1.8 },
  close: { d: "M4 4l8 8M12 4l-8 8", width: 1.8 },
  prev: { d: "M10 3 5 8l5 5", width: 2 },
  next: { d: "M6 3l5 5-5 5", width: 2 },
  chevron: { d: "M6 3l5 5-5 5", width: 1.8 },
  home: { d: "M2.5 8 8 3l5.5 5M4.5 6.5V13h7V6.5", width: 1.6 },
  fit: { d: "M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10", width: 1.6 },
  report: { d: "M4 2.5h6l2.5 2.5v8.5h-8.5zM6 7h4.5M6 9.5h4.5M6 12h3", width: 1.3 },
  hold: { d: "M8 2 3 4v4c0 3 2.2 5 5 6 2.8-1 5-3 5-6V4z", width: 1.5 },
  rest: { d: "M2 12V5M2 9.5h12V12M14 9.5V8a2 2 0 0 0-2-2H7v3.5", width: 1.5 },
  more: { d: "M3.5 8h.01M8 8h.01M12.5 8h.01", width: 2.6 },
  clear: { d: "M3 8a5 5 0 1 0 1.6-3.7M3 2.5v3h3", width: 1.8 },
  barrage: { d: "M8 1.5v3.5M8 11v3.5M1.5 8H5M11 8h3.5M11.5 8a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0z", width: 1.5 },
  waypoint: { d: "M8 2.5 13.5 8 8 13.5 2.5 8z", width: 1.5 },
  swap: { d: "M3 5.5h9.5L10 3M13 10.5H3.5L6 13", width: 1.6 },
} as const;

export type IconName = keyof typeof PATHS | "play";

export function Icon({ name, size = 20, className = "" }: { name: IconName; size?: number; className?: string }) {
  if (name === "play") {
    return (
      <svg viewBox="0 0 16 16" width={size} height={size} className={`shrink-0 ${className}`} fill="currentColor" aria-hidden>
        <path d="M5 3.5v9l7-4.5z" />
      </svg>
    );
  }
  const { d, width } = PATHS[name];
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      className={`shrink-0 ${className}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={width}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d={d} />
    </svg>
  );
}
