"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AdminShell, money } from "@/app/admin/AdminShell";
import { adminGet } from "@/lib/adminApi";

type Order = {
  order_id: string;
  status: string;
  customer_name?: string | null;
  fulfillment_method?: string | null;
  total?: number;
  currency?: string;
  updated_at?: string;
};

export default function AdminOrdersPage() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [status, setStatus] = useState("");

  useEffect(() => {
    let active = true;
    async function load() {
      const suffix = status ? `?status=${encodeURIComponent(status)}` : "";
      const result = await adminGet<{ orders: Order[] }>(`/api/admin/orders${suffix}`);
      if (active) setOrders(result.orders);
    }
    void load().catch(console.error);
    const timer = window.setInterval(() => void load().catch(console.error), 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [status]);

  return (
    <AdminShell title="Live Orders">
      <div className="admin-toolbar">
        <select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All statuses</option>
          {["submitted_to_restaurant", "accepted", "preparing", "ready_for_pickup", "out_for_delivery", "delivered", "completed", "rejected", "failed"].map((entry) => (
            <option key={entry} value={entry}>{entry}</option>
          ))}
        </select>
      </div>
      <table className="admin-table">
        <thead><tr><th>Order</th><th>Customer</th><th>Status</th><th>Fulfillment</th><th>Total</th><th>Updated</th></tr></thead>
        <tbody>
          {orders.map((order) => (
            <tr key={order.order_id}>
              <td><Link href={`/admin/orders/${order.order_id}`}>{order.order_id}</Link></td>
              <td>{order.customer_name || "Unknown"}</td>
              <td>{order.status}</td>
              <td>{order.fulfillment_method || "-"}</td>
              <td>{money(order.total, order.currency)}</td>
              <td>{order.updated_at || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </AdminShell>
  );
}
