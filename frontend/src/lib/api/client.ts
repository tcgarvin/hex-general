import createClient, { type Middleware } from "openapi-fetch";

import { clearImpersonatedUserId, getImpersonatedUserId } from "@/dev/impersonation";
import { supabase } from "@/lib/supabase";

import type { paths } from "./schema";

/**
 * Base URL of the Cloud Run API.
 *
 * Defaults to the deployed service so the production frontend works without extra
 * configuration. Override with VITE_API_URL for local dev (e.g. http://localhost:8080).
 * Vite inlines an unset VITE_ var as an empty string, so use `||` (not `??`) to fall
 * back for both the unset and empty cases.
 */
export const API_URL =
  import.meta.env.VITE_API_URL || "https://hex-api-qfzj5tg56a-uc.a.run.app";

// The API's detail string when a non-dev sends X-Dev-Profile (games/routes.py).
const IMPERSONATION_FORBIDDEN = "not authorized to impersonate users";

/** Read FastAPI's `{ "detail": ... }` from a response without consuming it. */
async function responseDetail(response: Response): Promise<string> {
  try {
    const body = await response.clone().json();
    return typeof body?.detail === "string" ? body.detail : "";
  } catch {
    return "";
  }
}

/**
 * Attaches identity to every request:
 * - the Supabase access token as a Bearer JWT (auto-refreshed by the client), so
 *   the API can verify the caller;
 * - `X-Dev-Profile` when a dev is impersonating another user (ignored by the API
 *   for non-devs — it 403s, so it is never set unless the switcher is in use).
 *
 * `onResponse` self-heals a stale impersonation: if the API rejects our
 * `X-Dev-Profile` because this account isn't on the dev allowlist, we drop the
 * impersonation and retry as ourselves. Otherwise a leftover `localStorage`
 * profile from a stack where we *were* a dev would 403 every request and wedge
 * the whole app (e.g. on the dashboard's first load).
 */
const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const { data } = await supabase.auth.getSession();
    const token = data.session?.access_token;
    if (token) {
      request.headers.set("Authorization", `Bearer ${token}`);
    }
    const impersonating = getImpersonatedUserId();
    if (impersonating) {
      request.headers.set("X-Dev-Profile", impersonating);
    }
    return request;
  },
  async onResponse({ request, response }) {
    if (response.status !== 403 || !request.headers.has("X-Dev-Profile")) {
      return response;
    }
    if ((await responseDetail(response)) !== IMPERSONATION_FORBIDDEN) {
      return response;
    }
    // We're not a dev on this stack — forget the impersonation so it stops
    // poisoning future requests. Transparently retry bodyless reads as
    // ourselves; other methods just succeed on the user's next action.
    clearImpersonatedUserId();
    if (request.method === "GET" || request.method === "HEAD") {
      const headers = new Headers(request.headers);
      headers.delete("X-Dev-Profile");
      return fetch(new Request(request.url, { method: request.method, headers }));
    }
    return response;
  },
};

/** The single typed API client; paths/types come from the API's OpenAPI schema. */
export const api = createClient<paths>({ baseUrl: API_URL });
api.use(authMiddleware);
