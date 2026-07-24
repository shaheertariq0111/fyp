import {
  adminGet,
  adminPatch,
  adminPost,
} from "@/lib/adminApi";
import type {
  AdminTicketDetailResponse,
  AdminTicketListResponse,
  AdminTicketNoteCreateRequest,
  AdminTicketPriorityUpdateRequest,
  AdminTicketReopenRequest,
  AdminTicketStatusUpdateRequest,
  TicketPriority,
  TicketStatus,
  TicketType,
} from "@/lib/adminTicketTypes";

export type AdminTicketListFilters = {
  status?: TicketStatus;
  ticket_type?: TicketType;
  priority?: TicketPriority;
  limit?: number;
  cursor?: string;
};

function requestOptions(signal?: AbortSignal): RequestInit {
  return signal ? { signal } : {};
}

function ticketPath(ticketId: string): string {
  return `/api/admin/tickets/${encodeURIComponent(ticketId)}`;
}

export function listAdminTickets(
  filters: AdminTicketListFilters = {},
  signal?: AbortSignal,
): Promise<AdminTicketListResponse> {
  const params = new URLSearchParams();
  if (filters.status) {
    params.set("status", filters.status);
  }
  if (filters.ticket_type) {
    params.set("ticket_type", filters.ticket_type);
  }
  if (filters.priority) {
    params.set("priority", filters.priority);
  }
  if (filters.limit !== undefined) {
    params.set("limit", String(filters.limit));
  }
  if (filters.cursor) {
    params.set("cursor", filters.cursor);
  }
  const query = params.toString();
  const path = query ? `/api/admin/tickets?${query}` : "/api/admin/tickets";
  return adminGet<AdminTicketListResponse>(path, requestOptions(signal));
}

export function getAdminTicket(
  ticketId: string,
  signal?: AbortSignal,
): Promise<AdminTicketDetailResponse> {
  return adminGet<AdminTicketDetailResponse>(
    ticketPath(ticketId),
    requestOptions(signal),
  );
}

export function updateAdminTicketStatus(
  ticketId: string,
  request: AdminTicketStatusUpdateRequest,
  signal?: AbortSignal,
): Promise<AdminTicketDetailResponse> {
  const body: AdminTicketStatusUpdateRequest = {
    status: request.status,
    expected_version: request.expected_version,
    ...(request.reason !== undefined ? { reason: request.reason } : {}),
  };
  return adminPatch<AdminTicketDetailResponse>(
    `${ticketPath(ticketId)}/status`,
    body,
    requestOptions(signal),
  );
}

export function updateAdminTicketPriority(
  ticketId: string,
  request: AdminTicketPriorityUpdateRequest,
  signal?: AbortSignal,
): Promise<AdminTicketDetailResponse> {
  const body: AdminTicketPriorityUpdateRequest = {
    priority: request.priority,
    expected_version: request.expected_version,
    ...(request.reason !== undefined ? { reason: request.reason } : {}),
  };
  return adminPatch<AdminTicketDetailResponse>(
    `${ticketPath(ticketId)}/priority`,
    body,
    requestOptions(signal),
  );
}

export function addAdminTicketNote(
  ticketId: string,
  request: AdminTicketNoteCreateRequest,
  signal?: AbortSignal,
): Promise<AdminTicketDetailResponse> {
  const body: AdminTicketNoteCreateRequest = {
    text: request.text,
    expected_version: request.expected_version,
  };
  return adminPost<AdminTicketDetailResponse>(
    `${ticketPath(ticketId)}/notes`,
    body,
    requestOptions(signal),
  );
}

export function reopenAdminTicket(
  ticketId: string,
  request: AdminTicketReopenRequest,
  signal?: AbortSignal,
): Promise<AdminTicketDetailResponse> {
  const body: AdminTicketReopenRequest = {
    target_status: request.target_status,
    reason: request.reason,
    expected_version: request.expected_version,
  };
  return adminPost<AdminTicketDetailResponse>(
    `${ticketPath(ticketId)}/reopen`,
    body,
    requestOptions(signal),
  );
}
