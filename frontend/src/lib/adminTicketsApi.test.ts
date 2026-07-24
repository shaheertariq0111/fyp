import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  AdminApiError,
  adminGet,
  adminPatch,
  adminPost,
} from "@/lib/adminApi";
import {
  addAdminTicketNote,
  getAdminTicket,
  listAdminTickets,
  reopenAdminTicket,
  updateAdminTicketPriority,
  updateAdminTicketStatus,
} from "@/lib/adminTicketsApi";
import type {
  AdminTicketDetailResponse,
  AdminTicketListResponse,
} from "@/lib/adminTicketTypes";

vi.mock("@/lib/adminApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/adminApi")>();
  return {
    ...actual,
    adminGet: vi.fn(),
    adminPatch: vi.fn(),
    adminPost: vi.fn(),
  };
});

const mockedAdminGet = vi.mocked(adminGet);
const mockedAdminPatch = vi.mocked(adminPatch);
const mockedAdminPost = vi.mocked(adminPost);

const listResponse: AdminTicketListResponse = {
  tickets: [],
  next_cursor: null,
};

const detailResponse = {
  ticket: {
    ticket_id: "TKT-20260724-A1B2C3",
    user_id: "user-test",
    customer_id: null,
    customer_name: null,
    customer_phone: null,
    ticket_type: "human_assistance",
    category: "support",
    description: null,
    priority: "normal",
    status: "open",
    order_id: null,
    order_status_snapshot: null,
    source: "web",
    created_at: "2026-07-24T10:00:00+00:00",
    updated_at: "2026-07-24T10:00:00+00:00",
    status_history: [],
    priority_history: [],
    admin_notes: [],
    version: 1,
    linked_order: null,
  },
} satisfies AdminTicketDetailResponse;

beforeEach(() => {
  mockedAdminGet.mockResolvedValue(listResponse);
  mockedAdminPatch.mockResolvedValue(detailResponse);
  mockedAdminPost.mockResolvedValue(detailResponse);
});

describe("listAdminTickets", () => {
  it("uses the exact unfiltered endpoint", async () => {
    await expect(listAdminTickets()).resolves.toEqual(listResponse);
    expect(mockedAdminGet).toHaveBeenCalledWith("/api/admin/tickets", {});
  });

  it.each([
    ["status", { status: "in_review" as const }, "in_review"],
    ["ticket_type", { ticket_type: "order_complaint" as const }, "order_complaint"],
    ["priority", { priority: "urgent" as const }, "urgent"],
    ["limit", { limit: 25 }, "25"],
  ])("encodes the %s filter", async (name, filters, expected) => {
    await listAdminTickets(filters);
    const [path] = mockedAdminGet.mock.calls[0];
    const url = new URL(path, "https://frontend.test");
    expect(url.searchParams.get(name)).toBe(expected);
    expect([...url.searchParams.keys()]).toEqual([name]);
  });

  it("encodes combined filters and omits undefined values", async () => {
    await listAdminTickets({
      status: "waiting_for_customer",
      ticket_type: "human_assistance",
      priority: "high",
      limit: 100,
      cursor: undefined,
    });
    const [path] = mockedAdminGet.mock.calls[0];
    const params = new URL(path, "https://frontend.test").searchParams;
    expect(Object.fromEntries(params)).toEqual({
      status: "waiting_for_customer",
      ticket_type: "human_assistance",
      priority: "high",
      limit: "100",
    });
    expect([...params.keys()]).not.toEqual(expect.arrayContaining([
      "actor",
      "admin",
      "user_id",
      "customer_id",
    ]));
  });

  it("preserves an opaque cursor through URLSearchParams encoding", async () => {
    const cursor = "synthetic.cursor-_~+/=";

    await listAdminTickets({ cursor });

    const [path] = mockedAdminGet.mock.calls[0];
    expect(new URL(path, "https://frontend.test").searchParams.get("cursor")).toBe(cursor);
    expect(path).not.toContain(cursor);
  });

  it("omits empty optional string values at runtime", async () => {
    await listAdminTickets({
      status: "" as never,
      cursor: "",
    });

    expect(mockedAdminGet).toHaveBeenCalledWith("/api/admin/tickets", {});
  });

  it("propagates a signal and AdminApiError unchanged without retrying", async () => {
    const signal = new AbortController().signal;
    const error = new AdminApiError(
      409,
      "TICKET_VERSION_CONFLICT",
      "The ticket changed before this update could be applied.",
    );
    mockedAdminGet.mockRejectedValueOnce(error);

    const caught = await listAdminTickets({}, signal).catch((caughtError: unknown) => caughtError);

    expect(caught).toBe(error);
    expect(caught).toBeInstanceOf(AdminApiError);
    expect(caught).toMatchObject({
      status: 409,
      errorCode: "TICKET_VERSION_CONFLICT",
      userMessage: "The ticket changed before this update could be applied.",
    });
    expect(caught).not.toHaveProperty("response");
    expect(caught).not.toHaveProperty("body");
    expect(mockedAdminGet).toHaveBeenCalledWith("/api/admin/tickets", { signal });
    expect(mockedAdminGet).toHaveBeenCalledTimes(1);
  });
});

