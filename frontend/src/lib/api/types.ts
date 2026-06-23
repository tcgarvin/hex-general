import type { components } from "./schema";

// Convenience aliases for the API's response/request models (generated from the
// FastAPI OpenAPI schema — the single source of truth). Import these instead of
// reaching into `components["schemas"][…]` throughout the app.
export type GameView = components["schemas"]["GameView"];
export type DispatchView = components["schemas"]["DispatchView"];
export type ActiveGame = components["schemas"]["ActiveGame"];
export type PlayerCredentials = components["schemas"]["PlayerCredentials"];
export type MeView = components["schemas"]["MeView"];
export type DevProfile = components["schemas"]["DevProfile"];
export type SubmitResult = components["schemas"]["SubmitResult"];
export type TurnReplay = components["schemas"]["TurnReplay"];
export type VictoryStatus = components["schemas"]["VictoryStatus"];
export type GameHistoryEntry = components["schemas"]["GameHistoryEntry"];

// PlayerId is a plain string server-side (engine models.py); the v0.1 scenarios
// use these two slots.
export type PlayerId = string;
export const SLOTS: readonly PlayerId[] = ["blue", "red"];

// Mirrors the API's DEFAULT_SCENARIO (schemas.py). openapi-typescript marks the
// `scenario` body field required (it has a default), so create-game must send it;
// when a scenario picker lands this becomes the picker's default.
export const DEFAULT_SCENARIO = "twin_rivers";
