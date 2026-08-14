"use client";

import Link from "next/link";
import { ReactNode } from "react";
import { money } from "@/app/admin/AdminShell";
import {
  formatDateTime,
  fulfillmentLabel,
  shortOrderId,
  StatusBadge,
  statusLabel,
  statusToneClass,
} from "@/app/admin/orders/orderPresentation";

export type AnalyticsOrder = {
  order_id: string;
  status: string;
  total?: number;
  currency?: string;
};

export type Analytics = {
  today_orders: number;
  active_orders: number;
  revenue: number;
  failed_orders: number;
  by_status: Record<string, number>;
  recent_orders: AnalyticsOrder[];
  chart_window: {
    start_at: string;
    end_at: string;
    timezone: "UTC";
    day_count: 7 | 30;
  };
  orders_revenue_trend: Array<{
    date: string;
    order_count: number;
    revenue_by_currency: Record<string, number>;
  }>;
  orders_by_hour: Array<{
    hour: number;
    order_count: number;
  }>;
  status_distribution: Record<string, number>;
  top_selling_items: Array<{
    item_id: string | null;
    name: string;
    quantity: number;
  }>;
  by_fulfillment: {
    delivery: number;
    takeaway: number;
    unspecified: number;
  };
};

export type OrderDetail = AnalyticsOrder & {
  customer_name?: string | null;
  customer_phone?: string | null;
  fulfillment_method?: string | null;
  delivery_address?: string | null;
  items?: Array<{
    name: string;
    quantity: number;
    line_total?: number;
  }>;
  subtotal?: number;
  delivery_fee?: number | null;
  created_at?: string;
  updated_at?: string;
};

type IconName =
  | "orders"
  | "activity"
  | "revenue"
  | "failed"
  | "received"
  | "accepted"
  | "preparing"
  | "ready"
  | "delivery"
  | "completed"
  | "exception"
  | "health"
  | "document"
  | "view"
  | "open";

export const statusStages: Array<{
  icon: IconName;
  label: string;
  statuses: string[];
  tone: string;
}> = [
  { icon: "received", label: "Received", statuses: ["submitted_to_restaurant"], tone: "blue" },
  { icon: "accepted", label: "Accepted", statuses: ["accepted"], tone: "navy" },
  { icon: "preparing", label: "Preparing", statuses: ["preparing"], tone: "blue" },
  { icon: "ready", label: "Ready", statuses: ["ready_for_pickup"], tone: "teal" },
  { icon: "delivery", label: "Delivery", statuses: ["out_for_delivery"], tone: "neutral" },
  { icon: "completed", label: "Completed", statuses: ["delivered", "completed"], tone: "success" },
];

function OperationsIcon({ name, size = 20 }: { name: IconName; size?: number }) {
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
    case "activity":
      return <svg {...common}><path d="M3 12h4l2-6 4 13 3-9 2 4h3" /></svg>;
    case "revenue":
      return <svg {...common}><ellipse cx="12" cy="6" rx="7" ry="3" /><path d="M5 6v5c0 1.7 3.1 3 7 3s7-1.3 7-3V6" /><path d="M5 11v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5" /></svg>;
    case "failed":
      return <svg {...common}><path d="M12 3 5 6v5c0 4.6 2.9 8.1 7 10 4.1-1.9 7-5.4 7-10V6z" /><path d="M12 8v5" /><path d="M12 16h.01" /></svg>;
    case "received":
      return <svg {...common}><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M4 17v4h16v-4" /></svg>;
    case "accepted":
    case "completed":
    case "health":
      return <svg {...common}><path d="m5 12 4 4L19 6" /></svg>;
    case "preparing":
      return <svg {...common}><path d="M5 11h14v8H5z" /><path d="M8 11V8" /><path d="M12 11V6" /><path d="M16 11V8" /><path d="M3 19h18" /></svg>;
    case "ready":
      return <svg {...common}><path d="M4 18h16" /><path d="M6 18a6 6 0 0 1 12 0" /><path d="M12 9V6" /><path d="M10 6h4" /></svg>;
    case "delivery":
      return <svg {...common}><path d="M3 7h11v10H3z" /><path d="M14 11h4l3 3v3h-7z" /><circle cx="7" cy="18" r="2" /><circle cx="17" cy="18" r="2" /></svg>;
    case "exception":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6" /><path d="m15 9-6 6" /></svg>;
    case "document":
      return <svg {...common}><path d="M6 3h9l3 3v15H6z" /><path d="M15 3v4h4" /><path d="M9 11h6" /><path d="M9 15h6" /></svg>;
    case "view":
      return <svg {...common}><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" /><circle cx="12" cy="12" r="2.5" /></svg>;
    case "open":
      return <svg {...common}><path d="M14 4h6v6" /><path d="m20 4-9 9" /><path d="M18 13v7H4V6h7" /></svg>;
  }
}

