"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { apiPost } from "@/lib/api";
import { branchId } from "@/lib/config";
import { getLocalSessionId, getLocalUserId, resetLocalSessionId } from "@/lib/session";
import type { ChatApiResponse, ChatMessage, ToolResponse } from "@/types";

const initialMessages: ChatMessage[] = [
  {
    id: "welcome",
    role: "assistant",
    text: "Hi. What would you like to order today?",
  },
];

type CartState = {
  cart_id?: string;
  status?: string;
  items?: Array<{ name?: string; quantity?: number; line_total?: number; current_price?: number }>;
  subtotal?: number;
  currency?: string;
};

type OrderState = {
  order_id?: string;
  status?: string;
  total?: number;
  currency?: string;
  fulfillment_method?: string | null;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function cartFromState(state: Record<string, unknown>): CartState | null {
  const cart = asRecord(state.cart);
  if (!cart) return null;
  const summary = asRecord(cart.cart_summary);
  const source = summary ?? cart;
  return {
    cart_id: typeof cart.cart_id === "string" ? cart.cart_id : undefined,
    status: typeof cart.status === "string" ? cart.status : typeof cart.cart_status === "string" ? cart.cart_status : undefined,
    items: Array.isArray(source.items) ? (source.items as CartState["items"]) : [],
    subtotal: typeof source.subtotal === "number" ? source.subtotal : undefined,
    currency: typeof source.currency === "string" ? source.currency : undefined,
  };
}

function orderFromState(state: Record<string, unknown>): OrderState | null {
  const order = asRecord(state.order);
  if (!order) return null;
  return {
    order_id: typeof order.order_id === "string" ? order.order_id : undefined,
    status: typeof order.status === "string" ? order.status : undefined,
    total: typeof order.total === "number" ? order.total : undefined,
    currency: typeof order.currency === "string" ? order.currency : undefined,
    fulfillment_method:
      typeof order.fulfillment_method === "string" || order.fulfillment_method === null
        ? order.fulfillment_method
        : undefined,
  };
}

function stateFromToolResponse(response: ToolResponse): Record<string, unknown> {
  const agent = asRecord(response.agent);
  if (agent?.entity === "cart") {
    return {
      cart: {
        cart_id: agent.cart_id,
        status: agent.cart_status,
        cart_summary: agent.cart_summary,
      },
    };
  }
  if (agent?.entity === "order") {
    return { order: response.data };
  }
  if (agent?.entity === "orders") {
    const data = asRecord(response.data);
    return { orders: data?.orders ?? [] };
  }
  return {};
}

export default function ChatPage() {
  const [sessionId, setSessionId] = useState("");
  const [userId, setUserId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [state, setState] = useState<Record<string, unknown>>({});
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setSessionId(getLocalSessionId());
    setUserId(getLocalUserId());
  }, []);

  const canSend = useMemo(() => input.trim().length > 0 && !loading, [input, loading]);

  async function sendMessage(text: string) {
    if (!text.trim() || !sessionId || !userId) return;
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      text: text.trim(),
    };
    setMessages((current) => [...current, userMessage]);
    setInput("");
    setLoading(true);
    try {
      const response = await apiPost<ChatApiResponse>("/api/chat", {
        message: text.trim(),
        session_id: sessionId,
        user_id: userId,
        branch_id: branchId,
      });
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: response.text,
          buttons: response.buttons,
          toolCalls: response.tool_calls,
          writeSucceeded: response.write_succeeded,
        },
      ]);
      if (response.state && Object.keys(response.state).length > 0) {
        setState(response.state);
      }
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: error instanceof Error ? error.message : "Something went wrong.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function sendAction(action: string, metadata: Record<string, unknown> = {}) {
    setLoading(true);
    try {
      const response = await apiPost<ToolResponse>("/api/actions", {
        action,
        metadata,
        session_id: sessionId,
        user_id: userId,
        branch_id: branchId,
      });
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: response.user_message,
          buttons: response.buttons,
        },
      ]);
      const nextState = stateFromToolResponse(response);
      if (Object.keys(nextState).length > 0) {
        setState((current) => ({ ...current, ...nextState }));
      }
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: error instanceof Error ? error.message : "Action failed.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (canSend) void sendMessage(input);
  }

  function startNewOrder() {
    const nextSessionId = resetLocalSessionId();
    setSessionId(nextSessionId);
    setMessages(initialMessages);
    setState({});
    setInput("");
  }

  const currentCart = cartFromState(state);
  const currentOrder = orderFromState(state);

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">Pizza Ordering Agent</div>
        <nav className="nav">
          <Link href="/chat">Chat</Link>
          <Link href="/menu">Menu</Link>
        </nav>
      </header>
      <section className="chat-layout">
        <div className="chat-main">
          <div className="messages">
            {messages.map((message) => (
              <div key={message.id} className={`bubble ${message.role}`}>
                {message.text}
                {message.role === "assistant" && message.toolCalls?.some((call) => call.is_write) && (
                  <div className={message.writeSucceeded ? "status success" : "status error"}>
                    {message.writeSucceeded ? "Backend update verified." : "No backend update was completed."}
                  </div>
                )}
                {message.role === "assistant" &&
                  message.toolCalls
                    ?.filter((call) => call.error_code)
                    .map((call) => (
                      <div className="status error" key={`${message.id}-${call.tool_name}-${call.error_code}`}>
                        {call.result?.user_message || "Backend action failed."}
                      </div>
                    ))}
                {message.buttons && message.buttons.length > 0 && (
                  <div className="actions">
                    {message.buttons.map((button) => (
                      <button
                        className="secondary"
                        key={`${message.id}-${button.label}`}
                        onClick={() => sendAction(button.action, button.metadata)}
                        type="button"
                      >
                        {button.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {loading && <div className="bubble assistant">Working...</div>}
          </div>
          <form className="composer" onSubmit={onSubmit}>
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="Ask for a recommendation or start an order"
            />
            <button className="primary" disabled={!canSend} type="submit">
              Send
            </button>
          </form>
        </div>
        <aside className="side">
          <button className="primary full" onClick={startNewOrder} type="button">
            New order
          </button>
          <p className="hint">
            Session: {sessionId || "loading"}
            <br />
            The assistant uses backend tools for menu, cart, and order state.
          </p>
          {(currentCart || currentOrder) && (
            <div className="summary">
              {currentCart && (
                <>
                  <h2>Cart</h2>
                  <p className="hint">Status: {currentCart.status || "unknown"}</p>
                  {currentCart.items && currentCart.items.length > 0 && (
                    <ul>
                      {currentCart.items.map((item, index) => (
                        <li key={`${item.name || "item"}-${index}`}>
                          {item.quantity || 1} x {item.name || "Item"}
                        </li>
                      ))}
                    </ul>
                  )}
                  {currentCart.subtotal !== undefined && (
                    <p className="hint">
                      Subtotal: {currentCart.currency || ""} {currentCart.subtotal}
                    </p>
                  )}
                </>
              )}
              {currentOrder && (
                <>
                  <h2>Order</h2>
                  <p className="hint">
                    {currentOrder.order_id || "Order"}: {currentOrder.status || "unknown"}
                  </p>
                  {currentOrder.total !== undefined && (
                    <p className="hint">
                      Total: {currentOrder.currency || ""} {currentOrder.total}
                    </p>
                  )}
                </>
              )}
            </div>
          )}
        </aside>
      </section>
    </main>
  );
}
