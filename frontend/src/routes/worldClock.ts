// How the world list words a world's clock and its next resolve.

/**
 * "Twice daily · next resolve Wed, Nov 11, 1:00 PM" for a scheduled world; for a
 * manual one, "Next resolve 7:01 AM (driven manually)" when a tick loop has
 * announced one, else "Manual clock: resolves when ticked". The time is in the
 * viewer's zone, with the day only when it isn't today.
 */
export function clockText(
  clock: "manual" | "twice_daily",
  nextTickAt: string | null,
  now: Date,
  locale: string | undefined = undefined,
): string {
  const when = nextTickAt === null ? null : resolveTime(new Date(nextTickAt), now, locale);
  if (clock === "twice_daily") return when === null ? "Twice daily" : `Twice daily · next resolve ${when}`;
  return when === null ? "Manual clock: resolves when ticked" : `Next resolve ${when} (driven manually)`;
}

function resolveTime(at: Date, now: Date, locale: string | undefined): string {
  const sameDay = at.toDateString() === now.toDateString();
  return at.toLocaleString(locale, {
    ...(sameDay ? {} : { weekday: "short", month: "short", day: "numeric" }),
    hour: "numeric",
    minute: "2-digit",
  });
}