function CardHeading({ title, description }: { title: string; description: string }) {
  return (
    <div className="ops-card-heading">
      <h2>{title}</h2>
      <p>{description}</p>
    </div>
  );
}

function snapshotHourLabel(hour: number) {
  const label = (value: number) => {
    const normalized = value % 24;
    if (normalized === 0) return "12 AM";
    if (normalized === 12) return "12 PM";
    return `${normalized > 12 ? normalized - 12 : normalized} ${normalized >= 12 ? "PM" : "AM"}`;
  };
  return `${label(hour)} \u2013 ${label(hour + 1)}`;
}

function OrdersSparkline({ points }: { points: Analytics["orders_revenue_trend"] }) {
  const width = 180;
  const height = 34;
  const inset = 3;
  const maximum = Math.max(...points.map((point) => point.order_count), 1);
  const xStep = (width - inset * 2) / Math.max(points.length - 1, 1);
  const polyline = points.map((point, index) => {
    const x = inset + index * xStep;
    const y = height - inset - (point.order_count / maximum) * (height - inset * 2);
    return `${x},${y}`;
  }).join(" ");
  return (
    <svg
      aria-label={`Daily orders: ${points.map((point) => `${point.date} ${point.order_count}`).join(", ")}`}
      className="ops-revenue-sparkline"
      role="img"
      viewBox={`0 0 ${width} ${height}`}
    >
      <line stroke="#d5dce3" x1={inset} x2={width - inset} y1={height - inset} y2={height - inset} />
      <polyline fill="none" points={polyline} stroke="#168438" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" />
    </svg>
  );
}

export function RevenueOverview({ analytics, loading }: { analytics: Analytics | null; loading: boolean }) {
  const windowOrders = analytics?.orders_revenue_trend.reduce(
    (total, point) => total + point.order_count,
    0,
  ) ?? 0;
  const peakHour = analytics?.orders_by_hour.reduce(
    (peak, point) => point.order_count > peak.order_count ? point : peak,
    analytics.orders_by_hour[0] ?? { hour: 0, order_count: 0 },
  );
  const topItem = analytics?.top_selling_items[0];
  const snapshotLabel = analytics && windowOrders > 0
    ? `${windowOrders} orders in ${analytics.chart_window.day_count} days${peakHour && peakHour.order_count > 0 ? `, peak ${snapshotHourLabel(peakHour.hour)}` : ""}${topItem ? `, top item ${topItem.name}` : ""}`
    : analytics ? "No order activity in this period" : "Analytics snapshot unavailable";
  return (
    <section className="admin-panel ops-revenue-card">
      <div className="ops-revenue-copy">
        <span className="ops-eyebrow">Recorded Revenue</span>
        {loading ? <span className="admin-skeleton admin-skeleton-value" /> : <strong>{analytics ? money(analytics.revenue) : "—"}</strong>}
        <p>{analytics ? "Revenue currently reported by the analytics service." : "Unavailable until analytics reconnects."}</p>
      </div>
      <div className="ops-revenue-visual" aria-label={snapshotLabel} aria-live="polite" role="group">
        <div className="ops-revenue-snapshot-heading">
          <div className="ops-revenue-icon"><OperationsIcon name="revenue" size={20} /></div>
          <div>
            <span>Current analytics snapshot</span>
            {analytics && <small>{analytics.chart_window.day_count} Days {"\u00b7"} UTC</small>}
          </div>
        </div>
        {loading ? (
          <div className="ops-revenue-snapshot-loading" aria-label="Loading current analytics snapshot">
            <span className="admin-skeleton admin-skeleton-line" />
            <span className="admin-skeleton admin-skeleton-line" />
          </div>
        ) : !analytics ? (
          <p className="ops-revenue-snapshot-empty">Analytics snapshot unavailable.</p>
        ) : windowOrders === 0 ? (
          <p className="ops-revenue-snapshot-empty">No order activity in this period.</p>
        ) : (
          <>
            <strong className="ops-revenue-snapshot-total">{windowOrders} {windowOrders === 1 ? "order" : "orders"}</strong>
            <div className="ops-revenue-snapshot-details">
              {peakHour && peakHour.order_count > 0 && <span><b>Peak:</b> {snapshotHourLabel(peakHour.hour)}</span>}
              {topItem && <span><b>Top:</b> {topItem.name}</span>}
            </div>
            <OrdersSparkline points={analytics.orders_revenue_trend} />
          </>
        )}
      </div>
    </section>
  );
}

