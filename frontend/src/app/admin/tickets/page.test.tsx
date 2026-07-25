import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminApiError } from "@/lib/adminApi";
import { listAdminTickets } from "@/lib/adminTicketsApi";
import type {
  AdminTicketListItem,
  AdminTicketListResponse,
} from "@/lib/adminTicketTypes";
import { TicketsPageClient } from "@/app/admin/tickets/TicketsPageClient";
import {
  formatTicketDateTime,
  priorityClassName,
  priorityLabel,
  statusClassName,
  statusLabel,
  ticketTypeLabel,
} from "@/app/admin/tickets/ticketPresentation";
import { formatDateTime } from "@/app/admin/orders/orderPresentation";

let query = "";
const replace = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/tickets",
  useRouter: () => ({ replace }),
  useSearchParams: () => new URLSearchParams(query),
}));

vi.mock("@/lib/adminTicketsApi", () => ({
  listAdminTickets: vi.fn(),
}));

const mockedListAdminTickets = vi.mocked(listAdminTickets);

const ticket: AdminTicketListItem = {
  ticket_id: "TKT-20260724-ABC123",
  user_id: "internal-user",
  customer_id: "internal-customer",
  customer_name: "Synthetic Customer",
  customer_phone: "+10000000000",
  ticket_type: "order_complaint",
  category: "Late delivery",
  priority: "urgent",
  status: "in_review",
  order_id: "ORD-SYNTHETIC-001",
  source: "web",
  created_at: "2026-07-24T10:00:00+00:00",
  updated_at: "2026-07-24T11:00:00+00:00",
  version: 7,
};

const emptyResponse: AdminTicketListResponse = {
  tickets: [],
  next_cursor: null,
};

function response(
  tickets: AdminTicketListItem[] = [ticket],
  nextCursor: string | null = null,
): AdminTicketListResponse {
  return { tickets, next_cursor: nextCursor };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  query = "";
  replace.mockReset();
  mockedListAdminTickets.mockReset();
  mockedListAdminTickets.mockResolvedValue(response());
});

describe("ticket presentation", () => {
  it.each([
    ["open", "Open"],
    ["in_review", "In Review"],
    ["waiting_for_customer", "Waiting for Customer"],
    ["resolved", "Resolved"],
    ["closed", "Closed"],
  ] as const)("labels status %s", (value, label) => {
    expect(statusLabel(value)).toBe(label);
  });

  it.each([
    ["normal", "Normal"],
    ["high", "High"],
    ["urgent", "Urgent"],
  ] as const)("labels priority %s", (value, label) => {
    expect(priorityLabel(value)).toBe(label);
  });

  it.each([
    ["human_assistance", "Human Assistance"],
    ["order_complaint", "Order Complaint"],
  ] as const)("labels ticket type %s", (value, label) => {
    expect(ticketTypeLabel(value)).toBe(label);
  });

  it("delegates timestamp presentation to the order date utility", () => {
    expect(formatTicketDateTime(ticket.created_at)).toBe(formatDateTime(ticket.created_at));
  });

  it.each([
    ["open", "is-status-open"],
    ["in_review", "is-status-in_review"],
    ["waiting_for_customer", "is-status-waiting_for_customer"],
    ["resolved", "is-status-resolved"],
    ["closed", "is-status-closed"],
  ] as const)("maps status %s to an explicit badge class", (value, className) => {
    expect(statusClassName(value)).toBe(className);
  });

  it.each([
    ["normal", "is-priority-normal"],
    ["high", "is-priority-high"],
    ["urgent", "is-priority-urgent"],
  ] as const)("maps priority %s to an explicit badge class", (value, className) => {
    expect(priorityClassName(value)).toBe(className);
  });

  it("does not include malformed runtime values in badge classes", () => {
    expect(statusClassName("open injected-class" as never)).toBe("is-status-neutral");
    expect(priorityClassName("urgent injected-class" as never)).toBe("is-priority-neutral");
  });
});

