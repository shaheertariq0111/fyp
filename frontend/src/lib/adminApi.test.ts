import { describe, expect, it, vi } from "vitest";
import {
  AdminApiError,
  adminGet,
  adminPatch,
  adminPost,
  adminPut,
} from "@/lib/adminApi";

const apiBaseUrl = "https://api.test.invalid";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(response: Response | Promise<never>) {
  const fetchMock = vi.fn().mockImplementation(() => (
    response instanceof Response ? Promise.resolve(response) : response
  ));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("administrator request compatibility", () => {
  it.each([
    ["GET", () => adminGet("/api/admin/analytics"), undefined],
    ["POST", () => adminPost("/api/admin/login", { username: "admin" }), { username: "admin" }],
    ["PUT", () => adminPut("/api/admin/menu/items/item-1", { name: "Item" }), { name: "Item" }],
    ["PATCH", () => adminPatch("/api/admin/orders/order-1/status", { action: "accept" }), { action: "accept" }],
  ])("preserves the existing %s wrapper call shape", async (method, request, body) => {
    const fetchMock = mockFetch(jsonResponse({ ok: true }));

    await request();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url.startsWith(apiBaseUrl)).toBe(true);
    expect(init.credentials).toBe("include");
    expect(init.cache).toBe("no-store");
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
    if (method === "GET") {
      expect(init.method).toBeUndefined();
      expect(init.body).toBeUndefined();
    } else {
      expect(init.method).toBe(method);
      expect(init.body).toBe(JSON.stringify(body));
    }
  });

  it("uses the existing API URL construction and parses successful JSON", async () => {
    const fetchMock = mockFetch(jsonResponse({ value: 7 }));

    await expect(adminGet<{ value: number }>("/api/admin/analytics")).resolves.toEqual({ value: 7 });
    expect(fetchMock).toHaveBeenCalledWith(
      `${apiBaseUrl}/api/admin/analytics`,
      expect.any(Object),
    );
  });

  it.each([
    ["plain object", { "X-Test-Header": "object" }, "object"],
    ["Headers", new Headers({ "X-Test-Header": "headers" }), "headers"],
    ["tuple array", [["X-Test-Header", "tuples"]] as [string, string][], "tuples"],
  ])("normalizes and preserves %s caller headers", async (_name, headers, expected) => {
    const fetchMock = mockFetch(jsonResponse({ ok: true }));

    await adminPost("/api/admin/example", {}, { headers });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const normalizedHeaders = new Headers(init.headers);
    expect(normalizedHeaders.get("X-Test-Header")).toBe(expected);
    expect(normalizedHeaders.get("Content-Type")).toBe("application/json");
  });

  it.each([
    ["POST", (init: RequestInit) => adminPost("/api/admin/example", { value: 1 }, init)],
    ["PUT", (init: RequestInit) => adminPut("/api/admin/example", { value: 1 }, init)],
    ["PATCH", (init: RequestInit) => adminPatch("/api/admin/example", { value: 1 }, init)],
  ])("protects required %s request options", async (method, request) => {
    const fetchMock = mockFetch(jsonResponse({ ok: true }));

    await request({
      method: "DELETE",
      body: "forged",
      credentials: "omit",
      cache: "force-cache",
      headers: { "Content-Type": "text/plain", "X-Safe": "preserved" },
    });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const normalizedHeaders = new Headers(init.headers);
    expect(init.method).toBe(method);
    expect(init.body).toBe(JSON.stringify({ value: 1 }));
    expect(init.credentials).toBe("include");
    expect(init.cache).toBe("no-store");
    expect(normalizedHeaders.get("Content-Type")).toBe("application/json");
    expect(normalizedHeaders.get("X-Safe")).toBe("preserved");
  });

  it("preserves default GET behavior while rejecting caller method and body", async () => {
    const fetchMock = mockFetch(jsonResponse({ ok: true }));

    await adminGet("/api/admin/example", {
      method: "DELETE",
      body: "forged",
    });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBeUndefined();
    expect(init.body).toBeUndefined();
  });

  it.each([200, 204, 205])("handles an empty successful %s response", async (status) => {
    mockFetch(new Response(null, { status }));

    await expect(adminPost<void>("/api/admin/logout", {})).resolves.toBeUndefined();
  });

  it("rejects malformed successful JSON with a sanitized structured error", async () => {
    mockFetch(new Response("{bad", { status: 200 }));

    await expect(adminGet("/api/admin/analytics")).rejects.toMatchObject({
      name: "AdminApiError",
      status: 200,
      errorCode: "ADMIN_INVALID_RESPONSE",
      userMessage: "The administrator service returned an invalid response.",
      message: "The administrator service returned an invalid response.",
    });
  });
});

