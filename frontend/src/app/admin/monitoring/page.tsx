"use client";

import { useEffect, useState } from "react";
import { AdminShell } from "@/app/admin/AdminShell";
import { adminGet } from "@/lib/adminApi";

type FailedOrder = { order_id: string; status: string; updated_at?: string };
type ErrorEvent = {
  event_type: string;
  session_id?: string;
  user_id?: string;
  created_at?: string;
  details_redacted?: Record<string, unknown>;
};

export default function AdminMonitoringPage() {
  const [orders, setOrders] = useState<FailedOrder[]>([]);
  const [events, setEvents] = useState<ErrorEvent[]>([]);

  useEffect(() => {
    adminGet<{ orders: FailedOrder[] }>("/api/admin/monitoring/failed-orders").then((result) => setOrders(result.orders)).catch(console.error);
    adminGet<{ events: ErrorEvent[] }>("/api/admin/monitoring/errors").then((result) => setEvents(result.events)).catch(console.error);
  }, []);

  return (
    <AdminShell title="Monitoring">
      <div className="admin-grid-two">
        <section className="admin-panel">
          <h2>Failed Orders</h2>
          <table className="admin-table">
            <tbody>
              {orders.map((order) => (
                <tr key={order.order_id}><td>{order.order_id}</td><td>{order.status}</td><td>{order.updated_at}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="admin-panel">
          <h2>Error Events</h2>
          <table className="admin-table">
            <tbody>
              {events.map((event, index) => (
                <tr key={`${event.created_at}-${index}`}>
                  <td>{event.event_type}</td>
                  <td>{event.user_id || "-"}</td>
                  <td><pre>{JSON.stringify(event.details_redacted ?? {}, null, 2)}</pre></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </AdminShell>
  );
}
