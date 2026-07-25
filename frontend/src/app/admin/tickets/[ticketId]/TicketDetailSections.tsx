import Link from "next/link";
import { money } from "@/app/admin/AdminShell";
import {
  formatTicketDateTime,
  priorityClassName,
  priorityLabel,
  statusClassName,
  statusLabel,
  ticketTypeLabel,
  visibleValue,
} from "@/app/admin/tickets/ticketPresentation";
import type { AdminTicketDetail } from "@/lib/adminTicketTypes";

const FALLBACK = "Not provided";

function DetailFields({
  children,
}: {
  children: React.ReactNode;
}) {
  return <dl className="admin-ticket-detail-fields">{children}</dl>;
}

function DetailField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function safeTelephoneHref(phone: string | null) {
  if (!phone || !/^\+?[0-9]+$/.test(phone)) {
    return null;
  }
  return `tel:${phone}`;
}

function TicketHeader({ ticket }: { ticket: AdminTicketDetail }) {
  return (
    <section
      aria-label="Ticket summary"
      className="admin-ticket-detail-panel admin-ticket-detail-header"
    >
      <Link className="admin-ticket-detail-back" href="/admin/tickets">
        Back to Support Tickets
      </Link>
      <div className="admin-ticket-detail-heading">
        <div>
          <span className="admin-ticket-detail-eyebrow">Ticket ID</span>
          <code className="admin-ticket-detail-id">{ticket.ticket_id}</code>
        </div>
        <div className="admin-ticket-detail-badges">
          <span className={`admin-ticket-badge ${statusClassName(ticket.status)}`}>
            {statusLabel(ticket.status)}
          </span>
          <span className={`admin-ticket-badge ${priorityClassName(ticket.priority)}`}>
            {priorityLabel(ticket.priority)}
          </span>
        </div>
      </div>
      <DetailFields>
        <DetailField label="Type">{ticketTypeLabel(ticket.ticket_type)}</DetailField>
        <DetailField label="Category">{visibleValue(ticket.category)}</DetailField>
        <DetailField label="Created">{formatTicketDateTime(ticket.created_at)}</DetailField>
        <DetailField label="Updated">{formatTicketDateTime(ticket.updated_at)}</DetailField>
      </DetailFields>
    </section>
  );
}

function CustomerSection({ ticket }: { ticket: AdminTicketDetail }) {
  const phoneHref = safeTelephoneHref(ticket.customer_phone);
  return (
    <section
      aria-label="Customer"
      className="admin-ticket-detail-panel"
    >
      <h2>Customer</h2>
      <DetailFields>
        <DetailField label="Customer ID">
          <span className="admin-ticket-detail-long-value">
            {visibleValue(ticket.customer_id, FALLBACK)}
          </span>
        </DetailField>
        <DetailField label="User ID">
          <span className="admin-ticket-detail-long-value">
            {visibleValue(ticket.user_id, FALLBACK)}
          </span>
        </DetailField>
        <DetailField label="Customer name">
          {visibleValue(ticket.customer_name, FALLBACK)}
        </DetailField>
        <DetailField label="Customer phone">
          {phoneHref ? (
            <a href={phoneHref}>{ticket.customer_phone}</a>
          ) : (
            visibleValue(ticket.customer_phone, FALLBACK)
          )}
        </DetailField>
      </DetailFields>
    </section>
  );
}

function DescriptionSection({ ticket }: { ticket: AdminTicketDetail }) {
  return (
    <section
      aria-label="Description"
      className="admin-ticket-detail-panel admin-ticket-detail-wide"
    >
      <h2>Description</h2>
      <p className="admin-ticket-preserved-text">
        {visibleValue(ticket.description, FALLBACK)}
      </p>
    </section>
  );
}

function OrderSections({ ticket }: { ticket: AdminTicketDetail }) {
  const linkedOrder = ticket.linked_order;
  return (
    <>
      <section aria-label="Order context" className="admin-ticket-detail-panel">
        <h2>Order context</h2>
        <DetailFields>
          <DetailField label="Linked order ID">
            <span className="admin-ticket-detail-long-value">
              {visibleValue(ticket.order_id, "No linked order")}
            </span>
          </DetailField>
          <DetailField label="Order status snapshot">
            {visibleValue(ticket.order_status_snapshot, "Not available")}
          </DetailField>
        </DetailFields>
      </section>
      <section
        aria-label="Linked order details"
        className="admin-ticket-detail-panel"
      >
        <h2>Linked order details</h2>
        {linkedOrder ? (
          <DetailFields>
            <DetailField label="Order ID">
              <span className="admin-ticket-detail-long-value">
                {linkedOrder.order_id}
              </span>
            </DetailField>
            <DetailField label="Status">{linkedOrder.status}</DetailField>
            <DetailField label="Fulfillment method">
              {visibleValue(linkedOrder.fulfillment_method, FALLBACK)}
            </DetailField>
            <DetailField label="Total and currency">
              {money(linkedOrder.total, linkedOrder.currency)} {linkedOrder.currency}
            </DetailField>
            <DetailField label="Created">
              {formatTicketDateTime(linkedOrder.created_at)}
            </DetailField>
            <DetailField label="Updated">
              {formatTicketDateTime(linkedOrder.updated_at)}
            </DetailField>
          </DetailFields>
        ) : (
          <p className="admin-ticket-detail-empty">
            No linked order details are available
          </p>
        )}
      </section>
    </>
  );
}

