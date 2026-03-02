// ── API Client ───────────────────────────────────────────────────────────────
// Thin fetch wrapper used by React Query hooks.  All URLs are relative
// (`/api/...`) so the Vite dev proxy and nginx prod proxy handle routing.

/** Error thrown when the API returns a non-2xx status. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    public body: unknown,
  ) {
    super(`API ${status}: ${statusText}`);
    this.name = 'ApiError';
  }
}

/**
 * Fetch JSON from the backend API.
 *
 * @param url   Relative URL starting with `/api/...`
 * @param params  Optional query parameters (falsy values are stripped).
 */
export async function fetchApi<T>(
  url: string,
  params?: Record<string, string | number | undefined | null>,
): Promise<T> {
  const target = new URL(url, window.location.origin);

  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value != null && value !== '') {
        target.searchParams.set(key, String(value));
      }
    }
  }

  const res = await fetch(target.toString());

  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    throw new ApiError(res.status, res.statusText, body);
  }

  return (await res.json()) as T;
}
