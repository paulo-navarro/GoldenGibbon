// ── API Client ───────────────────────────────────────────────────────────────
// Thin fetch wrapper used by React Query hooks.  All URLs are relative
// (`/api/...`) so the Vite dev proxy and nginx prod proxy handle routing.

/** Error thrown when the API returns a non-2xx status. */
export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  readonly body: unknown;

  constructor(status: number, statusText: string, body: unknown) {
    super(`API ${status}: ${statusText}`);
    this.name = 'ApiError';
    this.status = status;
    this.statusText = statusText;
    this.body = body;
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

/**
 * Send a POST request with a JSON body.
 */
export async function postApi<T>(
  url: string,
  body: Record<string, unknown>,
): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    let data: unknown;
    try {
      data = await res.json();
    } catch {
      data = await res.text();
    }
    throw new ApiError(res.status, res.statusText, data);
  }

  return (await res.json()) as T;
}

/**
 * Send a DELETE request.
 */
export async function deleteApi<T>(url: string): Promise<T> {
  const res = await fetch(url, { method: 'DELETE' });

  if (!res.ok) {
    let data: unknown;
    try {
      data = await res.json();
    } catch {
      data = await res.text();
    }
    throw new ApiError(res.status, res.statusText, data);
  }

  return (await res.json()) as T;
}

/**
 * Send a PATCH request with a JSON body.
 */
export async function patchApi<T>(
  url: string,
  body: Record<string, unknown>,
): Promise<T> {
  const res = await fetch(url, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    let data: unknown;
    try {
      data = await res.json();
    } catch {
      data = await res.text();
    }
    throw new ApiError(res.status, res.statusText, data);
  }

  return (await res.json()) as T;
}
