// Pure beat grouping (UI-SPEC §13.1): partition the engine's flat event log
// into ordered beats so the replay controller can step through them one at a
// time. Beats map to the six engine phases; empty beats are omitted.

export type BeatPhase =
  | "barrage"
  | "pulse"
  | "capture"
  | "repair"
  | "economy"
  | "recruitment";

export interface AnyEvent {
  kind: string;
  [key: string]: unknown;
}

export interface Beat {
  phase: BeatPhase;
  /** 1-indexed pulse number; set only when phase === "pulse". */
  pulse?: number;
  /** Short display label: "BARRAGE", "PULSE 1", "CAPTURE", etc. */
  label: string;
  events: AnyEvent[];
}

const PULSE_KINDS = new Set([
  "unit_moved",
  "move_blocked",
  "traffic_jam",
  "combat_resolved",
]);

/**
 * Group the raw engine event log into ordered beats (UI-SPEC §13.1). Empty
 * beats are omitted — a turn with no barrage produces no Barrage beat, etc.
 */
export function groupBeats(rawEvents: { [key: string]: unknown }[]): Beat[] {
  const events = rawEvents as AnyEvent[];
  const beats: Beat[] = [];

  // Phase 1: Barrage
  const barrageEvents = events.filter((e) => e.kind === "barrage_resolved");
  if (barrageEvents.length > 0) {
    beats.push({ phase: "barrage", label: "BARRAGE", events: barrageEvents });
  }

  // Phase 2: Movement pulses — group by pulse number, emit in ascending order.
  const pulseMap = new Map<number, AnyEvent[]>();
  for (const e of events) {
    if (!PULSE_KINDS.has(e.kind)) continue;
    const pulse = (e as unknown as { pulse: number }).pulse;
    let bucket = pulseMap.get(pulse);
    if (bucket === undefined) {
      bucket = [];
      pulseMap.set(pulse, bucket);
    }
    bucket.push(e);
  }
  for (const pulse of Array.from(pulseMap.keys()).sort((a, b) => a - b)) {
    beats.push({
      phase: "pulse",
      pulse,
      label: `PULSE ${pulse}`,
      events: pulseMap.get(pulse)!,
    });
  }

  // Phase 3: Capture
  const captureEvents = events.filter((e) => e.kind === "settlement_captured");
  if (captureEvents.length > 0) {
    beats.push({ phase: "capture", label: "CAPTURE", events: captureEvents });
  }

  // Phase 4: Repair
  const repairEvents = events.filter((e) => e.kind === "unit_repaired");
  if (repairEvents.length > 0) {
    beats.push({ phase: "repair", label: "REPAIR", events: repairEvents });
  }

  // Phase 5: Economy
  const economyEvents = events.filter((e) => e.kind === "economy_resolved");
  if (economyEvents.length > 0) {
    beats.push({ phase: "economy", label: "ECONOMY", events: economyEvents });
  }

  // Phase 6: Recruitment
  const recruitEvents = events.filter((e) => e.kind === "unit_recruited");
  if (recruitEvents.length > 0) {
    beats.push({
      phase: "recruitment",
      label: "RECRUITMENT",
      events: recruitEvents,
    });
  }

  return beats;
}

/** Human-readable label for the current beat, with "of N" context for pulses. */
export function beatDisplayLabel(beats: Beat[], currentBeat: number): string {
  if (currentBeat < 0) return "PRE-TURN";
  const beat = beats[currentBeat];
  if (beat === undefined) return "—";
  if (beat.phase === "pulse") {
    const totalPulses = beats.filter((b) => b.phase === "pulse").length;
    return `${beat.label} of ${totalPulses}`;
  }
  return beat.label;
}