describe("ticket list requests and filters", () => {
  it("loads page one with limit 25, no cursor, and an AbortSignal", async () => {
    render(<TicketsPageClient />);

    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenCalledTimes(1));
    const [filters, signal] = mockedListAdminTickets.mock.calls[0];
    expect(filters).toEqual({ limit: 25 });
    expect(signal).toBeInstanceOf(AbortSignal);
  });

  it("reads valid combined URL filters and ignores invalid values", async () => {
    query = "status=in_review&ticket_type=order_complaint&priority=urgent&cursor=private";
    render(<TicketsPageClient />);

    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenCalledWith(
      {
        status: "in_review",
        ticket_type: "order_complaint",
        priority: "urgent",
        limit: 25,
      },
      expect.any(AbortSignal),
    ));
    expect(replace).toHaveBeenCalledWith(
      "/admin/tickets?status=in_review&ticket_type=order_complaint&priority=urgent",
      { scroll: false },
    );
    expect(replace.mock.calls.flat().join(" ")).not.toContain("cursor");
  });

  it("does not send invalid direct URL filters", async () => {
    query = "status=unknown&ticket_type=other&priority=critical";
    render(<TicketsPageClient />);

    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenCalledWith(
      { limit: 25 },
      expect.any(AbortSignal),
    ));
  });

  it("updates the URL, clears All values, and resets pagination", async () => {
    const user = userEvent.setup();
    mockedListAdminTickets
      .mockResolvedValueOnce(response([ticket], "synthetic.cursor.value"))
      .mockResolvedValue(response());
    render(<TicketsPageClient />);
    await screen.findByText("Page 1");
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("Page 2");

    await user.selectOptions(screen.getByLabelText("Status"), "resolved");

    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenLastCalledWith(
      { status: "resolved", limit: 25 },
      expect.any(AbortSignal),
    ));
    expect(screen.getByText("Page 1")).toBeInTheDocument();
    expect(replace).toHaveBeenLastCalledWith(
      "/admin/tickets?status=resolved",
      { scroll: false },
    );

    await user.selectOptions(screen.getByLabelText("Status"), "");
    expect(replace).toHaveBeenLastCalledWith("/admin/tickets", { scroll: false });
  });

  it("clears all URL filters", async () => {
    query = "status=open&ticket_type=human_assistance&priority=high";
    const user = userEvent.setup();
    render(<TicketsPageClient />);

    await user.click(await screen.findByRole("button", { name: "Clear filters" }));

    expect(replace).toHaveBeenLastCalledWith("/admin/tickets", { scroll: false });
    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenLastCalledWith(
      { limit: 25 },
      expect.any(AbortSignal),
    ));
  });
});

