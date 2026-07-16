"use client";

import Link from "next/link";
import { ReactNode } from "react";
import { adminPost } from "@/lib/adminApi";

export function AdminShell({ title, children }: { title: string; children: ReactNode }) {
  async function logout() {
    await adminPost("/api/admin/logout", {});
    window.location.href = "/admin/login";
  }

  return (
    <main className="admin-shell">
      <aside className="admin-nav">
        <div className="admin-brand">MVP Pizza Admin</div>
        <Link href="/admin">Dashboard</Link>
        <Link href="/admin/orders">Live Orders</Link>
        <Link href="/admin/menu">Menu</Link>
        <Link href="/admin/customers">Customers</Link>
        <Link href="/admin/monitoring">Monitoring</Link>
        <button className="admin-nav-button" onClick={logout}>Logout</button>
      </aside>
      <section className="admin-content">
        <header className="admin-header">
          <h1>{title}</h1>
        </header>
        {children}
      </section>
    </main>
  );
}

export function money(value?: number, currency = "PKR") {
  return `${currency} ${value ?? 0}`;
}