function StatusHistorySection({ ticket }: { ticket: AdminTicketDetail }) {
  return (
    <section
      aria-label="Status history"
      className="admin-ticket-detail-panel admin-ticket-detail-wide"
    >
      <h2>Status history</h2>
      {ticket.status_history.length ? (
        <ol
          aria-label="Status history entries"
          className="admin-ticket-detail-timeline"
        >
          {ticket.status_history.map((entry, index) => (
            <li key={`${entry.timestamp}-${index}`}>
              <strong>
                {statusLabel(entry.previous_status)} to {statusLabel(entry.new_status)}
              </strong>
              <DetailFields>
                <DetailField label="Actor">{visibleValue(entry.actor, "System")}</DetailField>
                <DetailField label="Timestamp">
                  {formatTicketDateTime(entry.timestamp)}
                </DetailField>
                <DetailField label="Reason">
                  {visibleValue(entry.reason, "No reason provided")}
                </DetailField>
              </DetailFields>
            </li>
          ))}
        </ol>
      ) : (
        <p className="admin-ticket-detail-empty">
          No status changes have been recorded.
        </p>
      )}
    </section>
  );
}

function PriorityHistorySection({ ticket }: { ticket: AdminTicketDetail }) {
  return (
    <section
      aria-label="Priority history"
      className="admin-ticket-detail-panel admin-ticket-detail-wide"
    >
      <h2>Priority history</h2>
      {ticket.priority_history.length ? (
        <ol
          aria-label="Priority history entries"
          className="admin-ticket-detail-timeline"
        >
          {ticket.priority_history.map((entry, index) => (
            <li key={`${entry.timestamp}-${index}`}>
              <strong>
                {priorityLabel(entry.previous_priority)} to{" "}
                {priorityLabel(entry.new_priority)}
              </strong>
              <DetailFields>
                <DetailField label="Actor">{visibleValue(entry.actor, "System")}</DetailField>
                <DetailField label="Timestamp">
                  {formatTicketDateTime(entry.timestamp)}
                </DetailField>
                <DetailField label="Reason">
                  {visibleValue(entry.reason, "No reason provided")}
                </DetailField>
              </DetailFields>
            </li>
          ))}
        </ol>
      ) : (
        <p className="admin-ticket-detail-empty">
          No priority changes have been recorded.
        </p>
      )}
    </section>
  );
}

function NotesSection({ ticket }: { ticket: AdminTicketDetail }) {
  return (
    <section
      aria-label="Internal notes"
      className="admin-ticket-detail-panel admin-ticket-detail-wide"
    >
      <h2>Internal notes</h2>
      {ticket.admin_notes.length ? (
        <ul
          aria-label="Internal note entries"
          className="admin-ticket-detail-timeline"
        >
          {ticket.admin_notes.map((note, index) => (
            <li key={`${note.timestamp}-${note.note_id ?? index}`}>
              <DetailFields>
                <DetailField label="Note ID">
                  {visibleValue(note.note_id, "Not assigned")}
                </DetailField>
                <DetailField label="Actor">{visibleValue(note.actor, "System")}</DetailField>
                <DetailField label="Timestamp">
                  {formatTicketDateTime(note.timestamp)}
                </DetailField>
              </DetailFields>
              <p className="admin-ticket-preserved-text">{note.text}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="admin-ticket-detail-empty">
          No internal notes have been added.
        </p>
      )}
    </section>
  );
}

function MetadataSection({ ticket }: { ticket: AdminTicketDetail }) {
  return (
    <section
      aria-label="Metadata"
      className="admin-ticket-detail-panel admin-ticket-detail-wide"
    >
      <h2>Metadata</h2>
      <DetailFields>
        <DetailField label="Source">{visibleValue(ticket.source, FALLBACK)}</DetailField>
        <DetailField label="Version">{ticket.version}</DetailField>
      </DetailFields>
    </section>
  );
}

export function TicketDetailSections({ ticket }: { ticket: AdminTicketDetail }) {
  return (
    <div className="admin-ticket-detail-layout">
      <TicketHeader ticket={ticket} />
      <CustomerSection ticket={ticket} />
      <DescriptionSection ticket={ticket} />
      <OrderSections ticket={ticket} />
      <StatusHistorySection ticket={ticket} />
      <PriorityHistorySection ticket={ticket} />
      <NotesSection ticket={ticket} />
      <MetadataSection ticket={ticket} />
    </div>
  );
}
