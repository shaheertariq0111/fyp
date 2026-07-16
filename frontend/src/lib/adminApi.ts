import { apiUrl } from "@/lib/api";

async function adminRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    cache: "no-store",
  });
  if (response.status === 401 && typeof window !== "undefined") {
    window.location.href = "/admin/login";
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail?.detail?.user_message || detail?.detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const adminGet = <T>(path: string): Promise<T> => adminRequest<T>(path);

export const adminPost = <T>(path: string, body: unknown): Promise<T> =>
  adminRequest<T>(path, { method: "POST", body: JSON.stringify(body) });

export const adminPut = <T>(path: string, body: unknown): Promise<T> =>
  adminRequest<T>(path, { method: "PUT", body: JSON.stringify(body) });

export const adminPatch = <T>(path: string, body: unknown = {}): Promise<T> =>
  adminRequest<T>(path, { method: "PATCH", body: JSON.stringify(body) });
