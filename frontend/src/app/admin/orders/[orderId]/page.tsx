"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AdminShell, money } from "@/app/admin/AdminShell";
import { adminGet, adminPatch } from "@/lib/adminApi";

type Order = {
  order_id: string;
  status: string;
  customer_name?: string | null;
  customer_phone?: string | null;
  fulfillment_method?: string | null;
  delivery_address?: string | null;
  items?: Array<{ name: string; quantity: number; line_total?: number; customizations?: Record<string, unknown> }>;
  total?: number;
  currency?: string;
  allowed_actions?: string[];
  status_history?: Array<{ action: string; from_status: string; to_status: string; reason?: string; created_at: string }>;
};

export default function AdminOrderDetailPage() {
  const params = useParams<{ orderId: string }>();
  const [order, setOrder] = useState<Order | null>(null);
  const [reason, setReason] = useState("");

  async function load() {
    const result = await adminGet<{ order: Order }>(`/api/admin/orders/${params.orderId}`);
    setOrder(result.order);
  }

  async function update(action: string) {
    const result = await adminPatch<{ order: Order }>(`/api/admin/orders/${params.orderId}/status`, { action, reason });
    setOrder(result.order);
    setReason("");
  }

  useEffect(() => {
    void load().catch(console.error);
  }, [params.orderId]);

  return (
    <AdminShell title={order?.order_id ?? "Order"}>
      <section className="admin-panel">
        <div className="admin-detail-grid">
          <div><span>Status</span><strong>{order?.status}</strong></div>
          <div><span>Customer</span><strong>{order?.customer_name || "Unknown"}</strong></div>
          <div><span>Phone</span><strong>{order?.customer_phone || "-"}</strong></div>
          <div><span>Fulfillment</span><strong>{order?.fulfillment_method || "-"}</strong></div>
          <div><span>Address</span><strong>{order?.delivery_address || "-"}</strong></div>
          <div><span>Total</span><strong>{money(order?.total, order?.currency)}</strong></div>
        </div>
      </section>
      <section className="admin-panel">
        <h2>Items</h2>
        <table className="admin-table">
          <tbody>
            {(order?.items ?? []).map((item, index) => (
              <tr key={`${item.name}-${index}`}>
                <td>{item.quantity} x {item.name}</td>
                <td>{JSON.stringify(item.customizations ?? {})}</td>
                <td>{money(item.line_total, order?.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="admin-panel">
        <h2>Status Updates</h2>
        <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Reason" />
        <div className="admin-actions">
          {(order?.allowed_actions ?? []).map((action) => (
            <button className="secondary" key={action} onClick={() => void update(action)}>{action}</button>
          ))}
        </div>
        <table className="admin-table">
          <tbody>
            {(order?.status_history ?? []).map((entry, index) => (
              <tr key={`${entry.created_at}-${index}`}>
                <td>{entry.action}</td>
                <td>{entry.from_status} &gt; {entry.to_status}</td>
                <td>{entry.reason || "-"}</td>
                <td>{entry.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </AdminShell>
  );
}
