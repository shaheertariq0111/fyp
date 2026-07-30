"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AdminShell, humanizeStatus } from "@/app/admin/AdminShell";
import { AdminApiError } from "@/lib/adminApi";
import {
  AdminConversationMessage,
  AdminConversationMessagesResponse,
  AdminConversationSummary,
  getAdminConversationMessages,
  listAdminConversations,
} from "@/lib/adminConversationsApi";

type LoadState = "idle" | "loading" | "refreshing";
type TranscriptState = "idle" | "loading";

function isAbortError(error: unknown) {
  return typeof error === "object"
    && error !== null
    && "name" in error
    && error.name === "AbortError";
}

function normalizeError(error: unknown) {
  if (error instanceof AdminApiError) {
    return error;
  }
  return new AdminApiError(
    0,
    "ADMIN_NETWORK_ERROR",
    "The administrator service could not be reached. Please try again.",
  );
}

function formatTimestamp(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value || "Unknown";
  }
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function statusClass(status?: string | null) {
  const normalized = (status ?? "").toLowerCase();
  if (["accepted", "sent", "delivered", "read"].includes(normalized)) {
    return "is-success";
  }
  if (["failed", "rejected", "undelivered"].includes(normalized)) {
    return "is-danger";
  }
  if (["skipped", "pending"].includes(normalized)) {
    return "is-warning";
  }
  return "is-neutral";
}

function ConversationError({
  error,
  onRetry,
  disabled,
}: {
  error: AdminApiError;
  onRetry: () => void;
  disabled: boolean;
}) {
  return (
    <section className="admin-error-panel" role="alert">
      <div>
        <strong>Conversation history unavailable</strong>
        <p>{error.userMessage}</p>
      </div>
      <button className="secondary" disabled={disabled} onClick={onRetry} type="button">
        Retry
      </button>
    </section>
  );
}

