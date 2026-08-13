"use client";

import Link from "next/link";
import type { KeyboardEvent, ReactNode } from "react";
import { money } from "@/app/admin/AdminShell";
import {
  formatDateTime,
  formatRelativeTime,
  fulfillmentLabel,
  MiniIcon,
  shortOrderId,
  StatusBadge,
} from "@/app/admin/orders/orderPresentation";

export type LiveOrder = {
  order_id: string;
  status: string;
  customer_name?: string | null;
  fulfillment_method?: string | null;
  total?: number;
  currency?: string;
  updated_at?: string;
};

export type LiveOrderSummary = {
  total: number;
  awaiting: number;
  preparing: number;
  completed: number;
  failed: number;
};

type IconName = "orders" | "awaiting" | "preparing" | "completed" | "failed" | "user" | "status" | "total" | "question" | "open";

function LiveOrdersIcon({ name, size = 18 }: { name: IconName; size?: number }) {
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
    case "orders":
      return <svg {...common}><path d="M6 7h12l-1 14H7z" /><path d="M9 8V6a3 3 0 0 1 6 0v2" /></svg>;
    case "awaiting":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 7v6l4 2" /></svg>;
    case "preparing":
      return <svg {...common}><path d="M5 11h14v8H5z" /><path d="M8 11V8" /><path d="M12 11V6" /><path d="M16 11V8" /><path d="M3 19h18" /></svg>;
    case "completed":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="m7.5 12 3 3 6-7" /></svg>;
    case "failed":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="m8.5 8.5 7 7" /><path d="m15.5 8.5-7 7" /></svg>;
    case "user":
      return <svg {...common}><circle cx="12" cy="8" r="3.5" /><path d="M5.5 20a6.5 6.5 0 0 1 13 0" /></svg>;
    case "status":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="m8 12 2.5 2.5L16 9" /></svg>;
    case "total":
      return <svg {...common}><path d="M5 7h14v12H5z" /><path d="M8 7V5h7l2 2" /><path d="M9 13h6" /></svg>;
    case "question":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M9.8 9a2.4 2.4 0 0 1 4.6 1c0 1.8-2.4 2-2.4 3.7" /><path d="M12 17h.01" /></svg>;
    case "open":
      return <svg {...common}><path d="M14 4h6v6" /><path d="m20 4-9 9" /><path d="M18 13v7H4V6h7" /></svg>;
  }
}

const summaryItems: Array<{ key: keyof LiveOrderSummary; icon: IconName; label: string; tone: string }> = [
  { key: "total", icon: "orders", label: "Total Loaded", tone: "blue" },
  { key: "awaiting", icon: "awaiting", label: "Awaiting Action", tone: "warning" },
  { key: "preparing", icon: "preparing", label: "In Preparation", tone: "blue" },
  { key: "completed", icon: "completed", label: "Completed or Delivered", tone: "success" },
  { key: "failed", icon: "failed", label: "Failed or Rejected", tone: "danger" },
];

export function LiveOrderKpiStrip({ summary, loading, unavailable }: { summary: LiveOrderSummary; loading: boolean; unavailable: boolean }) {
  return (
    <section className="admin-panel live-orders-kpis" aria-label="Summary of currently loaded orders">
      {summaryItems.map((item) => (
        <article className={`live-orders-kpi is-${item.tone}`} key={item.key}>
          <span className="live-orders-kpi-icon"><LiveOrdersIcon name={item.icon} size={22} /></span>
          <div>
            <span>{item.label}</span>
            {loading ? <strong className="admin-skeleton admin-skeleton-value" /> : <strong>{unavailable ? "—" : summary[item.key]}</strong>}
          </div>
        </article>
      ))}
    </section>
  );
}

function FulfillmentChip({ method }: { method?: string | null }) {
  const tone = method === "delivery" ? "is-delivery" : method === "takeaway" ? "is-takeaway" : "is-unspecified";
  return (
    <span className={`live-orders-fulfillment ${tone}`}>
      {method === "delivery" ? <MiniIcon name="truck" /> : method === "takeaway" ? <MiniIcon name="bag" /> : <LiveOrdersIcon name="question" size={14} />}
      {fulfillmentLabel(method)}
    </span>
  );
}

function selectFromKeyboard(event: KeyboardEvent<HTMLTableRowElement>, onSelect: () => void) {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    onSelect();
  }
}

function isRecentlyUpdated(value?: string | null) {
  if (!value) return false;
  const updated = new Date(value);
  return !Number.isNaN(updated.getTime()) && Date.now() - updated.getTime() < 5 * 60 * 1000;
}

