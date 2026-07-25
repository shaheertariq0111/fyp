import { formatDateTime } from "@/app/admin/orders/orderPresentation";
import type {
  TicketPriority,
  TicketStatus,
  TicketType,
} from "@/lib/adminTicketTypes";

const statusLabels: Record<TicketStatus, string> = {
  open: "Open",
  in_review: "In Review",
  waiting_for_customer: "Waiting for Customer",
  resolved: "Resolved",
  closed: "Closed",
};

const priorityLabels: Record<TicketPriority, string> = {
  normal: "Normal",
  high: "High",
  urgent: "Urgent",
};

const ticketTypeLabels: Record<TicketType, string> = {
  human_assistance: "Human Assistance",
  order_complaint: "Order Complaint",
};

const statusClassNames: Record<TicketStatus, string> = {
  open: "is-status-open",
  in_review: "is-status-in_review",
  waiting_for_customer: "is-status-waiting_for_customer",
  resolved: "is-status-resolved",
  closed: "is-status-closed",
};

const priorityClassNames: Record<TicketPriority, string> = {
  normal: "is-priority-normal",
  high: "is-priority-high",
  urgent: "is-priority-urgent",
};

function mappedValue<T extends string>(
  values: Record<T, string>,
  value: unknown,
  fallback: string,
) {
  if (
    typeof value !== "string"
    || !Object.prototype.hasOwnProperty.call(values, value)
  ) {
    return fallback;
  }
  return values[value as T];
}

export function statusLabel(status: unknown) {
  return mappedValue(statusLabels, status, "Unknown status");
}

export function priorityLabel(priority: unknown) {
  return mappedValue(priorityLabels, priority, "Unknown priority");
}

export function ticketTypeLabel(ticketType: unknown) {
  return mappedValue(ticketTypeLabels, ticketType, "Unknown ticket type");
}

export function statusClassName(status: unknown) {
  return mappedValue(statusClassNames, status, "is-status-neutral");
}

export function priorityClassName(priority: unknown) {
  return mappedValue(priorityClassNames, priority, "is-priority-neutral");
}

export const formatTicketDateTime = formatDateTime;

export function visibleValue(value: string | null | undefined, fallback = "Not provided") {
  return value?.trim() ? value : fallback;
}
