"use client";

import { FormEvent, useEffect, useState } from "react";
import { AdminShell, money } from "@/app/admin/AdminShell";
import { adminGet, adminPatch, adminPost, adminPut } from "@/lib/adminApi";

type MenuItem = {
  product_id: string;
  name: string;
  category: string;
  currency: string;
  description?: string;
  available: boolean;
  archived?: boolean;
  starting_price?: number;
  tags?: string[];
};

const emptyItem = {
  product_id: "",
  name: "",
  category: "",
  currency: "PKR",
  description: "",
  available: true,
  starting_price: 0,
  tags: "",
};

export default function AdminMenuPage() {
  const [items, setItems] = useState<MenuItem[]>([]);
  const [form, setForm] = useState(emptyItem);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [category, setCategory] = useState({ category_id: "", name: "", sort_order: 999 });
  const [optionGroupJson, setOptionGroupJson] = useState('{"option_group_id":"","name":"","type":"single_select","question":"","options":[]}');
  const [upsellGroupJson, setUpsellGroupJson] = useState('{"upsell_group_id":"","question":"","items":[]}');
  const [message, setMessage] = useState("");

  async function load() {
    const result = await adminGet<{ items: MenuItem[] }>("/api/admin/menu/entities?type=menu_item");
    setItems(result.items);
  }

  useEffect(() => {
    void load().catch(console.error);
  }, []);

  async function saveItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    const payload = {
      ...form,
      tags: form.tags.split(",").map((tag) => tag.trim()).filter(Boolean),
      base_prices: {},
      requires_customization: false,
      customization_group_ids: [],
      upsell_group_ids: [],
      search_terms: [],
      image_url: null,
      metadata: {},
    };
    if (editingId) {
      await adminPut(`/api/admin/menu/items/${editingId}`, payload);
    } else {
      await adminPost("/api/admin/menu/items", payload);
    }
    setForm(emptyItem);
    setEditingId(null);
    setMessage("Menu item saved.");
    await load();
  }

  async function toggle(item: MenuItem) {
    await adminPatch(`/api/admin/menu/items/${item.product_id}/availability`, { available: !item.available });
    await load();
  }

  async function archive(item: MenuItem) {
    await adminPatch(`/api/admin/menu/items/${item.product_id}/archive`);
    await load();
  }

  function edit(item: MenuItem) {
    setEditingId(item.product_id);
    setForm({
      product_id: item.product_id,
      name: item.name,
      category: item.category,
      currency: item.currency,
      description: item.description ?? "",
      available: item.available,
      starting_price: item.starting_price ?? 0,
      tags: (item.tags ?? []).join(", "),
    });
  }

  async function saveCategory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await adminPost("/api/admin/menu/categories", category);
    setCategory({ category_id: "", name: "", sort_order: 999 });
    setMessage("Category saved.");
  }

  async function saveJson(path: string, value: string) {
    const payload = JSON.parse(value);
    await adminPost(path, payload);
    setMessage("Menu entity saved.");
  }

  return (
    <AdminShell title="Menu Management">
      {message && <div className="admin-success">{message}</div>}
      <section className="admin-panel">
        <h2>{editingId ? "Edit Item" : "Add Item"}</h2>
        <form className="admin-form-grid" onSubmit={saveItem}>
          <input placeholder="Product ID" value={form.product_id} disabled={Boolean(editingId)} onChange={(event) => setForm({ ...form, product_id: event.target.value })} />
          <input placeholder="Name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
          <input placeholder="Category ID" value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })} />
          <input placeholder="Currency" value={form.currency} onChange={(event) => setForm({ ...form, currency: event.target.value })} />
          <input placeholder="Starting price" type="number" value={form.starting_price} onChange={(event) => setForm({ ...form, starting_price: Number(event.target.value) })} />
          <input placeholder="Tags, comma separated" value={form.tags} onChange={(event) => setForm({ ...form, tags: event.target.value })} />
          <textarea placeholder="Description" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
          <button className="primary" type="submit">Save Item</button>
        </form>
      </section>
      <section className="admin-panel">
        <h2>Items</h2>
        <table className="admin-table">
          <thead><tr><th>Name</th><th>Category</th><th>Price</th><th>Available</th><th>Archived</th><th>Actions</th></tr></thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.product_id}>
                <td>{item.name}</td>
                <td>{item.category}</td>
                <td>{money(item.starting_price, item.currency)}</td>
                <td>{item.available ? "Yes" : "No"}</td>
                <td>{item.archived ? "Yes" : "No"}</td>
                <td className="admin-row-actions">
                  <button className="secondary" onClick={() => edit(item)}>Edit</button>
                  <button className="secondary" onClick={() => void toggle(item)}>{item.available ? "Disable" : "Enable"}</button>
                  <button className="secondary danger" onClick={() => void archive(item)}>Archive</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="admin-grid-two">
        <form className="admin-panel admin-form-grid" onSubmit={saveCategory}>
          <h2>Category</h2>
          <input placeholder="Category ID" value={category.category_id} onChange={(event) => setCategory({ ...category, category_id: event.target.value })} />
          <input placeholder="Name" value={category.name} onChange={(event) => setCategory({ ...category, name: event.target.value })} />
          <input type="number" value={category.sort_order} onChange={(event) => setCategory({ ...category, sort_order: Number(event.target.value) })} />
          <button className="primary" type="submit">Save Category</button>
        </form>
        <section className="admin-panel">
          <h2>Option Group JSON</h2>
          <textarea value={optionGroupJson} onChange={(event) => setOptionGroupJson(event.target.value)} />
          <button className="primary" onClick={() => void saveJson("/api/admin/menu/option-groups", optionGroupJson)}>Save Option Group</button>
        </section>
        <section className="admin-panel">
          <h2>Upsell Group JSON</h2>
          <textarea value={upsellGroupJson} onChange={(event) => setUpsellGroupJson(event.target.value)} />
          <button className="primary" onClick={() => void saveJson("/api/admin/menu/upsell-groups", upsellGroupJson)}>Save Upsell Group</button>
        </section>
      </section>
    </AdminShell>
  );
}
