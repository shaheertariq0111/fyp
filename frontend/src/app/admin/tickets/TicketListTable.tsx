import Link from "next/link";
import { TicketIcon, type TicketIconName } from "@/app/admin/tickets/TicketIcon";
import type { AdminTicketListItem } from "@/lib/adminTicketTypes";
import {
  formatTicketDateTime,
  priorityClassName,
  priorityLabel,
  statusClassName,
  statusLabel,
  ticketTypeLabel,
  visibleValue,
} from "@/app/admin/tickets/ticketPresentation";

function ticketDetailHref(ticketId: string) {
  return `/admin/tickets/${encodeURIComponent(ticketId)}`;
}

function StatusBadge({ ticket }: { ticket: AdminTicketListItem }) {
  return (
    <span className={`admin-ticket-badge ${statusClassName(ticket.status)}`}>
      {statusLabel(ticket.status)}
    </span>
  );
}

function PriorityBadge({ ticket }: { ticket: AdminTicketListItem }) {
  return (
    <span className={`admin-ticket-badge ${priorityClassName(ticket.priority)}`}>
      {priorityLabel(ticket.priority)}
    </span>
  );
}

function sourcePresentation(source: string): { className: string; icon: TicketIconName } {
  if (source === "whatsapp") return { className: "is-whatsapp", icon: "whatsapp" };
  return { className: source === "web" ? "is-web" : "is-neutral", icon: "web" };
}

type TicketMetrics = {
  total: number;
  open: number;
  resolved: number;
  urgent: number;
  linked: number;
};

const metricItems: Array<{ key: keyof TicketMetrics; label: string; tone: string; icon: string }> = [
  { key: "total", label: "Total Tickets", tone: "blue", icon: "ticket" },
  { key: "open", label: "Open Tickets", tone: "blue", icon: "folder" },
  { key: "resolved", label: "Resolved / Closed", tone: "success", icon: "check" },
  { key: "urgent", label: "Urgent Tickets", tone: "danger", icon: "warning" },
  { key: "linked", label: "Linked Orders", tone: "purple", icon: "link" },
];

