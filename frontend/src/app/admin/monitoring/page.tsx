"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AdminShell } from "@/app/admin/AdminShell";
import { StatusBadge, formatDateTime, shortOrderId } from "@/app/admin/orders/orderPresentation";
import { MonitoringIcon, formatEventType, safeDetailRows, shortReference } from "@/app/admin/monitoring/monitoringPresentation";
import { adminGet } from "@/lib/adminApi";

type FailedOrder = {
  order_id: string;
  status: string;
  updated_at?: string;
  created_at?: string;
};

type ErrorEvent = {
  event_type: string;
  session_id?: string;
  user_id?: string;
  created_at?: string;
  details_redacted?: Record<string, unknown>;
};

type EndpointStatus = "idle" | "loading" | "loaded" | "failed" | "stale";
type RefreshSource = "initial" | "manual" | "background";

const unavailableValue = "\u2014";

function latestTime<T>(items: T[], selector: (item: T) => string | undefined) {
  return items
    .map(selector)
    .filter((value): value is string => Boolean(value))
    .sort((a, b) => b.localeCompare(a))[0];
}

function SummaryCard({ label, value, description, loading }: { label: string; value: string | number; description: string; loading: boolean }) {
  return (
    <article className="admin-monitoring-summary-card">
      <span>{label}</span>
      {loading ? <strong className="admin-skeleton admin-skeleton-value" /> : <strong>{value}</strong>}
      <p>{description}</p>
    </article>
  );
}

