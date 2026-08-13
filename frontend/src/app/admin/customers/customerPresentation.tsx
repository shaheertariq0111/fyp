"use client";

export function shortReference(value?: string | null) {
  if (!value) {
    return "Not specified";
  }
  if (value.length <= 14) {
    return value;
  }
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

export function customerName(value?: string | null) {
  return value?.trim() || "Unknown customer";
}

export function addressLabel(value?: string | null) {
  return value?.trim() || "Saved address";
}

export function CustomerIcon({ name }: { name: "search" | "refresh" | "clear" | "phone" | "location" | "customer" | "orders" | "completed" | "spend" }) {
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
    case "search":
      return (
        <svg {...common}>
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3-3" />
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
    case "clear":
      return (
        <svg {...common}>
          <path d="M18 6 6 18" />
          <path d="m6 6 12 12" />
        </svg>
      );
    case "phone":
      return (
        <svg {...common}>
          <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.3 19.3 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.3 1.7.6 2.5a2 2 0 0 1-.5 2.1L8 9.5a16 16 0 0 0 6.5 6.5l1.2-1.2a2 2 0 0 1 2.1-.5c.8.3 1.6.5 2.5.6a2 2 0 0 1 1.7 2z" />
        </svg>
      );
    case "location":
      return (
        <svg {...common}>
          <path d="M12 21s7-4.4 7-11a7 7 0 0 0-14 0c0 6.6 7 11 7 11z" />
          <circle cx="12" cy="10" r="2.5" />
        </svg>
      );
    case "customer":
      return (
        <svg {...common}>
          <circle cx="12" cy="8" r="3.5" />
          <path d="M5.5 20a6.5 6.5 0 0 1 13 0" />
        </svg>
      );
    case "orders":
      return (
        <svg {...common}>
          <path d="M6 8h12l-1 12H7z" />
          <path d="M9 8a3 3 0 0 1 6 0" />
        </svg>
      );
    case "completed":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="m7.5 12 3 3 6-7" />
        </svg>
      );
    case "spend":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M15 8.5c-.7-.7-1.7-1-3-1-1.7 0-3 .9-3 2s1.3 2 3 2 3 .9 3 2-1.3 2-3 2c-1.3 0-2.4-.4-3-1" />
          <path d="M12 5.5v13" />
        </svg>
      );
  }
}
