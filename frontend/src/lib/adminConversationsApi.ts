import { adminGet } from "@/lib/adminApi";

export type AdminConversationSummary = {
  conversation_id: string;
  channel: string;
  latest_message_preview: string;
  latest_timestamp_utc: string;
  message_count: number;
  customer_message_count: number;
  agent_message_count: number;
  masked_customer_phone?: string | null;
  latest_delivery_status?: string | null;
};

export type AdminConversationMessage = {
  timestamp_utc: string;
  direction: "inbound" | "outbound" | string;
  sender_type: "customer" | "agent" | string;
  message_text: string;
  outbound_status?: string | null;
  delivery_status?: string | null;
  masked_customer_phone?: string | null;
};

export type AdminConversationListResponse = {
  conversations: AdminConversationSummary[];
};

export type AdminConversationMessagesResponse = {
  conversation_id: string;
  channel: string;
  messages: AdminConversationMessage[];
};

export function listAdminConversations(signal?: AbortSignal) {
  return adminGet<AdminConversationListResponse>(
    "/api/admin/conversations",
    signal ? { signal } : {},
  );
}

export function getAdminConversationMessages(
  conversationId: string,
  signal?: AbortSignal,
) {
  return adminGet<AdminConversationMessagesResponse>(
    `/api/admin/conversations/${encodeURIComponent(conversationId)}/messages`,
    signal ? { signal } : {},
  );
}