export function LiveOrdersTable({ orders, selectedOrderId, loading, onSelect }: {
  orders: LiveOrder[];
  selectedOrderId?: string;
  loading: boolean;
  onSelect: (orderId: string) => void;
}) {
  return (
    <div className="live-orders-table-wrap">
      <table className="admin-table live-orders-table">
        <thead><tr><th>Order ID</th><th>Customer</th><th>Fulfillment</th><th>Status</th><th>Total</th><th>Updated</th><th>Action</th></tr></thead>
        <tbody>
          {loading && [0, 1, 2, 3, 4].map((row) => (
            <tr className="admin-loading-row" key={row}>
              <td><span className="admin-skeleton admin-skeleton-line" /></td><td><span className="admin-skeleton admin-skeleton-line" /></td>
              <td><span className="admin-skeleton admin-skeleton-pill" /></td><td><span className="admin-skeleton admin-skeleton-pill" /></td>
              <td><span className="admin-skeleton admin-skeleton-line" /></td><td><span className="admin-skeleton admin-skeleton-line" /></td>
              <td><span className="admin-skeleton admin-skeleton-line" /></td>
            </tr>
          ))}
          {!loading && orders.map((order) => {
            const selected = selectedOrderId === order.order_id;
            const recentlyUpdated = isRecentlyUpdated(order.updated_at);
            return (
              <tr
                aria-selected={selected}
                className={`${selected ? "is-selected" : ""}${recentlyUpdated ? " is-recent" : ""}`.trim()}
                key={order.order_id}
                onClick={() => onSelect(order.order_id)}
                onKeyDown={(event) => selectFromKeyboard(event, () => onSelect(order.order_id))}
                tabIndex={0}
              >
                <td data-label="Order ID"><Link className="live-orders-id" href={`/admin/orders/${order.order_id}`} title={order.order_id}>{shortOrderId(order.order_id)}</Link></td>
                <td data-label="Customer"><span className="live-orders-customer"><LiveOrdersIcon name="user" size={14} />{order.customer_name || "Unknown customer"}</span></td>
                <td data-label="Fulfillment"><FulfillmentChip method={order.fulfillment_method} /></td>
                <td data-label="Status"><StatusBadge status={order.status} /></td>
                <td data-label="Total">{money(order.total, order.currency)}</td>
                <td data-label="Updated"><span title={formatDateTime(order.updated_at)}>{formatRelativeTime(order.updated_at)}</span></td>
                <td data-label="Action"><Link className="live-orders-view" href={`/admin/orders/${order.order_id}`}>View</Link></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SnapshotRow({ icon, label, children }: { icon: IconName; label: string; children: ReactNode }) {
  return <div><span><LiveOrdersIcon name={icon} size={16} /></span><dt>{label}</dt><dd>{children}</dd></div>;
}

export function LiveOrderSnapshot({ order }: { order: LiveOrder | null }) {
  return (
    <aside className="admin-panel live-order-snapshot" aria-label="Selected order snapshot">
      <div className="live-orders-section-heading"><h2>Order Snapshot</h2></div>
      {!order ? (
        <div className="live-order-snapshot-empty">
          <span><LiveOrdersIcon name="orders" size={24} /></span>
          <strong>No order selected</strong>
          <p>Select an order from the loaded set to view its current summary.</p>
        </div>
      ) : (
        <>
          <div className="live-order-snapshot-title">
            <strong title={order.order_id}>{shortOrderId(order.order_id)}</strong>
            <StatusBadge status={order.status} />
          </div>
          <dl className="live-order-snapshot-fields">
            {order.customer_name && <SnapshotRow icon="user" label="Customer">{order.customer_name}</SnapshotRow>}
            <SnapshotRow icon={order.fulfillment_method ? "status" : "question"} label="Fulfillment"><FulfillmentChip method={order.fulfillment_method} /></SnapshotRow>
            <SnapshotRow icon="status" label="Status"><StatusBadge status={order.status} /></SnapshotRow>
            {order.total !== undefined && <SnapshotRow icon="total" label="Total"><strong>{money(order.total, order.currency)}</strong></SnapshotRow>}
            {order.updated_at && <SnapshotRow icon="awaiting" label="Last Updated"><span title={formatDateTime(order.updated_at)}>{formatRelativeTime(order.updated_at)}</span></SnapshotRow>}
          </dl>
          <Link className="live-order-open" href={`/admin/orders/${order.order_id}`}><LiveOrdersIcon name="open" size={16} />Open Order</Link>
        </>
      )}
    </aside>
  );
}
