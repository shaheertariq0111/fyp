"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AdminShell, money } from "@/app/admin/AdminShell";
import { adminGet, adminPatch } from "@/lib/adminApi";
import {
  actionLabel,
  customizationRows,
  FieldValue,
  formatDateTime,
  fulfillmentLabel,
  isDangerAction,
  isTerminalDangerStatus,
  MiniIcon,
  shortOrderId,
  StatusBadge,
  statusLabel,
} from "@/app/admin/orders/orderPresentation";

type OrderItem = {
  name: string;
  quantity: number;
  line_total?: number;
  customizations?: Record<string, unknown>;
};

type StatusHistoryEntry = {
  action: string;
  from_status: string;
  to_status: string;
  reason?: string | null;
  actor?: string | null;
  created_at: string;
};

type Order = {
  order_id: string;
  status: string;
  customer_name?: string | null;
  customer_phone?: string | null;
  fulfillment_method?: string | null;
  delivery_address?: string | null;
  items?: OrderItem[];
  subtotal?: number;
  delivery_fee?: number | null;
  total?: number;
  currency?: string;
  created_at?: string;
  updated_at?: string;
  allowed_actions?: string[];
  status_history?: StatusHistoryEntry[];
};

type LoadSource = "initial" | "manual" | "after-update";
type LoadFailure = {
  attemptedAt: Date;
  hadPreviousData: boolean;
};

const positiveActions = new Set(["accept", "start_preparing", "mark_ready", "dispatch", "complete", "deliver"]);
const takeawayProgress = ["submitted_to_restaurant", "accepted", "preparing", "ready_for_pickup", "completed"];
const deliveryProgress = ["submitted_to_restaurant", "accepted", "preparing", "out_for_delivery", "delivered"];

function progressStatuses(order: Order | null) {
  return order?.fulfillment_method === "delivery" ? deliveryProgress : takeawayProgress;
}

function statusTimestamp(order: Order | null, status: string) {
  const entry = order?.status_history?.find((history) => history.to_status === status);
  if (entry?.created_at) {
    return entry.created_at;
  }
  if (status === "submitted_to_restaurant") {
    return order?.created_at;
  }
  return undefined;
}

