/**
 * Base URL of the Cloud Run API.
 *
 * Defaults to the deployed service so the production frontend works without
 * extra configuration. Override with NEXT_PUBLIC_API_URL for local dev
 * (e.g. http://localhost:8080) or a different deployment. NEXT_PUBLIC_ vars are
 * inlined into the client bundle at build time. The bundler inlines an unset
 * NEXT_PUBLIC_ var as an empty string, so use `||` (not `??`) to fall back to
 * the default for both the unset and empty cases.
 */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "https://hex-api-qfzj5tg56a-uc.a.run.app";
