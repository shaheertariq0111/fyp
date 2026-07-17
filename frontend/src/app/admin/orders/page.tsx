"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AdminShell, money } from "@/app/admin/AdminShell";
import { adminGet } from "@/lib/adminApi";
import {
  fulfillmentLabel,
  formatDateTime,
  formatRefreshTime,
  formatRelativeTime,
  MiniIcon,
  ORDER_STATUSES,
  shortOrderId,
  StatusBadge,
  statusLabel,
} from "@/app/admin/orders/orderPresentation";

type Order = {
  order_id: string;
  status: string;
  customer_name?: string | null;
  fulfillment_method?: string | null;
  total?: number;
  currency?: string;
  updated_at?: string;
};

type RefreshSource = "initial" | "manual" | "auto";
type RefreshFailure = {
  attemptedAt: Date;
  hadPreviousData: boolean;
};

const terminalSuccessStatuses = new Set(["completed", "delivered"]);
const dangerStatuses = new Set(["failed", "rejected", "cancelled"]);
const awaitingActionStatuses = new Set(["submitted_to_restaurant", "accepted", "preparing", "ready_for_pickup", "out_for_delivery"]);
const unavailableValue = "—";

function formatFailureTime(value: Date) {
  return value.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function isRecentlyUpdated(value?: string | null) {
  if (!value) {
    return false;
  }
  const updated = new Date(value);
  if (Number.isNaN(updated.getTime())) {
    return false;
  }
  return Date.now() - updated.getTime() < 5 * 60 * 1000;
}

function SummaryCard({
  label,
  value,
  description,
  loading,
}: {
  label: string;
  value: number | string;
  description: string;
  loading: boolean;
}) {
  return (
    <article className="admin-orders-summary-card">
      <span>{label}</span>
      {loading ? <strong className="admin-skeleton admin-skeleton-value" /> : <strong>{value}</strong>}
      <p>{description}</p>
    </article>
  );
}

export default function AdminOrdersPage() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [fulfillmentFilter, setFulfillmentFilter] = useState("");
  const [search, setSearch] = useState("");
  const [hasSuccessfulResponse, setHasSuccessfulResponse] = useState(false);
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const [manualRefreshing, setManualRefreshing] = useState(false);
  const [backgroundRefreshing, setBackgroundRefreshing] = useState(false);
  const [lastSuccessfulRefresh, setLastSuccessfulRefresh] = useState<Date | null>(null);
  const [lastFailedRefresh, setLastFailedRefresh] = useState<RefreshFailure | null>(null);
  const requestInFlight = useRef(false);
  const shouldReloadAfterCurrent = useRef(false);
  const mounted = useRef(false);
  const ordersRef = useRef<Order[]>([]);
  const hasSuccessfulResponseRef = useRef(false);
  const statusFilterRef = useRef(statusFilter);
  const manualFailureLogged = useRef(false);

  const loadOrders = useCallback(async (source: RefreshSource, requestedStatus = statusFilterRef.current) => {
    if (requestInFlight.current) {
      if (source !== "auto") {
        shouldReloadAfterCurrent.current = true;
      }
      return;
    }

    requestInFlight.current = true;
    const refreshKind = source === "manual" ? "manual" : hasSuccessfulResponseRef.current ? "background" : "initial";
    if (refreshKind === "manual") {
      setManualRefreshing(true);
    } else if (refreshKind === "background") {
      setBackgroundRefreshing(true);
    }

    try {
      const suffix = requestedStatus ? `?status=${encodeURIComponent(requestedStatus)}` : "";
      const result = await adminGet<{ orders: Order[] }>(`/api/admin/orders${suffix}`);
      if (!mounted.current) {
        return;
      }

      if (statusFilterRef.current === requestedStatus) {
        ordersRef.current = result.orders;
        hasSuccessfulResponseRef.current = true;
        setOrders(result.orders);
        setHasSuccessfulResponse(true);
        setLastSuccessfulRefresh(new Date());
        setLastFailedRefresh(null);
      } else {
        shouldReloadAfterCurrent.current = true;
      }
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      if (!mounted.current) {
        return;
      }
      setLastFailedRefresh({
        attemptedAt: new Date(),
        hadPreviousData: hasSuccessfulResponseRef.current,
      });
      if (source === "manual" && !manualFailureLogged.current) {
        console.warn("Admin orders refresh failed", exc);
        manualFailureLogged.current = true;
      }
    } finally {
      if (mounted.current) {
        setIsInitialLoading(false);
        if (refreshKind === "manual") {
          setManualRefreshing(false);
        } else if (refreshKind === "background") {
          setBackgroundRefreshing(false);
        }
      }
      requestInFlight.current = false;
      if (mounted.current && shouldReloadAfterCurrent.current) {
        shouldReloadAfterCurrent.current = false;
        void loadOrders("auto", statusFilterRef.current);
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadOrders("auto", statusFilterRef.current);
    }, 10000);
    return () => {
      window.clearInterval(timer);
    };
  }, [loadOrders]);

  useEffect(() => {
    statusFilterRef.current = statusFilter;
    if (!hasSuccessfulResponseRef.current) {
      setIsInitialLoading(true);
    }
    void loadOrders("initial", statusFilter);
  }, [loadOrders, statusFilter]);

  const filteredOrders = useMemo(() => {
    if (!hasSuccessfulResponse) {
      return [];
    }
    const normalizedSearch = search.trim().toLowerCase();
    return orders.filter((order) => {
      const matchesSearch = !normalizedSearch
        || order.order_id.toLowerCase().includes(normalizedSearch)
        || (order.customer_name ?? "").toLowerCase().includes(normalizedSearch);
      const matchesFulfillment = !fulfillmentFilter || order.fulfillment_method === fulfillmentFilter;
      return matchesSearch && matchesFulfillment;
    });
  }, [orders, fulfillmentFilter, hasSuccessfulResponse, search]);

  const summary = useMemo(() => ({
    total: orders.length,
    awaiting: orders.filter((order) => awaitingActionStatuses.has(order.status)).length,
    preparing: orders.filter((order) => order.status === "preparing").length,
    completed: orders.filter((order) => terminalSuccessStatuses.has(order.status)).length,
    failed: orders.filter((order) => dangerStatuses.has(order.status)).length,
  }), [orders]);

  const hasInitialFailure = !hasSuccessfulResponse && lastFailedRefresh !== null;
  const hasStaleWarning = hasSuccessfulResponse && lastFailedRefresh !== null && lastFailedRefresh.hadPreviousData;
  const showSkeleton = isInitialLoading && !hasSuccessfulResponse && !lastFailedRefresh;
  const summaryUnavailable = !hasSuccessfulResponse && lastFailedRefresh !== null;
  const summaryUnavailableText = "Unavailable until orders reconnect.";
  const hasActiveFilters = Boolean(search.trim() || statusFilter || fulfillmentFilter);
  const showFilteredEmpty = !showSkeleton && hasSuccessfulResponse && orders.length > 0 && filteredOrders.length === 0;
  const showNoOrdersEmpty = !showSkeleton && hasSuccessfulResponse && orders.length === 0;

  const clearFilters = () => {
    setSearch("");
    setStatusFilter("");
    setFulfillmentFilter("");
  };

  const actions = (
    <div className="admin-dashboard-actions">
      <span aria-live="polite">
        {lastFailedRefresh && !lastSuccessfulRefresh
          ? `Refresh failed at ${formatFailureTime(lastFailedRefresh.attemptedAt)}`
          : lastFailedRefresh && lastSuccessfulRefresh
            ? `Update failed at ${formatFailureTime(lastFailedRefresh.attemptedAt)} · Showing previous orders`
            : formatRefreshTime(lastSuccessfulRefresh)}
      </span>
      <button className="admin-refresh-button" disabled={manualRefreshing || backgroundRefreshing || showSkeleton} onClick={() => void loadOrders("manual")} type="button">
        <MiniIcon name="refresh" />
        {manualRefreshing ? "Refreshing..." : "Refresh"}
      </button>
    </div>
  );

  return (
    <AdminShell
      actions={actions}
      subtitle="Track and manage restaurant orders in real time"
      title="Live Orders"
    >
      <div className="admin-orders-page">
        {hasInitialFailure && (
          <section className="admin-error-panel" role="alert">
            <div>
              <strong>Orders unavailable</strong>
              <p>Live orders could not be loaded. Retry when the service is reachable.</p>
            </div>
            <button className="secondary" disabled={manualRefreshing || backgroundRefreshing} onClick={() => void loadOrders("manual")} type="button">
              Retry
            </button>
          </section>
        )}
        {hasStaleWarning && (
          <section className="admin-warning-panel" role="status">
            <div>
              <strong>Latest refresh failed</strong>
              <p>Latest refresh failed. Showing previously loaded orders.</p>
            </div>
            <button className="secondary" disabled={manualRefreshing || backgroundRefreshing} onClick={() => void loadOrders("manual")} type="button">
              Retry
            </button>
          </section>
        )}

        <section className="admin-orders-summary" aria-label="Summary of currently loaded orders">
          <SummaryCard description={summaryUnavailable ? summaryUnavailableText : "Orders in the currently loaded response."} label="Total loaded" loading={showSkeleton} value={summaryUnavailable ? unavailableValue : summary.total} />
          <SummaryCard description={summaryUnavailable ? summaryUnavailableText : "Loaded orders waiting for staff movement."} label="Awaiting action" loading={showSkeleton} value={summaryUnavailable ? unavailableValue : summary.awaiting} />
          <SummaryCard description={summaryUnavailable ? summaryUnavailableText : "Loaded orders currently being prepared."} label="In preparation" loading={showSkeleton} value={summaryUnavailable ? unavailableValue : summary.preparing} />
          <SummaryCard description={summaryUnavailable ? summaryUnavailableText : "Loaded orders completed or delivered."} label="Completed or delivered" loading={showSkeleton} value={summaryUnavailable ? unavailableValue : summary.completed} />
          <SummaryCard description={summaryUnavailable ? summaryUnavailableText : "Loaded orders failed, rejected, or cancelled."} label="Failed or rejected" loading={showSkeleton} value={summaryUnavailable ? unavailableValue : summary.failed} />
        </section>

        <section className="admin-orders-toolbar" aria-label="Order filters">
          <label className="admin-search-control">
            <span>Search orders</span>
            <span>
              <MiniIcon name="search" />
              <input
                disabled={showSkeleton}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Order ID or customer"
                value={search}
              />
            </span>
          </label>
          <label>
            <span>Status</span>
            <select disabled={showSkeleton} onChange={(event) => setStatusFilter(event.target.value)} value={statusFilter}>
              <option value="">All statuses</option>
              {ORDER_STATUSES.map((entry) => (
                <option key={entry} value={entry}>{statusLabel(entry)}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Fulfillment</span>
            <select disabled={showSkeleton} onChange={(event) => setFulfillmentFilter(event.target.value)} value={fulfillmentFilter}>
              <option value="">All fulfillment</option>
              <option value="delivery">Delivery</option>
              <option value="takeaway">Takeaway</option>
            </select>
          </label>
          <div className="admin-toolbar-status">
            <span><MiniIcon name="clock" /> Auto-refresh every 10s</span>
            {backgroundRefreshing && <strong>Checking for updates...</strong>}
          </div>
          {hasActiveFilters && (
            <button className="secondary" onClick={clearFilters} type="button">
              Clear filters
            </button>
          )}
        </section>

        <section className="admin-panel admin-orders-panel">
          <div className="admin-section-heading">
            <div>
              <h2>Loaded order set</h2>
              <p>Summary cards and filters reflect the current response, not all-time analytics.</p>
            </div>
          </div>
          {hasInitialFailure ? (
            <div className="admin-empty-state admin-orders-unavailable">
              <strong>Order data is unavailable.</strong>
              <p>Retry when the service is reachable.</p>
              <button className="secondary" disabled={manualRefreshing || backgroundRefreshing} onClick={() => void loadOrders("manual")} type="button">
                Retry
              </button>
            </div>
          ) : (
            <div className="admin-order-table-wrap">
              <table className="admin-table admin-orders-table">
                <thead>
                  <tr>
                    <th>Order</th>
                    <th>Customer</th>
                    <th>Fulfillment</th>
                    <th>Status</th>
                    <th>Total</th>
                    <th>Updated</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {showSkeleton &&
                    [0, 1, 2, 3, 4].map((row) => (
                      <tr className="admin-loading-row" key={row}>
                        <td><span className="admin-skeleton admin-skeleton-line" /></td>
                        <td><span className="admin-skeleton admin-skeleton-line" /></td>
                        <td><span className="admin-skeleton admin-skeleton-pill" /></td>
                        <td><span className="admin-skeleton admin-skeleton-pill" /></td>
                        <td><span className="admin-skeleton admin-skeleton-line" /></td>
                        <td><span className="admin-skeleton admin-skeleton-line" /></td>
                        <td><span className="admin-skeleton admin-skeleton-line" /></td>
                      </tr>
                    ))}
                  {!showSkeleton &&
                    filteredOrders.map((order) => {
                      const updatedLabel = formatRelativeTime(order.updated_at);
                      const fullUpdated = formatDateTime(order.updated_at);
                      return (
                        <tr className={isRecentlyUpdated(order.updated_at) ? "is-recent" : ""} key={order.order_id}>
                          <td data-label="Order">
                            <Link className="admin-order-id-link" href={`/admin/orders/${order.order_id}`} title={order.order_id}>
                              {shortOrderId(order.order_id)}
                            </Link>
                          </td>
                          <td data-label="Customer">{order.customer_name || "Unknown customer"}</td>
                          <td data-label="Fulfillment">
                            <span className="admin-fulfillment-chip">
                              <MiniIcon name={order.fulfillment_method === "delivery" ? "truck" : "bag"} />
                              {fulfillmentLabel(order.fulfillment_method)}
                            </span>
                          </td>
                          <td data-label="Status"><StatusBadge status={order.status} /></td>
                          <td data-label="Total">{money(order.total, order.currency)}</td>
                          <td data-label="Updated">
                            <span title={fullUpdated}>{updatedLabel}</span>
                          </td>
                          <td data-label="Action">
                            <Link className="admin-text-link" href={`/admin/orders/${order.order_id}`}>
                              View
                            </Link>
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          )}
          {showNoOrdersEmpty && (
            <div className="admin-empty-state">
              <strong>No orders are currently available.</strong>
              <p>New restaurant orders will appear here after they are submitted.</p>
            </div>
          )}
          {showFilteredEmpty && (
            <div className="admin-empty-state">
              <strong>No orders match the selected filters.</strong>
              <p>Adjust the search, status, or fulfillment filters to broaden the loaded order set.</p>
              <button className="secondary" onClick={clearFilters} type="button">Clear filters</button>
            </div>
          )}
        </section>
      </div>
    </AdminShell>
  );
}
