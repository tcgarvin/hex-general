import type { components } from "./schema";

// Convenience aliases for the API's response/request models (generated from the
// FastAPI OpenAPI schema — the single source of truth). Import these instead of
// reaching into `components["schemas"][…]` throughout the app.
export type MeView = components["schemas"]["MeView"];
export type DevProfile = components["schemas"]["DevProfile"];
type WorldReplay = components["schemas"]["WorldReplay"];

// One entry of a replay's event log: the engine's `Event` discriminated union
// (api/app/engine/events.py), tagged on `kind`. Narrow on `kind` to access the
// per-event fields.
export type ReplayEvent = WorldReplay["events"][number];

// The admin screen (`GET /dev/worlds` and `POST /dev/worlds`).
export type AdminWorld = components["schemas"]["AdminWorld"];
export type AdminWorldList = components["schemas"]["AdminWorldList"];
export type ClockRunView = components["schemas"]["ClockRunView"];
