import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminApiError } from "@/lib/adminApi";
import { getAdminTicket } from "@/lib/adminTicketsApi";
import type {
  AdminTicketDetail,
  AdminTicketDetailResponse,
} from "@/lib/adminTicketTypes";
import TicketDetailPage from "@/app/admin/tickets/[ticketId]/page";
import { TicketDetailPageClient } from "@/app/admin/tickets/[ticketId]/TicketDetailPageClient";
import { formatDateTime } from "@/app/admin/orders/orderPresentation";

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/tickets/TKT-20260724-ABC123",
}));

vi.mock("@/lib/adminTicketsApi", () => ({
  getAdminTicket: vi.fn(),
}));

const mockedGetAdminTicket = vi.mocked(getAdminTicket);

const detail: AdminTicketDetail = {
  ticket_id: "TKT-20260724-ABC123",
  user_id: "USR-SYNTHETIC-001",
  customer_id: "CUST-SYNTHETIC-001",
  customer_name: "Synthetic Customer",
  customer_phone: "+10000000000",
  ticket_type: "order_complaint",
  category: "Delivery concern",
  description: "First line\n  Preserved second line <script>unsafe()</script>",
  priority: "urgent",
  status: "in_review",
  order_id: "ORD-SYNTHETIC-001",
  order_status_snapshot: "out_for_delivery",
  source: "web",
  created_at: "2026-07-24T10:00:00+00:00",
  updated_at: "2026-07-24T11:00:00+00:00",
  status_history: [
    {
      previous_status: "open",
      new_status: "in_review",
      timestamp: "2026-07-24T10:30:00+00:00",
      actor: null,
      reason: null,
    },
    {
      previous_status: "in_review",
      new_status: "waiting_for_customer",
      timestamp: "2026-07-24T10:45:00+00:00",
      actor: "admin-synthetic",
      reason: "Asked for details",
    },
  ],
  priority_history: [
    {
      previous_priority: "normal",
      new_priority: "urgent",
      timestamp: "2026-07-24T10:35:00+00:00",
      actor: null,
      reason: null,
    },
  ],
  admin_notes: [
    {
      note_id: null,
      actor: null,
      timestamp: "2026-07-24T10:40:00+00:00",
      text: "  Preserve note\nline two",
    },
  ],
  version: 7,
  linked_order: {
    order_id: "ORD-SYNTHETIC-001",
    status: "out_for_delivery",
    fulfillment_method: null,
    total: 0,
    currency: "GBP",
    created_at: "2026-07-24T09:00:00+00:00",
    updated_at: "2026-07-24T10:50:00+00:00",
  },
};

function detailResponse(ticket: AdminTicketDetail = detail): AdminTicketDetailResponse {
  return { ticket };
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
  mockedGetAdminTicket.mockReset();
  mockedGetAdminTicket.mockResolvedValue(detailResponse());
});

describe("ticket detail route wrapper", () => {
  it("awaits and passes the unchanged logical ticket ID as the client key", async () => {
    const routeTicketId = "TKT/Unicode-\u0394 100%?#";
    const element = await TicketDetailPage({
      params: Promise.resolve({ ticketId: routeTicketId }),
    });

    expect(element.type).toBe(TicketDetailPageClient);
    expect(element.key).toBe(routeTicketId);
    expect(element.props).toEqual({ ticketId: routeTicketId });
  });
});

