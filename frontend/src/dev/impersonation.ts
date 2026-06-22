/**
 * Dev impersonation store (roadmap Phase 7).
 *
 * A dev (allowlisted server-side; see `GET /me`) can act as any other user by
 * having every API request carry `X-Dev-Profile: <user_id>`. The chosen user id
 * is persisted in localStorage so it survives reloads, and a custom event lets the
 * switcher UI react to changes without prop-drilling. The API client reads it via
 * `getImpersonatedUserId()`; a non-dev sending the header is rejected 403, so this
 * is harmless for non-devs.
 */
const STORAGE_KEY = "hexgeneral.devProfile";

/** Event fired on the window whenever the impersonated user id changes. */
export const IMPERSONATION_CHANGED = "hexgeneral:impersonation-changed";

/** The user id currently being impersonated, or null when acting as oneself. */
export function getImpersonatedUserId(): string | null {
  return localStorage.getItem(STORAGE_KEY);
}

/** Act as `userId` on every subsequent API request. */
export function setImpersonatedUserId(userId: string): void {
  localStorage.setItem(STORAGE_KEY, userId);
  window.dispatchEvent(new Event(IMPERSONATION_CHANGED));
}

/** Stop impersonating; revert to the real authenticated user. */
export function clearImpersonatedUserId(): void {
  localStorage.removeItem(STORAGE_KEY);
  window.dispatchEvent(new Event(IMPERSONATION_CHANGED));
}
