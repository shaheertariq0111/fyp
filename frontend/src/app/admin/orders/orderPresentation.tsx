"use client";

import { ReactNode } from "react";

export const ORDER_STATUSES = [
  "submitted_to_restaurant",
  "accepted",
  "preparing",
  "ready_for_pickup",
  "out_for_delivery",
  "delivered",
  "completed",
  "rejected",
  "failed",
  "cancelled",
] as const;

const statusLabels: Record<string, string> = {
  submitted_to_restaurant: "Submitted",
  accepted: "Accepted",
  preparing: "Preparing",
  ready_for_pickup: "Ready for pickup",
  out_for_delivery: "Out for delivery",
  delivered: "Delivered",
  completed: "Completed",
  rejected: "Rejected",
  failed: "Failed",
  cancelled: "Cancelled",
};

const actionLabels: Record<string, string> = {
  accept: "Accept order",
  reject: "Reject order",
  fail: "Mark as failed",
  start_preparing: "Start preparing",
  mark_ready: "Mark ready",
  dispatch: "Dispatch order",
  complete: "Complete order",
  deliver: "Mark delivered",
};

const fulfillmentLabels: Record<string, string> = {
  delivery: "Delivery",
  takeaway: "Takeaway",
};

const statusTone: Record<string, string> = {
  submitted_to_restaurant: "blue",
  accepted: "navy",
  preparing: "warning",
  ready_for_pickup: "success",
  out_for_delivery: "purple",
  delivered: "success",
  completed: "success",
  rejected: "danger",
  failed: "danger",
  cancelled: "danger",
};

export function readableText(value: string) {
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function statusLabel(status?: string | null) {
  if (!status) {
    return "Unknown";
  }
  return statusLabels[status] ?? readableText(status);
}

export function statusToneClass(status?: string | null) {
  return `is-${statusTone[status ?? ""] ?? "neutral"}`;
}

export function actionLabel(action: string) {
  return actionLabels[action] ?? readableText(action);
}

export function fulfillmentLabel(value?: string | null) {
  if (!value) {
    return "Not specified";
  }
  return fulfillmentLabels[value] ?? readableText(value);
}

export function isDangerAction(action: string) {
  return action === "reject" || action === "fail";
}

export function isTerminalDangerStatus(status?: string | null) {
  return status === "rejected" || status === "failed" || status === "cancelled";
}

export function shortOrderId(orderId: string) {
  if (orderId.length <= 16) {
    return orderId;
  }
  return `${orderId.slice(0, 8)}...${orderId.slice(-6)}`;
}

export function formatDateTime(value?: string | null) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatRelativeTime(value?: string | null) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  const diffSeconds = Math.round((Date.now() - date.getTime()) / 1000);
  const absSeconds = Math.abs(diffSeconds);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (absSeconds < 60) {
    return rtf.format(-diffSeconds, "second");
  }
  const diffMinutes = Math.round(diffSeconds / 60);
  if (Math.abs(diffMinutes) < 60) {
    return rtf.format(-diffMinutes, "minute");
  }
  const diffHours = Math.round(diffMinutes / 60);
  if (Math.abs(diffHours) < 24) {
    return rtf.format(-diffHours, "hour");
  }
  const diffDays = Math.round(diffHours / 24);
  return rtf.format(-diffDays, "day");
}

export function formatRefreshTime(value: Date | null) {
  if (!value) {
    return "Not refreshed yet";
  }
  return `Updated ${value.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
}

export function stringifyUnknown(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "Not specified";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => stringifyUnknown(item)).join(", ");
  }
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, entry]) => `${readableText(key)}: ${stringifyUnknown(entry)}`)
      .join(", ");
  }
  return String(value);
}

export function customizationRows(customizations?: Record<string, unknown>) {
  if (!customizations || Object.keys(customizations).length === 0) {
    return [];
  }
  return Object.entries(customizations).map(([key, value]) => ({
    key,
    label: readableText(key),
    value: stringifyUnknown(value),
  }));
}

export function StatusBadge({ status }: { status?: string | null }) {
  return (
    <span className={`admin-status-badge ${statusToneClass(status)}`}>
      <span aria-hidden="true" />
      {statusLabel(status)}
    </span>
  );
}

export function MiniIcon({ name }: { name: "clock" | "filter" | "search" | "truck" | "bag" | "arrowLeft" | "refresh" }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  switch (name) {
    case "clock":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3 2" />
        </svg>
      );
    case "filter":
      return (
        <svg {...common}>
          <path d="M4 5h16" />
          <path d="M7 12h10" />
          <path d="M10 19h4" />
        </svg>
      );
    case "search":
      return (
        <svg {...common}>
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3-3" />
        </svg>
      );
    case "truck":
      return (
        <svg {...common}>
          <path d="M3 7h11v10H3z" />
          <path d="M14 11h4l3 3v3h-7z" />
          <circle cx="7" cy="18" r="2" />
          <circle cx="17" cy="18" r="2" />
        </svg>
      );
    case "bag":
      return (
        <svg {...common}>
          <path d="M6 8h12l-1 12H7z" />
          <path d="M9 8a3 3 0 0 1 6 0" />
        </svg>
      );
    case "arrowLeft":
      return (
        <svg {...common}>
          <path d="M19 12H5" />
          <path d="m12 19-7-7 7-7" />
        </svg>
      );
    case "refresh":
      return (
        <svg {...common}>
          <path d="M20 12a8 8 0 0 1-14.9 4" />
          <path d="M4 16H2v5h5" />
          <path d="M4 12a8 8 0 0 1 14.9-4" />
          <path d="M20 8h2V3h-5" />
        </svg>
      );
  }
}

export function FieldValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="admin-field-value">
      <span>{label}</span>
      <strong>{children}</strong>
    </div>
  );
}