describe("ticket detail loading and header", () => {
  it("loads the route ticket ID with an AbortSignal and one announcement", async () => {
    const request = deferred<AdminTicketDetailResponse>();
    mockedGetAdminTicket.mockReturnValue(request.promise);
    const { container } = render(
      <TicketDetailPageClient ticketId="TKT-20260724-ABC123" />,
    );

    expect(mockedGetAdminTicket).toHaveBeenCalledWith(
      "TKT-20260724-ABC123",
      expect.any(AbortSignal),
    );
    expect(container.querySelector(".admin-ticket-detail-skeleton")).toBeInTheDocument();
    const announcements = [...container.querySelectorAll("[aria-live='polite']")]
      .filter((element) => element.textContent?.includes("Loading support ticket"));
    expect(announcements).toHaveLength(1);
    expect(screen.queryByText(detail.description!)).not.toBeInTheDocument();
  });

  it("renders the read-only header and no mutation controls", async () => {
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);

    expect(await screen.findByText(detail.ticket_id)).toBeInTheDocument();
    expect(screen.getByText("Order Complaint")).toBeInTheDocument();
    expect(screen.getByText("Delivery concern")).toBeInTheDocument();
    expect(screen.getByText("In Review")).toBeInTheDocument();
    expect(screen.getByText("Urgent")).toBeInTheDocument();
    expect(screen.getByText(formatDateTime(detail.created_at))).toBeInTheDocument();
    expect(screen.getByText(formatDateTime(detail.updated_at))).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Support Tickets" }))
      .toHaveAttribute("href", "/admin/tickets");
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
    for (const name of [
      "Resolve",
      "Close",
      "Change status",
      "Change priority",
      "Reopen",
      "Add note",
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("uses neutral visible labels and classes for malformed header enums", async () => {
    mockedGetAdminTicket.mockResolvedValue(detailResponse({
      ...detail,
      ticket_type: "runtime-ticket-type",
      status: "runtime-status",
      priority: "runtime-priority",
    } as unknown as AdminTicketDetail));
    const { container } = render(
      <TicketDetailPageClient ticketId={detail.ticket_id} />,
    );

    expect(await screen.findByText("Unknown ticket type")).toBeInTheDocument();
    expect(screen.getByText("Unknown status")).toHaveClass("is-status-neutral");
    expect(screen.getByText("Unknown priority")).toHaveClass("is-priority-neutral");
    expect(container).not.toHaveTextContent("runtime-ticket-type");
    expect(container).not.toHaveTextContent("runtime-status");
    expect(container).not.toHaveTextContent("runtime-priority");
  });
});

describe("ticket detail sections", () => {
  it("renders customer fields and a safe telephone link", async () => {
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const section = await screen.findByRole("region", { name: "Customer" });

    expect(within(section).getByText("CUST-SYNTHETIC-001")).toBeInTheDocument();
    expect(within(section).getByText("USR-SYNTHETIC-001")).toBeInTheDocument();
    expect(within(section).getByText("Synthetic Customer")).toBeInTheDocument();
    expect(within(section).getByRole("link", { name: "+10000000000" }))
      .toHaveAttribute("href", "tel:+10000000000");
  });

  it("uses customer fallbacks and does not link an unsafe phone", async () => {
    mockedGetAdminTicket.mockResolvedValue(detailResponse({
      ...detail,
      customer_id: null,
      customer_name: null,
      customer_phone: "123\njavascript:unsafe",
    }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const section = await screen.findByRole("region", { name: "Customer" });

    expect(within(section).getAllByText("Not provided")).toHaveLength(2);
    expect(within(section).getByText("123 javascript:unsafe")).toBeInTheDocument();
    expect(within(section).queryByRole("link")).not.toBeInTheDocument();
  });

  it("preserves description and note whitespace without interpreting HTML", async () => {
    const { container } = render(<TicketDetailPageClient ticketId={detail.ticket_id} />);

    const description = await screen.findByText((_, element) => (
      element?.classList.contains("admin-ticket-preserved-text") === true
      && element.textContent === detail.description
    ));
    expect(description).toHaveClass("admin-ticket-preserved-text");
    expect(container.querySelector("script")).not.toBeInTheDocument();
    const note = screen.getByText((_, element) => (
      element?.classList.contains("admin-ticket-preserved-text") === true
      && element.textContent === detail.admin_notes[0].text
    ));
    expect(note).toHaveClass("admin-ticket-preserved-text");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("uses safe fallbacks for blank descriptions and invalid timestamps", async () => {
    mockedGetAdminTicket.mockResolvedValue(detailResponse({
      ...detail,
      description: " \n ",
      created_at: "not-a-timestamp",
      updated_at: "also-not-a-timestamp",
    }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);

    expect(await screen.findByRole("region", { name: "Description" }))
      .toHaveTextContent("Not provided");
    expect(screen.getAllByText("—")).toHaveLength(2);
  });

  it("renders order context and the exact linked-order summary including zero", async () => {
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const context = await screen.findByRole("region", { name: "Order context" });
    const linked = screen.getByRole("region", { name: "Linked order details" });

    expect(within(context).getByText("ORD-SYNTHETIC-001")).toBeInTheDocument();
    expect(within(context).getByText("out_for_delivery")).toBeInTheDocument();
    expect(within(linked).getByText("ORD-SYNTHETIC-001")).toBeInTheDocument();
    expect(within(linked).getByText("out_for_delivery")).toBeInTheDocument();
    expect(within(linked).getByText("Not provided")).toBeInTheDocument();
    expect(within(linked).getByText(/GBP/)).toBeInTheDocument();
    expect(within(linked).getAllByText(/Jul/)).toHaveLength(2);
  });

  it("renders null order and linked-order states without error", async () => {
    mockedGetAdminTicket.mockResolvedValue(detailResponse({
      ...detail,
      order_id: null,
      order_status_snapshot: null,
      linked_order: null,
    }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);

    expect(await screen.findByText("No linked order")).toBeInTheDocument();
    expect(screen.getByText("Not available")).toBeInTheDocument();
    expect(screen.getByText("No linked order details are available")).toBeInTheDocument();
  });

  it("preserves history and note order with actor/reason/ID fallbacks", async () => {
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const statuses = await screen.findByRole("list", { name: "Status history entries" });
    const priorities = screen.getByRole("list", { name: "Priority history entries" });
    const notes = screen.getByRole("list", { name: "Internal note entries" });

    expect(within(statuses).getAllByRole("listitem")[0]).toHaveTextContent(
      "Open to In Review",
    );
    expect(within(statuses).getAllByRole("listitem")[1]).toHaveTextContent(
      "In Review to Waiting for Customer",
    );
    expect(within(statuses).getByText("System")).toBeInTheDocument();
    expect(within(statuses).getByText("No reason provided")).toBeInTheDocument();
    expect(within(priorities).getByText("Normal to Urgent")).toBeInTheDocument();
    expect(within(notes).getByText("Not assigned")).toBeInTheDocument();
    expect(within(notes).getByText("System")).toBeInTheDocument();
  });

  it("uses shared neutral labels for malformed history enums", async () => {
    mockedGetAdminTicket.mockResolvedValue(detailResponse({
      ...detail,
      status_history: [{
        ...detail.status_history[0],
        previous_status: "runtime-previous-status",
        new_status: "runtime-new-status",
      }],
      priority_history: [{
        ...detail.priority_history[0],
        previous_priority: "runtime-previous-priority",
        new_priority: "runtime-new-priority",
      }],
    } as unknown as AdminTicketDetail));
    const { container } = render(
      <TicketDetailPageClient ticketId={detail.ticket_id} />,
    );

    const statuses = await screen.findByRole("list", { name: "Status history entries" });
    const priorities = screen.getByRole("list", { name: "Priority history entries" });
    expect(statuses).toHaveTextContent("Unknown status to Unknown status");
    expect(priorities).toHaveTextContent("Unknown priority to Unknown priority");
    expect(container).not.toHaveTextContent("runtime-previous-status");
    expect(container).not.toHaveTextContent("runtime-new-status");
    expect(container).not.toHaveTextContent("runtime-previous-priority");
    expect(container).not.toHaveTextContent("runtime-new-priority");
  });

  it("renders history and notes empty states", async () => {
    mockedGetAdminTicket.mockResolvedValue(detailResponse({
      ...detail,
      status_history: [],
      priority_history: [],
      admin_notes: [],
    }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);

    expect(await screen.findByText("No status changes have been recorded.")).toBeInTheDocument();
    expect(screen.getByText("No priority changes have been recorded.")).toBeInTheDocument();
    expect(screen.getByText("No internal notes have been added.")).toBeInTheDocument();
  });

  it("projects only approved metadata despite runtime extras", async () => {
    mockedGetAdminTicket.mockResolvedValue(detailResponse({
      ...detail,
      PK: "PRIVATE-PK",
      SK: "PRIVATE-SK",
      session_id: "PRIVATE-SESSION",
      request_id: "PRIVATE-REQUEST",
      idempotency_key: "PRIVATE-IDEMPOTENCY",
    } as AdminTicketDetail));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const metadata = await screen.findByRole("region", { name: "Metadata" });

    expect(within(metadata).getByText("web")).toBeInTheDocument();
    expect(within(metadata).getByText("7")).toBeInTheDocument();
    expect(screen.queryByText(/PRIVATE-/)).not.toBeInTheDocument();
  });
});

describe("ticket detail errors", () => {
  it("shows confirmed not-found behavior without Retry", async () => {
    mockedGetAdminTicket.mockRejectedValue(new AdminApiError(
      404,
      "TICKET_NOT_FOUND",
      "private",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Ticket not found");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "This support ticket does not exist or is no longer available.",
    );
    expect(screen.getByRole("link", { name: "Back to Support Tickets" }))
      .toHaveAttribute("href", "/admin/tickets");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it.each([
    ["TICKET_DATA_INVALID", "This ticket cannot be safely displayed."],
    ["TICKET_BACKEND_UNAVAILABLE", "Support tickets are temporarily unavailable."],
    ["TICKET_INTERNAL_ERROR", "This support ticket could not be loaded safely."],
    ["ADMIN_NETWORK_ERROR", "The administrator service could not be reached."],
  ])("shows sanitized retryable behavior for %s", async (errorCode, message) => {
    mockedGetAdminTicket.mockRejectedValueOnce(new AdminApiError(500, errorCode, "raw-private"))
      .mockResolvedValueOnce(detailResponse());
    const user = userEvent.setup();
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("alert")).not.toHaveTextContent("raw-private");
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(detail.ticket_id)).toBeInTheDocument();
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps 401 quiet without Retry", async () => {
    mockedGetAdminTicket.mockRejectedValue(new AdminApiError(
      401,
      "ADMIN_AUTHENTICATION_REQUIRED",
      "private",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);

    await waitFor(() => expect(mockedGetAdminTicket).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("sanitizes unrecognized administrator errors", async () => {
    mockedGetAdminTicket.mockRejectedValue(new AdminApiError(
      409,
      "SYNTHETIC_PRIVATE_FAILURE",
      "PRIVATE-PK raw backend body",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The support ticket could not be loaded.");
    expect(alert).not.toHaveTextContent("PRIVATE-PK");
    expect(alert).not.toHaveTextContent("raw backend body");
  });
});

describe("ticket detail request lifecycle and Refresh", () => {
  it("synchronously hides every old-ticket section while the new route loads", async () => {
    const nextRoute = deferred<AdminTicketDetailResponse>();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockReturnValueOnce(nextRoute.promise);
    const { container, rerender } = render(
      <TicketDetailPageClient ticketId="TKT-20260724-ABC123" />,
    );
    await screen.findByText("TKT-20260724-ABC123");

    rerender(<TicketDetailPageClient ticketId="TKT-20260724-DEF456" />);

    for (const oldValue of [
      "TKT-20260724-ABC123",
      detail.description!,
      "Synthetic Customer",
      "ORD-SYNTHETIC-001",
      "Asked for details",
      "Preserve note",
    ]) {
      expect(screen.queryByText(oldValue, { exact: false })).not.toBeInTheDocument();
    }
    expect(container.querySelector(".admin-ticket-detail-skeleton")).toBeInTheDocument();

    nextRoute.resolve(detailResponse({
      ...detail,
      ticket_id: "TKT-20260724-DEF456",
      category: "Ticket B category",
    }));
    expect(await screen.findByText("TKT-20260724-DEF456")).toBeInTheDocument();
    expect(screen.queryByText("TKT-20260724-ABC123")).not.toBeInTheDocument();
  });

  it("never restores old detail when the new route fails", async () => {
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockRejectedValueOnce(new AdminApiError(
        503,
        "TICKET_BACKEND_UNAVAILABLE",
        "private route failure",
      ));
    const { rerender } = render(
      <TicketDetailPageClient ticketId="TKT-20260724-ABC123" />,
    );
    await screen.findByText("TKT-20260724-ABC123");

    rerender(<TicketDetailPageClient ticketId="TKT-20260724-DEF456" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Support tickets are temporarily unavailable.",
    );
    expect(screen.queryByText("TKT-20260724-ABC123")).not.toBeInTheDocument();
    expect(screen.queryByText(detail.description!)).not.toBeInTheDocument();
  });

  it("aborts the old parameter request and ignores its late response", async () => {
    const first = deferred<AdminTicketDetailResponse>();
    const second = deferred<AdminTicketDetailResponse>();
    mockedGetAdminTicket
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { rerender } = render(
      <TicketDetailPageClient ticketId="TKT-20260724-ABC123" />,
    );
    const firstSignal = mockedGetAdminTicket.mock.calls[0][1]!;

    rerender(<TicketDetailPageClient ticketId="TKT-20260724-DEF456" />);
    expect(firstSignal.aborted).toBe(true);
    second.resolve(detailResponse({ ...detail, ticket_id: "TKT-20260724-DEF456" }));
    expect(await screen.findByText("TKT-20260724-DEF456")).toBeInTheDocument();
    first.resolve(detailResponse());
    await Promise.resolve();
    expect(screen.queryByText("TKT-20260724-ABC123")).not.toBeInTheDocument();
  });

  it("ignores an AdminApiError rejected by a stale route request", async () => {
    const first = deferred<AdminTicketDetailResponse>();
    const second = deferred<AdminTicketDetailResponse>();
    mockedGetAdminTicket
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { rerender } = render(
      <TicketDetailPageClient ticketId="TKT-20260724-ABC123" />,
    );

    rerender(<TicketDetailPageClient ticketId="TKT-20260724-DEF456" />);
    second.resolve(detailResponse({
      ...detail,
      ticket_id: "TKT-20260724-DEF456",
      category: "Newest route",
    }));
    expect(await screen.findByText("Newest route")).toBeInTheDocument();
    first.reject(new AdminApiError(500, "TICKET_INTERNAL_ERROR", "stale private error"));
    await Promise.resolve();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.getByText("TKT-20260724-DEF456")).toBeInTheDocument();
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(2);
  });

  it("keeps current detail during Refresh and replaces the full response", async () => {
    const user = userEvent.setup();
    const refresh = deferred<AdminTicketDetailResponse>();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockReturnValueOnce(refresh.promise);
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    await screen.findByText(detail.ticket_id);

    await user.click(screen.getByRole("button", { name: "Refresh" }));

    expect(screen.getByText(detail.ticket_id)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refreshing ticket..." })).toBeDisabled();
    expect(screen.getAllByText("Refreshing ticket")).toHaveLength(1);
    refresh.resolve(detailResponse({
      ...detail,
      category: "Updated category",
      status_history: [],
      admin_notes: [],
    }));
    expect(await screen.findByText("Updated category")).toBeInTheDocument();
    expect(screen.getByText("No status changes have been recorded.")).toBeInTheDocument();
    expect(screen.getByText("No internal notes have been added.")).toBeInTheDocument();
  });

  it("aborts a Refresh on route change and ignores its late result", async () => {
    const user = userEvent.setup();
    const refresh = deferred<AdminTicketDetailResponse>();
    const nextRoute = deferred<AdminTicketDetailResponse>();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockReturnValueOnce(refresh.promise)
      .mockReturnValueOnce(nextRoute.promise);
    const { rerender } = render(
      <TicketDetailPageClient ticketId="TKT-20260724-ABC123" />,
    );
    await user.click(await screen.findByRole("button", { name: "Refresh" }));
    const refreshSignal = mockedGetAdminTicket.mock.calls[1][1]!;

    rerender(<TicketDetailPageClient ticketId="TKT-20260724-DEF456" />);
    expect(refreshSignal.aborted).toBe(true);
    nextRoute.resolve(detailResponse({
      ...detail,
      ticket_id: "TKT-20260724-DEF456",
      category: "Newest route",
    }));
    expect(await screen.findByText("Newest route")).toBeInTheDocument();
    refresh.resolve(detailResponse({ ...detail, category: "Stale refresh" }));
    await Promise.resolve();
    expect(screen.queryByText("Stale refresh")).not.toBeInTheDocument();
  });

  it("ignores a stale Refresh rejection after a newer route succeeds", async () => {
    const user = userEvent.setup();
    const refresh = deferred<AdminTicketDetailResponse>();
    const nextRoute = deferred<AdminTicketDetailResponse>();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockReturnValueOnce(refresh.promise)
      .mockReturnValueOnce(nextRoute.promise);
    const { rerender } = render(
      <TicketDetailPageClient ticketId="TKT-20260724-ABC123" />,
    );
    await user.click(await screen.findByRole("button", { name: "Refresh" }));

    rerender(<TicketDetailPageClient ticketId="TKT-20260724-DEF456" />);
    nextRoute.resolve(detailResponse({
      ...detail,
      ticket_id: "TKT-20260724-DEF456",
      category: "Newest route after refresh",
    }));
    expect(await screen.findByText("Newest route after refresh")).toBeInTheDocument();
    refresh.reject(new AdminApiError(
      503,
      "TICKET_BACKEND_UNAVAILABLE",
      "stale refresh error",
    ));
    await Promise.resolve();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.getByText("TKT-20260724-DEF456")).toBeInTheDocument();
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(3);
  });

  it("keeps the newer route loading after the superseded request aborts", async () => {
    const first = deferred<AdminTicketDetailResponse>();
    const second = deferred<AdminTicketDetailResponse>();
    mockedGetAdminTicket
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { container, rerender } = render(
      <TicketDetailPageClient ticketId="TKT-20260724-ABC123" />,
    );

    rerender(<TicketDetailPageClient ticketId="TKT-20260724-DEF456" />);
    first.reject(new DOMException("raw superseded abort", "AbortError"));
    await Promise.resolve();

    expect(container.querySelector(".admin-ticket-detail-skeleton")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeDisabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("raw superseded abort")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(2);

    second.resolve(detailResponse({
      ...detail,
      ticket_id: "TKT-20260724-DEF456",
    }));
    expect(await screen.findByText("TKT-20260724-DEF456")).toBeInTheDocument();
  });

  it("keeps the newer route loading after a superseded Refresh aborts", async () => {
    const user = userEvent.setup();
    const refresh = deferred<AdminTicketDetailResponse>();
    const nextRoute = deferred<AdminTicketDetailResponse>();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockReturnValueOnce(refresh.promise)
      .mockReturnValueOnce(nextRoute.promise);
    const { container, rerender } = render(
      <TicketDetailPageClient ticketId="TKT-20260724-ABC123" />,
    );
    await user.click(await screen.findByRole("button", { name: "Refresh" }));
    const refreshSignal = mockedGetAdminTicket.mock.calls[1][1]!;

    rerender(<TicketDetailPageClient ticketId="TKT-20260724-DEF456" />);
    expect(refreshSignal.aborted).toBe(true);
    refresh.reject(new DOMException("raw refresh abort", "AbortError"));
    await Promise.resolve();

    expect(container.querySelector(".admin-ticket-detail-skeleton")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeDisabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("raw refresh abort")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(3);

    nextRoute.resolve(detailResponse({
      ...detail,
      ticket_id: "TKT-20260724-DEF456",
    }));
    expect(await screen.findByText("TKT-20260724-DEF456")).toBeInTheDocument();
  });

  it("retains detail after failed Refresh and clears warning after one retry", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockRejectedValueOnce(new AdminApiError(
        503,
        "TICKET_BACKEND_UNAVAILABLE",
        "private",
      ))
      .mockResolvedValueOnce(detailResponse({ ...detail, category: "Recovered category" }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    await user.click(await screen.findByRole("button", { name: "Refresh" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(detail.ticket_id)).toBeInTheDocument();
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Recovered category")).toBeInTheDocument();
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(3);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps AbortError silent and aborts on unmount", async () => {
    const request = deferred<AdminTicketDetailResponse>();
    mockedGetAdminTicket.mockReturnValue(request.promise);
    const { unmount } = render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const signal = mockedGetAdminTicket.mock.calls[0][1]!;

    unmount();
    expect(signal.aborted).toBe(true);
    request.reject(new DOMException("synthetic abort detail", "AbortError"));
    await Promise.resolve();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not poll after successful loading", async () => {
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    await waitFor(() => expect(mockedGetAdminTicket).toHaveBeenCalledTimes(1));

    vi.useFakeTimers();
    try {
      vi.advanceTimersByTime(60_000);
      expect(mockedGetAdminTicket).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
