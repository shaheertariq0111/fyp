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

function TicketCard({ ticket }: { ticket: AdminTicketListItem }) {
  return (
    <article className="admin-ticket-card">
      <div className="admin-ticket-card-heading">
        <code>{ticket.ticket_id}</code>
        <StatusBadge ticket={ticket} />
      </div>
      <dl>
        <div><dt>Priority</dt><dd><PriorityBadge ticket={ticket} /></dd></div>
        <div><dt>Type</dt><dd>{ticketTypeLabel(ticket.ticket_type)}</dd></div>
        <div><dt>Category</dt><dd>{visibleValue(ticket.category)}</dd></div>
        <div><dt>Customer</dt><dd>{visibleValue(ticket.customer_name)}</dd></div>
        <div><dt>Phone</dt><dd>{visibleValue(ticket.customer_phone)}</dd></div>
        <div><dt>Order</dt><dd>{visibleValue(ticket.order_id, "No linked order")}</dd></div>
        <div><dt>Source</dt><dd>{visibleValue(ticket.source)}</dd></div>
        <div><dt>Created</dt><dd>{formatTicketDateTime(ticket.created_at)}</dd></div>
        <div><dt>Updated</dt><dd>{formatTicketDateTime(ticket.updated_at)}</dd></div>
      </dl>
    </article>
  );
}

export function TicketListTable({ tickets }: { tickets: AdminTicketListItem[] }) {
  return (
    <>
      <div className="admin-ticket-table-wrap">
        <table className="admin-table admin-ticket-table" aria-label="Support tickets">
          <thead>
            <tr>
              <th>Ticket</th>
              <th>Type / Category</th>
              <th>Customer</th>
              <th>Priority</th>
              <th>Status</th>
              <th>Order</th>
              <th>Source</th>
              <th>Created</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {tickets.map((ticket) => (
              <tr key={ticket.ticket_id}>
                <td><code className="admin-ticket-id">{ticket.ticket_id}</code></td>
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
                <td>{visibleValue(ticket.order_id, "No linked order")}</td>
                <td>{visibleValue(ticket.source)}</td>
                <td>{formatTicketDateTime(ticket.created_at)}</td>
                <td>{formatTicketDateTime(ticket.updated_at)}</td>
              </tr>
            ))}
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
