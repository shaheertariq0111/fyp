"use client";

import { readableText } from "@/app/admin/orders/orderPresentation";

export function formatEventType(value?: string | null) {
  return readableText(value || "unknown_event");
}

export function shortReference(value?: string | null) {
  if (!value) {
    return "Not specified";
  }
  if (value.length <= 14) {
    return value;
  }
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

export function safeDetailRows(details?: Record<string, unknown>) {
  if (!details || Object.keys(details).length === 0) {
    return [];
  }
  return Object.entries(details).map(([key, value]) => ({
    key,
    label: readableText(key),
    value: summarizeDetail(value),
  }));
}

function summarizeDetail(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "Not specified";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? "Empty list" : `${value.length} item${value.length === 1 ? "" : "s"}`;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) {
      return "Empty object";
    }
    return entries
      .slice(0, 3)
      .map(([key, entry]) => `${readableText(key)}: ${summarizeDetail(entry)}`)
      .join(", ");
  }
  return String(value);
}

export function MonitoringIcon({ name }: { name: "refresh" | "search" | "alert" }) {
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
    case "refresh":
      return (
        <svg {...common}>
          <path d="M20 12a8 8 0 0 1-14.9 4" />
          <path d="M4 16H2v5h5" />
          <path d="M4 12a8 8 0 0 1 14.9-4" />
          <path d="M20 8h2V3h-5" />
        </svg>
      );
    case "search":
      return (
        <svg {...common}>
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3-3" />
        </svg>
      );
    case "alert":
      return (
        <svg {...common}>
          <path d="M12 9v4" />
          <path d="M12 17h.01" />
          <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
        </svg>
      );
  }
}
