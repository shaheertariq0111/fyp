"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { getLocalCustomerId, getLocalSessionId } from "@/lib/session";
import type { MenuItem, MenuOrderItem, OptionGroup, ToolResponse } from "@/types";

type MenuResponse = ToolResponse<{ items: MenuItem[] }>;
type ItemResponse = ToolResponse<{ item: MenuItem & { customization_groups: OptionGroup[] } }>;

const formatPrice = (item: MenuItem): string => {
  if (item.base_prices && Object.keys(item.base_prices).length > 0) {
    const values = Object.values(item.base_prices);
    return `From PKR ${Math.min(...values)}`;
  }
  if (item.starting_price) return `From PKR ${item.starting_price}`;
  if (item.price) return `PKR ${item.price}`;
  return item.currency;
};

export default function MenuPage() {
  const [items, setItems] = useState<MenuItem[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<(MenuItem & { customization_groups?: OptionGroup[] }) | null>(null);
  const [choices, setChoices] = useState<Record<string, string | string[]>>({});
  const [quantity, setQuantity] = useState(1);
  const [cart, setCart] = useState<MenuOrderItem[]>([]);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  const loadMenu = useCallback(async (nextQuery: string) => {
    setLoading(true);
    try {
      const suffix = nextQuery ? `?query=${encodeURIComponent(nextQuery)}` : "";
      const response = await apiGet<MenuResponse>(`/api/menu${suffix}`);
      setItems(response.data.items);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load menu.");
    } finally {
      setLoading(false);
    }
  }, []);

  const selectItem = useCallback(async (itemId: string) => {
    setLoading(true);
    try {
      const response = await apiGet<ItemResponse>(`/api/menu/items/${itemId}`);
      setSelected(response.data.item);
      setChoices({});
      setQuantity(1);
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load item.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const itemId = params.get("item_id");
    void loadMenu("");
    if (itemId) void selectItem(itemId);
  }, [loadMenu, selectItem]);

  function choose(group: OptionGroup, optionId: string) {
    setChoices((current) => {
      if (group.type === "multi_select") {
        const selectedValues = Array.isArray(current[group.option_group_id])
          ? (current[group.option_group_id] as string[])
          : [];
        const next = selectedValues.includes(optionId)
          ? selectedValues.filter((value) => value !== optionId)
          : [...selectedValues, optionId];
        return { ...current, [group.option_group_id]: next };
      }
      return { ...current, [group.option_group_id]: optionId };
    });
  }

  const missingRequired = useMemo(() => {
    return (selected?.customization_groups || [])
      .filter((group) => group.required)
      .filter((group) => {
        const value = choices[group.option_group_id];
        return Array.isArray(value) ? value.length === 0 : !value;
      });
  }, [choices, selected]);

  function addToCart() {
    if (!selected || missingRequired.length > 0) return;
    setCart((current) => [
      ...current,
      {
        item_id: selected.product_id,
        quantity,
        selected_options: choices,
        label: selected.name,
      },
    ]);
    setMessage(`${selected.name} added.`);
  }

  async function submitCart() {
    if (cart.length === 0) return;
    setLoading(true);
    try {
      const params = new URLSearchParams(window.location.search);
      const sessionToken = params.get("session_token");
      const response = await apiPost<ToolResponse<{ order_id: string }>>("/api/menu-orders", {
        items: cart,
        session_token: sessionToken,
        session_id: sessionToken ? undefined : getLocalSessionId(),
        user_id: sessionToken ? undefined : getLocalCustomerId(),
        customer_id: sessionToken ? undefined : getLocalCustomerId(),
        channel: "web",
      });
      setCart([]);
      setMessage(`Pending order ready: ${response.data.order_id}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not create order.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">Pizza Ordering Agent</div>
        <nav className="nav">
          <Link href="/chat">Chat</Link>
          <Link href="/menu">Menu</Link>
        </nav>
      </header>
      <section className="menu-layout">
        <div className="menu-main">
          <div className="searchbar">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search menu"
            />
            <button className="primary" onClick={() => loadMenu(query)} type="button">
              Search
            </button>
          </div>
          {loading && <p className="hint">Loading...</p>}
          <div className="grid">
            {items.map((item) => (
              <button
                className={`item-card ${selected?.product_id === item.product_id ? "selected" : ""}`}
                key={item.product_id}
                onClick={() => selectItem(item.product_id)}
                type="button"
              >
                <h3>{item.name}</h3>
                <p>{item.description}</p>
                <div className="price">{formatPrice(item)}</div>
                <div className="tag-row">
                  {(item.tags || []).slice(0, 3).map((tag) => (
                    <span className="tag" key={tag}>
                      {tag}
                    </span>
                  ))}
                </div>
              </button>
            ))}
          </div>
        </div>
        <aside className="panel">
          <h2>{selected ? selected.name : "Select an item"}</h2>
          {selected ? (
            <div className="builder">
              <p className="hint">{selected.description}</p>
              {(selected.customization_groups || []).map((group) => (
                <div className="option-group" key={group.option_group_id}>
                  <h3>{group.question}</h3>
                  <div className="option-list">
                    {group.options.map((option) => {
                      const value = choices[group.option_group_id];
                      const selectedOption = Array.isArray(value)
                        ? value.includes(option.option_id)
                        : value === option.option_id;
                      return (
                        <button
                          className={`chip ${selectedOption ? "selected" : ""}`}
                          key={option.option_id}
                          onClick={() => choose(group, option.option_id)}
                          type="button"
                        >
                          {option.name || option.label || option.option_id}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
              <div className="qty">
                <span>Qty</span>
                <input
                  min={1}
                  type="number"
                  value={quantity}
                  onChange={(event) => setQuantity(Math.max(1, Number(event.target.value)))}
                />
              </div>
              <button
                className="primary"
                disabled={missingRequired.length > 0}
                onClick={addToCart}
                type="button"
              >
                Add to Cart
              </button>
            </div>
          ) : (
            <p className="hint">Choose a menu item to see details and required options.</p>
          )}
          <h2 style={{ marginTop: 24 }}>Cart</h2>
          <div className="cart-list">
            {cart.map((item, index) => (
              <div className="cart-row" key={`${item.item_id}-${index}`}>
                <strong>{item.label}</strong>
                <span className="hint">Quantity {item.quantity}</span>
              </div>
            ))}
          </div>
          <button className="primary" disabled={cart.length === 0 || loading} onClick={submitCart} type="button">
            Create Pending Order
          </button>
          {message && <p className={message.includes("ready") || message.includes("added") ? "success" : "error"}>{message}</p>}
        </aside>
      </section>
    </main>
  );
}
