"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AdminShell, humanizeStatus, money } from "@/app/admin/AdminShell";
import { adminGet } from "@/lib/adminApi";

type Analytics = {
  today_orders: number;
  active_orders: number;
  revenue: number;
  failed_orders: number;
  by_status: Record<string, number>;
  recent_orders: Array<{ order_id: string; status: string; total?: number; currency?: string }>;
};

type MetricKey = "today" | "active" | "revenue" | "failed";
type RefreshSource = "initial" | "manual" | "auto";
type RefreshFailure = {
  attemptedAt: Date;
  hadPreviousData: boolean;
};

const statusStages: Array<{ label: string; statuses: string[] }> = [
  { label: "Received", statuses: ["submitted_to_restaurant"] },
  { label: "Accepted", statuses: ["accepted"] },
  { label: "Preparing", statuses: ["preparing"] },
  { label: "Ready", statuses: ["ready_for_pickup"] },
  { label: "Delivery", statuses: ["out_for_delivery"] },
  { label: "Completed", statuses: ["delivered", "completed"] },
];

function DashboardIcon({ name }: { name: MetricKey }) {
  const common = {
    width: 22,
    height: 22,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  switch (name) {
    case "today":
      return (
        <svg {...common}>
          <path d="M7 3v4" />
          <path d="M17 3v4" />
          <path d="M4 9h16" />
          <path d="M5 5h14v15H5z" />
        </svg>
      );
    case "active":
      return (
        <svg {...common}>
          <path d="M4 12h4l3-7 4 14 3-7h2" />
        </svg>
      );
    case "revenue":
      return (
        <svg {...common}>
          <path d="M12 2v20" />
          <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7H14a3.5 3.5 0 0 1 0 7H6" />
        </svg>
      );
    case "failed":
      return (
        <svg {...common}>
          <path d="M12 9v4" />
          <path d="M12 17h.01" />
          <path d="M10.3 3.9 2.9 17a2 2 0 0 0 1.7 3h14.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
        </svg>
      );
  }
}

function shortOrderId(orderId: string) {
  if (orderId.length <= 12) {
    return orderId;
  }
  return `${orderId.slice(0, 6)}...${orderId.slice(-4)}`;
}

function formatRefreshTime(value: Date) {
  return value.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatRefreshStatus(lastSuccess: Date | null, failure: RefreshFailure | null) {
  if (failure && !lastSuccess) {
    return `Refresh failed at ${formatRefreshTime(failure.attemptedAt)}`;
  }
  if (failure && lastSuccess) {
    return `Update failed at ${formatRefreshTime(failure.attemptedAt)} · Showing previous data`;
  }
  if (lastSuccess) {
    return `Updated ${formatRefreshTime(lastSuccess)}`;
  }
  if (!failure) {
    return "Not refreshed yet";
  }
  return `Refresh failed at ${formatRefreshTime(failure.attemptedAt)}`;
}

function MetricCard({
  icon,
  label,
  value,
  description,
  loading,
  urgent,
}: {
  icon: MetricKey;
  label: string;
  value: string;
  description: string;
  loading: boolean;
  urgent?: boolean;
}) {
  return (
    <article className={`admin-metric-card admin-metric-${icon}${urgent ? " is-urgent" : ""}`}>
      <div className="admin-metric-icon">
        <DashboardIcon name={icon} />
      </div>
      <div>
        <span>{label}</span>
        {loading ? <strong className="admin-skeleton admin-skeleton-value" /> : <strong>{value}</strong>}
        <p>{description}</p>
      </div>
    </article>
  );
}

export default function AdminDashboardPage() {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [refreshFailure, setRefreshFailure] = useState<RefreshFailure | null>(null);
  const [lastSuccessfulRefresh, setLastSuccessfulRefresh] = useState<Date | null>(null);
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const requestInFlight = useRef(false);
  const analyticsRef = useRef<Analytics | null>(null);
  const manualFailureLogged = useRef(false);

  const loadAnalytics = useCallback(async (source: RefreshSource) => {
    if (requestInFlight.current) {
      return;
    }
    requestInFlight.current = true;
    setIsRefreshing(true);
    try {
      const result = await adminGet<Analytics>("/api/admin/analytics");
      analyticsRef.current = result;
      setAnalytics(result);
      setLastSuccessfulRefresh(new Date());
      setRefreshFailure(null);
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      const attemptedAt = new Date();
      const hadPreviousData = analyticsRef.current !== null;
      setRefreshFailure({ attemptedAt, hadPreviousData });
      if (source === "manual" && !manualFailureLogged.current) {
        console.warn("Admin analytics refresh failed", exc);
        manualFailureLogged.current = true;
      }
    } finally {
      setIsInitialLoading(false);
      setIsRefreshing(false);
      requestInFlight.current = false;
    }
  }, []);

  useEffect(() => {
    void loadAnalytics("initial");
    const timer = window.setInterval(() => {
      void loadAnalytics("auto");
    }, 30000);
    return () => {
      window.clearInterval(timer);
    };
  }, [loadAnalytics]);

  const knownStatuses = useMemo(() => new Set(statusStages.flatMap((stage) => stage.statuses)), []);
  const otherStatuses = Object.entries(analytics?.by_status ?? {}).filter(([status]) => !knownStatuses.has(status));

  const refreshActions = (
    <div className="admin-dashboard-actions">
      <span aria-live="polite">{formatRefreshStatus(lastSuccessfulRefresh, refreshFailure)}</span>
      <button className="admin-refresh-button" disabled={isRefreshing} onClick={() => void loadAnalytics("manual")} type="button">
        {isRefreshing ? "Refreshing..." : "Refresh"}
      </button>
    </div>
  );

  const hasAnalytics = analytics !== null;
  const failedOrders = analytics?.failed_orders;
  const hasInitialFailure = !hasAnalytics && refreshFailure !== null;
  const hasStaleDataWarning = hasAnalytics && refreshFailure !== null && refreshFailure.hadPreviousData;
  const showSkeleton = isInitialLoading && !hasAnalytics && !refreshFailure;
  const unavailableValue = "—";

  return (
    <AdminShell
      actions={refreshActions}
      subtitle="Live restaurant performance and order activity"
      title="Operations Overview"
    >
      <div className="admin-dashboard">
        {hasInitialFailure && (
          <section className="admin-error-panel" role="alert">
            <div>
              <strong>Analytics unavailable</strong>
              <p>Dashboard analytics could not be loaded. Retry when the service is reachable.</p>
            </div>
            <button className="secondary" disabled={isRefreshing} onClick={() => void loadAnalytics("manual")} type="button">
              Retry
            </button>
          </section>
        )}
        {hasStaleDataWarning && (
          <section className="admin-warning-panel" role="status">
            <div>
              <strong>Latest refresh failed</strong>
              <p>Showing the previous analytics values until the next successful update.</p>
            </div>
            <button className="secondary" disabled={isRefreshing} onClick={() => void loadAnalytics("manual")} type="button">
              Retry
            </button>
          </section>
        )}

        <section className="admin-metrics" aria-label="Restaurant metrics">
          <MetricCard
            description={hasAnalytics ? "Orders submitted since the start of the day." : "Unavailable until analytics reconnects."}
            icon="today"
            label="Today's Orders"
            loading={showSkeleton}
            value={hasAnalytics ? `${analytics.today_orders}` : unavailableValue}
          />
          <MetricCard
            description={hasAnalytics ? "Orders currently moving through operations." : "Unavailable until analytics reconnects."}
            icon="active"
            label="Active Orders"
            loading={showSkeleton}
            value={hasAnalytics ? `${analytics.active_orders}` : unavailableValue}
          />
          <MetricCard
            description={hasAnalytics ? "Recorded order revenue for today." : "Unavailable until analytics reconnects."}
            icon="revenue"
            label="Revenue"
            loading={showSkeleton}
            value={hasAnalytics ? money(analytics.revenue) : unavailableValue}
          />
          <MetricCard
            description={
              !hasAnalytics
                ? "Unavailable until analytics reconnects."
                : failedOrders && failedOrders > 0
                  ? "Orders need staff attention."
                  : "No failed orders reported."
            }
            icon="failed"
            label="Failed Orders"
            loading={showSkeleton}
            urgent={Boolean(failedOrders && failedOrders > 0)}
            value={hasAnalytics ? `${failedOrders ?? 0}` : unavailableValue}
          />
        </section>

        <div className="admin-dashboard-grid">
          <section className="admin-panel admin-status-panel">
            <div className="admin-section-heading">
              <div>
                <h2>Order Status Pipeline</h2>
                <p>Current order counts grouped by operational stage.</p>
              </div>
            </div>
            <div className="admin-status-pipeline">
              {statusStages.map((stage) => {
                const count = hasAnalytics
                  ? stage.statuses.reduce((total, status) => total + (analytics.by_status?.[status] ?? 0), 0)
                  : null;
                return (
                  <article className="admin-status-stage" key={stage.label}>
                    {showSkeleton ? (
                      <span className="admin-status-step">
                        <span className="admin-skeleton admin-skeleton-count" />
                      </span>
                    ) : (
                      <span className={`admin-status-step${count === null ? " is-unavailable" : ""}`}>
                        {count === null ? unavailableValue : count}
                      </span>
                    )}
                    <div>
                      <strong>{stage.label}</strong>
                    </div>
                  </article>
                );
              })}
            </div>
            {otherStatuses.length > 0 && (
              <div className="admin-other-statuses">
                <strong>Other statuses</strong>
                <div>
                  {otherStatuses.map(([status, count]) => (
                    <span className="admin-status-badge" key={status}>
                      {humanizeStatus(status)}: {count}
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div className={`admin-ops-callout${failedOrders && failedOrders > 0 ? " is-alert" : ""}${!hasAnalytics ? " is-unavailable" : ""}`}>
              <strong>
                {!hasAnalytics
                  ? "Operational status is unavailable until analytics reconnects."
                  : failedOrders && failedOrders > 0
                    ? "Failed orders need review"
                    : "Operations are running normally"}
              </strong>
              <p>
                {!hasAnalytics
                  ? "Retry analytics to restore current restaurant activity."
                  : failedOrders && failedOrders > 0
                    ? "Review live orders and resolve any failed handoffs."
                    : "No failed orders are reported by the analytics endpoint."}
              </p>
              {failedOrders && failedOrders > 0 && <Link href="/admin/orders">Open Live Orders</Link>}
            </div>
          </section>

          <section className="admin-panel admin-recent-orders-panel">
            <div className="admin-section-heading">
              <div>
                <h2>Recent Orders</h2>
                <p>Latest orders returned by analytics.</p>
              </div>
            </div>
            <div className="admin-responsive-table">
              <table className="admin-table admin-recent-orders-table">
                <thead>
                  <tr>
                    <th>Order</th>
                    <th>Status</th>
                    <th>Total</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {showSkeleton &&
                    [0, 1, 2].map((row) => (
                      <tr key={row}>
                        <td><span className="admin-skeleton admin-skeleton-line" /></td>
                        <td><span className="admin-skeleton admin-skeleton-pill" /></td>
                        <td><span className="admin-skeleton admin-skeleton-line" /></td>
                        <td><span className="admin-skeleton admin-skeleton-line" /></td>
                      </tr>
                    ))}
                  {!showSkeleton &&
                    (analytics?.recent_orders ?? []).map((order) => (
                      <tr key={order.order_id}>
                        <td>
                          <span aria-label={`Order ${order.order_id}`} title={order.order_id}>
                            {shortOrderId(order.order_id)}
                          </span>
                        </td>
                        <td>
                          <span className="admin-status-badge">{humanizeStatus(order.status)}</span>
                        </td>
                        <td>{money(order.total, order.currency)}</td>
                        <td>
                          <Link className="admin-text-link" href={`/admin/orders/${order.order_id}`}>
                            View order
                          </Link>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
            {!showSkeleton && !hasAnalytics && (
              <div className="admin-empty-state">
                <strong>Recent orders unavailable</strong>
                <p>Analytics must reconnect before recent order activity can be shown.</p>
              </div>
            )}
            {!showSkeleton && hasAnalytics && analytics.recent_orders.length === 0 && (
              <div className="admin-empty-state">
                <strong>No recent orders</strong>
                <p>Recent order activity will appear here after customers submit orders.</p>
              </div>
            )}
          </section>
        </div>
      </div>
    </AdminShell>
  );
}