function ConversationList({
  conversations,
  selectedConversationId,
  onSelect,
  loading,
}: {
  conversations: AdminConversationSummary[];
  selectedConversationId: string | null;
  onSelect: (conversation: AdminConversationSummary) => void;
  loading: boolean;
}) {
  const [copiedConversationId, setCopiedConversationId] = useState<string | null>(null);

  async function copyConversationId(conversationId: string) {
    try {
      await navigator.clipboard.writeText(conversationId);
      setCopiedConversationId(conversationId);
      window.setTimeout(() => {
        setCopiedConversationId((current) => current === conversationId ? null : current);
      }, 1600);
    } catch {
      setCopiedConversationId(null);
    }
  }

  return (
    <section className="admin-panel admin-conversation-list-panel" aria-label="WhatsApp conversations">
      <div className="admin-section-heading">
        <div>
          <h2>WhatsApp Conversations</h2>
          <p>{loading ? "Refreshing" : `${conversations.length} recent conversations`}</p>
        </div>
      </div>
      <div className="admin-conversation-table-wrap">
        <table className="admin-conversation-table">
          <thead>
            <tr>
              <th>Conversation</th>
              <th>Latest</th>
              <th>Messages</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {conversations.map((conversation) => {
              const selected = conversation.conversation_id === selectedConversationId;
              return (
                <tr className={selected ? "is-selected" : ""} key={conversation.conversation_id}>
                  <td data-label="Conversation">
                    <div className="admin-conversation-identity">
                      <button
                        aria-current={selected ? "true" : undefined}
                        className="admin-conversation-select"
                        onClick={() => onSelect(conversation)}
                        title={conversation.conversation_id}
                        type="button"
                      >
                        <strong>{conversation.masked_customer_phone ?? "WhatsApp customer"}</strong>
                        <small>{conversation.conversation_id}</small>
                      </button>
                      <button
                        aria-label="Copy conversation ID"
                        className="admin-conversation-copy"
                        onClick={() => void copyConversationId(conversation.conversation_id)}
                        title="Copy conversation ID"
                        type="button"
                      >
                        {copiedConversationId === conversation.conversation_id ? "Copied" : "Copy"}
                      </button>
                    </div>
                  </td>
                  <td className="admin-conversation-latest-cell" data-label="Latest">
                    <span>{formatTimestamp(conversation.latest_timestamp_utc)}</span>
                    <p>{conversation.latest_message_preview || "No message text"}</p>
                  </td>
                  <td className="admin-conversation-count-cell" data-label="Messages">
                    <span>{conversation.message_count}</span>
                    <small>{conversation.customer_message_count} customer / {conversation.agent_message_count} agent</small>
                  </td>
                  <td data-label="Status">
                    {conversation.latest_delivery_status ? (
                      <span className={`admin-status-badge ${statusClass(conversation.latest_delivery_status)}`}>
                        <span aria-hidden="true" />
                        {humanizeStatus(conversation.latest_delivery_status)}
                      </span>
                    ) : (
                      <span className="admin-muted-text">None</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Transcript({
  selected,
  transcript,
  state,
  error,
  onRetry,
}: {
  selected: AdminConversationSummary | null;
  transcript: AdminConversationMessagesResponse | null;
  state: TranscriptState;
  error: AdminApiError | null;
  onRetry: () => void;
}) {
  return (
    <section className="admin-panel admin-conversation-transcript-panel" aria-label="Conversation transcript">
      <div className="admin-section-heading">
        <div>
          <h2>Transcript</h2>
          <p className="admin-conversation-heading-id" title={selected?.conversation_id}>
            {selected ? selected.conversation_id : "No conversation selected"}
          </p>
        </div>
        {selected?.latest_delivery_status && (
          <span className={`admin-status-badge ${statusClass(selected.latest_delivery_status)}`}>
            <span aria-hidden="true" />
            {humanizeStatus(selected.latest_delivery_status)}
          </span>
        )}
      </div>

      {!selected && (
        <section className="admin-empty-state">
          <strong>Select a conversation</strong>
          <p>Choose a WhatsApp conversation from the list to inspect the customer-agent transcript.</p>
        </section>
      )}

      {selected && state === "loading" && (
        <div className="admin-conversation-loading" role="status">Loading transcript...</div>
      )}

      {selected && error && (
        <ConversationError disabled={state === "loading"} error={error} onRetry={onRetry} />
      )}

      {selected && transcript && transcript.messages.length === 0 && !error && state !== "loading" && (
        <section className="admin-empty-state">
          <strong>No messages stored</strong>
          <p>This conversation does not have readable history records yet.</p>
        </section>
      )}

      {selected && transcript && transcript.messages.length > 0 && !error && (
        <div className="admin-conversation-transcript">
          {transcript.messages.map((message, index) => (
            <MessageBubble key={`${message.timestamp_utc}-${index}`} message={message} />
          ))}
        </div>
      )}
    </section>
  );
}

function MessageBubble({ message }: { message: AdminConversationMessage }) {
  const outbound = message.direction === "outbound" || message.sender_type === "agent";
  return (
    <article className={`admin-message-row${outbound ? " is-agent" : " is-customer"}`}>
      <div className="admin-message-bubble">
        <p>{message.message_text || "No message text"}</p>
        <footer>
          <span>{formatTimestamp(message.timestamp_utc)}</span>
          {outbound && (message.delivery_status || message.outbound_status) && (
            <span>
              {humanizeStatus(message.delivery_status || message.outbound_status || "")}
            </span>
          )}
        </footer>
      </div>
    </article>
  );
}

export function ConversationsPageClient() {
  const [conversations, setConversations] = useState<AdminConversationSummary[]>([]);
  const [selected, setSelected] = useState<AdminConversationSummary | null>(null);
  const [transcript, setTranscript] = useState<AdminConversationMessagesResponse | null>(null);
  const [listState, setListState] = useState<LoadState>("loading");
  const [transcriptState, setTranscriptState] = useState<TranscriptState>("idle");
  const [listError, setListError] = useState<AdminApiError | null>(null);
  const [transcriptError, setTranscriptError] = useState<AdminApiError | null>(null);
  const selectedConversationId = useRef<string | null>(null);
  const listController = useRef<AbortController | null>(null);
  const transcriptController = useRef<AbortController | null>(null);

  const loadTranscript = useCallback(async (conversation: AdminConversationSummary) => {
    transcriptController.current?.abort();
    const controller = new AbortController();
    transcriptController.current = controller;
    selectedConversationId.current = conversation.conversation_id;
    setSelected(conversation);
    setTranscript(null);
    setTranscriptError(null);
    setTranscriptState("loading");
    try {
      const result = await getAdminConversationMessages(
        conversation.conversation_id,
        controller.signal,
      );
      setTranscript(result);
    } catch (error) {
      if (!isAbortError(error)) {
        setTranscriptError(normalizeError(error));
      }
    } finally {
      if (transcriptController.current === controller) {
        transcriptController.current = null;
        setTranscriptState("idle");
      }
    }
  }, []);

  const loadConversations = useCallback(async (kind: LoadState = "loading") => {
    listController.current?.abort();
    const controller = new AbortController();
    listController.current = controller;
    setListState(kind);
    setListError(null);
    try {
      const result = await listAdminConversations(controller.signal);
      setConversations(result.conversations);
      if (
        result.conversations.length > 0
        && !result.conversations.some(
          (item) => item.conversation_id === selectedConversationId.current,
        )
      ) {
        void loadTranscript(result.conversations[0]);
      }
      if (result.conversations.length === 0) {
        selectedConversationId.current = null;
        setSelected(null);
        setTranscript(null);
      }
    } catch (error) {
      if (!isAbortError(error)) {
        setListError(normalizeError(error));
      }
    } finally {
      if (listController.current === controller) {
        listController.current = null;
        setListState("idle");
      }
    }
  }, [loadTranscript]);

  useEffect(() => {
    void loadConversations("loading");
    return () => {
      listController.current?.abort();
      transcriptController.current?.abort();
    };
  }, [loadConversations]);

  const isListLoading = listState !== "idle";
  const actions = (
    <button
      className="admin-refresh-button"
      disabled={isListLoading}
      onClick={() => void loadConversations("refreshing")}
      type="button"
    >
      {listState === "refreshing" ? "Refreshing..." : "Refresh"}
    </button>
  );

  return (
    <AdminShell
      actions={actions}
      subtitle="Inspect stored WhatsApp customer-agent message history"
      title="Conversations"
    >
      <div className="admin-conversations-page">
        {listError && (
          <ConversationError
            disabled={isListLoading}
            error={listError}
            onRetry={() => void loadConversations("loading")}
          />
        )}

        {isListLoading && conversations.length === 0 && !listError && (
          <div className="admin-conversation-loading" role="status">Loading conversations...</div>
        )}

        {!isListLoading && conversations.length === 0 && !listError && (
          <section className="admin-empty-state">
            <strong>No conversations stored</strong>
            <p>WhatsApp conversations will appear here after customer and agent messages are saved.</p>
          </section>
        )}

        {conversations.length > 0 && (
          <div className="admin-conversation-layout">
            <ConversationList
              conversations={conversations}
              loading={isListLoading}
              onSelect={(conversation) => void loadTranscript(conversation)}
              selectedConversationId={selected?.conversation_id ?? null}
            />
            <Transcript
              error={transcriptError}
              onRetry={() => {
                if (selected) {
                  void loadTranscript(selected);
                }
              }}
              selected={selected}
              state={transcriptState}
              transcript={transcript}
            />
          </div>
        )}
      </div>
    </AdminShell>
  );
}
