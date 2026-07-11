const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;

if (!apiBaseUrl) {
  throw new Error("NEXT_PUBLIC_API_BASE_URL is required");
}

export const apiUrl = (path: string): string =>
  `${apiBaseUrl.replace(/\/$/, "")}/${path.replace(/^\//, "")}`;

export class ApiRequestError extends Error {
  constructor(message: string, public readonly status?: number) {
    super(message);
    this.name = "ApiRequestError";
  }
}

export async function apiGet<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(apiUrl(path), { ...init, cache: "no-store" });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new ApiRequestError(
      detail?.detail?.user_message || `Request failed: ${response.status}`,
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body: unknown, init: RequestInit = {}): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new ApiRequestError(
      detail?.detail?.user_message || `Request failed: ${response.status}`,
      response.status,
    );
  }
  return response.json() as Promise<T>;
}