describe("AdminApiError", () => {
  it("uses a valid backend error code and user message", async () => {
    mockFetch(jsonResponse({
      detail: {
        error_code: "TICKET_VERSION_CONFLICT",
        user_message: "The ticket changed before this update could be applied.",
      },
    }, 409));

    const error = await adminGet("/api/admin/tickets/TKT-1").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(AdminApiError);
    expect(error).toMatchObject({
      name: "AdminApiError",
      status: 409,
      errorCode: "TICKET_VERSION_CONFLICT",
      userMessage: "The ticket changed before this update could be applied.",
      message: "The ticket changed before this update could be applied.",
    });
    expect(JSON.stringify(error)).not.toContain("cursor");
  });

  it("supports a safe plain-string detail", async () => {
    mockFetch(jsonResponse({ detail: "Admin login required" }, 401));
    const testWindow = { location: { href: "/admin" } };
    vi.stubGlobal("window", testWindow);

    const error = await adminGet("/api/admin/me").catch((caught: unknown) => caught);

    expect(testWindow.location.href).toBe("/admin/login");
    expect(error).toMatchObject({
      status: 401,
      errorCode: "ADMIN_AUTHENTICATION_REQUIRED",
      userMessage: "Admin login required",
    });
  });

  it.each([
    [400, "ADMIN_REQUEST_FAILED", "The administrator request could not be completed."],
    [401, "ADMIN_AUTHENTICATION_REQUIRED", "Administrator authentication is required."],
    [404, "ADMIN_REQUEST_FAILED", "The administrator request could not be completed."],
    [409, "ADMIN_REQUEST_FAILED", "The administrator request could not be completed."],
    [500, "ADMIN_SERVER_ERROR", "The administrator service is temporarily unavailable."],
    [503, "ADMIN_SERVER_ERROR", "The administrator service is temporarily unavailable."],
    [302, "ADMIN_HTTP_ERROR", "The administrator request failed."],
  ])("uses a safe fallback for HTTP %s", async (status, errorCode, userMessage) => {
    mockFetch(new Response(null, { status }));
    if (status === 401) {
      vi.stubGlobal("window", { location: { href: "/admin" } });
    }

    await expect(adminGet("/api/admin/example")).rejects.toMatchObject({
      status,
      errorCode,
      userMessage,
    });
  });

  it.each([
    new Response("<html>private</html>", { status: 500, headers: { "Content-Type": "text/html" } }),
    new Response("{bad", { status: 400 }),
    jsonResponse({ unexpected: "private" }, 400),
    jsonResponse({ detail: { error_code: "CODE", user_message: " " } }, 400),
    jsonResponse({ detail: "Traceback:\nprivate stack" }, 500),
    jsonResponse({
      detail: {
        error_code: "INVALID_CURSOR",
        user_message: `${"A".repeat(40)}.${"f".repeat(64)}`,
      },
    }, 400),
  ])("never exposes malformed or unsafe error bodies", async (response) => {
    mockFetch(response);

    const error = await adminGet("/api/admin/example").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(AdminApiError);
    expect((error as AdminApiError).message).not.toMatch(
      /private|traceback|html/i,
    );
  });
});

