/**
 * Turn an openapi-fetch error body (or a thrown network error) into a message.
 *
 * FastAPI errors are `{ detail: string }` for HTTPExceptions and
 * `{ detail: [...] }` for 422 validation failures.
 */
export function describeApiError(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
    return JSON.stringify(detail);
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Request failed";
}