type Kpi = {
  key: "orders" | "activity" | "revenue" | "failed";
  label: string;
  value: string;
  description: string;
  urgent?: boolean;
};

export function OperationsKpiStrip({ analytics, loading }: { analytics: Analytics | null; loading: boolean }) {
  const unavailable = "—";
  const kpis: Kpi[] = [
    {
      key: "orders",
      label: "Today's Orders",
      value: analytics ? String(analytics.today_orders) : unavailable,
      description: analytics ? "Orders submitted since the start of the current day." : "Unavailable until analytics reconnects.",
    },
    {
      key: "activity",
      label: "Active Orders",
      value: analytics ? String(analytics.active_orders) : unavailable,
      description: analytics ? "Orders currently moving through operations." : "Unavailable until analytics reconnects.",
    },
    {
      key: "revenue",
      label: "Revenue",
      value: analytics ? money(analytics.revenue) : unavailable,
      description: analytics ? "Recorded order revenue from the analytics service." : "Unavailable until analytics reconnects.",
    },
    {
      key: "failed",
      label: "Failed Orders",
      value: analytics ? String(analytics.failed_orders) : unavailable,
      description: analytics
        ? analytics.failed_orders > 0 ? "Orders need staff attention." : "No failed orders reported."
        : "Unavailable until analytics reconnects.",
      urgent: Boolean(analytics && analytics.failed_orders > 0),
    },
  ];

  return (
    <section className="admin-panel ops-kpi-strip" aria-label="Restaurant metrics">
      {kpis.map((kpi) => (
        <article className={`ops-kpi ops-kpi-${kpi.key}${kpi.urgent ? " is-urgent" : ""}`} key={kpi.key}>
          <div className="ops-kpi-icon"><OperationsIcon name={kpi.key} size={21} /></div>
          <div>
            <span>{kpi.label}</span>
            {loading ? <strong className="admin-skeleton admin-skeleton-value" /> : <strong>{kpi.value}</strong>}
            <p>{kpi.description}</p>
          </div>
        </article>
      ))}
    </section>
  );
}

