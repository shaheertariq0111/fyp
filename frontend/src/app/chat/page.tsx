"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ApiRequestError, apiGet, apiPost } from "@/lib/api";
import { branchId } from "@/lib/config";
import {
  getLocalCustomerId,
  getLocalSessionId,
  resetLocalSessionId,
  saveLocalCustomerId,
  saveLocalSessionId,
} from "@/lib/session";
import type { ChatMessage, ChatStatusResponse, ChatSubmitResponse, ToolResponse } from "@/types";

const pollIntervalMs = 1500;
const maxPollWaitMs = 90000;

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

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("The operation was aborted.", "AbortError"));
      return;
    }
    const timeout = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      window.clearTimeout(timeout);
      signal.removeEventListener("abort", onAbort);
      reject(new DOMException("The operation was aborted.", "AbortError"));
    }
    signal.addEventListener("abort", onAbort);
  });
}

export default function ChatPage() {
  const [sessionId, setSessionId] = useState("");
  const [customerId, setCustomerId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [state, setState] = useState<Record<string, unknown>>({});
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const activeControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(false);
  const submittingRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    setSessionId(getLocalSessionId());
    setCustomerId(getLocalCustomerId());
    return () => {
      mountedRef.current = false;
      activeControllerRef.current?.abort();
      activeControllerRef.current = null;
      submittingRef.current = false;
    };
  }, []);

  const canSend = useMemo(() => input.trim().length > 0 && !loading, [input, loading]);

  function applyIdentity(response: ChatSubmitResponse | ChatStatusResponse) {
    if (response.session_id && response.session_id !== sessionId) {
      saveLocalSessionId(response.session_id);
      setSessionId(response.session_id);
    }
    if (response.customer_id && response.customer_id !== customerId) {
      saveLocalCustomerId(response.customer_id);
      setCustomerId(response.customer_id);
    }
  }

  async function pollChatRequest(requestId: string, signal: AbortSignal): Promise<ChatStatusResponse> {
    const deadline = Date.now() + maxPollWaitMs;
    while (Date.now() <= deadline) {
      try {
        const status = await apiGet<ChatStatusResponse>(`/api/chat/${requestId}`, { signal });
        if (status.status === "completed" || status.status === "failed") {
          return status;
        }
      } catch (error) {
        if (isAbortError(error)) {
          throw error;
        }
        if (error instanceof ApiRequestError && error.status && error.status >= 400 && error.status < 500) {
          return {
            request_id: requestId,
            status: "failed",
            error_code: "CHAT_STATUS_UNAVAILABLE",
            message: error.message,
            data: {},
            tool_calls: [],
            write_succeeded: false,
            state: {},
            buttons: [],
          };
        }
      }
      await delay(Math.min(pollIntervalMs, Math.max(0, deadline - Date.now())), signal);
    }
    return {
      request_id: requestId,
      status: "failed",
      error_code: "CLIENT_POLL_TIMEOUT",
      message: "The request is still processing. Please try again shortly.",
      data: {},
      tool_calls: [],
      write_succeeded: false,
      state: {},
      buttons: [],
    };
  }

  async function sendMessage(text: string) {
    if (!text.trim() || !sessionId || !customerId || submittingRef.current) return;
    submittingRef.current = true;
    activeControllerRef.current?.abort();
    const controller = new AbortController();
    activeControllerRef.current = controller;
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      text: text.trim(),
    };
    setMessages((current) => [...current, userMessage]);
    setInput("");
    setLoading(true);
    try {
      const submission = await apiPost<ChatSubmitResponse>("/api/chat", {
        message: text.trim(),
        session_id: sessionId,
        user_id: customerId,
        customer_id: customerId,
        channel: "web",
        branch_id: branchId,
      }, { signal: controller.signal });
      if (!mountedRef.current) return;
      applyIdentity(submission);
      const response = await pollChatRequest(submission.request_id, controller.signal);
      if (!mountedRef.current) return;
      applyIdentity(response);
      if (response.status === "failed") {
        throw new Error(response.message || "The request could not be completed.");
      }
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: response.text || response.response || "",
          buttons: response.buttons,
          toolCalls: response.tool_calls,
          writeSucceeded: response.write_succeeded,
        },
      ]);
      if (response.state && Object.keys(response.state).length > 0) {
        setState(response.state);
      }
    } catch (error) {
      if (isAbortError(error)) return;
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: error instanceof Error ? error.message : "Something went wrong.",
        },
      ]);
    } finally {
      if (activeControllerRef.current === controller) {
        activeControllerRef.current = null;
      }
      submittingRef.current = false;
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }

  async function sendAction(action: string, metadata: Record<string, unknown> = {}) {
    setLoading(true);
    try {
      const response = await apiPost<ToolResponse>("/api/actions", {
        action,
        metadata,
        session_id: sessionId,
        user_id: customerId,
        customer_id: customerId,
        channel: "web",
        branch_id: branchId,
      });
      const data = asRecord(response.data);
      const session = asRecord(data?.session);
      const customer = asRecord(data?.customer);
      if (typeof session?.session_id === "string" && session.session_id !== sessionId) {
        saveLocalSessionId(session.session_id);
        setSessionId(session.session_id);
      }
      if (typeof customer?.customer_id === "string" && customer.customer_id !== customerId) {
        saveLocalCustomerId(customer.customer_id);
        setCustomerId(customer.customer_id);
      }
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
    activeControllerRef.current?.abort();
    activeControllerRef.current = null;
    submittingRef.current = false;
    setLoading(false);
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
            Customer: {customerId || "loading"}
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
