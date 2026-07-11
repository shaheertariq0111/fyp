"use client";

import { FormEvent, useState } from "react";
import { AdminShell, money } from "@/app/admin/AdminShell";
import { adminGet } from "@/lib/adminApi";

type Customer = {
  customer_id: string;
  display_name?: string | null;
  phone_e164?: string | null;
  addresses?: Array<{ label?: string; address_text?: string; is_default?: boolean }>;
};

type Order = { order_id: string; status: string; total?: number; currency?: string };

export default function AdminCustomersPage() {
  const [query, setQuery] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [selected, setSelected] = useState<{ customer: Customer; orders: Order[] } | null>(null);

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const result = await adminGet<{ customers: Customer[] }>(`/api/admin/customers?query=${encodeURIComponent(query)}`);
    setCustomers(result.customers);
  }

  async function open(customerId: string) {
    const result = await adminGet<{ customer: Customer; orders: Order[] }>(`/api/admin/customers/${customerId}`);
    setSelected(result);
  }

  return (
    <AdminShell title="Customer Search">
      <form className="admin-toolbar" onSubmit={search}>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search name, phone, or address" />
        <button className="primary" type="submit">Search</button>
      </form>
      <div className="admin-grid-two">
        <section className="admin-panel">
          <h2>Customers</h2>
          <table className="admin-table">
            <tbody>
              {customers.map((customer) => (
                <tr key={customer.customer_id} onClick={() => void open(customer.customer_id)}>
                  <td>{customer.display_name || "Unknown"}</td>
                  <td>{customer.phone_e164 || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="admin-panel">
          <h2>Profile</h2>
          <p>{selected?.customer.display_name || "No customer selected"}</p>
          <p>{selected?.customer.phone_e164}</p>
          <h3>Addresses</h3>
          <ul>
            {(selected?.customer.addresses ?? []).map((address, index) => (
              <li key={`${address.address_text}-${index}`}>{address.label}: {address.address_text}</li>
            ))}
          </ul>
          <h3>Orders</h3>
          <table className="admin-table">
            <tbody>
              {(selected?.orders ?? []).map((order) => (
                <tr key={order.order_id}><td>{order.order_id}</td><td>{order.status}</td><td>{money(order.total, order.currency)}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </AdminShell>
  );
}
