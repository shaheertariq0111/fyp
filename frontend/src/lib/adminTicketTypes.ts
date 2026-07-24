export type TicketStatus =
  | "open"
  | "in_review"
  | "waiting_for_customer"
  | "resolved"
  | "closed";

export type TicketPriority =
  | "normal"
  | "high"
  | "urgent";

export type TicketType =
  | "human_assistance"
  | "order_complaint";

export type AdminTicketListItem = {
  ticket_id: string;
  user_id: string;
  customer_id: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  ticket_type: TicketType;
  category: string;
  priority: TicketPriority;
  status: TicketStatus;
  order_id: string | null;
  source: string;
  created_at: string;
  updated_at: string;
  version: number;
};

export type AdminTicketListResponse = {
  tickets: AdminTicketListItem[];
  next_cursor: string | null;
};

export type StatusHistoryEntry = {
  previous_status: TicketStatus;
  new_status: TicketStatus;
  timestamp: string;
  actor: string | null;
  reason: string | null;
};

export type PriorityHistoryEntry = {
  previous_priority: TicketPriority;
  new_priority: TicketPriority;
  timestamp: string;
  actor: string | null;
  reason: string | null;
};

export type AdminNote = {
  note_id: string | null;
  actor: string | null;
  timestamp: string;
  text: string;
};

export type LinkedOrderSummary = {
  order_id: string;
  status: string;
  fulfillment_method: string | null;
  total: number;
  currency: string;
  created_at: string;
  updated_at: string;
};

export type AdminTicketDetail = {
  ticket_id: string;
  user_id: string;
  customer_id: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  ticket_type: TicketType;
  category: string;
  description: string | null;
  priority: TicketPriority;
  status: TicketStatus;
  order_id: string | null;
  order_status_snapshot: string | null;
  source: string;
  created_at: string;
  updated_at: string;
  status_history: StatusHistoryEntry[];
  priority_history: PriorityHistoryEntry[];
  admin_notes: AdminNote[];
  version: number;
  linked_order: LinkedOrderSummary | null;
};

export type AdminTicketDetailResponse = {
  ticket: AdminTicketDetail;
};

export type AdminTicketStatusUpdateRequest = {
  status: TicketStatus;
  reason?: string | null;
  expected_version: number;
};

export type AdminTicketPriorityUpdateRequest = {
  priority: TicketPriority;
  reason?: string | null;
  expected_version: number;
};

export type AdminTicketNoteCreateRequest = {
  text: string;
  expected_version: number;
};

export type AdminTicketReopenRequest = {
  target_status: "open" | "in_review";
  reason: string;
  expected_version: number;
};

export type AdminApiErrorEnvelope = {
  detail:
    | {
        error_code: string;
        user_message: string;
      }
    | string;
};