function MetricIcon({ name }: { name: string }) {
  const common = { width: 20, height: 20, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.9, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (name === "folder") return <svg {...common}><path d="M3 6h7l2 2h9v11H3z" /></svg>;
  if (name === "check") return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="m8 12 2.5 2.5L16 9" /></svg>;
  if (name === "warning") return <svg {...common}><path d="m12 3 9 17H3z" /><path d="M12 9v4" /><path d="M12 17h.01" /></svg>;
  if (name === "link") return <svg {...common}><path d="m10 13 4-4" /><path d="M7 16 5 18a3 3 0 0 1-4-4l4-4a3 3 0 0 1 4 0" /><path d="m17 8 2-2a3 3 0 0 1 4 4l-4 4a3 3 0 0 1-4 0" /></svg>;
  return <svg {...common}><path d="M4 7h16v4a2 2 0 0 0 0 4v4H4v-4a2 2 0 0 0 0-4z" /><path d="M12 7v12" /></svg>;
}

export function SupportTicketKpiStrip({ tickets, loading }: { tickets: AdminTicketListItem[]; loading: boolean }) {
  const metrics: TicketMetrics = {
    total: tickets.length,
    open: tickets.filter((ticket) => ticket.status === "open").length,
    resolved: tickets.filter((ticket) => ticket.status === "resolved" || ticket.status === "closed").length,
    urgent: tickets.filter((ticket) => ticket.priority === "urgent").length,
    linked: tickets.filter((ticket) => Boolean(ticket.order_id)).length,
  };
  return (
    <section className="admin-panel support-ticket-kpis" aria-label="Current loaded ticket page metrics">
      {metricItems.map((item) => (
        <article className={`support-ticket-kpi is-${item.tone}`} key={item.key} title="Metric for the currently loaded cursor page">
          <span className="support-ticket-kpi-icon"><MetricIcon name={item.icon} /></span>
          <div><span>{item.label}</span>{loading ? <strong className="admin-skeleton admin-skeleton-value" /> : <strong>{metrics[item.key]}</strong>}</div>
        </article>
      ))}
    </section>
  );
}

function TicketCard({ ticket }: { ticket: AdminTicketListItem }) {
  return (
    <article className="admin-ticket-card">
      <div className="admin-ticket-card-heading">
        <Link href={ticketDetailHref(ticket.ticket_id)}>
          <code>{ticket.ticket_id}</code>
        </Link>
        <StatusBadge ticket={ticket} />
      </div>
      <dl>
        <div><dt>Priority</dt><dd><PriorityBadge ticket={ticket} /></dd></div>
        <div><dt>Type</dt><dd>{ticketTypeLabel(ticket.ticket_type)}</dd></div>
        <div><dt>Category</dt><dd>{visibleValue(ticket.category)}</dd></div>
        <div><dt>Customer</dt><dd>{visibleValue(ticket.customer_name)}</dd></div>
        <div><dt>Phone</dt><dd>{visibleValue(ticket.customer_phone)}</dd></div>
        <div><dt>Order</dt><dd>{ticket.order_id ? <Link href={`/admin/orders/${encodeURIComponent(ticket.order_id)}`}>{ticket.order_id}</Link> : "No linked order"}</dd></div>
        <div><dt>Source</dt><dd>{visibleValue(ticket.source)}</dd></div>
        <div><dt>Created</dt><dd>{formatTicketDateTime(ticket.created_at)}</dd></div>
        <div><dt>Updated</dt><dd>{formatTicketDateTime(ticket.updated_at)}</dd></div>
      </dl>
      <Link className="support-ticket-view" href={ticketDetailHref(ticket.ticket_id)}><TicketIcon name="eye" />View ticket</Link>
    </article>
  );
}

export function TicketListTable({ tickets, rowOffset = 0 }: { tickets: AdminTicketListItem[]; rowOffset?: number }) {
  return (
    <>
      <div className="admin-ticket-table-wrap">
        <table className="admin-table admin-ticket-table" aria-label="Support tickets">
          <thead>
            <tr>
              <th aria-label="Row number" />
              <th>Ticket</th>
              <th>Type / Category</th>
              <th>Customer</th>
              <th>Priority</th>
              <th>Status</th>
              <th>Order</th>
              <th>Source</th>
              <th>Created</th>
              <th>Updated</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {tickets.map((ticket, index) => {
              const source = sourcePresentation(ticket.source);
              return (
              <tr key={ticket.ticket_id}>
                <td aria-label={`Row ${rowOffset + index + 1}`} className="support-ticket-row-number">{rowOffset + index + 1}</td>
                <td>
                  <Link href={ticketDetailHref(ticket.ticket_id)}>
                    <code className="admin-ticket-id">{ticket.ticket_id}</code>
                  </Link>
                </td>
                <td>
                  <strong>{ticketTypeLabel(ticket.ticket_type)}</strong>
                  <small>{visibleValue(ticket.category)}</small>
                </td>
                <td>
                  <strong>{visibleValue(ticket.customer_name)}</strong>
                  <small>{visibleValue(ticket.customer_phone)}</small>
                </td>
                <td><PriorityBadge ticket={ticket} /></td>
                <td><StatusBadge ticket={ticket} /></td>
                <td>
                  {ticket.order_id ? <Link className="support-ticket-order-link" href={`/admin/orders/${encodeURIComponent(ticket.order_id)}`} title={ticket.order_id}>{ticket.order_id}</Link> : "No linked order"}
                </td>
                <td><span className={`support-ticket-source ${source.className}`}><TicketIcon name={source.icon} />{visibleValue(ticket.source)}</span></td>
                <td>{formatTicketDateTime(ticket.created_at)}</td>
                <td>{formatTicketDateTime(ticket.updated_at)}</td>
                <td><Link className="support-ticket-view" href={ticketDetailHref(ticket.ticket_id)}><TicketIcon name="eye" />View</Link></td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="admin-ticket-cards" aria-label="Support ticket cards">
        {tickets.map((ticket) => <TicketCard key={ticket.ticket_id} ticket={ticket} />)}
      </div>
    </>
  );
}

export function TicketListSkeleton({ announce = true }: { announce?: boolean }) {
  return (
    <section
      aria-hidden={announce ? undefined : true}
      aria-live={announce ? "polite" : undefined}
      className="admin-ticket-skeleton"
      role={announce ? "status" : undefined}
    >
      {announce && <span className="admin-visually-hidden">Loading support tickets</span>}
      <div aria-hidden="true" className="admin-ticket-skeleton-table">
        {Array.from({ length: 5 }, (_, index) => (
          <div key={index}>
            {Array.from({ length: 6 }, (__, cellIndex) => (
              <span className="admin-skeleton admin-skeleton-line" key={cellIndex} />
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}
