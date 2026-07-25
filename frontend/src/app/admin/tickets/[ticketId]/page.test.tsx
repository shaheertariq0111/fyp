import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminApiError } from "@/lib/adminApi";
import {
  addAdminTicketNote,
  getAdminTicket,
  reopenAdminTicket,
  updateAdminTicketPriority,
  updateAdminTicketStatus,
} from "@/lib/adminTicketsApi";
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
  addAdminTicketNote: vi.fn(),
  getAdminTicket: vi.fn(),
  reopenAdminTicket: vi.fn(),
  updateAdminTicketPriority: vi.fn(),
  updateAdminTicketStatus: vi.fn(),
}));

const mockedAddAdminTicketNote = vi.mocked(addAdminTicketNote);
const mockedGetAdminTicket = vi.mocked(getAdminTicket);
const mockedReopenAdminTicket = vi.mocked(reopenAdminTicket);
const mockedUpdateAdminTicketPriority = vi.mocked(updateAdminTicketPriority);
const mockedUpdateAdminTicketStatus = vi.mocked(updateAdminTicketStatus);
const CONFLICT_TEXT = "This ticket changed while you were working.";

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
  mockedAddAdminTicketNote.mockReset();
  mockedGetAdminTicket.mockReset();
  mockedReopenAdminTicket.mockReset();
  mockedUpdateAdminTicketPriority.mockReset();
  mockedUpdateAdminTicketStatus.mockReset();
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
    const summary = screen.getByRole("region", { name: "Ticket summary" });
    expect(within(summary).getByText("Order Complaint")).toBeInTheDocument();
    expect(within(summary).getByText("Delivery concern")).toBeInTheDocument();
    expect(within(summary).getByText("In Review")).toBeInTheDocument();
    expect(within(summary).getByText("Urgent")).toBeInTheDocument();
    expect(within(summary).getByText(formatDateTime(detail.created_at))).toBeInTheDocument();
    expect(within(summary).getByText(formatDateTime(detail.updated_at))).toBeInTheDocument();
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

    const summary = await screen.findByRole("region", { name: "Ticket summary" });
    expect(within(summary).getByText("Unknown ticket type")).toBeInTheDocument();
    expect(within(summary).getByText("Unknown status")).toHaveClass("is-status-neutral");
    expect(within(summary).getByText("Unknown priority")).toHaveClass("is-priority-neutral");
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
    const notes = screen.getByRole("region", { name: "Internal notes" });
    expect(within(notes).queryByRole("textbox")).not.toBeInTheDocument();
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