export function OrderStatusPipeline({ analytics, loading }: { analytics: Analytics | null; loading: boolean }) {
  return (
    <section className="admin-panel ops-pipeline-card">
      <CardHeading title="Order Status Pipeline" description="Current order counts grouped by operational stage." />
      <div className="ops-pipeline-scroll">
        <div className="ops-pipeline">
          {statusStages.map((stage) => {
            const count = analytics
              ? stage.statuses.reduce((total, status) => total + (analytics.by_status[status] ?? 0), 0)
              : null;
            return (
              <article className={`ops-pipeline-stage is-${stage.tone}`} key={stage.label}>
                <div className="ops-stage-track">
                  <span className="ops-stage-icon"><OperationsIcon name={stage.icon} size={22} /></span>
                </div>
                <strong>{stage.label}</strong>
                {loading ? <span className="admin-skeleton admin-skeleton-count" /> : <b>{count ?? "—"}</b>}
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}

export function OtherStatuses({ statuses, loading }: { statuses: Array<[string, number]>; loading: boolean }) {
  return (
    <section className="admin-panel ops-other-card">
      <CardHeading title="Other Statuses" description="Orders outside the main operational pipeline." />
      {loading ? (
        <div className="ops-other-list" aria-hidden="true">
          {[0, 1, 2, 3].map((row) => <span className="admin-skeleton admin-skeleton-line" key={row} />)}
        </div>
      ) : statuses.length > 0 ? (
        <ul className="ops-other-list">
          {statuses.map(([status, count]) => (
            <li className={statusToneClass(status)} key={status}>
              <span className={`ops-other-icon ${statusToneClass(status)}`}><OperationsIcon name="exception" size={14} /></span>
              <span>{statusLabel(status)}</span>
              <strong>{count}</strong>
            </li>
          ))}
        </ul>
      ) : (
        <div className="ops-compact-empty">No orders in other statuses.</div>
      )}
    </section>
  );
}

export function OperationalHealth({ analytics, loading }: { analytics: Analytics | null; loading: boolean }) {
  const hasFailures = Boolean(analytics && analytics.failed_orders > 0);
  return (
    <section className="admin-panel ops-health-card">
      <CardHeading title="Operational Health" description="Current failed-order signal from analytics." />
      <div className={`ops-health-state${hasFailures ? " is-alert" : ""}${!analytics && !loading ? " is-unavailable" : ""}`}>
        <span className="ops-health-icon"><OperationsIcon name={hasFailures ? "failed" : "health"} size={31} /></span>
        {loading ? (
          <span className="admin-skeleton admin-skeleton-line" />
        ) : (
          <>
            <strong>{!analytics ? "Operational status unavailable" : hasFailures ? "Failed orders need review" : "Operations are running normally"}</strong>
            <p>{!analytics ? "Retry analytics to restore current health data." : hasFailures ? `${analytics.failed_orders} failed ${analytics.failed_orders === 1 ? "order requires" : "orders require"} staff attention.` : "No failed orders are currently reported by analytics."}</p>
            {hasFailures && <Link href="/admin/orders">Review live orders</Link>}
          </>
        )}
      </div>
    </section>
  );
}

export function RecentOrders({
  orders,
  selectedOrderId,
  loading,
  available,
  onSelect,
}: {
  orders: AnalyticsOrder[];
  selectedOrderId: string | null;
  loading: boolean;
  available: boolean;
  onSelect: (orderId: string) => void;
}) {
  return (
    <section className="admin-panel ops-recent-card">
      <CardHeading title="Recent Orders" description="Latest orders returned by analytics." />
      {loading ? (
        <div className="ops-orders-skeleton">
          {[0, 1, 2, 3, 4, 5].map((row) => <span className="admin-skeleton admin-skeleton-line" key={row} />)}
        </div>
      ) : orders.length > 0 ? (
        <div className="ops-orders-table-wrap">
          <table className="ops-orders-table">
            <thead><tr><th aria-label="Selection" /><th>Order</th><th>Status</th><th>Total</th><th>Action</th></tr></thead>
            <tbody>
              {orders.map((order) => {
                const isSelected = selectedOrderId === order.order_id;
                return (
                  <tr className={isSelected ? "is-selected" : ""} key={order.order_id}>
                    <td><span className={`ops-selection-dot${isSelected ? " is-selected" : ""}`} aria-hidden="true" /></td>
                    <td><span className="ops-order-id" title={order.order_id}>{shortOrderId(order.order_id)}</span></td>
                    <td><StatusBadge status={order.status} /></td>
                    <td>{money(order.total, order.currency)}</td>
                    <td>
                      <button className="ops-view-order" onClick={() => onSelect(order.order_id)} type="button" aria-pressed={isSelected}>
                        <OperationsIcon name="view" size={14} />
                        View Order
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="admin-empty-state">
          <strong>{available ? "No recent orders" : "Recent orders unavailable"}</strong>
          <p>{available ? "Recent order activity will appear here after customers submit orders." : "Analytics must reconnect before recent order activity can be shown."}</p>
        </div>
      )}
    </section>
  );
}

function SnapshotField({ label, children }: { label: string; children: ReactNode }) {
  return <div><dt>{label}</dt><dd>{children}</dd></div>;
}

export function OrderSnapshot({
  order,
  selectedOrderId,
  loading,
  error,
  onRetry,
}: {
  order: OrderDetail | null;
  selectedOrderId: string | null;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}) {
  const itemCount = order?.items?.reduce((total, item) => total + (item.quantity || 0), 0);
  const hasSummary = order && (
    itemCount !== undefined || order.subtotal !== undefined || order.delivery_fee !== undefined || order.total !== undefined
  );

  return (
    <section className="admin-panel ops-snapshot-card">
      <CardHeading title="Order Snapshot" description="Selected order details from the live order service." />
      {!selectedOrderId ? (
        <div className="ops-snapshot-empty">
          <span><OperationsIcon name="document" size={26} /></span>
          <strong>Select a recent order</strong>
          <p>Choose an order from the list to inspect its current details.</p>
        </div>
      ) : loading ? (
        <div className="ops-snapshot-skeleton">
          <span className="admin-skeleton admin-skeleton-value" />
          {[0, 1, 2, 3, 4].map((row) => <span className="admin-skeleton admin-skeleton-line" key={row} />)}
        </div>
      ) : error || !order ? (
        <div className="ops-snapshot-empty is-error" role="alert">
          <span><OperationsIcon name="failed" size={26} /></span>
          <strong>Order details unavailable</strong>
          <p>The selected order could not be loaded. You can retry without leaving this page.</p>
          <button className="secondary" onClick={onRetry} type="button">Retry</button>
        </div>
      ) : (
        <>
          <div className="ops-snapshot-title">
            <span className="ops-document-icon"><OperationsIcon name="document" size={22} /></span>
            <div><small>Order</small><strong title={order.order_id}>{shortOrderId(order.order_id)}</strong></div>
            <StatusBadge status={order.status} />
          </div>
          <div className="ops-snapshot-body">
            <dl className="ops-snapshot-meta">
              <SnapshotField label="Order ID"><span title={order.order_id}>{shortOrderId(order.order_id)}</span></SnapshotField>
              <SnapshotField label="Current Status"><StatusBadge status={order.status} /></SnapshotField>
              {order.total !== undefined && <SnapshotField label="Order Total">{money(order.total, order.currency)}</SnapshotField>}
              {order.fulfillment_method && <SnapshotField label="Fulfillment Type">{fulfillmentLabel(order.fulfillment_method)}</SnapshotField>}
              {order.created_at && <SnapshotField label="Placed At">{formatDateTime(order.created_at)}</SnapshotField>}
              {order.customer_name && <SnapshotField label="Customer">{order.customer_name}</SnapshotField>}
            </dl>
            {hasSummary && (
              <div className="ops-order-summary">
                <h3>Order Summary</h3>
                {itemCount !== undefined && <div><span>Items</span><strong>{itemCount}</strong></div>}
                {order.subtotal !== undefined && <div><span>Subtotal</span><strong>{money(order.subtotal, order.currency)}</strong></div>}
                {order.delivery_fee !== undefined && order.delivery_fee !== null && <div><span>Delivery Fee</span><strong>{money(order.delivery_fee, order.currency)}</strong></div>}
                {order.total !== undefined && <div className="is-total"><span>Total</span><strong>{money(order.total, order.currency)}</strong></div>}
              </div>
            )}
          </div>
          <Link className="ops-open-order" href={`/admin/orders/${order.order_id}`}>
            <OperationsIcon name="open" size={16} />
            Open Order
          </Link>
        </>
      )}
    </section>
  );
}