function CustomizationSummary({ customizations }: { customizations?: Record<string, unknown> }) {
  const rows = customizationRows(customizations);
  if (rows.length === 0) {
    return <p className="admin-muted-text">Standard configuration</p>;
  }
  return (
    <dl className="admin-customization-list">
      {rows.map((row) => (
        <div key={row.key}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function DetailSkeleton() {
  return (
    <div className="admin-order-detail">
      <section className="admin-panel admin-order-detail-header">
        {[0, 1, 2, 3, 4, 5].map((item) => (
          <span className="admin-skeleton admin-skeleton-line" key={item} />
        ))}
      </section>
      <section className="admin-panel">
        <span className="admin-skeleton admin-skeleton-value" />
        <span className="admin-skeleton admin-skeleton-line" />
        <span className="admin-skeleton admin-skeleton-line" />
      </section>
    </div>
  );
}

export default function AdminOrderDetailPage() {
  const params = useParams<{ orderId: string }>();
  const orderId = params.orderId;
  const [order, setOrder] = useState<Order | null>(null);
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [loadFailure, setLoadFailure] = useState<LoadFailure | null>(null);
  const [processingAction, setProcessingAction] = useState<string | null>(null);
  const [pendingDangerAction, setPendingDangerAction] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [updateError, setUpdateError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  const requestInFlight = useRef(false);
  const updateInFlight = useRef(false);
  const mounted = useRef(false);
  const orderRef = useRef<Order | null>(null);
  const manualLoadFailureLogged = useRef(false);

  const loadOrder = useCallback(async (source: LoadSource) => {
    if (requestInFlight.current) {
      return;
    }
    requestInFlight.current = true;
    setIsRefreshing(true);
    try {
      const result = await adminGet<{ order: Order }>(`/api/admin/orders/${orderId}`);
      if (!mounted.current) {
        return;
      }
      orderRef.current = result.order;
      setOrder(result.order);
      setLoadFailure(null);
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      if (!mounted.current) {
        return;
      }
      setLoadFailure({ attemptedAt: new Date(), hadPreviousData: orderRef.current !== null });
      if (source === "manual" && !manualLoadFailureLogged.current) {
        console.warn("Admin order detail refresh failed", exc);
        manualLoadFailureLogged.current = true;
      }
    } finally {
      if (mounted.current) {
        setIsInitialLoading(false);
        setIsRefreshing(false);
      }
      requestInFlight.current = false;
    }
  }, [orderId]);

  useEffect(() => {
    mounted.current = true;
    void loadOrder("initial");
    return () => {
      mounted.current = false;
    };
  }, [loadOrder]);

  const updateStatus = useCallback(async (action: string, actionReason?: string) => {
    if (updateInFlight.current) {
      return;
    }
    updateInFlight.current = true;
    setProcessingAction(action);
    setUpdateError("");
    setSuccessMessage("");
    try {
      const payload = actionReason ? { action, reason: actionReason } : { action };
      const result = await adminPatch<{ order: Order }>(`/api/admin/orders/${orderId}/status`, payload);
      if (!mounted.current) {
        return;
      }
      orderRef.current = result.order;
      setOrder(result.order);
      setPendingDangerAction(null);
      setReason("");
      setSuccessMessage(`${actionLabel(action)} completed.`);
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      if (!mounted.current) {
        return;
      }
      setUpdateError("The order status could not be updated. Please retry after checking the current order state.");
      console.warn("Admin order status update failed", exc);
    } finally {
      if (mounted.current) {
        setProcessingAction(null);
      }
      updateInFlight.current = false;
    }
  }, [orderId]);

  const orderedHistory = useMemo(() => {
    return [...(order?.status_history ?? [])].sort((a, b) => {
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });
  }, [order?.status_history]);

  const stages = progressStatuses(order);
  const currentStageIndex = order ? stages.indexOf(order.status) : -1;
  const isDangerTerminal = isTerminalDangerStatus(order?.status);
  const title = `Order ${shortOrderId(order?.order_id ?? orderId)}`;
  const actions = (
    <div className="admin-dashboard-actions">
      <Link className="secondary admin-inline-action" href="/admin/orders">
        <MiniIcon name="arrowLeft" />
        Back to Live Orders
      </Link>
      <button className="admin-refresh-button" disabled={isRefreshing} onClick={() => void loadOrder("manual")} type="button">
        <MiniIcon name="refresh" />
        {isRefreshing ? "Refreshing..." : "Refresh order"}
      </button>
    </div>
  );

  return (
    <AdminShell actions={actions} subtitle="Order details and fulfillment progress" title={title}>
      {isInitialLoading && !order && !loadFailure ? (
        <DetailSkeleton />
      ) : (
        <div className="admin-order-detail">
          {!order && loadFailure && (
            <section className="admin-error-panel" role="alert">
              <div>
                <strong>Order unavailable</strong>
                <p>The order could not be found or loaded. Retry from Live Orders if it still appears there.</p>
              </div>
              <button className="secondary" disabled={isRefreshing} onClick={() => void loadOrder("manual")} type="button">
                Retry
              </button>
            </section>
          )}
          {order && loadFailure && loadFailure.hadPreviousData && (
            <section className="admin-warning-panel" role="status">
              <div>
                <strong>Latest refresh failed</strong>
                <p>Showing the previously loaded order. Failed at {formatDateTime(loadFailure.attemptedAt.toISOString())}.</p>
              </div>
              <button className="secondary" disabled={isRefreshing} onClick={() => void loadOrder("manual")} type="button">
                Retry
              </button>
            </section>
          )}

          {order && (
            <>
              <section className="admin-panel admin-order-detail-header">
                <div className="admin-order-detail-title">
                  <div>
                    <span>Full order ID</span>
                    <h2 title={order.order_id}>{order.order_id}</h2>
                  </div>
                  <StatusBadge status={order.status} />
                </div>
                <div className="admin-detail-grid">
                  <FieldValue label="Customer">{order.customer_name || "Unknown customer"}</FieldValue>
                  <FieldValue label="Phone">
                    {order.customer_phone ? <a href={`tel:${order.customer_phone}`}>{order.customer_phone}</a> : "Not specified"}
                  </FieldValue>
                  <FieldValue label="Fulfillment">{fulfillmentLabel(order.fulfillment_method)}</FieldValue>
                  {order.fulfillment_method === "delivery" && (
                    <FieldValue label="Delivery address">{order.delivery_address || "Not specified"}</FieldValue>
                  )}
                  <FieldValue label="Total">{money(order.total, order.currency)}</FieldValue>
                  <FieldValue label="Last updated">
                    <span title={formatDateTime(order.updated_at)}>{formatDateTime(order.updated_at)}</span>
                  </FieldValue>
                </div>
              </section>

              <section className="admin-panel admin-order-progress">
                <div className="admin-section-heading">
                  <div>
                    <h2>Fulfillment Progress</h2>
                    <p>Current status is the source of truth. Stage times appear only when recorded.</p>
                  </div>
                </div>
                {isDangerTerminal && (
                  <div className="admin-terminal-state" role="status">
                    <StatusBadge status={order.status} />
                    <span>This order is in a terminal exception state.</span>
                  </div>
                )}
                <ol>
                  {stages.map((stage, index) => {
                    const state = isDangerTerminal
                      ? "upcoming"
                      : index < currentStageIndex
                        ? "complete"
                        : index === currentStageIndex
                          ? "current"
                          : "upcoming";
                    const timestamp = statusTimestamp(order, stage);
                    return (
                      <li className={`is-${state}`} key={stage}>
                        <span aria-hidden="true" />
                        <div>
                          <strong>{statusLabel(stage)}</strong>
                          <small>{timestamp ? formatDateTime(timestamp) : "No timestamp recorded"}</small>
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </section>

              <div className="admin-order-detail-grid">
                <section className="admin-panel admin-order-items">
                  <div className="admin-section-heading">
                    <div>
                      <h2>Items</h2>
                      <p>Order contents and customer-selected options.</p>
                    </div>
                  </div>
                  {(order.items ?? []).length === 0 ? (
                    <div className="admin-empty-state">
                      <strong>No items returned</strong>
                      <p>The order response did not include item details.</p>
                    </div>
                  ) : (
                    <div className="admin-order-item-list">
                      {(order.items ?? []).map((item, index) => (
                        <article className="admin-order-item" key={`${item.name}-${index}`}>
                          <div>
                            <strong>{item.quantity} x {item.name}</strong>
                            <span>{money(item.line_total, order.currency)}</span>
                          </div>
                          <CustomizationSummary customizations={item.customizations} />
                        </article>
                      ))}
                    </div>
                  )}
                  <div className="admin-order-total-box">
                    {order.subtotal !== undefined && (
                      <div><span>Subtotal</span><strong>{money(order.subtotal, order.currency)}</strong></div>
                    )}
                    {order.delivery_fee !== undefined && order.delivery_fee !== null && (
                      <div><span>Delivery fee</span><strong>{money(order.delivery_fee, order.currency)}</strong></div>
                    )}
                    <div><span>Total</span><strong>{money(order.total, order.currency)}</strong></div>
                    <small>Currency: {order.currency || "PKR"}</small>
                  </div>
                </section>

                <section className="admin-panel admin-order-action-panel">
                  <div className="admin-section-heading">
                    <div>
                      <h2>Status Actions</h2>
                      <p>Allowed actions come from the backend for this order state.</p>
                    </div>
                  </div>
                  {successMessage && <div className="admin-success">{successMessage}</div>}
                  {updateError && <div className="admin-error-panel" role="alert"><strong>{updateError}</strong></div>}
                  {(order.allowed_actions ?? []).length === 0 ? (
                    <div className="admin-empty-state">
                      <strong>No staff actions available</strong>
                      <p>This order cannot be advanced from its current status.</p>
                    </div>
                  ) : (
                    <div className="admin-order-actions">
                      {(order.allowed_actions ?? []).map((action) => (
                        <button
                          className={isDangerAction(action) ? "secondary danger" : positiveActions.has(action) ? "primary" : "secondary"}
                          disabled={Boolean(processingAction)}
                          key={action}
                          onClick={() => {
                            if (isDangerAction(action)) {
                              setPendingDangerAction(action);
                              setReason("");
                              setUpdateError("");
                              setSuccessMessage("");
                            } else {
                              void updateStatus(action);
                            }
                          }}
                          type="button"
                        >
                          {processingAction === action ? `${actionLabel(action)}...` : actionLabel(action)}
                        </button>
                      ))}
                    </div>
                  )}
                  {pendingDangerAction && (
                    <div className="admin-danger-confirmation" role="region" aria-label={`${actionLabel(pendingDangerAction)} confirmation`}>
                      <strong>{actionLabel(pendingDangerAction)}</strong>
                      <p>This exception action changes the customer-visible order state. Enter a reason before confirming.</p>
                      <label>
                        Reason
                        <textarea
                          disabled={Boolean(processingAction)}
                          onChange={(event) => setReason(event.target.value)}
                          placeholder="Explain why this action is required"
                          value={reason}
                        />
                      </label>
                      <div className="admin-actions">
                        <button
                          className="secondary danger"
                          disabled={Boolean(processingAction) || !reason.trim()}
                          onClick={() => void updateStatus(pendingDangerAction, reason.trim())}
                          type="button"
                        >
                          Confirm {actionLabel(pendingDangerAction).toLowerCase()}
                        </button>
                        <button
                          className="secondary"
                          disabled={Boolean(processingAction)}
                          onClick={() => {
                            setPendingDangerAction(null);
                            setReason("");
                          }}
                          type="button"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </section>
              </div>

              <section className="admin-panel admin-order-timeline">
                <div className="admin-section-heading">
                  <div>
                    <h2>Status History</h2>
                    <p>Most recent staff status changes first.</p>
                  </div>
                </div>
                {orderedHistory.length === 0 ? (
                  <div className="admin-empty-state">
                    <strong>No staff status changes have been recorded yet.</strong>
                  </div>
                ) : (
                  <ol>
                    {orderedHistory.map((entry, index) => (
                      <li key={`${entry.created_at}-${entry.action}-${index}`}>
                        <span aria-hidden="true" />
                        <div>
                          <strong>{actionLabel(entry.action)}</strong>
                          <p>{statusLabel(entry.from_status)} to {statusLabel(entry.to_status)}</p>
                          {entry.reason && <p>Reason: {entry.reason}</p>}
                          <small>
                            {formatDateTime(entry.created_at)}
                            {entry.actor ? ` · ${entry.actor}` : ""}
                          </small>
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
              </section>
            </>
          )}
        </div>
      )}
    </AdminShell>
  );
}
