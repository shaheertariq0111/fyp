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

export function statusLabel(status: TicketStatus) {
  return statusLabels[status];
}

export function priorityLabel(priority: TicketPriority) {
  return priorityLabels[priority];
}

export function ticketTypeLabel(ticketType: TicketType) {
  return ticketTypeLabels[ticketType];
}

export function statusClassName(status: TicketStatus) {
  return statusClassNames[status] ?? "is-status-neutral";
}

export function priorityClassName(priority: TicketPriority) {
  return priorityClassNames[priority] ?? "is-priority-neutral";
}

export const formatTicketDateTime = formatDateTime;

export function visibleValue(value: string | null | undefined, fallback = "Not provided") {
  return value?.trim() ? value : fallback;
}