export default function AdminMonitoringPage() {
  const [orders, setOrders] = useState<FailedOrder[]>([]);
  const [events, setEvents] = useState<ErrorEvent[]>([]);
  const [ordersStatus, setOrdersStatus] = useState<EndpointStatus>("idle");
  const [eventsStatus, setEventsStatus] = useState<EndpointStatus>("idle");
  const [lastSuccessfulRefresh, setLastSuccessfulRefresh] = useState<Date | null>(null);
  const [lastFailedRefresh, setLastFailedRefresh] = useState<Date | null>(null);
  const [isManualRefreshing, setIsManualRefreshing] = useState(false);
  const [isBackgroundRefreshing, setIsBackgroundRefreshing] = useState(false);
  const [orderSearch, setOrderSearch] = useState("");
  const [eventSearch, setEventSearch] = useState("");
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const refreshInFlight = useRef(false);
  const mounted = useRef(false);
  const manualWarningLogged = useRef(false);

  const refresh = useCallback(async (source: RefreshSource) => {
    if (refreshInFlight.current) {
      return;
    }
    refreshInFlight.current = true;
    if (source === "manual") {
      setIsManualRefreshing(true);
    } else if (source === "background") {
      setIsBackgroundRefreshing(true);
    }
    setOrdersStatus((current) => current === "loaded" || current === "stale" ? current : "loading");
    setEventsStatus((current) => current === "loaded" || current === "stale" ? current : "loading");

    const [ordersResult, eventsResult] = await Promise.allSettled([
      adminGet<{ orders: FailedOrder[] }>("/api/admin/monitoring/failed-orders"),
      adminGet<{ events: ErrorEvent[] }>("/api/admin/monitoring/errors"),
    ]);

    if (!mounted.current) {
      refreshInFlight.current = false;
      return;
    }

    const ordersLoaded = ordersResult.status === "fulfilled";
    const eventsLoaded = eventsResult.status === "fulfilled";
    if (ordersLoaded) {
      setOrders(ordersResult.value.orders);
      setOrdersStatus("loaded");
    } else {
      setOrdersStatus((current) => current === "loaded" || current === "stale" ? "stale" : "failed");
    }
    if (eventsLoaded) {
      setEvents(eventsResult.value.events);
      setEventsStatus("loaded");
    } else {
      setEventsStatus((current) => current === "loaded" || current === "stale" ? "stale" : "failed");
    }

    if (ordersLoaded || eventsLoaded) {
      setLastSuccessfulRefresh(new Date());
    }
    if (!ordersLoaded || !eventsLoaded) {
      setLastFailedRefresh(new Date());
      if (source === "manual" && !manualWarningLogged.current) {
        console.warn("Admin monitoring refresh partially failed", {
          failedOrders: !ordersLoaded,
          errorEvents: !eventsLoaded,
        });
        manualWarningLogged.current = true;
      }
    } else {
      setLastFailedRefresh(null);
      manualWarningLogged.current = false;
    }

    setIsManualRefreshing(false);
    setIsBackgroundRefreshing(false);
    refreshInFlight.current = false;
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh("initial");
    const timer = window.setInterval(() => {
      void refresh("background");
    }, 30000);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
    };
  }, [refresh]);

  const eventTypes = useMemo(() => (
    Array.from(new Set(events.map((event) => event.event_type).filter(Boolean))).sort((a, b) => a.localeCompare(b))
  ), [events]);

  const filteredOrders = useMemo(() => {
    const normalized = orderSearch.trim().toLowerCase();
    if (!normalized) {
      return orders;
    }
    return orders.filter((order) => order.order_id.toLowerCase().includes(normalized));
  }, [orderSearch, orders]);

  const filteredEvents = useMemo(() => {
    const normalized = eventSearch.trim().toLowerCase();
    return events.filter((event) => {
      const matchesSearch = !normalized
        || event.event_type.toLowerCase().includes(normalized)
        || (event.session_id ?? "").toLowerCase().includes(normalized)
        || (event.user_id ?? "").toLowerCase().includes(normalized);
      const matchesType = !eventTypeFilter || event.event_type === eventTypeFilter;
      return matchesSearch && matchesType;
    });
  }, [eventSearch, eventTypeFilter, events]);

  const ordersEverLoaded = ordersStatus === "loaded" || ordersStatus === "stale";
  const eventsEverLoaded = eventsStatus === "loaded" || eventsStatus === "stale";
  const ordersLoadingInitial = ordersStatus === "loading" && !ordersEverLoaded;
  const eventsLoadingInitial = eventsStatus === "loading" && !eventsEverLoaded;
  const failedOrderLatest = latestTime(orders, (order) => order.updated_at ?? order.created_at);
  const errorEventLatest = latestTime(events, (event) => event.created_at);
  const hasPartialFailure = (ordersStatus === "failed" || ordersStatus === "stale" || eventsStatus === "failed" || eventsStatus === "stale")
    && (ordersEverLoaded || eventsEverLoaded);
  const bothUnavailable = ordersStatus === "failed" && eventsStatus === "failed";

  const refreshText = bothUnavailable
    ? "Monitoring data unavailable"
    : lastFailedRefresh && hasPartialFailure
      ? `Update failed at ${lastFailedRefresh.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} \u00b7 Showing previous data where available`
      : lastSuccessfulRefresh && (ordersStatus === "loaded" || eventsStatus === "loaded") && (ordersStatus !== "loaded" || eventsStatus !== "loaded")
        ? `Updated with partial data ${lastSuccessfulRefresh.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
        : lastSuccessfulRefresh
          ? `Updated ${lastSuccessfulRefresh.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
          : "Not refreshed yet";

  const actions = (
    <div className="admin-dashboard-actions">
      <span aria-live="polite">{refreshText}</span>
      <button className="admin-refresh-button" disabled={isManualRefreshing || isBackgroundRefreshing} onClick={() => void refresh("manual")} type="button">
        <MonitoringIcon name="refresh" />
        {isManualRefreshing ? "Refreshing..." : "Refresh"}
      </button>
    </div>
  );

  return (
    <AdminShell
      actions={actions}
      subtitle="Review failed orders and application error events"
      title="Monitoring"
    >
      <div className="admin-monitoring-page">
        {isBackgroundRefreshing && (
          <div className="admin-toolbar-status" role="status">
            <strong>Checking monitoring endpoints...</strong>
          </div>
        )}
        {bothUnavailable && (
          <section className="admin-error-panel" role="alert">
            <div>
              <strong>Monitoring data unavailable</strong>
              <p>Failed orders and error events could not be refreshed.</p>
            </div>
            <button className="secondary" onClick={() => void refresh("manual")} type="button">Retry</button>
          </section>
        )}
        {hasPartialFailure && !bothUnavailable && (
          <section className="admin-warning-panel" role="status">
            <div>
              <strong>Some monitoring data could not be refreshed</strong>
              <p>Loaded panels remain visible with previous data where available.</p>
            </div>
            <button className="secondary" onClick={() => void refresh("manual")} type="button">Retry</button>
          </section>
        )}

        <section className="admin-monitoring-summary" aria-label="Current monitoring response summary">
          <SummaryCard description="Count in the current failed-orders response." label="Failed orders" loading={ordersLoadingInitial} value={ordersEverLoaded ? orders.length : unavailableValue} />
          <SummaryCard description="Count in the current error-events response." label="Error events" loading={eventsLoadingInitial} value={eventsEverLoaded ? events.length : unavailableValue} />
          <SummaryCard description="Latest timestamp returned by failed-orders." label="Latest failed order time" loading={ordersLoadingInitial} value={ordersEverLoaded ? formatDateTime(failedOrderLatest) : unavailableValue} />
          <SummaryCard description="Latest timestamp returned by error events." label="Latest error-event time" loading={eventsLoadingInitial} value={eventsEverLoaded ? formatDateTime(errorEventLatest) : unavailableValue} />
        </section>

        <div className="admin-monitoring-grid">
          <section className="admin-panel admin-monitoring-panel" aria-labelledby="failed-orders-heading">
            <div className="admin-section-heading">
              <div>
                <h2 id="failed-orders-heading">Failed orders</h2>
                <p>Orders returned by the failed-orders monitoring endpoint.</p>
              </div>
            </div>
            <div className="admin-monitoring-toolbar">
              <label className="admin-search-control">
                <span>Search order ID</span>
                <span>
                  <MonitoringIcon name="search" />
                  <input disabled={!ordersEverLoaded} value={orderSearch} onChange={(event) => setOrderSearch(event.target.value)} />
                </span>
              </label>
            </div>
            {ordersLoadingInitial && (
              <div className="admin-monitoring-list">
                {[0, 1, 2].map((row) => <span className="admin-skeleton admin-skeleton-line" key={row} />)}
              </div>
            )}
            {ordersStatus === "failed" && !ordersEverLoaded && (
              <div className="admin-empty-state">
                <strong>Failed orders are unavailable.</strong>
                <p>Retry when the monitoring endpoint is reachable.</p>
                <button className="secondary" onClick={() => void refresh("manual")} type="button">Retry</button>
              </div>
            )}
            {ordersStatus === "stale" && (
              <div className="admin-warning-panel" role="status">
                <div>
                  <strong>Failed orders refresh failed</strong>
                  <p>Showing previous failed-order data.</p>
                </div>
              </div>
            )}
            {ordersEverLoaded && orders.length === 0 && (
              <div className="admin-empty-state">
                <strong>No failed orders were returned.</strong>
              </div>
            )}
            {ordersEverLoaded && orders.length > 0 && filteredOrders.length === 0 && (
              <div className="admin-empty-state">
                <strong>No failed orders match the selected filters.</strong>
                <button className="secondary" onClick={() => setOrderSearch("")} type="button">Clear filter</button>
              </div>
            )}
            {ordersEverLoaded && filteredOrders.length > 0 && (
              <div className="admin-monitoring-list">
                {filteredOrders.map((order) => (
                  <article className="admin-failed-order-row" key={order.order_id}>
                    <div>
                      <Link className="admin-order-id-link" href={`/admin/orders/${order.order_id}`} title={order.order_id}>
                        {shortOrderId(order.order_id)}
                      </Link>
                      <StatusBadge status={order.status} />
                    </div>
                    <span>{formatDateTime(order.updated_at ?? order.created_at)}</span>
                    <Link className="secondary" href={`/admin/orders/${order.order_id}`}>View order</Link>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="admin-panel admin-monitoring-panel" aria-labelledby="error-events-heading">
            <div className="admin-section-heading">
              <div>
                <h2 id="error-events-heading">Error events</h2>
                <p>Application events returned with redacted operational details.</p>
              </div>
            </div>
            <div className="admin-monitoring-toolbar">
              <label className="admin-search-control">
                <span>Search events</span>
                <span>
                  <MonitoringIcon name="search" />
                  <input disabled={!eventsEverLoaded} value={eventSearch} onChange={(event) => setEventSearch(event.target.value)} />
                </span>
              </label>
              <label>
                <span>Event type</span>
                <select disabled={!eventsEverLoaded} value={eventTypeFilter} onChange={(event) => setEventTypeFilter(event.target.value)}>
                  <option value="">All event types</option>
                  {eventTypes.map((eventType) => <option key={eventType} value={eventType}>{formatEventType(eventType)}</option>)}
                </select>
              </label>
            </div>
            {eventsLoadingInitial && (
              <div className="admin-monitoring-list">
                {[0, 1, 2].map((row) => <span className="admin-skeleton admin-skeleton-line" key={row} />)}
              </div>
            )}
            {eventsStatus === "failed" && !eventsEverLoaded && (
              <div className="admin-empty-state">
                <strong>Error events are unavailable.</strong>
                <p>Retry when the monitoring endpoint is reachable.</p>
                <button className="secondary" onClick={() => void refresh("manual")} type="button">Retry</button>
              </div>
            )}
            {eventsStatus === "stale" && (
              <div className="admin-warning-panel" role="status">
                <div>
                  <strong>Error events refresh failed</strong>
                  <p>Showing previous redacted error-event data.</p>
                </div>
              </div>
            )}
            {eventsEverLoaded && events.length === 0 && (
              <div className="admin-empty-state">
                <strong>No error events were returned.</strong>
              </div>
            )}
            {eventsEverLoaded && events.length > 0 && filteredEvents.length === 0 && (
              <div className="admin-empty-state">
                <strong>No error events match the selected filters.</strong>
                <button className="secondary" onClick={() => { setEventSearch(""); setEventTypeFilter(""); }} type="button">Clear filters</button>
              </div>
            )}
            {eventsEverLoaded && filteredEvents.length > 0 && (
              <div className="admin-monitoring-list">
                {filteredEvents.map((event) => {
                  const detailRows = safeDetailRows(event.details_redacted);
                  const stableKey = `${event.created_at ?? "event"}-${event.event_type}-${event.session_id ?? event.user_id ?? "unknown"}`;
                  return (
                    <article className="admin-error-event-card" key={stableKey}>
                      <div className="admin-error-event-header">
                        <span className="admin-event-icon"><MonitoringIcon name="alert" /></span>
                        <div>
                          <strong>{formatEventType(event.event_type)}</strong>
                          <p>{formatDateTime(event.created_at)}</p>
                        </div>
                      </div>
                      <div className="admin-detail-grid">
                        <div>
                          <span>Session</span>
                          <strong title={event.session_id}>{shortReference(event.session_id)}</strong>
                        </div>
                        <div>
                          <span>User</span>
                          <strong title={event.user_id}>{shortReference(event.user_id)}</strong>
                        </div>
                      </div>
                      {detailRows.length === 0 ? (
                        <p className="admin-muted-text">No additional redacted details were provided.</p>
                      ) : (
                        <>
                          <dl className="admin-redacted-details">
                            {detailRows.slice(0, 4).map((row) => (
                              <div key={row.key}>
                                <dt>{row.label}</dt>
                                <dd>{row.value}</dd>
                              </div>
                            ))}
                          </dl>
                          <details className="admin-technical-details">
                            <summary>Technical details</summary>
                            <dl className="admin-redacted-details">
                              {detailRows.map((row) => (
                                <div key={row.key}>
                                  <dt>{row.label}</dt>
                                  <dd>{row.value}</dd>
                                </div>
                              ))}
                            </dl>
                          </details>
                        </>
                      )}
                    </article>
                  );
                })}
              </div>
            )}
          </section>
        </div>
      </div>
    </AdminShell>
  );
}
