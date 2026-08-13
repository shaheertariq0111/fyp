"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AdminShell } from "@/app/admin/AdminShell";
import { MiniIcon } from "@/app/admin/orders/orderPresentation";
import {
  channelLabel,
  ConversationIcon,
  ConversationStatusBadge,
  CopyConversationButton,
  formatConversationTimestamp,
} from "@/app/admin/conversations/ConversationPresentation";
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
  return (
    <section className="admin-panel admin-conversation-list-panel" aria-label="WhatsApp conversations">
      <div className="conversation-panel-heading">
        <h2>Recent Conversations</h2>
        {loading && <span>Refreshing</span>}
      </div>
      <div className="conversation-card-list">
        {conversations.map((conversation) => {
          const selected = conversation.conversation_id === selectedConversationId;
          return (
            <article className={`conversation-card${selected ? " is-selected" : ""}`} key={conversation.conversation_id}>
              <button
                aria-current={selected ? "true" : undefined}
                aria-label={`Inspect conversation ${conversation.masked_customer_phone ?? conversation.conversation_id}`}
                className="conversation-card-select"
                onClick={() => onSelect(conversation)}
                type="button"
              >
                <span className="conversation-card-topline">
                  <strong>{conversation.masked_customer_phone ?? "WhatsApp customer"}</strong>
                  <time dateTime={conversation.latest_timestamp_utc}>{formatConversationTimestamp(conversation.latest_timestamp_utc)}</time>
                </span>
                <span className="conversation-card-id" title={conversation.conversation_id}>{conversation.conversation_id}</span>
                <span className="conversation-card-preview">{conversation.latest_message_preview || "No message text"}</span>
                <span className="conversation-card-footer">
                  <span>{conversation.message_count} messages · {conversation.customer_message_count} customer / {conversation.agent_message_count} agent</span>
                  <ConversationStatusBadge status={conversation.latest_delivery_status} />
                </span>
              </button>
              <CopyConversationButton conversationId={conversation.conversation_id} />
            </article>
          );
        })}
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
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const activeConversationIdRef = useRef<string | null>(null);
  const pendingSelectionScrollRef = useRef<string | null>(null);
  const wasNearBottomRef = useRef(true);
  const selectedConversationId = selected?.conversation_id ?? null;

  useLayoutEffect(() => {
    if (selectedConversationId !== activeConversationIdRef.current) {
      activeConversationIdRef.current = selectedConversationId;
      pendingSelectionScrollRef.current = selectedConversationId;
      wasNearBottomRef.current = true;
    }
  }, [selectedConversationId]);

  useLayoutEffect(() => {
    const container = scrollContainerRef.current;
    if (
      !container
      || !transcript
      || transcript.conversation_id !== selectedConversationId
    ) {
      return;
    }

    const isNewSelection = pendingSelectionScrollRef.current === selectedConversationId;
    if (isNewSelection || wasNearBottomRef.current) {
      container.scrollTop = container.scrollHeight;
      wasNearBottomRef.current = true;
    }
    if (isNewSelection) {
      pendingSelectionScrollRef.current = null;
    }
  }, [selectedConversationId, transcript]);

  function trackTranscriptScroll() {
    const container = scrollContainerRef.current;
    if (!container) return;
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    wasNearBottomRef.current = distanceFromBottom <= 64;
  }

  return (
    <section className="admin-panel admin-conversation-transcript-panel" aria-label="Conversation transcript">
      <div className="conversation-transcript-heading">
        <h2 className="admin-conversation-heading-id" title={selected?.conversation_id}>
          {selected ? selected.conversation_id : "Conversation transcript"}
        </h2>
        {selected && <ConversationStatusBadge status={selected.latest_delivery_status} />}
      </div>

      {selected && (
        <div className="conversation-selected-summary" aria-label="Selected conversation summary">
          <div><ConversationIcon name="messages" /><span>Total Messages<strong>{selected.message_count}</strong></span></div>
          <div><ConversationIcon name="customer" /><span>Customer<strong>{selected.customer_message_count}</strong></span></div>
          <div><ConversationIcon name="agent" /><span>Agent<strong>{selected.agent_message_count}</strong></span></div>
          <div><ConversationIcon name="clock" /><span>Latest Activity<strong>{formatConversationTimestamp(selected.latest_timestamp_utc)}</strong></span></div>
        </div>
      )}

      <div
        className="conversation-transcript-body"
        onScroll={trackTranscriptScroll}
        ref={scrollContainerRef}
      >
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
            <strong>No messages have been stored for this conversation.</strong>
          </section>
        )}

        {selected && transcript && transcript.messages.length > 0 && !error && (
          <div className="admin-conversation-transcript">
            {transcript.messages.map((message, index) => (
              <MessageBubble key={`${message.timestamp_utc}-${index}`} message={message} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function MessageBubble({ message }: { message: AdminConversationMessage }) {
  const outbound = message.direction === "outbound" || message.sender_type === "agent";
  return (
    <article className={`admin-message-row${outbound ? " is-agent" : " is-customer"}`}>
      <div className="admin-message-bubble">
        <header>
          <strong>{outbound ? "AI Agent" : "Customer"}</strong>
          <time dateTime={message.timestamp_utc}>{formatConversationTimestamp(message.timestamp_utc)}</time>
        </header>
        <p>{message.message_text || "No message text"}</p>
        {outbound && (message.delivery_status || message.outbound_status) && (
          <footer><ConversationStatusBadge status={message.delivery_status || message.outbound_status} /></footer>
        )}
      </div>
    </article>
  );
}

function ConversationKpiStrip({ conversations, loading }: { conversations: AdminConversationSummary[]; loading: boolean }) {
  const metrics = [
    { label: "Recent Conversations", value: conversations.length, icon: "conversation" as const },
    { label: "Total Messages", value: conversations.reduce((sum, item) => sum + item.message_count, 0), icon: "messages" as const },
    { label: "Customer Messages", value: conversations.reduce((sum, item) => sum + item.customer_message_count, 0), icon: "customer" as const },
    { label: "Agent Messages", value: conversations.reduce((sum, item) => sum + item.agent_message_count, 0), icon: "agent" as const },
  ];

  return (
    <section className="admin-panel conversation-kpi-strip" aria-label="Loaded conversation metrics">
      {metrics.map((metric) => (
        <article className="conversation-kpi" key={metric.label}>
          <span className="conversation-kpi-icon"><ConversationIcon name={metric.icon} size={22} /></span>
          <span>{metric.label}{loading && conversations.length === 0 ? <strong className="admin-skeleton admin-skeleton-value" /> : <strong>{metric.value}</strong>}</span>
        </article>
      ))}
    </section>
  );
}

function ConversationContext({ selected }: { selected: AdminConversationSummary }) {
  return (
    <aside className="admin-panel conversation-context-panel" aria-label="Conversation context">
      <div className="conversation-panel-heading"><h2>Conversation Context</h2></div>
      <dl className="conversation-context-fields">
        <div><dt>Masked ID</dt><dd><strong>{selected.masked_customer_phone ?? "Not available"}</strong></dd></div>
        <div>
          <dt>Full ID</dt>
          <dd className="conversation-context-id">
            <span title={selected.conversation_id}>{selected.conversation_id}</span>
            <CopyConversationButton conversationId={selected.conversation_id} />
          </dd>
        </div>
        <div><dt>Status</dt><dd><ConversationStatusBadge status={selected.latest_delivery_status} /></dd></div>
        <div><dt>Latest Activity</dt><dd><time dateTime={selected.latest_timestamp_utc}>{formatConversationTimestamp(selected.latest_timestamp_utc)}</time></dd></div>
        <div className="conversation-context-preview"><dt>Latest Preview</dt><dd>{selected.latest_message_preview || "No message text"}</dd></div>
        <div>
          <dt>Source / Channel</dt>
          <dd className={`conversation-channel${selected.channel.toLowerCase() === "whatsapp" ? " is-whatsapp" : ""}`}>
            <ConversationIcon name={selected.channel.toLowerCase() === "whatsapp" ? "whatsapp" : "conversation"} />
            {channelLabel(selected.channel)}
          </dd>
        </div>
      </dl>
      <p className="conversation-readonly-note"><ConversationIcon name="info" />This is read-only stored conversation history for monitoring and inspection.</p>
    </aside>
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
    <div className="admin-dashboard-actions">
      <button
        className="admin-refresh-button"
        disabled={isListLoading}
        onClick={() => void loadConversations("refreshing")}
        type="button"
      >
        <MiniIcon name="refresh" />
        {listState === "refreshing" ? "Refreshing..." : "Refresh"}
      </button>
    </div>
  );

  return (
    <AdminShell
      actions={actions}
      subtitle="Inspect stored WhatsApp customer-agent message history"
      title="Conversations"
    >
      <div className="admin-conversations-page">
        <ConversationKpiStrip conversations={conversations} loading={isListLoading} />
        <p className="conversation-refresh-note">Stored conversation history updates on refresh.</p>
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
            {selected && <ConversationContext selected={selected} />}
          </div>
        )}
      </div>
    </AdminShell>
  );
}
