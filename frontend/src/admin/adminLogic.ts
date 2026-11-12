// Pure helpers for the admin screen (`/admin`): how it words times and the clock
// schedule, how it validates the create-world form, and the short world id an
// admin types to confirm a delete. Kept apart from the page so Vitest covers them.

/** Most slots a world may have: `MAX_WORLD_SLOTS` in api/app/games/schemas.py. */
export const MAX_SLOTS = 400;

/** The highest seed the form accepts (the API takes any integer; this keeps it simple). */
export const MAX_SEED = 2_147_483_647;

export type Clock = "manual" | "twice_daily";

/** The create-world form as typed: raw strings until validated. */
export type CreateForm = {
  slots: string;
  bots: string;
  seed: string;
  clock: Clock;
};

/** The agreed rehearsal world: 60 slots, 30 bots, the scheduled clock. */
export const DEFAULT_FORM: CreateForm = { slots: "60", bots: "30", seed: "1", clock: "twice_daily" };

/** The body for `POST /dev/worlds` once the form is valid. */
export type CreateBody = { slots: number; bots: number; seed: number; clock: Clock };

export type FormCheck =
  | { ok: true; body: CreateBody }
  | { ok: false; errors: Partial<Record<keyof CreateForm, string>> };

function parseWhole(text: string): number | null {
  const trimmed = text.trim();
  return /^\d+$/.test(trimmed) ? Number(trimmed) : null;
}

/**
 * Check the form against the API's request model (`CreateWorldRequest`): slots
 * 1 to 400, bots 0 to slots, a whole-number seed. The API checks again; this
 * only saves a round trip and says which field is wrong.
 */
export function validateCreateForm(form: CreateForm): FormCheck {
  const errors: Partial<Record<keyof CreateForm, string>> = {};
  const slots = parseWhole(form.slots);
  const bots = parseWhole(form.bots);
  const seed = parseWhole(form.seed);
  if (slots === null || slots < 1 || slots > MAX_SLOTS) {
    errors.slots = `Slots must be a whole number from 1 to ${MAX_SLOTS}.`;
  }
  if (bots === null) {
    errors.bots = "Bots must be a whole number, 0 or more.";
  } else if (slots !== null && bots > slots) {
    errors.bots = "Bots cannot exceed slots: each bot holds a slot.";
  }
  if (seed === null || seed > MAX_SEED) {
    errors.seed = `Seed must be a whole number from 0 to ${MAX_SEED}.`;
  }
  if (slots === null || bots === null || seed === null || Object.keys(errors).length > 0) {
    return { ok: false, errors };
  }
  return { ok: true, body: { slots, bots, seed, clock: form.clock } };
}

/** A fresh seed for the "Random" button. */
export function randomSeed(random: () => number = Math.random): string {
  return String(Math.floor(random() * 1_000_000));
}

/** The first eight characters of a world id: shown, and typed to confirm a delete. */
export function shortId(gameId: string): string {
  return gameId.slice(0, 8);
}

/** Whether the typed text confirms deleting ``gameId`` (its short id, any case). */
export function confirmsDelete(typed: string, gameId: string): boolean {
  return typed.trim().toLowerCase() === shortId(gameId).toLowerCase();
}

/**
 * How long until ``at``: "in 5h 12m", "in 3d 4h", "in 12m", "in under a minute",
 * or "due now" once it has passed (the clock has not resolved it yet).
 */
export function untilText(at: Date, now: Date): string {
  const minutes = Math.floor((at.getTime() - now.getTime()) / 60_000);
  if (at.getTime() <= now.getTime()) return "due now";
  if (minutes < 1) return "in under a minute";
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  if (days > 0) return `in ${days}d ${hours}h`;
  if (hours > 0) return `in ${hours}h ${mins}m`;
  return `in ${mins}m`;
}

/** A date and time in the viewer's zone, e.g. "Wed, Nov 11, 1:00 PM". */
export function localTime(at: Date, locale: string | undefined = undefined): string {
  return at.toLocaleString(locale, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** "Scheduled ticks: 01:00 and 13:00 America/New_York" from the API's schedule. */
export function scheduleText(schedule: { zone: string; hours: number[] }): string {
  const times = schedule.hours.map((h) => `${String(h).padStart(2, "0")}:00`);
  const joined =
    times.length <= 1 ? times.join("") : `${times.slice(0, -1).join(", ")} and ${times[times.length - 1]}`;
  return `Scheduled ticks: ${joined} ${schedule.zone}`;
}

/**
 * An API failure for the error strip, verbatim: "HTTP 409: <detail>". A 422's
 * validation list becomes one "field: message" line per entry; a thrown network
 * error keeps its own message.
 */
export function errorText(status: number | null, error: unknown): string {
  const prefix = status === null ? "" : `HTTP ${status}: `;
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return prefix + detail;
    if (Array.isArray(detail)) {
      const lines = detail.map((entry: unknown) => {
        if (entry && typeof entry === "object" && "msg" in entry) {
          const { loc, msg } = entry as { loc?: unknown; msg: unknown };
          const field = Array.isArray(loc) ? loc.filter((part) => part !== "body").join(".") : "";
          return field ? `${field}: ${String(msg)}` : String(msg);
        }
        return JSON.stringify(entry);
      });
      return prefix + lines.join("\n");
    }
    return prefix + JSON.stringify(detail);
  }
  if (error instanceof Error) return prefix + error.message;
  return `${prefix}request failed`;
}