describe("abort and network behavior", () => {
  it("fails closed before an unmocked request can reach the network", async () => {
    expect(vi.isMockFunction(fetch)).toBe(true);

    await expect(adminGet("/api/admin/unmocked")).rejects.toMatchObject({
      status: 0,
      errorCode: "ADMIN_NETWORK_ERROR",
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("uses synthetic environment values during each test", () => {
    expect(process.env.NEXT_PUBLIC_API_BASE_URL).toBe(apiBaseUrl);
    expect(process.env.NEXT_PUBLIC_BRANCH_ID).toBe("test");
  });

  it("passes AbortSignal to fetch", async () => {
    const controller = new AbortController();
    const fetchMock = mockFetch(jsonResponse({ ok: true }));

    await adminGet("/api/admin/analytics", { signal: controller.signal });

    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it("preserves AbortError identity", async () => {
    const abortError = new DOMException("The operation was aborted.", "AbortError");
    mockFetch(Promise.reject(abortError));

    const error = await adminGet("/api/admin/analytics").catch((caught: unknown) => caught);

    expect(error).toBe(abortError);
    expect(error).not.toBeInstanceOf(AdminApiError);
  });

  it("preserves a pre-aborted signal error without retrying", async () => {
    const controller = new AbortController();
    const abortError = new DOMException("The operation was aborted.", "AbortError");
    controller.abort();
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      expect(init?.signal?.aborted).toBe(true);
      return Promise.reject(abortError);
    });
    vi.stubGlobal("fetch", fetchMock);

    const error = await adminGet("/api/admin/analytics", {
      signal: controller.signal,
    }).catch((caught: unknown) => caught);

    expect(error).toBe(abortError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("preserves AbortError while reading a successful response body", async () => {
    const abortError = new DOMException("The operation was aborted.", "AbortError");
    const response = jsonResponse({ ok: true });
    const textMock = vi.spyOn(response, "text").mockRejectedValue(abortError);
    const fetchMock = mockFetch(response);

    const error = await adminGet("/api/admin/analytics").catch((caught: unknown) => caught);

    expect(error).toBe(abortError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(textMock).toHaveBeenCalledTimes(1);
  });

  it("preserves AbortError while reading an error response without redirecting", async () => {
    const abortError = new Error("test-only abort");
    abortError.name = "AbortError";
    const response = jsonResponse({ detail: "Login required" }, 401);
    const textMock = vi.spyOn(response, "text").mockRejectedValue(abortError);
    const fetchMock = mockFetch(response);
    const testWindow = { location: { href: "/admin" } };
    vi.stubGlobal("window", testWindow);

    const error = await adminGet("/api/admin/me").catch((caught: unknown) => caught);

    expect(error).toBe(abortError);
    expect(testWindow.location.href).toBe("/admin");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(textMock).toHaveBeenCalledTimes(1);
  });

  it("sanitizes network failures without exposing internals", async () => {
    mockFetch(Promise.reject(new Error("connect ECONNREFUSED private-host:8001")));

    const error = await adminGet("/api/admin/analytics").catch((caught: unknown) => caught);

    expect(error).toMatchObject({
      name: "AdminApiError",
      status: 0,
      errorCode: "ADMIN_NETWORK_ERROR",
      userMessage: "The administrator service could not be reached. Please try again.",
    });
    expect((error as AdminApiError).message).not.toContain("private-host");
  });

  it.each([400, 403, 404, 409, 500, 503])(
    "does not redirect for HTTP %s",
    async (status) => {
      mockFetch(new Response(null, { status }));
      const testWindow = { location: { href: "/admin" } };
      vi.stubGlobal("window", testWindow);

      await expect(adminGet("/api/admin/example")).rejects.toBeInstanceOf(AdminApiError);

      expect(testWindow.location.href).toBe("/admin");
    },
  );

  it("does not redirect for a network failure", async () => {
    mockFetch(Promise.reject(new Error("test-only network failure")));
    const testWindow = { location: { href: "/admin" } };
    vi.stubGlobal("window", testWindow);

    await expect(adminGet("/api/admin/example")).rejects.toBeInstanceOf(AdminApiError);

    expect(testWindow.location.href).toBe("/admin");
  });

  it("does not redirect for AbortError", async () => {
    const abortError = new DOMException("The operation was aborted.", "AbortError");
    mockFetch(Promise.reject(abortError));
    const testWindow = { location: { href: "/admin" } };
    vi.stubGlobal("window", testWindow);

    await expect(adminGet("/api/admin/example")).rejects.toBe(abortError);

    expect(testWindow.location.href).toBe("/admin");
  });
});