describe("ticket detail and mutations", () => {
  it("encodes the ticket ID path segment and returns detail", async () => {
    mockedAdminGet.mockResolvedValueOnce(detailResponse);

    await expect(getAdminTicket("TKT/unsafe ?")).resolves.toEqual(detailResponse);

    expect(mockedAdminGet).toHaveBeenCalledWith(
      "/api/admin/tickets/TKT%2Funsafe%20%3F",
      {},
    );
  });

  it("sends the exact status body and strips runtime extras", async () => {
    const request = {
      status: "in_review" as const,
      reason: "  Review started.\n",
      expected_version: 7,
      actor: "forged",
      PK: "private",
    };

    await updateAdminTicketStatus("TKT/1", request);

    expect(mockedAdminPatch).toHaveBeenCalledWith(
      "/api/admin/tickets/TKT%2F1/status",
      {
        status: "in_review",
        reason: "  Review started.\n",
        expected_version: 7,
      },
      {},
    );
  });

  it("omits an undefined optional status reason but preserves null", async () => {
    await updateAdminTicketStatus("TKT-1", {
      status: "in_review",
      expected_version: 4,
    });
    await updateAdminTicketStatus("TKT-1", {
      status: "in_review",
      reason: null,
      expected_version: 4,
    });

    expect(mockedAdminPatch.mock.calls[0][1]).toEqual({
      status: "in_review",
      expected_version: 4,
    });
    expect(mockedAdminPatch.mock.calls[1][1]).toEqual({
      status: "in_review",
      reason: null,
      expected_version: 4,
    });
  });

  it("sends the exact priority body", async () => {
    const request = {
      priority: "urgent",
      reason: null,
      expected_version: 8,
      admin: "forged",
      version: 99,
    } as const;

    await updateAdminTicketPriority("TKT-1", request);

    expect(mockedAdminPatch).toHaveBeenCalledWith(
      "/api/admin/tickets/TKT-1/priority",
      { priority: "urgent", reason: null, expected_version: 8 },
      {},
    );
  });

  it("sends the exact note body", async () => {
    const request = {
      text: "  Preserve note whitespace.\n",
      expected_version: 9,
      note_id: "forged",
      timestamp: "private",
      cursor: "private",
    };

    await addAdminTicketNote("TKT-1", request);

    expect(mockedAdminPost).toHaveBeenCalledWith(
      "/api/admin/tickets/TKT-1/notes",
      { text: "  Preserve note whitespace.\n", expected_version: 9 },
      {},
    );
  });

  it("sends the exact reopen body", async () => {
    const request = {
      target_status: "open" as const,
      reason: "Customer replied",
      expected_version: 10,
      customer_id: "forged",
      status_history: ["private"],
      linked_order: { order_id: "private" },
    };

    await reopenAdminTicket("TKT-1", request);

    expect(mockedAdminPost).toHaveBeenCalledWith(
      "/api/admin/tickets/TKT-1/reopen",
      {
        target_status: "open",
        reason: "Customer replied",
        expected_version: 10,
      },
      {},
    );
  });

  it.each([
    ["detail", (signal: AbortSignal) => getAdminTicket("TKT-1", signal), mockedAdminGet],
    [
      "mutation",
      (signal: AbortSignal) => addAdminTicketNote(
        "TKT-1",
        { text: "Note", expected_version: 1 },
        signal,
      ),
      mockedAdminPost,
    ],
  ])("propagates a signal for %s requests", async (_name, request, mock) => {
    const signal = new AbortController().signal;

    await request(signal);

    expect(mock.mock.calls[0].at(-1)).toEqual({ signal });
  });
});
