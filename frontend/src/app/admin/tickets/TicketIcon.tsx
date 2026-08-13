export type TicketIconName =
  | "actions"
  | "customer"
  | "description"
  | "eye"
  | "linkedOrder"
  | "metadata"
  | "notes"
  | "orderContext"
  | "priorityHistory"
  | "statusHistory"
  | "web"
  | "whatsapp";

export function TicketIcon({ name, size = 15 }: { name: TicketIconName; size?: number }) {
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
    case "eye":
      return <svg {...common}><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" /><circle cx="12" cy="12" r="2.5" /></svg>;
    case "whatsapp":
      return <svg {...common}><path d="M20 11.5a8 8 0 0 1-11.8 7L4 20l1.4-4.1A8 8 0 1 1 20 11.5Z" /><path d="M8.5 8.2c.4 3.1 2.2 5 5.4 5.8" /><path d="m8.5 8.2 1.4-.5 1.1 2-1 .8" /><path d="m13.9 14 1-1 1.9 1.1-.5 1.4" /></svg>;
    case "web":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M3 12h18" /><path d="M12 3c2.2 2.4 3.3 5.4 3.3 9S14.2 18.6 12 21" /><path d="M12 3c-2.2 2.4-3.3 5.4-3.3 9S9.8 18.6 12 21" /></svg>;
    case "description":
      return <svg {...common}><path d="M5 5h14v11H9l-4 4Z" /><path d="M9 9h6" /><path d="M9 12h4" /></svg>;
    case "customer":
      return <svg {...common}><circle cx="12" cy="8" r="3.5" /><path d="M5.5 20a6.5 6.5 0 0 1 13 0" /></svg>;
    case "orderContext":
      return <svg {...common}><path d="M5 7h14v12H5z" /><path d="M8 7V5h7l2 2" /><path d="M9 13h6" /></svg>;
    case "linkedOrder":
      return <svg {...common}><path d="m10 13 4-4" /><path d="M7 16 5 18a3 3 0 0 1-4-4l4-4a3 3 0 0 1 4 0" /><path d="m17 8 2-2a3 3 0 0 1 4 4l-4 4a3 3 0 0 1-4 0" /></svg>;
    case "statusHistory":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /><path d="m7.5 12 2 2 4-5" /></svg>;
    case "priorityHistory":
      return <svg {...common}><path d="M5 21V4" /><path d="M5 5h11l-2 4 2 4H5" /></svg>;
    case "notes":
      return <svg {...common}><path d="M6 3h9l3 3v15H6z" /><path d="M15 3v4h4" /><path d="M9 11h6" /><path d="M9 15h6" /></svg>;
    case "actions":
      return <svg {...common}><path d="M12 3 5 6v5c0 4.6 2.9 8.1 7 10 4.1-1.9 7-5.4 7-10V6z" /><path d="m9 12 2 2 4-5" /></svg>;
    case "metadata":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 11v5" /><path d="M12 8h.01" /></svg>;
  }
}
