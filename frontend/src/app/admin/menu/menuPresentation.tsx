"use client";

export function readableMenuText(value: string) {
  return value
    .split(/[_-]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function availabilityLabel(available: boolean) {
  return available ? "Available" : "Unavailable";
}

export function archiveLabel(archived?: boolean) {
  return archived ? "Archived" : "Active";
}

export function parseJsonObject(value: string): { ok: true; data: Record<string, unknown> } | { ok: false; error: string } {
  if (!value.trim()) {
    return { ok: false, error: "JSON is required." };
  }
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false, error: "JSON must be an object." };
    }
    return { ok: true, data: parsed as Record<string, unknown> };
  } catch {
    return { ok: false, error: "JSON is not valid." };
  }
}

function nonEmptyString(value: unknown) {
  return typeof value === "string" && value.trim().length > 0;
}

export function validateAdvancedMenuJson(
  value: string,
  mode: "option" | "upsell",
): { ok: true; data: Record<string, unknown> } | { ok: false; error: string } {
  const parsed = parseJsonObject(value);
  if (!parsed.ok) {
    return parsed;
  }

  if (mode === "option") {
    if (!nonEmptyString(parsed.data.option_group_id)) {
      return { ok: false, error: "Option group ID is required." };
    }
    if (!nonEmptyString(parsed.data.name)) {
      return { ok: false, error: "Option group name is required." };
    }
    if (!nonEmptyString(parsed.data.type)) {
      return { ok: false, error: "Option group type is required." };
    }
    if (!nonEmptyString(parsed.data.question)) {
      return { ok: false, error: "Option group question is required." };
    }
    if (!Array.isArray(parsed.data.options)) {
      return { ok: false, error: "Option group options must be an array." };
    }
    return parsed;
  }

  if (!nonEmptyString(parsed.data.upsell_group_id)) {
    return { ok: false, error: "Upsell group ID is required." };
  }
  if (!nonEmptyString(parsed.data.question)) {
    return { ok: false, error: "Upsell group question is required." };
  }
  if (!Array.isArray(parsed.data.items)) {
    return { ok: false, error: "Upsell group items must be an array." };
  }
  if (parsed.data.max_suggestions !== undefined) {
    const maxSuggestions = parsed.data.max_suggestions;
    if (typeof maxSuggestions !== "number" || !Number.isFinite(maxSuggestions) || maxSuggestions <= 0) {
      return { ok: false, error: "Upsell max_suggestions must be greater than zero." };
    }
  }
  return parsed;
}

export function MenuIcon({ name }: { name: "refresh" | "plus" | "search" | "edit" | "archive" | "tag" | "settings" }) {
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
    case "plus":
      return (
        <svg {...common}>
          <path d="M12 5v14" />
          <path d="M5 12h14" />
        </svg>
      );
    case "search":
      return (
        <svg {...common}>
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3-3" />
        </svg>
      );
    case "edit":
      return (
        <svg {...common}>
          <path d="M12 20h9" />
          <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
        </svg>
      );
    case "archive":
      return (
        <svg {...common}>
          <path d="M4 7h16" />
          <path d="M6 7v13h12V7" />
          <path d="M9 11h6" />
          <path d="M8 3h8l1 4H7z" />
        </svg>
      );
    case "tag":
      return (
        <svg {...common}>
          <path d="M20 13 11 4H4v7l9 9z" />
          <circle cx="7.5" cy="7.5" r=".5" />
        </svg>
      );
    case "settings":
      return (
        <svg {...common}>
          <path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z" />
          <path d="M19.4 15a1.8 1.8 0 0 0 .36 2l.04.04a2.1 2.1 0 1 1-3 3l-.04-.04a1.8 1.8 0 0 0-2-.36 1.8 1.8 0 0 0-1.1 1.66V21a2.1 2.1 0 1 1-4.2 0v-.06a1.8 1.8 0 0 0-1.1-1.66 1.8 1.8 0 0 0-2 .36l-.04.04a2.1 2.1 0 1 1-3-3l.04-.04a1.8 1.8 0 0 0 .36-2 1.8 1.8 0 0 0-1.66-1.1H2a2.1 2.1 0 1 1 0-4.2h.06a1.8 1.8 0 0 0 1.66-1.1 1.8 1.8 0 0 0-.36-2l-.04-.04a2.1 2.1 0 1 1 3-3l.04.04a1.8 1.8 0 0 0 2 .36 1.8 1.8 0 0 0 1.1-1.66V2a2.1 2.1 0 1 1 4.2 0v.06a1.8 1.8 0 0 0 1.1 1.66 1.8 1.8 0 0 0 2-.36l.04-.04a2.1 2.1 0 1 1 3 3l-.04.04a1.8 1.8 0 0 0-.36 2 1.8 1.8 0 0 0 1.66 1.1H22a2.1 2.1 0 1 1 0 4.2h-.06A1.8 1.8 0 0 0 19.4 15z" />
        </svg>
      );
  }
}