describe("ticket list presentation and states", () => {
  it("renders the required fields in desktop and mobile presentations without internals or links", async () => {
    render(<TicketsPageClient />);

    const table = await screen.findByRole("table", { name: "Support tickets" });
    const cards = screen.getByLabelText("Support ticket cards");
    for (const container of [table, cards]) {
      const view = within(container);
      expect(view.getByText(ticket.ticket_id)).toBeInTheDocument();
      expect(view.getByText("Order Complaint")).toBeInTheDocument();
      expect(view.getByText("Late delivery")).toBeInTheDocument();
      expect(view.getByText("Synthetic Customer")).toBeInTheDocument();
      expect(view.getByText("+10000000000")).toBeInTheDocument();
      expect(view.getByText("Urgent")).toBeInTheDocument();
      expect(view.getByText("In Review")).toBeInTheDocument();
      expect(view.getByText("ORD-SYNTHETIC-001")).toBeInTheDocument();
      expect(view.getByText("web")).toBeInTheDocument();
      expect(view.getByText(formatDateTime(ticket.created_at))).toBeInTheDocument();
      expect(view.getByText(formatDateTime(ticket.updated_at))).toBeInTheDocument();
    }
    expect(screen.queryByText("internal-user")).not.toBeInTheDocument();
    expect(screen.queryByText("internal-customer")).not.toBeInTheDocument();
    expect(screen.queryByText("7")).not.toBeInTheDocument();
    const ticketLinks = screen.getAllByRole("link", { name: ticket.ticket_id });
    expect(ticketLinks).toHaveLength(2);
    for (const link of ticketLinks) {
      expect(link).toHaveAttribute(
        "href",
        `/admin/tickets/${encodeURIComponent(ticket.ticket_id)}`,
      );
      expect(link.getAttribute("href")).not.toMatch(/internal|customer|cursor/i);
    }
  });

  it("safely encodes special ticket ID characters in desktop and mobile links", async () => {
    const specialId = "TKT/SYNTHETIC ?#%";
    mockedListAdminTickets.mockResolvedValue(response([{ ...ticket, ticket_id: specialId }]));
    render(<TicketsPageClient />);

    const links = await screen.findAllByRole("link", { name: specialId });

    expect(links).toHaveLength(2);
    for (const link of links) {
      expect(link).toHaveAttribute(
        "href",
        `/admin/tickets/${encodeURIComponent(specialId)}`,
      );
    }
    expect(screen.getByRole("table", { name: "Support tickets" }).closest("a"))
      .not.toBeInTheDocument();
  });

  it("uses neutral presentation for malformed runtime enum values", async () => {
    mockedListAdminTickets.mockResolvedValue(response([{
      ...ticket,
      ticket_type: "runtime-ticket-type",
      status: "runtime-status",
      priority: "runtime-priority",
    } as unknown as AdminTicketListItem]));
    const { container } = render(<TicketsPageClient />);

    expect(await screen.findAllByText("Unknown ticket type")).toHaveLength(2);
    for (const badge of screen.getAllByText("Unknown status")) {
      expect(badge).toHaveClass("is-status-neutral");
    }
    for (const badge of screen.getAllByText("Unknown priority")) {
      expect(badge).toHaveClass("is-priority-neutral");
    }
    expect(container).not.toHaveTextContent("runtime-ticket-type");
    expect(container).not.toHaveTextContent("runtime-status");
    expect(container).not.toHaveTextContent("runtime-priority");
  });

  it("shows safe null and blank fallbacks", async () => {
    mockedListAdminTickets.mockResolvedValue(response([{
      ...ticket,
      customer_name: null,
      customer_phone: null,
      order_id: null,
      category: "",
      source: "",
    }]));
    render(<TicketsPageClient />);

    const table = await screen.findByRole("table", { name: "Support tickets" });
    expect(within(table).getAllByText("Not provided")).toHaveLength(4);
    expect(within(table).getByText("No linked order")).toBeInTheDocument();
  });

  it("shows a visual initial skeleton with exactly one live announcement", () => {
    mockedListAdminTickets.mockReturnValue(deferred<AdminTicketListResponse>().promise);
    const { container } = render(<TicketsPageClient />);

    const announcements = [...container.querySelectorAll("[aria-live='polite']")]
      .filter((element) => element.textContent?.includes("Loading support tickets"));
    expect(announcements).toHaveLength(1);
    expect(screen.getByText("Loading support tickets")).toBeInTheDocument();
    expect(container.querySelector(".admin-ticket-skeleton")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("table", { name: "Support tickets" })).not.toBeInTheDocument();
  });

  it("distinguishes unfiltered and filtered empty states", async () => {
    mockedListAdminTickets.mockResolvedValue(emptyResponse);
    const { unmount } = render(<TicketsPageClient />);
    expect(await screen.findByText("No support tickets")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    unmount();

    query = "status=closed";
    render(<TicketsPageClient />);
    expect(await screen.findByText("No tickets match these filters")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("ticket pagination and request races", () => {
  it("uses opaque next and previous cursors without placing them in the URL", async () => {
    const user = userEvent.setup();
    mockedListAdminTickets
      .mockResolvedValueOnce(response([ticket], "synthetic.cursor.value"))
      .mockResolvedValueOnce(response([{ ...ticket, ticket_id: "TKT-20260724-DEF456" }]))
      .mockResolvedValueOnce(response());
    render(<TicketsPageClient />);

    await user.click(await screen.findByRole("button", { name: "Next page" }));
    await screen.findByText("Page 2");
    expect(mockedListAdminTickets).toHaveBeenNthCalledWith(
      2,
      { limit: 25, cursor: "synthetic.cursor.value" },
      expect.any(AbortSignal),
    );
    expect(replace.mock.calls.flat().join(" ")).not.toContain("synthetic.cursor.value");

    await user.click(screen.getByRole("button", { name: "Previous page" }));
    await screen.findByText("Page 1");
    expect(mockedListAdminTickets).toHaveBeenNthCalledWith(
      3,
      { limit: 25 },
      expect.any(AbortSignal),
    );
  });

  it("keeps current tickets visible and blocks duplicate Next requests while loading", async () => {
    const user = userEvent.setup();
    const next = deferred<AdminTicketListResponse>();
    mockedListAdminTickets
      .mockResolvedValueOnce(response([ticket], "synthetic.cursor.value"))
      .mockReturnValueOnce(next.promise);
    render(<TicketsPageClient />);

    const nextButton = await screen.findByRole("button", { name: "Next page" });
    await user.click(nextButton);
    await user.click(nextButton);

    expect(screen.getAllByText(ticket.ticket_id).length).toBeGreaterThan(0);
    expect(nextButton).toBeDisabled();
    expect(mockedListAdminTickets).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Loading ticket page")).toBeInTheDocument();
  });

  it("aborts superseded filter requests and ignores stale responses", async () => {
    const user = userEvent.setup();
    const first = deferred<AdminTicketListResponse>();
    const second = deferred<AdminTicketListResponse>();
    mockedListAdminTickets
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    render(<TicketsPageClient />);
    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenCalledTimes(1));
    const firstSignal = mockedListAdminTickets.mock.calls[0][1]!;

    await user.selectOptions(screen.getByLabelText("Priority"), "high");
    expect(firstSignal.aborted).toBe(true);
    second.resolve(response([{ ...ticket, ticket_id: "TKT-20260724-NEW123" }]));
    expect(await screen.findAllByText("TKT-20260724-NEW123")).toHaveLength(2);
    first.resolve(response([{ ...ticket, ticket_id: "TKT-20260724-OLD123" }]));
    await Promise.resolve();
    expect(screen.queryByText("TKT-20260724-OLD123")).not.toBeInTheDocument();
  });

  it("keeps AbortError silent when an initial request is superseded by a filter", async () => {
    const user = userEvent.setup();
    const initial = deferred<AdminTicketListResponse>();
    mockedListAdminTickets
      .mockReturnValueOnce(initial.promise)
      .mockResolvedValueOnce(response([{ ...ticket, ticket_id: "TKT-20260724-FILTER" }]));
    render(<TicketsPageClient />);
    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenCalledTimes(1));

    await user.selectOptions(screen.getByLabelText("Priority"), "high");
    initial.reject(new DOMException("synthetic filter abort", "AbortError"));

    expect(await screen.findAllByText("TKT-20260724-FILTER")).toHaveLength(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("synthetic filter abort")).not.toBeInTheDocument();
  });

  it("aborts the active request on unmount", async () => {
    const request = deferred<AdminTicketListResponse>();
    mockedListAdminTickets.mockReturnValue(request.promise);
    const { unmount } = render(<TicketsPageClient />);
    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenCalledTimes(1));
    const signal = mockedListAdminTickets.mock.calls[0][1]!;

    unmount();

    expect(signal.aborted).toBe(true);
    request.reject(new DOMException("synthetic unmount abort", "AbortError"));
    await Promise.resolve();
  });

  it("retains the current page after a failed pagination request", async () => {
    const user = userEvent.setup();
    mockedListAdminTickets
      .mockResolvedValueOnce(response([ticket], "synthetic.cursor.value"))
      .mockRejectedValueOnce(new AdminApiError(
        500,
        "TICKET_PAGINATION_STALLED",
        "Ticket pagination is temporarily unavailable.",
      ));
    render(<TicketsPageClient />);

    await user.click(await screen.findByRole("button", { name: "Next page" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Ticket pagination is temporarily unavailable.",
    );
    expect(screen.getAllByText(ticket.ticket_id).length).toBeGreaterThan(0);
    expect(screen.getByText("Page 1")).toBeInTheDocument();
  });

  it("replaces stale forward history with a cursor returned by Refresh", async () => {
    const user = userEvent.setup();
    mockedListAdminTickets
      .mockResolvedValueOnce(response([ticket], "synthetic.cursor.A"))
      .mockResolvedValueOnce(response([{ ...ticket, ticket_id: "TKT-20260724-PAGE02" }]))
      .mockResolvedValueOnce(response([ticket], "synthetic.cursor.A"))
      .mockResolvedValueOnce(response([ticket], "synthetic.cursor.B"))
      .mockResolvedValueOnce(response([{ ...ticket, ticket_id: "TKT-20260724-NEWP02" }]));
    render(<TicketsPageClient />);

    await user.click(await screen.findByRole("button", { name: "Next page" }));
    await screen.findByText("Page 2");
    await user.click(screen.getByRole("button", { name: "Previous page" }));
    await screen.findByText("Page 1");
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenCalledTimes(4));
    await user.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenNthCalledWith(
      5,
      { limit: 25, cursor: "synthetic.cursor.B" },
      expect.any(AbortSignal),
    ));
    expect(replace.mock.calls.flat().join(" ")).not.toMatch(/synthetic\.cursor\.[AB]/);
  });

  it("starts from a cursorless page one after a hard remount", async () => {
    query = "status=open";
    const user = userEvent.setup();
    mockedListAdminTickets
      .mockResolvedValueOnce(response([ticket], "synthetic.cursor.value"))
      .mockResolvedValueOnce(response([{ ...ticket, ticket_id: "TKT-20260724-PAGE02" }]));
    const first = render(<TicketsPageClient />);
    await user.click(await screen.findByRole("button", { name: "Next page" }));
    await screen.findByText("Page 2");
    first.unmount();

    mockedListAdminTickets.mockResolvedValueOnce(response());
    render(<TicketsPageClient />);

    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenNthCalledWith(
      3,
      { status: "open", limit: 25 },
      expect.any(AbortSignal),
    ));
    expect(screen.getByText("Page 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Status")).toHaveValue("open");
    expect(replace.mock.calls.flat().join(" ")).not.toContain("cursor");
  });

  it("keeps page two after failed Previous and retries page one exactly once", async () => {
    const user = userEvent.setup();
    mockedListAdminTickets
      .mockResolvedValueOnce(response([ticket], "synthetic.cursor.value"))
      .mockResolvedValueOnce(response([{ ...ticket, ticket_id: "TKT-20260724-PAGE02" }]))
      .mockRejectedValueOnce(new AdminApiError(
        500,
        "TICKET_PAGINATION_STALLED",
        "Ticket pagination is temporarily unavailable.",
      ))
      .mockResolvedValueOnce(response([ticket], "synthetic.cursor.updated"));
    render(<TicketsPageClient />);
    await user.click(await screen.findByRole("button", { name: "Next page" }));
    await screen.findByText("Page 2");

    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Page 2")).toBeInTheDocument();
    expect(screen.getAllByText("TKT-20260724-PAGE02")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Retry current page" }));
    await screen.findByText("Page 1");
    expect(mockedListAdminTickets).toHaveBeenNthCalledWith(
      4,
      { limit: 25 },
      expect.any(AbortSignal),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("uses distinct mobile layout classes for page and pagination loading labels", async () => {
    const user = userEvent.setup();
    const next = deferred<AdminTicketListResponse>();
    mockedListAdminTickets
      .mockResolvedValueOnce(response([ticket], "synthetic.cursor.value"))
      .mockReturnValueOnce(next.promise);
    render(<TicketsPageClient />);

    await user.click(await screen.findByRole("button", { name: "Next page" }));

    expect(screen.getByText("Page 1")).toHaveClass("admin-ticket-page-label");
    expect(screen.getByText("Loading...")).toHaveClass("admin-ticket-loading-label");
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });
});

describe("ticket errors and refresh", () => {
  it.each([
    ["INVALID_CURSOR", "This ticket page has expired or is no longer valid."],
    ["TICKET_DATA_INVALID", "The ticket list cannot be safely displayed."],
    ["TICKET_BACKEND_UNAVAILABLE", "Support tickets are temporarily unavailable."],
    ["TICKET_INTERNAL_ERROR", "Support tickets could not be loaded safely."],
    ["ADMIN_NETWORK_ERROR", "The administrator service could not be reached."],
  ])("shows a sanitized panel for %s", async (errorCode, message) => {
    mockedListAdminTickets.mockRejectedValue(new AdminApiError(500, errorCode, "raw-private"));
    render(<TicketsPageClient />);

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("alert")).not.toHaveTextContent("raw-private");
  });

  it("does not show a page error while 401 redirect handling is active", async () => {
    mockedListAdminTickets.mockRejectedValue(new AdminApiError(
      401,
      "ADMIN_AUTHENTICATION_REQUIRED",
      "Administrator authentication is required.",
    ));
    render(<TicketsPageClient />);

    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("refreshes the current page once, preserves filters, and retains rows while loading", async () => {
    query = "status=open";
    const user = userEvent.setup();
    const refresh = deferred<AdminTicketListResponse>();
    mockedListAdminTickets
      .mockResolvedValueOnce(response())
      .mockReturnValueOnce(refresh.promise);
    render(<TicketsPageClient />);

    await user.click(await screen.findByRole("button", { name: "Refresh" }));

    expect(screen.getAllByText(ticket.ticket_id).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Refreshing..." })).toBeDisabled();
    expect(mockedListAdminTickets).toHaveBeenLastCalledWith(
      { status: "open", limit: 25 },
      expect.any(AbortSignal),
    );
    expect(mockedListAdminTickets).toHaveBeenCalledTimes(2);
    expect(screen.getAllByText("Refreshing support tickets")).toHaveLength(1);
  });

  it("keeps filtered data when an older Refresh resolves late", async () => {
    query = "status=open";
    const user = userEvent.setup();
    const refresh = deferred<AdminTicketListResponse>();
    const filtered = deferred<AdminTicketListResponse>();
    mockedListAdminTickets
      .mockResolvedValueOnce(response())
      .mockReturnValueOnce(refresh.promise)
      .mockReturnValueOnce(filtered.promise);
    render(<TicketsPageClient />);
    await user.click(await screen.findByRole("button", { name: "Refresh" }));

    await user.selectOptions(screen.getByLabelText("Priority"), "high");
    const refreshSignal = mockedListAdminTickets.mock.calls[1][1]!;
    expect(refreshSignal.aborted).toBe(true);
    filtered.resolve(response([{ ...ticket, ticket_id: "TKT-20260724-FILTER" }]));
    expect(await screen.findAllByText("TKT-20260724-FILTER")).toHaveLength(2);
    refresh.resolve(response([{ ...ticket, ticket_id: "TKT-20260724-STALE0" }]));
    await Promise.resolve();
    expect(screen.queryByText("TKT-20260724-STALE0")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("retains current data after Refresh failure and clears the error after one retry", async () => {
    const user = userEvent.setup();
    mockedListAdminTickets
      .mockResolvedValueOnce(response())
      .mockRejectedValueOnce(new AdminApiError(
        503,
        "TICKET_BACKEND_UNAVAILABLE",
        "private",
      ))
      .mockResolvedValueOnce(response([{ ...ticket, ticket_id: "TKT-20260724-RETRY1" }]));
    render(<TicketsPageClient />);
    await user.click(await screen.findByRole("button", { name: "Refresh" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getAllByText(ticket.ticket_id)).toHaveLength(2);
    expect(screen.getByText("Page 1")).toBeInTheDocument();
    expect(mockedListAdminTickets).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findAllByText("TKT-20260724-RETRY1")).toHaveLength(2);
    expect(mockedListAdminTickets).toHaveBeenCalledTimes(3);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("removes old rows during failed filter loading and retries that filter once", async () => {
    const user = userEvent.setup();
    const filterRequest = deferred<AdminTicketListResponse>();
    mockedListAdminTickets
      .mockResolvedValueOnce(response())
      .mockReturnValueOnce(filterRequest.promise)
      .mockResolvedValueOnce(response([{ ...ticket, ticket_id: "TKT-20260724-CLOSED" }]));
    render(<TicketsPageClient />);
    await screen.findAllByText(ticket.ticket_id);

    await user.selectOptions(screen.getByLabelText("Status"), "closed");
    expect(screen.queryByText(ticket.ticket_id)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Status")).toHaveValue("closed");
    expect(screen.getByText("Loading support tickets")).toBeInTheDocument();
    filterRequest.reject(new AdminApiError(503, "TICKET_BACKEND_UNAVAILABLE", "private"));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(replace).toHaveBeenLastCalledWith("/admin/tickets?status=closed", { scroll: false });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findAllByText("TKT-20260724-CLOSED")).toHaveLength(2);
    expect(mockedListAdminTickets).toHaveBeenNthCalledWith(
      3,
      { status: "closed", limit: 25 },
      expect.any(AbortSignal),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps AbortError silent when Refresh is superseded by a filter", async () => {
    const user = userEvent.setup();
    const refresh = deferred<AdminTicketListResponse>();
    mockedListAdminTickets
      .mockResolvedValueOnce(response())
      .mockReturnValueOnce(refresh.promise)
      .mockResolvedValueOnce(response([{ ...ticket, ticket_id: "TKT-20260724-LATEST" }]));
    render(<TicketsPageClient />);
    await user.click(await screen.findByRole("button", { name: "Refresh" }));
    await user.selectOptions(screen.getByLabelText("Priority"), "urgent");
    refresh.reject(new DOMException("synthetic abort detail", "AbortError"));

    expect(await screen.findAllByText("TKT-20260724-LATEST")).toHaveLength(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("synthetic abort detail")).not.toBeInTheDocument();
  });

  it("recovers from INVALID_CURSOR on a later page with one cursorless request", async () => {
    query = "status=open";
    const user = userEvent.setup();
    mockedListAdminTickets
      .mockResolvedValueOnce(response([ticket], "synthetic.invalid.cursor"))
      .mockRejectedValueOnce(new AdminApiError(
        400,
        "INVALID_CURSOR",
        "private",
      ))
      .mockResolvedValueOnce(response());
    render(<TicketsPageClient />);
    await user.click(await screen.findByRole("button", { name: "Next page" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This ticket page has expired or is no longer valid.",
    );
    expect(mockedListAdminTickets).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "Return to first page" }));

    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenCalledTimes(3));
    expect(mockedListAdminTickets).toHaveBeenNthCalledWith(
      3,
      { status: "open", limit: 25 },
      expect.any(AbortSignal),
    );
    expect(screen.getByLabelText("Status")).toHaveValue("open");
    expect(replace.mock.calls.flat().join(" ")).not.toContain("synthetic.invalid.cursor");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("recovers from page-one INVALID_CURSOR with one cursorless request", async () => {
    const user = userEvent.setup();
    mockedListAdminTickets
      .mockRejectedValueOnce(new AdminApiError(400, "INVALID_CURSOR", "private"))
      .mockResolvedValueOnce(response());
    render(<TicketsPageClient />);
    await user.click(await screen.findByRole("button", { name: "Return to first page" }));

    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenCalledTimes(2));
    expect(mockedListAdminTickets).toHaveBeenNthCalledWith(
      2,
      { limit: 25 },
      expect.any(AbortSignal),
    );
    expect(screen.getByText("Page 1")).toBeInTheDocument();
  });

  it.each([
    ["TICKET_PAGINATION_STALLED", "Retry current page"],
    ["TICKET_BACKEND_UNAVAILABLE", "Retry"],
    ["TICKET_INTERNAL_ERROR", "Retry"],
    ["ADMIN_NETWORK_ERROR", "Retry"],
  ])("starts exactly one request when retrying %s", async (errorCode, buttonName) => {
    const user = userEvent.setup();
    mockedListAdminTickets
      .mockRejectedValueOnce(new AdminApiError(500, errorCode, "Safe retry message."))
      .mockResolvedValueOnce(response());
    render(<TicketsPageClient />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(mockedListAdminTickets).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: buttonName }));

    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("blocks duplicate Retry clicks while the retry request is pending", async () => {
    const user = userEvent.setup();
    const retry = deferred<AdminTicketListResponse>();
    mockedListAdminTickets
      .mockRejectedValueOnce(new AdminApiError(
        503,
        "TICKET_BACKEND_UNAVAILABLE",
        "private",
      ))
      .mockReturnValueOnce(retry.promise);
    render(<TicketsPageClient />);
    const retryButton = await screen.findByRole("button", { name: "Retry" });

    await user.click(retryButton);
    await user.click(retryButton);

    expect(mockedListAdminTickets).toHaveBeenCalledTimes(2);
    expect(retryButton).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    retry.resolve(response());
  });

  it("does not poll after a successful initial request", async () => {
    render(<TicketsPageClient />);
    await waitFor(() => expect(mockedListAdminTickets).toHaveBeenCalledTimes(1));

    vi.useFakeTimers();
    try {
      vi.advanceTimersByTime(60_000);
      expect(mockedListAdminTickets).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