describe("administrator ticket mutations", () => {
  it.each([
    ["open", ["in_review", "waiting_for_customer", "resolved", "closed"]],
    ["in_review", ["waiting_for_customer", "resolved", "closed"]],
    ["waiting_for_customer", ["in_review", "resolved", "closed"]],
    ["resolved", ["closed"]],
    ["closed", []],
  ] as const)("offers only backend status transitions from %s", async (
    currentStatus,
    expectedTargets,
  ) => {
    mockedGetAdminTicket.mockResolvedValue(detailResponse({
      ...detail,
      status: currentStatus,
    }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);

    const select = await screen.findByRole("combobox", { name: "Target status" });
    const values = within(select).getAllByRole("option")
      .map((option) => (option as HTMLOptionElement).value)
      .filter(Boolean);
    expect(values).toEqual(expectedTargets);
    expect(values).not.toContain(currentStatus);
    if (currentStatus === "resolved" || currentStatus === "closed") {
      expect(screen.getByRole("region", { name: "Reopen ticket" })).toBeInTheDocument();
    } else {
      expect(screen.queryByRole("region", { name: "Reopen ticket" }))
        .not.toBeInTheDocument();
    }
  });

  it("enforces required status reasons and preserves optional reason whitespace", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket.mockResolvedValue(detailResponse({
      ...detail,
      status: "waiting_for_customer",
    }));
    mockedUpdateAdminTicketStatus.mockResolvedValue(detailResponse({
      ...detail,
      status: "in_review",
      version: 8,
    }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const target = await screen.findByRole("combobox", { name: "Target status" });
    const reason = screen.getByRole("textbox", { name: /Status reason/ });

    await user.selectOptions(target, "resolved");
    await user.click(screen.getByRole("button", { name: "Save status" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "A reason is required for this status change.",
    );
    expect(mockedUpdateAdminTicketStatus).not.toHaveBeenCalled();

    await user.selectOptions(target, "in_review");
    fireEvent.change(reason, { target: { value: "  Optional reason.\n" } });
    await user.click(screen.getByRole("button", { name: "Save status" }));

    expect(mockedUpdateAdminTicketStatus).toHaveBeenCalledWith(
      detail.ticket_id,
      {
        status: "in_review",
        reason: "  Optional reason.\n",
        expected_version: 7,
      },
      expect.any(AbortSignal),
    );
  });

  it("replaces the complete ticket after status success without local version changes", async () => {
    const user = userEvent.setup();
    mockedUpdateAdminTicketStatus.mockResolvedValue(detailResponse({
      ...detail,
      status: "resolved",
      version: 12,
      category: "Server replacement",
      status_history: [],
    }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);

    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Target status" }),
      "resolved",
    );
    fireEvent.change(screen.getByRole("textbox", { name: /Status reason/ }), {
      target: { value: "  Resolved exactly.\n" },
    });
    await user.click(screen.getByRole("button", { name: "Save status" }));

    expect(await screen.findByRole("status", { name: "Mutation success" }))
      .toHaveTextContent("Ticket status updated.");
    expect(screen.getByText("Server replacement")).toBeInTheDocument();
    expect(screen.getByText("No status changes have been recorded.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Metadata" })).toHaveTextContent("12");
    expect(screen.getByRole("combobox", { name: "Target status" })).toHaveValue("");
  });

  it("coordinates priority mutation and blocks same-priority or duplicate writes", async () => {
    const user = userEvent.setup();
    const pending = deferred<AdminTicketDetailResponse>();
    mockedUpdateAdminTicketPriority.mockReturnValue(pending.promise);
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const priority = await screen.findByRole("combobox", { name: "New priority" });
    const save = screen.getByRole("button", { name: "Save priority" });

    expect(within(priority).getAllByRole("option").map(
      (option) => (option as HTMLOptionElement).value,
    )).toEqual(["", "normal", "high", "urgent"]);
    await user.selectOptions(priority, "urgent");
    expect(save).toBeDisabled();
    await user.selectOptions(priority, "normal");
    fireEvent.change(screen.getByRole("textbox", { name: "Priority reason" }), {
      target: { value: "  Lowered.\n" },
    });
    await user.click(save);
    await user.click(save);

    expect(mockedUpdateAdminTicketPriority).toHaveBeenCalledTimes(1);
    expect(mockedUpdateAdminTicketPriority).toHaveBeenCalledWith(
      detail.ticket_id,
      {
        priority: "normal",
        reason: "  Lowered.\n",
        expected_version: 7,
      },
      expect.any(AbortSignal),
    );
    expect(screen.getByRole("button", { name: "Updating priority..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save status" })).toBeDisabled();

    pending.resolve(detailResponse({
      ...detail,
      priority: "normal",
      version: 8,
      priority_history: [],
    }));
    expect(await screen.findByText("Ticket priority updated.")).toBeInTheDocument();
    expect(screen.getByText("No priority changes have been recorded.")).toBeInTheDocument();
  });

  it("validates note length and sends only original note text and version", async () => {
    const user = userEvent.setup();
    mockedAddAdminTicketNote.mockResolvedValue(detailResponse({
      ...detail,
      version: 8,
      admin_notes: [],
    }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const note = await screen.findByRole("textbox", { name: "Internal note" });
    const submit = screen.getByRole("button", { name: "Add internal note" });

    expect(note).toHaveAttribute("maxLength", "2000");
    expect(screen.getByText("0 / 2000")).toBeInTheDocument();
    expect(submit).toBeDisabled();
    fireEvent.change(note, { target: { value: "   " } });
    expect(submit).toBeDisabled();
    fireEvent.change(note, { target: { value: "  Preserve note.\n" } });
    expect(screen.getByText("17 / 2000")).toBeInTheDocument();
    await user.click(submit);

    expect(mockedAddAdminTicketNote).toHaveBeenCalledWith(
      detail.ticket_id,
      {
        text: "  Preserve note.\n",
        expected_version: 7,
      },
      expect.any(AbortSignal),
    );
    expect(mockedAddAdminTicketNote.mock.calls[0][1]).toEqual({
      text: "  Preserve note.\n",
      expected_version: 7,
    });
    expect(await screen.findByText("Internal note added.")).toBeInTheDocument();
    expect(note).toHaveValue("");
    expect(screen.getByText("No internal notes have been added.")).toBeInTheDocument();
  });

  it.each([
    ["NOTE_REQUIRED", "An internal note is required."],
    ["NOTE_TOO_LONG", "The internal note cannot exceed 2,000 characters."],
    ["NOTE_ID_GENERATION_FAILED", "The change could not be confirmed."],
    ["TICKET_ITEM_TOO_LARGE", "This ticket is too large to save this change."],
  ])("keeps note text after %s", async (errorCode, message) => {
    const user = userEvent.setup();
    mockedAddAdminTicketNote.mockRejectedValue(new AdminApiError(
      errorCode === "NOTE_ID_GENERATION_FAILED" ? 503 : 400,
      errorCode,
      "raw private mutation error",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const note = await screen.findByRole("textbox", { name: "Internal note" });
    fireEvent.change(note, { target: { value: "  Retain me.\n" } });

    await user.click(screen.getByRole("button", { name: "Add internal note" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("alert")).not.toHaveTextContent("raw private mutation error");
    expect(note).toHaveValue("  Retain me.\n");
    expect(mockedAddAdminTicketNote).toHaveBeenCalledTimes(1);
  });

  it("reopens terminal tickets with exact input and hides reopening after success", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket.mockResolvedValue(detailResponse({
      ...detail,
      status: "resolved",
    }));
    mockedReopenAdminTicket.mockResolvedValue(detailResponse({
      ...detail,
      status: "open",
      version: 8,
    }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const section = await screen.findByRole("region", { name: "Reopen ticket" });
    const target = within(section).getByRole("combobox", { name: "Reopen as" });
    expect(within(target).getAllByRole("option").map(
      (option) => (option as HTMLOptionElement).value,
    )).toEqual(["", "open", "in_review"]);
    await user.selectOptions(target, "open");
    await user.click(within(section).getByRole("button", { name: "Reopen ticket" }));
    expect(within(section).getByRole("alert")).toHaveTextContent(
      "A reason is required to reopen this ticket.",
    );

    fireEvent.change(within(section).getByRole("textbox", { name: /Reopen reason/ }), {
      target: { value: "  Customer replied.\n" },
    });
    await user.click(within(section).getByRole("button", { name: "Reopen ticket" }));

    expect(mockedReopenAdminTicket).toHaveBeenCalledWith(
      detail.ticket_id,
      {
        target_status: "open",
        reason: "  Customer replied.\n",
        expected_version: 7,
      },
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("Ticket reopened.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Reopen ticket" }))
      .not.toBeInTheDocument();
  });

  it("recovers a status conflict without retrying and resubmits with the new version", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockResolvedValueOnce(detailResponse({
        ...detail,
        status: "waiting_for_customer",
        version: 9,
        category: "Recovered latest",
      }));
    mockedUpdateAdminTicketStatus
      .mockRejectedValueOnce(new AdminApiError(
        409,
        "TICKET_VERSION_CONFLICT",
        "raw conflict",
      ))
      .mockResolvedValueOnce(detailResponse({
        ...detail,
        status: "resolved",
        version: 10,
      }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Target status" }),
      "resolved",
    );
    const reason = screen.getByRole("textbox", { name: /Status reason/ });
    fireEvent.change(reason, { target: { value: "  Keep conflict draft.\n" } });

    await user.click(screen.getByRole("button", { name: "Save status" }));

    const guidance = await screen.findByRole("status", {
      name: "Conflict guidance",
    });
    expect(guidance).toHaveTextContent(
      "This ticket changed while you were working. The latest ticket has been loaded.",
    );
    expect(screen.getAllByRole("status", { name: "Conflict guidance" })).toHaveLength(1);
    expect(screen.getByText("Recovered latest")).toBeInTheDocument();
    expect(reason).toHaveValue("  Keep conflict draft.\n");
    expect(mockedUpdateAdminTicketStatus).toHaveBeenCalledTimes(1);
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "Save status" }));
    expect(mockedUpdateAdminTicketStatus).toHaveBeenCalledTimes(2);
    expect(mockedUpdateAdminTicketStatus.mock.calls[1][1]).toMatchObject({
      expected_version: 9,
    });
  });

  it("invalidates a preserved status target after conflict recovery", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse({ ...detail, status: "open" }))
      .mockResolvedValueOnce(detailResponse({
        ...detail,
        status: "resolved",
        version: 9,
      }));
    mockedUpdateAdminTicketStatus.mockRejectedValue(new AdminApiError(
      409,
      "TICKET_VERSION_CONFLICT",
      "private",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Target status" }),
      "waiting_for_customer",
    );
    const reason = screen.getByRole("textbox", { name: /Status reason/ });
    fireEvent.change(reason, { target: { value: "  Still useful.\n" } });

    await user.click(screen.getByRole("button", { name: "Save status" }));

    expect(await screen.findByText("This status target is no longer available."))
      .toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Target status" })).toHaveValue("");
    expect(reason).toHaveValue("  Still useful.\n");
  });

  it("preserves note and reopen drafts through conflict recovery", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse({ ...detail, status: "resolved" }))
      .mockResolvedValue(detailResponse({
        ...detail,
        status: "closed",
        version: 9,
      }));
    mockedAddAdminTicketNote.mockRejectedValueOnce(new AdminApiError(
      409,
      "TICKET_VERSION_CONFLICT",
      "private",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const note = await screen.findByRole("textbox", { name: "Internal note" });
    const reopenReason = screen.getByRole("textbox", { name: /Reopen reason/ });
    fireEvent.change(note, { target: { value: "  Note draft.\n" } });
    fireEvent.change(reopenReason, { target: { value: "  Reopen draft.\n" } });

    await user.click(screen.getByRole("button", { name: "Add internal note" }));

    expect(await screen.findByText(
      "This ticket changed while you were working.",
      { exact: false },
    ))
      .toBeInTheDocument();
    expect(note).toHaveValue("  Note draft.\n");
    expect(screen.getByRole("textbox", { name: /Reopen reason/ }))
      .toHaveValue("  Reopen draft.\n");
    expect(mockedAddAdminTicketNote).toHaveBeenCalledTimes(1);
  });

  it("recovers a reopen conflict and resubmits explicitly with the refreshed version", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse({ ...detail, status: "closed" }))
      .mockResolvedValueOnce(detailResponse({
        ...detail,
        status: "resolved",
        version: 9,
      }));
    mockedReopenAdminTicket
      .mockRejectedValueOnce(new AdminApiError(
        409,
        "TICKET_VERSION_CONFLICT",
        "private",
      ))
      .mockResolvedValueOnce(detailResponse({
        ...detail,
        status: "in_review",
        version: 10,
      }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const section = await screen.findByRole("region", { name: "Reopen ticket" });
    await user.selectOptions(
      within(section).getByRole("combobox", { name: "Reopen as" }),
      "in_review",
    );
    fireEvent.change(
      within(section).getByRole("textbox", { name: /Reopen reason/ }),
      { target: { value: "  Reopen after review.\n" } },
    );

    await user.click(within(section).getByRole("button", { name: "Reopen ticket" }));

    expect(await screen.findByText(CONFLICT_TEXT, { exact: false }))
      .toBeInTheDocument();
    expect(mockedReopenAdminTicket).toHaveBeenCalledTimes(1);
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(2);
    const recoveredSection = screen.getByRole("region", { name: "Reopen ticket" });
    expect(within(recoveredSection).getByRole("textbox", { name: /Reopen reason/ }))
      .toHaveValue("  Reopen after review.\n");

    await user.click(
      within(recoveredSection).getByRole("button", { name: "Reopen ticket" }),
    );

    expect(mockedReopenAdminTicket).toHaveBeenCalledTimes(2);
    expect(mockedReopenAdminTicket.mock.calls[1][1]).toEqual({
      target_status: "in_review",
      reason: "  Reopen after review.\n",
      expected_version: 9,
    });
  });

  it("disables a preserved priority target that matches conflict recovery data", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockResolvedValueOnce(detailResponse({
        ...detail,
        priority: "normal",
        version: 9,
      }));
    mockedUpdateAdminTicketPriority.mockRejectedValue(new AdminApiError(
      409,
      "TICKET_VERSION_CONFLICT",
      "private",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "New priority" }),
      "normal",
    );
    await user.click(screen.getByRole("button", { name: "Save priority" }));

    expect(await screen.findByText(
      "This ticket changed while you were working.",
      { exact: false },
    ))
      .toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "New priority" })).toHaveValue("normal");
    expect(screen.getByRole("button", { name: "Save priority" })).toBeDisabled();
    expect(mockedUpdateAdminTicketPriority).toHaveBeenCalledTimes(1);
  });

  it("keeps conflict state safe when the recovery GET fails", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockRejectedValueOnce(new AdminApiError(
        503,
        "TICKET_BACKEND_UNAVAILABLE",
        "private recovery failure",
      ));
    mockedAddAdminTicketNote.mockRejectedValue(new AdminApiError(
      409,
      "TICKET_VERSION_CONFLICT",
      "private",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const note = await screen.findByRole("textbox", { name: "Internal note" });
    fireEvent.change(note, { target: { value: "Preserved recovery draft" } });
    await user.click(screen.getByRole("button", { name: "Add internal note" }));

    const guidance = await screen.findByRole("status", {
      name: "Conflict guidance",
    });
    expect(guidance).toHaveTextContent(
      "This ticket changed while you were working, but the latest ticket "
      + "could not be loaded. Refresh the ticket before submitting another change.",
    );
    expect(note).toHaveValue("Preserved recovery draft");
    expect(screen.getByText(detail.ticket_id)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
  });

  it("uses cautious ambiguous-outcome messaging without retrying", async () => {
    const user = userEvent.setup();
    mockedAddAdminTicketNote.mockRejectedValue(new AdminApiError(
      0,
      "ADMIN_NETWORK_ERROR",
      "raw network failure",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const note = await screen.findByRole("textbox", { name: "Internal note" });
    fireEvent.change(note, { target: { value: "Unconfirmed note" } });
    await user.click(screen.getByRole("button", { name: "Add internal note" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The change could not be confirmed. Refresh the ticket before trying again.",
    );
    expect(note).toHaveValue("Unconfirmed note");
    expect(mockedAddAdminTicketNote).toHaveBeenCalledTimes(1);
    expect(screen.getByText(detail.ticket_id)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
  });

  it("maps status domain errors to a fixed form-specific message", async () => {
    const user = userEvent.setup();
    mockedUpdateAdminTicketStatus.mockRejectedValue(new AdminApiError(
      409,
      "INVALID_TICKET_TRANSITION",
      "raw status failure",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Target status" }),
      "waiting_for_customer",
    );
    fireEvent.change(screen.getByRole("textbox", { name: /Status reason/ }), {
      target: { value: "Required reason" },
    });
    await user.click(screen.getByRole("button", { name: "Save status" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "This ticket transition is no longer available. Refresh the ticket and review it.",
    );
    expect(alert).not.toHaveTextContent("raw status failure");
  });

  it("maps NOTE_REQUIRED according to the mutation action", async () => {
    const user = userEvent.setup();
    mockedUpdateAdminTicketStatus.mockRejectedValueOnce(new AdminApiError(
      400,
      "NOTE_REQUIRED",
      "raw status reason failure",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Target status" }),
      "waiting_for_customer",
    );
    fireEvent.change(screen.getByRole("textbox", { name: /Status reason/ }), {
      target: { value: "Sent but rejected" },
    });

    await user.click(screen.getByRole("button", { name: "Save status" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("A reason is required for this status change.");
    expect(alert).not.toHaveTextContent("raw status reason failure");
    expect(alert).not.toHaveTextContent("internal note");
  });

  it("uses a neutral NOTE_REQUIRED message for an unexpected priority response", async () => {
    const user = userEvent.setup();
    mockedUpdateAdminTicketPriority.mockRejectedValueOnce(new AdminApiError(
      400,
      "NOTE_REQUIRED",
      "raw priority reason failure",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "New priority" }),
      "normal",
    );

    await user.click(screen.getByRole("button", { name: "Save priority" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Complete the required information before submitting.");
    expect(alert).not.toHaveTextContent("raw priority reason failure");
    expect(alert).not.toHaveTextContent("internal note");
  });

  it("maps REOPEN_REASON_REQUIRED near the reopen form", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket.mockResolvedValue(detailResponse({
      ...detail,
      status: "resolved",
    }));
    mockedReopenAdminTicket.mockRejectedValueOnce(new AdminApiError(
      400,
      "REOPEN_REASON_REQUIRED",
      "raw reopen reason failure",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const section = await screen.findByRole("region", { name: "Reopen ticket" });
    await user.selectOptions(
      within(section).getByRole("combobox", { name: "Reopen as" }),
      "open",
    );
    fireEvent.change(within(section).getByRole("textbox", { name: /Reopen reason/ }), {
      target: { value: "Sent but rejected" },
    });

    await user.click(within(section).getByRole("button", { name: "Reopen ticket" }));

    const alert = await within(section).findByRole("alert");
    expect(alert).toHaveTextContent("A reason is required to reopen this ticket.");
    expect(alert).not.toHaveTextContent("raw reopen reason failure");
  });

  it("maps priority domain errors to a fixed form-specific message", async () => {
    const user = userEvent.setup();
    mockedUpdateAdminTicketPriority.mockRejectedValue(new AdminApiError(
      400,
      "INVALID_TICKET_PRIORITY",
      "raw priority failure",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "New priority" }),
      "normal",
    );

    await user.click(screen.getByRole("button", { name: "Save priority" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Select a valid ticket priority.");
    expect(alert).not.toHaveTextContent("raw priority failure");
  });

  it("maps reopen domain errors to a fixed form-specific message", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket.mockResolvedValue(detailResponse({
      ...detail,
      status: "resolved",
    }));
    mockedReopenAdminTicket.mockRejectedValue(new AdminApiError(
      400,
      "INVALID_REOPEN_TARGET",
      "raw reopen failure",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const section = await screen.findByRole("region", { name: "Reopen ticket" });
    await user.selectOptions(
      within(section).getByRole("combobox", { name: "Reopen as" }),
      "open",
    );
    fireEvent.change(
      within(section).getByRole("textbox", { name: /Reopen reason/ }),
      { target: { value: "Required reason" } },
    );

    await user.click(within(section).getByRole("button", { name: "Reopen ticket" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Select open or in review as the reopen status.");
    expect(alert).not.toHaveTextContent("raw reopen failure");
  });

  it("moves to the not-found state when a mutation reports a missing ticket", async () => {
    const user = userEvent.setup();
    mockedAddAdminTicketNote.mockRejectedValue(new AdminApiError(
      404,
      "TICKET_NOT_FOUND",
      "raw missing record",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    fireEvent.change(
      await screen.findByRole("textbox", { name: "Internal note" }),
      { target: { value: "Do not retain a mutable page" } },
    );

    await user.click(screen.getByRole("button", { name: "Add internal note" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Ticket not found");
    expect(alert).not.toHaveTextContent("raw missing record");
    expect(screen.queryByRole("region", { name: "Administrator actions" }))
      .not.toBeInTheDocument();
  });

  it("ignores conflict recovery after the route changes", async () => {
    const user = userEvent.setup();
    const recovery = deferred<AdminTicketDetailResponse>();
    const nextRoute = deferred<AdminTicketDetailResponse>();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockReturnValueOnce(recovery.promise)
      .mockReturnValueOnce(nextRoute.promise);
    mockedAddAdminTicketNote.mockRejectedValueOnce(new AdminApiError(
      409,
      "TICKET_VERSION_CONFLICT",
      "private",
    ));
    const { rerender } = render(
      <TicketDetailPageClient ticketId="TKT-20260724-ABC123" />,
    );
    fireEvent.change(
      await screen.findByRole("textbox", { name: "Internal note" }),
      { target: { value: "Old conflict draft" } },
    );
    await user.click(screen.getByRole("button", { name: "Add internal note" }));
    await waitFor(() => expect(mockedGetAdminTicket).toHaveBeenCalledTimes(2));
    const recoverySignal = mockedGetAdminTicket.mock.calls[1][1]!;

    rerender(<TicketDetailPageClient ticketId="TKT-20260724-DEF456" />);
    expect(recoverySignal.aborted).toBe(true);
    nextRoute.resolve(detailResponse({
      ...detail,
      ticket_id: "TKT-20260724-DEF456",
      category: "Newest route",
    }));
    recovery.resolve(detailResponse({
      ...detail,
      category: "Stale conflict recovery",
      version: 9,
    }));

    expect(await screen.findByText("Newest route")).toBeInTheDocument();
    expect(screen.queryByText("Stale conflict recovery")).not.toBeInTheDocument();
    expect(screen.queryByText(CONFLICT_TEXT, { exact: false })).not.toBeInTheDocument();
  });

  it.each([
    [400, "TICKET_VERSION_CONFLICT", "The ticket change could not be completed."],
    [500, "TICKET_VERSION_CONFLICT", "The change could not be confirmed."],
    [409, "ANOTHER_CONFLICT", "The ticket change could not be completed."],
  ])(
    "does not recover mismatched conflict status %s and code %s",
    async (status, errorCode, expectedMessage) => {
      const user = userEvent.setup();
      mockedAddAdminTicketNote.mockRejectedValueOnce(new AdminApiError(
        status,
        errorCode,
        "raw mismatched conflict",
      ));
      render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
      const note = await screen.findByRole("textbox", { name: "Internal note" });
      fireEvent.change(note, { target: { value: "Preserve mismatch draft" } });

      await user.click(screen.getByRole("button", { name: "Add internal note" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(expectedMessage);
      expect(screen.getByRole("alert")).not.toHaveTextContent("raw mismatched conflict");
      expect(note).toHaveValue("Preserve mismatch draft");
      expect(mockedAddAdminTicketNote).toHaveBeenCalledTimes(1);
      expect(mockedGetAdminTicket).toHaveBeenCalledTimes(1);
    },
  );

  it("retains failed-recovery guidance and drafts while manual Refresh is pending", async () => {
    const user = userEvent.setup();
    const refresh = deferred<AdminTicketDetailResponse>();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockRejectedValueOnce(new AdminApiError(
        503,
        "TICKET_BACKEND_UNAVAILABLE",
        "private recovery failure",
      ))
      .mockReturnValueOnce(refresh.promise);
    mockedAddAdminTicketNote.mockRejectedValueOnce(new AdminApiError(
      409,
      "TICKET_VERSION_CONFLICT",
      "private conflict",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const note = await screen.findByRole("textbox", { name: "Internal note" });
    fireEvent.change(note, { target: { value: "Pending refresh draft" } });
    await user.click(screen.getByRole("button", { name: "Add internal note" }));
    const guidance = await screen.findByRole("status", {
      name: "Conflict guidance",
    });
    expect(guidance).toHaveTextContent("the latest ticket could not be loaded");

    await user.click(screen.getByRole("button", { name: "Refresh" }));

    expect(screen.getByRole("button", { name: "Refreshing ticket..." })).toBeDisabled();
    expect(screen.getByRole("status", { name: "Conflict guidance" }))
      .toHaveTextContent("the latest ticket could not be loaded");
    expect(note).toHaveValue("Pending refresh draft");
    expect(mockedAddAdminTicketNote).toHaveBeenCalledTimes(1);
  });

  it("clears conflict guidance only after successful manual Refresh", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockRejectedValueOnce(new AdminApiError(
        503,
        "TICKET_BACKEND_UNAVAILABLE",
        "private recovery failure",
      ))
      .mockResolvedValueOnce(detailResponse({
        ...detail,
        category: "Latest after Refresh",
        version: 12,
      }));
    mockedAddAdminTicketNote
      .mockRejectedValueOnce(new AdminApiError(
        409,
        "TICKET_VERSION_CONFLICT",
        "private conflict",
      ))
      .mockResolvedValueOnce(detailResponse({
        ...detail,
        version: 13,
      }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const note = await screen.findByRole("textbox", { name: "Internal note" });
    fireEvent.change(note, { target: { value: "Refresh then submit" } });
    await user.click(screen.getByRole("button", { name: "Add internal note" }));
    await screen.findByRole("status", { name: "Conflict guidance" });

    await user.click(screen.getByRole("button", { name: "Refresh" }));

    expect(await screen.findByText("Latest after Refresh")).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "Conflict guidance" }))
      .not.toBeInTheDocument();
    expect(note).toHaveValue("Refresh then submit");
    expect(mockedAddAdminTicketNote).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Add internal note" }));
    expect(mockedAddAdminTicketNote).toHaveBeenCalledTimes(2);
    expect(mockedAddAdminTicketNote.mock.calls[1][1]).toMatchObject({
      expected_version: 12,
    });
  });

  it("retains conflict guidance and drafts when manual Refresh fails", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockRejectedValueOnce(new AdminApiError(
        503,
        "TICKET_BACKEND_UNAVAILABLE",
        "private recovery failure",
      ))
      .mockRejectedValueOnce(new AdminApiError(
        500,
        "TICKET_INTERNAL_ERROR",
        "private refresh failure",
      ));
    mockedAddAdminTicketNote.mockRejectedValueOnce(new AdminApiError(
      409,
      "TICKET_VERSION_CONFLICT",
      "private conflict",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const note = await screen.findByRole("textbox", { name: "Internal note" });
    fireEvent.change(note, { target: { value: "Failed refresh draft" } });
    await user.click(screen.getByRole("button", { name: "Add internal note" }));
    await screen.findByRole("status", { name: "Conflict guidance" });

    await user.click(screen.getByRole("button", { name: "Refresh" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This support ticket could not be loaded safely.",
    );
    expect(screen.getByRole("status", { name: "Conflict guidance" }))
      .toHaveTextContent("the latest ticket could not be loaded");
    expect(note).toHaveValue("Failed refresh draft");
    expect(screen.getByText(detail.ticket_id)).toBeInTheDocument();
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(3);
    expect(mockedAddAdminTicketNote).toHaveBeenCalledTimes(1);
  });

  it("exposes required reason semantics without overriding visible labels", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket.mockResolvedValueOnce(detailResponse({
      ...detail,
      status: "waiting_for_customer",
    }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const statusReason = await screen.findByRole("textbox", {
      name: "Status reason (optional)",
    });
    expect(statusReason).not.toHaveAttribute("aria-required", "true");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Target status" }),
      "resolved",
    );
    expect(screen.getByRole("textbox", { name: "Status reason (required)" }))
      .toHaveAttribute("aria-required", "true");

    await user.selectOptions(
      screen.getByRole("combobox", { name: "Target status" }),
      "in_review",
    );
    expect(screen.getByRole("textbox", { name: "Status reason (optional)" }))
      .not.toHaveAttribute("aria-required", "true");
  });

  it("marks reopen reason required and keeps validation as an alert", async () => {
    const user = userEvent.setup();
    mockedGetAdminTicket.mockResolvedValueOnce(detailResponse({
      ...detail,
      status: "resolved",
    }));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const section = await screen.findByRole("region", { name: "Reopen ticket" });
    const reason = within(section).getByRole("textbox", {
      name: "Reopen reason (required)",
    });
    expect(reason).toHaveAttribute("aria-required", "true");
    await user.selectOptions(
      within(section).getByRole("combobox", { name: "Reopen as" }),
      "open",
    );
    await user.click(within(section).getByRole("button", { name: "Reopen ticket" }));
    expect(within(section).getByRole("alert")).toHaveTextContent(
      "A reason is required to reopen this ticket.",
    );
    expect(reason).toHaveAttribute("aria-invalid", "true");
  });

  it("blocks programmatic notes over 2,000 characters without truncation", async () => {
    const user = userEvent.setup();
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const note = await screen.findByRole("textbox", { name: "Internal note" });
    const overlong = "x".repeat(2_001);
    fireEvent.change(note, { target: { value: overlong } });

    await user.click(screen.getByRole("button", { name: "Add internal note" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "The internal note cannot exceed 2,000 characters.",
    );
    expect(note).toHaveValue(overlong);
    expect(screen.getByText("2001 / 2000")).toBeInTheDocument();
    expect(mockedAddAdminTicketNote).not.toHaveBeenCalled();
  });

  it.each([
    [500, "TICKET_INTERNAL_ERROR"],
    [503, "TICKET_BACKEND_UNAVAILABLE"],
  ])("treats HTTP %s mutation failures as ambiguous", async (status, errorCode) => {
    const user = userEvent.setup();
    mockedAddAdminTicketNote.mockRejectedValueOnce(new AdminApiError(
      status,
      errorCode,
      "raw ambiguous backend text",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    const note = await screen.findByRole("textbox", { name: "Internal note" });
    fireEvent.change(note, { target: { value: `Retain after ${status}` } });

    await user.click(screen.getByRole("button", { name: "Add internal note" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The change could not be confirmed. Refresh the ticket before trying again.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent(
      "raw ambiguous backend text",
    );
    expect(note).toHaveValue(`Retain after ${status}`);
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(1);
    expect(mockedAddAdminTicketNote).toHaveBeenCalledTimes(1);
  });

  it("keeps a status draft after an ambiguous HTTP 500 response", async () => {
    const user = userEvent.setup();
    mockedUpdateAdminTicketStatus.mockRejectedValueOnce(new AdminApiError(
      500,
      "TICKET_INTERNAL_ERROR",
      "raw status backend text",
    ));
    render(<TicketDetailPageClient ticketId={detail.ticket_id} />);
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Target status" }),
      "waiting_for_customer",
    );
    const reason = screen.getByRole("textbox", { name: /Status reason/ });
    fireEvent.change(reason, { target: { value: "  Keep status draft.\n" } });

    await user.click(screen.getByRole("button", { name: "Save status" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The change could not be confirmed. Refresh the ticket before trying again.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent(
      "raw status backend text",
    );
    expect(reason).toHaveValue("  Keep status draft.\n");
    expect(screen.getByText(detail.ticket_id)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(1);
    expect(mockedUpdateAdminTicketStatus).toHaveBeenCalledTimes(1);
  });

  it("ignores a stale mutation rejection after the next route succeeds", async () => {
    const user = userEvent.setup();
    const mutation = deferred<AdminTicketDetailResponse>();
    const nextRoute = deferred<AdminTicketDetailResponse>();
    mockedAddAdminTicketNote.mockReturnValueOnce(mutation.promise);
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockReturnValueOnce(nextRoute.promise);
    const { rerender } = render(
      <TicketDetailPageClient ticketId="TKT-20260724-ABC123" />,
    );
    fireEvent.change(
      await screen.findByRole("textbox", { name: "Internal note" }),
      { target: { value: "Old rejected draft" } },
    );
    await user.click(screen.getByRole("button", { name: "Add internal note" }));

    rerender(<TicketDetailPageClient ticketId="TKT-20260724-DEF456" />);
    nextRoute.resolve(detailResponse({
      ...detail,
      ticket_id: "TKT-20260724-DEF456",
      category: "Ticket B current",
    }));
    mutation.reject(new AdminApiError(
      409,
      "TICKET_VERSION_CONFLICT",
      "stale private conflict",
    ));

    expect(await screen.findByText("Ticket B current")).toBeInTheDocument();
    expect(screen.queryByText("stale private conflict")).not.toBeInTheDocument();
    expect(screen.queryByText(CONFLICT_TEXT, { exact: false })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Target status" })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "Internal note" })).toBeEnabled();
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(2);
    expect(mockedAddAdminTicketNote).toHaveBeenCalledTimes(1);
  });

  it("aborts and invalidates conflict recovery on unmount", async () => {
    const user = userEvent.setup();
    const recovery = deferred<AdminTicketDetailResponse>();
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockReturnValueOnce(recovery.promise);
    mockedAddAdminTicketNote.mockRejectedValueOnce(new AdminApiError(
      409,
      "TICKET_VERSION_CONFLICT",
      "private conflict",
    ));
    const { unmount } = render(
      <TicketDetailPageClient ticketId={detail.ticket_id} />,
    );
    fireEvent.change(
      await screen.findByRole("textbox", { name: "Internal note" }),
      { target: { value: "Unmount recovery draft" } },
    );
    await user.click(screen.getByRole("button", { name: "Add internal note" }));
    await waitFor(() => expect(mockedGetAdminTicket).toHaveBeenCalledTimes(2));
    const recoverySignal = mockedGetAdminTicket.mock.calls[1][1]!;

    unmount();

    expect(recoverySignal.aborted).toBe(true);
    recovery.resolve(detailResponse({ ...detail, version: 9 }));
    await Promise.resolve();
    expect(mockedGetAdminTicket).toHaveBeenCalledTimes(2);
    expect(mockedAddAdminTicketNote).toHaveBeenCalledTimes(1);
  });

  it("aborts and invalidates an active mutation on route change", async () => {
    const user = userEvent.setup();
    const mutation = deferred<AdminTicketDetailResponse>();
    const nextRoute = deferred<AdminTicketDetailResponse>();
    mockedAddAdminTicketNote.mockReturnValue(mutation.promise);
    mockedGetAdminTicket
      .mockResolvedValueOnce(detailResponse())
      .mockReturnValueOnce(nextRoute.promise);
    const { rerender } = render(
      <TicketDetailPageClient ticketId="TKT-20260724-ABC123" />,
    );
    fireEvent.change(
      await screen.findByRole("textbox", { name: "Internal note" }),
      { target: { value: "Old route draft" } },
    );
    await user.click(screen.getByRole("button", { name: "Add internal note" }));
    const mutationSignal = mockedAddAdminTicketNote.mock.calls[0][2]!;

    rerender(<TicketDetailPageClient ticketId="TKT-20260724-DEF456" />);
    expect(mutationSignal.aborted).toBe(true);
    mutation.resolve(detailResponse({
      ...detail,
      category: "Stale mutation result",
      version: 8,
    }));
    nextRoute.resolve(detailResponse({
      ...detail,
      ticket_id: "TKT-20260724-DEF456",
      category: "New route result",
    }));

    expect(await screen.findByText("New route result")).toBeInTheDocument();
    expect(screen.queryByText("Stale mutation result")).not.toBeInTheDocument();
    expect(screen.queryByText("Ticket status updated.")).not.toBeInTheDocument();
    expect(screen.queryByText("Old route draft")).not.toBeInTheDocument();
  });

  it("aborts an active mutation on unmount", async () => {
    const mutation = deferred<AdminTicketDetailResponse>();
    mockedAddAdminTicketNote.mockReturnValue(mutation.promise);
    const user = userEvent.setup();
    const { unmount } = render(
      <TicketDetailPageClient ticketId={detail.ticket_id} />,
    );
    fireEvent.change(
      await screen.findByRole("textbox", { name: "Internal note" }),
      { target: { value: "Unmount draft" } },
    );
    await user.click(screen.getByRole("button", { name: "Add internal note" }));
    const signal = mockedAddAdminTicketNote.mock.calls[0][2]!;

    unmount();

    expect(signal.aborted).toBe(true);
    mutation.reject(new DOMException("raw mutation abort", "AbortError"));
    await Promise.resolve();
    expect(mockedAddAdminTicketNote).toHaveBeenCalledTimes(1);
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
