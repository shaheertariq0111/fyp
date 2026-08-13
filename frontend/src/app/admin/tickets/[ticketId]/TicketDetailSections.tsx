import Link from "next/link";
import { money } from "@/app/admin/AdminShell";
import { statusToneClass } from "@/app/admin/orders/orderPresentation";
import { TicketIcon, type TicketIconName } from "@/app/admin/tickets/TicketIcon";
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

function SectionHeading({ children, icon }: { children: React.ReactNode; icon: TicketIconName }) {
  return <h2><TicketIcon name={icon} /><span>{children}</span></h2>;
}

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
      <DetailFields>
        <DetailField label="Ticket ID"><code className="admin-ticket-detail-id">{ticket.ticket_id}</code></DetailField>
        <DetailField label="Ticket Type">{ticketTypeLabel(ticket.ticket_type)}</DetailField>
        <DetailField label="Category">{visibleValue(ticket.category)}</DetailField>
        <DetailField label="Created">{formatTicketDateTime(ticket.created_at)}</DetailField>
        <DetailField label="Updated">{formatTicketDateTime(ticket.updated_at)}</DetailField>
        <DetailField label="Current Status"><span className={`admin-ticket-badge ${statusClassName(ticket.status)}`}>{statusLabel(ticket.status)}</span></DetailField>
        <DetailField label="Current Priority"><span className={`admin-ticket-badge ${priorityClassName(ticket.priority)}`}>{priorityLabel(ticket.priority)}</span></DetailField>
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
      <SectionHeading icon="customer">Customer</SectionHeading>
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
      <SectionHeading icon="description">Description</SectionHeading>
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
        <SectionHeading icon="orderContext">Order Context</SectionHeading>
        <DetailFields>
          <DetailField label="Linked order ID">
            {ticket.order_id ? <Link className="admin-ticket-detail-long-value" href={`/admin/orders/${encodeURIComponent(ticket.order_id)}`} title={ticket.order_id}>{ticket.order_id}</Link> : "No linked order"}
          </DetailField>
          <DetailField label="Order status snapshot">
            {ticket.order_status_snapshot ? (
              <span className={`admin-status-badge ${statusToneClass(ticket.order_status_snapshot)}`}>
                <span aria-hidden="true" />
                {ticket.order_status_snapshot}
              </span>
            ) : "Not available"}
          </DetailField>
        </DetailFields>
      </section>
      <section
        aria-label="Linked order details"
        className="admin-ticket-detail-panel"
      >
        <SectionHeading icon="linkedOrder">Linked Order Details</SectionHeading>
        {linkedOrder ? (
          <DetailFields>
            <DetailField label="Order ID">
              <Link className="admin-ticket-detail-long-value" href={`/admin/orders/${encodeURIComponent(linkedOrder.order_id)}`} title={linkedOrder.order_id}>{linkedOrder.order_id}</Link>
            </DetailField>
            <DetailField label="Status">
              <span className={`admin-status-badge ${statusToneClass(linkedOrder.status)}`}>
                <span aria-hidden="true" />
                {linkedOrder.status}
              </span>
            </DetailField>
            <DetailField label="Fulfillment method">
              {linkedOrder.fulfillment_method ?? FALLBACK}
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
      <SectionHeading icon="statusHistory">Status History</SectionHeading>
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
      <SectionHeading icon="priorityHistory">Priority History</SectionHeading>
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
      <SectionHeading icon="notes">Internal Notes History</SectionHeading>
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
      <SectionHeading icon="metadata">Metadata</SectionHeading>
      <DetailFields>
        <DetailField label="Source">{visibleValue(ticket.source, FALLBACK)}</DetailField>
        <DetailField label="Version">{ticket.version}</DetailField>
      </DetailFields>
    </section>
  );
}

export function TicketDetailSections({
  ticket,
  actions,
}: {
  ticket: AdminTicketDetail;
  actions?: React.ReactNode;
}) {
  return (
    <div className="admin-ticket-detail-layout">
      <Link className="admin-ticket-detail-back" href="/admin/tickets">
        <span aria-hidden="true">← </span>
        Back to Support Tickets
      </Link>
      <TicketHeader ticket={ticket} />
      <div className="support-ticket-detail-context">
        <DescriptionSection ticket={ticket} />
        <CustomerSection ticket={ticket} />
        <OrderSections ticket={ticket} />
        <StatusHistorySection ticket={ticket} />
        <PriorityHistorySection ticket={ticket} />
        <NotesSection ticket={ticket} />
      </div>
      <div className="support-ticket-detail-controls">
        {actions}
        <MetadataSection ticket={ticket} />
      </div>
    </div>
  );
}
