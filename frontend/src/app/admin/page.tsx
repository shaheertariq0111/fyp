"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AdminShell } from "@/app/admin/AdminShell";
import { MiniIcon } from "@/app/admin/orders/orderPresentation";
import {
  Analytics,
  OperationalHealth,
  OperationsKpiStrip,
  OrderDetail,
  OrderSnapshot,
  OrderStatusPipeline,
  OtherStatuses,
  RecentOrders,
  RevenueOverview,
  statusStages,
} from "@/app/admin/OperationsOverview";
import { adminGet } from "@/lib/adminApi";

type RefreshSource = "initial" | "manual" | "auto";
type RefreshFailure = { attemptedAt: Date; hadPreviousData: boolean };

function formatRefreshTime(value: Date) {
  return value.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatRefreshStatus(lastSuccess: Date | null, failure: RefreshFailure | null) {
  if (failure && !lastSuccess) return `Refresh failed at ${formatRefreshTime(failure.attemptedAt)}`;
  if (failure && lastSuccess) return `Update failed at ${formatRefreshTime(failure.attemptedAt)} · Showing previous data`;
  if (lastSuccess) return `Updated ${formatRefreshTime(lastSuccess)}`;
  return "Not refreshed yet";
}

export default function AdminDashboardPage() {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [refreshFailure, setRefreshFailure] = useState<RefreshFailure | null>(null);
  const [lastSuccessfulRefresh, setLastSuccessfulRefresh] = useState<Date | null>(null);
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null);
  const [selectedOrder, setSelectedOrder] = useState<OrderDetail | null>(null);
  const [isOrderLoading, setIsOrderLoading] = useState(false);
  const [orderLoadFailed, setOrderLoadFailed] = useState(false);
  const requestInFlight = useRef(false);
  const analyticsRef = useRef<Analytics | null>(null);
  const manualFailureLogged = useRef(false);

  const loadAnalytics = useCallback(async (source: RefreshSource) => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    setIsRefreshing(true);
    try {
      const result = await adminGet<Analytics>("/api/admin/analytics");
      analyticsRef.current = result;
      setAnalytics(result);
      setLastSuccessfulRefresh(new Date());
      setRefreshFailure(null);
      setSelectedOrderId((current) => {
        if (current && result.recent_orders.some((order) => order.order_id === current)) return current;
        return result.recent_orders[0]?.order_id ?? null;
      });
    } catch (exc) {
      if (!(exc instanceof Error)) throw exc;
      setRefreshFailure({ attemptedAt: new Date(), hadPreviousData: analyticsRef.current !== null });
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

  const loadSelectedOrder = useCallback(async (orderId: string, signal?: AbortSignal) => {
    setIsOrderLoading(true);
    setOrderLoadFailed(false);
    try {
      const result = await adminGet<{ order: OrderDetail }>(`/api/admin/orders/${orderId}`, { signal });
      if (signal?.aborted) return;
      setSelectedOrder(result.order);
    } catch (exc) {
      if (exc instanceof DOMException && exc.name === "AbortError") return;
      if (!(exc instanceof Error)) throw exc;
      setSelectedOrder(null);
      setOrderLoadFailed(true);
    } finally {
      if (!signal?.aborted) setIsOrderLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAnalytics("initial");
    const timer = window.setInterval(() => void loadAnalytics("auto"), 30000);
    return () => window.clearInterval(timer);
  }, [loadAnalytics]);

  useEffect(() => {
    if (!selectedOrderId) {
      setSelectedOrder(null);
      setOrderLoadFailed(false);
      return;
    }
    const controller = new AbortController();
    void loadSelectedOrder(selectedOrderId, controller.signal);
    return () => controller.abort();
  }, [loadSelectedOrder, selectedOrderId]);

  const knownStatuses = useMemo(() => new Set(statusStages.flatMap((stage) => stage.statuses)), []);
  const otherStatuses = useMemo(
    () => Object.entries(analytics?.by_status ?? {})
      .filter(([status]) => !knownStatuses.has(status))
      .sort(([left], [right]) => left.localeCompare(right)),
    [analytics?.by_status, knownStatuses],
  );

  const refreshActions = (
    <div className="admin-dashboard-actions">
      <span aria-live="polite">{formatRefreshStatus(lastSuccessfulRefresh, refreshFailure)}</span>
      <button className="admin-refresh-button" disabled={isRefreshing} onClick={() => void loadAnalytics("manual")} type="button">
        <MiniIcon name="refresh" />
        {isRefreshing ? "Refreshing..." : "Refresh"}
      </button>
    </div>
  );

  const hasAnalytics = analytics !== null;
  const hasInitialFailure = !hasAnalytics && refreshFailure !== null;
  const hasStaleDataWarning = hasAnalytics && refreshFailure !== null && refreshFailure.hadPreviousData;
  const showSkeleton = isInitialLoading && !hasAnalytics && !refreshFailure;

  return (
    <AdminShell actions={refreshActions} subtitle="Live restaurant performance and order activity" title="Operations Overview">
      <div className="admin-dashboard ops-dashboard">
        {hasInitialFailure && (
          <section className="admin-error-panel" role="alert">
            <div><strong>Analytics unavailable</strong><p>Dashboard analytics could not be loaded. Retry when the service is reachable.</p></div>
            <button className="secondary" disabled={isRefreshing} onClick={() => void loadAnalytics("manual")} type="button">Retry</button>
          </section>
        )}
        {hasStaleDataWarning && (
          <section className="admin-warning-panel" role="status">
            <div><strong>Latest refresh failed</strong><p>Showing the previous analytics values until the next successful update.</p></div>
            <button className="secondary" disabled={isRefreshing} onClick={() => void loadAnalytics("manual")} type="button">Retry</button>
          </section>
        )}

        <div className="ops-top-grid">
          <RevenueOverview analytics={analytics} loading={showSkeleton} />
          <OperationsKpiStrip analytics={analytics} loading={showSkeleton} />
        </div>

        <div className="ops-status-grid">
          <OrderStatusPipeline analytics={analytics} loading={showSkeleton} />
          <OtherStatuses statuses={otherStatuses} loading={showSkeleton} />
          <OperationalHealth analytics={analytics} loading={showSkeleton} />
        </div>

        <div className="ops-workspace">
          <RecentOrders
            available={hasAnalytics}
            loading={showSkeleton}
            onSelect={setSelectedOrderId}
            orders={analytics?.recent_orders ?? []}
            selectedOrderId={selectedOrderId}
          />
          <OrderSnapshot
            error={orderLoadFailed}
            loading={isOrderLoading}
            onRetry={() => selectedOrderId && void loadSelectedOrder(selectedOrderId)}
            order={selectedOrder}
            selectedOrderId={selectedOrderId}
          />
        </div>
      </div>
    </AdminShell>
  );
}
