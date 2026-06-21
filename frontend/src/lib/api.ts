/**
 * Base URL of the Cloud Run API.
 *
 * Defaults to the deployed service so the production frontend works without
 * extra configuration. Override with NEXT_PUBLIC_API_URL for local dev
 * (e.g. http://localhost:8080) or a different deployment. NEXT_PUBLIC_ vars are
 * inlined into the client bundle at build time.
 */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "https://hex-api-qfzj5tg56a-uc.a.run.app";
