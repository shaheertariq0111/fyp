"use client";

import { useEffect, useState } from "react";
import { AdminShell, money } from "@/app/admin/AdminShell";
import { adminGet } from "@/lib/adminApi";

type Analytics = {
  today_orders: number;
  active_orders: number;
  revenue: number;
  failed_orders: number;
  by_status: Record<string, number>;
  recent_orders: Array<{ order_id: string; status: string; total?: number; currency?: string }>;
};

export default function AdminDashboardPage() {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);

  useEffect(() => {
    adminGet<Analytics>("/api/admin/analytics").then(setAnalytics).catch(console.error);
  }, []);

  return (
    <AdminShell title="Dashboard">
      <div className="admin-metrics">
        <div><strong>{analytics?.today_orders ?? 0}</strong><span>Today orders</span></div>
        <div><strong>{analytics?.active_orders ?? 0}</strong><span>Active orders</span></div>
        <div><strong>{money(analytics?.revenue)}</strong><span>Revenue</span></div>
        <div><strong>{analytics?.failed_orders ?? 0}</strong><span>Failed orders</span></div>
      </div>
      <div className="admin-grid-two">
        <section className="admin-panel">
          <h2>Status Counts</h2>
          <table className="admin-table">
            <tbody>
              {Object.entries(analytics?.by_status ?? {}).map(([status, count]) => (
                <tr key={status}><td>{status}</td><td>{count}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="admin-panel">
          <h2>Recent Orders</h2>
          <table className="admin-table">
            <tbody>
              {(analytics?.recent_orders ?? []).map((order) => (
                <tr key={order.order_id}>
                  <td>{order.order_id}</td>
                  <td>{order.status}</td>
                  <td>{money(order.total, order.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </AdminShell>
  );
}
