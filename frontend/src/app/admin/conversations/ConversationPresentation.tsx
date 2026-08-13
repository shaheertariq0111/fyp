"use client";

import { useState } from "react";
import { humanizeStatus } from "@/app/admin/AdminShell";

export type ConversationIconName =
  | "agent"
  | "clock"
  | "conversation"
  | "copy"
  | "customer"
  | "info"
  | "messages"
  | "whatsapp";

export function ConversationIcon({ name, size = 16 }: { name: ConversationIconName; size?: number }) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.9,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  switch (name) {
    case "conversation":
      return <svg {...common}><path d="M4 5h16v11H9l-5 4Z" /><path d="M8 10h.01" /><path d="M12 10h.01" /><path d="M16 10h.01" /></svg>;
    case "messages":
      return <svg {...common}><path d="M5 4h14v11H9l-4 4Z" /><path d="M9 8h6" /><path d="M9 11h4" /></svg>;
    case "customer":
      return <svg {...common}><circle cx="12" cy="8" r="3.5" /><path d="M5.5 20a6.5 6.5 0 0 1 13 0" /></svg>;
    case "agent":
      return <svg {...common}><path d="M4 13v-2a8 8 0 0 1 16 0v2" /><path d="M4 12H2v5h4v-5Z" /><path d="M20 12h2v5h-4v-5Z" /><path d="M18 18c-1 2-3 3-6 3" /></svg>;
    case "clock":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>;
    case "copy":
      return <svg {...common}><rect x="8" y="8" width="11" height="11" rx="2" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" /></svg>;
    case "whatsapp":
      return <svg {...common}><path d="M20 11.5a8 8 0 0 1-11.8 7L4 20l1.4-4.1A8 8 0 1 1 20 11.5Z" /><path d="M8.5 8.2c.4 3.1 2.2 5 5.4 5.8" /><path d="m8.5 8.2 1.4-.5 1.1 2-1 .8" /><path d="m13.9 14 1-1 1.9 1.1-.5 1.4" /></svg>;
    case "info":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 11v5" /><path d="M12 8h.01" /></svg>;
  }
}

export function formatConversationTimestamp(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "Unknown";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function conversationStatusClass(status?: string | null) {
  const normalized = (status ?? "").toLowerCase();
  if (["accepted", "sent", "delivered", "read"].includes(normalized)) return "is-success";
  if (["failed", "rejected", "undelivered"].includes(normalized)) return "is-danger";
  if (["skipped", "pending"].includes(normalized)) return "is-warning";
  return "is-neutral";
}

export function ConversationStatusBadge({ status }: { status?: string | null }) {
  if (!status) return <span className="admin-muted-text">None</span>;
  return (
    <span className={`admin-status-badge ${conversationStatusClass(status)}`}>
      <span aria-hidden="true" />
      {humanizeStatus(status)}
    </span>
  );
}

export function channelLabel(channel: string) {
  return channel.toLowerCase() === "whatsapp" ? "WhatsApp" : humanizeStatus(channel);
}

export function CopyConversationButton({ conversationId }: { conversationId: string }) {
  const [copied, setCopied] = useState(false);

  async function copyConversationId() {
    try {
      await navigator.clipboard.writeText(conversationId);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <button
      aria-label="Copy conversation ID"
      className="admin-conversation-copy"
      onClick={() => void copyConversationId()}
      title={copied ? "Conversation ID copied" : "Copy conversation ID"}
      type="button"
    >
      <ConversationIcon name="copy" />
      <span>{copied ? "Copied" : "Copy"}</span>
    </button>
  );
}
