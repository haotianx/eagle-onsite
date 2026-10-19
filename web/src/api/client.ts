import createClient from "openapi-fetch";

import type { paths } from "./schema";

/** Typed client over the engineer API. Same origin: the dev server proxies /api. */
export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "",
  // Looked up per request so tests can stub fetch.
  fetch: (request) => globalThis.fetch(request),
});

/** A non-2xx API response, with FastAPI's `detail` (a string, or validation errors). */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(errorMessage(status, detail));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function errorMessage(status: number, detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: unknown) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : JSON.stringify(d)))
      .join("; ");
  }
  if (detail != null) return JSON.stringify(detail);
  return `Request failed (${status})`;
}

/** Builds an ApiError from a FastAPI error body ({detail}) or any other body. */
export function toApiError(status: number, body: unknown): ApiError {
  const detail = body && typeof body === "object" && "detail" in body ? body.detail : body;
  return new ApiError(status, detail);
}

/** Resolves an openapi-fetch call to its data, or throws an ApiError. */
export async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call;
  if (!response.ok) throw toApiError(response.status, error);
  return data as T;
}
