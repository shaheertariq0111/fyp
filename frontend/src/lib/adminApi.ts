import { apiUrl } from "@/lib/api";

type ParsedAdminError = {
  errorCode: string;
  userMessage: string;
};

export class AdminApiError extends Error {
  readonly status: number;
  readonly errorCode: string;
  readonly userMessage: string;

  constructor(status: number, errorCode: string, userMessage: string) {
    super(userMessage);
    this.name = "AdminApiError";
    this.status = status;
    this.errorCode = errorCode;
    this.userMessage = userMessage;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeErrorCode(value: unknown): string | null {
  if (
    typeof value !== "string"
    || !/^[A-Z][A-Z0-9_]{0,127}$/.test(value)
  ) {
    return null;
  }
  return value;
}

function safeUserMessage(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const message = value.trim();
  if (
    !message
    || message.length > 500
    || /[<>]/.test(message)
    || /(?:traceback|stack trace|arn:aws|exception\b)/i.test(message)
    || /[A-Za-z0-9_-]{24,}\.[A-Fa-f0-9]{32,}/.test(message)
  ) {
    return null;
  }
  return message;
}

function fallbackError(status: number): ParsedAdminError {
  if (status === 401) {
    return {
      errorCode: "ADMIN_AUTHENTICATION_REQUIRED",
      userMessage: "Administrator authentication is required.",
    };
  }
  if (status >= 400 && status < 500) {
    return {
      errorCode: "ADMIN_REQUEST_FAILED",
      userMessage: "The administrator request could not be completed.",
    };
  }
  if (status >= 500 && status < 600) {
    return {
      errorCode: "ADMIN_SERVER_ERROR",
      userMessage: "The administrator service is temporarily unavailable.",
    };
  }
  return {
    errorCode: "ADMIN_HTTP_ERROR",
    userMessage: "The administrator request failed.",
  };
}

async function parseError(response: Response): Promise<ParsedAdminError> {
  const fallback = fallbackError(response.status);
  let body: unknown;
  try {
    const text = await response.text();
    if (!text) {
      return fallback;
    }
    body = JSON.parse(text) as unknown;
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    return fallback;
  }
  if (!isRecord(body)) {
    return fallback;
  }
  const detail = body.detail;
  if (isRecord(detail)) {
    const errorCode = safeErrorCode(detail.error_code);
    const userMessage = safeUserMessage(detail.user_message);
    return {
      errorCode: errorCode ?? fallback.errorCode,
      userMessage: userMessage ?? fallback.userMessage,
    };
  }
  const detailMessage = safeUserMessage(detail);
  if (detailMessage) {
    return {
      errorCode: fallback.errorCode,
      userMessage: detailMessage,
    };
  }
  return fallback;
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object"
    && error !== null
    && "name" in error
    && error.name === "AbortError"
  );
}

async function adminRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const method = init.method?.toUpperCase();
  if (
    method === "POST"
    || method === "PUT"
    || method === "PATCH"
    || !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      credentials: "include",
      headers,
      cache: "no-store",
    });
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new AdminApiError(
      0,
      "ADMIN_NETWORK_ERROR",
      "The administrator service could not be reached. Please try again.",
    );
  }
  if (!response.ok) {
    const parsed = await parseError(response);
    if (response.status === 401 && typeof window !== "undefined") {
      window.location.href = "/admin/login";
    }
    throw new AdminApiError(
      response.status,
      parsed.errorCode,
      parsed.userMessage,
    );
  }
  let text: string;
  try {
    text = await response.text();
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new AdminApiError(
      response.status,
      "ADMIN_INVALID_RESPONSE",
      "The administrator service returned an invalid response.",
    );
  }
  if (!text) {
    return undefined as T;
  }
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new AdminApiError(
      response.status,
      "ADMIN_INVALID_RESPONSE",
      "The administrator service returned an invalid response.",
    );
  }
}

export const adminGet = <T>(
  path: string,
  init: RequestInit = {},
): Promise<T> => adminRequest<T>(path, {
  ...init,
  method: undefined,
  body: undefined,
});

export const adminPost = <T>(
  path: string,
  body: unknown,
  init: RequestInit = {},
): Promise<T> => adminRequest<T>(path, {
  ...init,
  method: "POST",
  body: JSON.stringify(body),
});

export const adminPut = <T>(
  path: string,
  body: unknown,
  init: RequestInit = {},
): Promise<T> => adminRequest<T>(path, {
  ...init,
  method: "PUT",
  body: JSON.stringify(body),
});

export const adminPatch = <T>(
  path: string,
  body: unknown = {},
  init: RequestInit = {},
): Promise<T> => adminRequest<T>(path, {
  ...init,
  method: "PATCH",
  body: JSON.stringify(body),
});
