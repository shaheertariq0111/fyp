"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { apiPost } from "@/lib/api";
import { getLocalSessionId, getLocalUserId } from "@/lib/session";
import type { ChatMessage, ToolResponse } from "@/types";

type ChatApiResponse = {
  text: string;
  session_id: string;
  user_id: string;
};

const starterPrompts = [
  "What should I order?",
  "Recommend something spicy",
  "Show me chicken pizzas",
  "I want a deal",
];

export default function ChatPage() {
  const [sessionId, setSessionId] = useState("");
  const [userId, setUserId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "welcome",
      role: "assistant",
      text: "Hi. What would you like to order today?",
    },
  ]);
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
        branch_id: "default",
      });
      setMessages((current) => [
        ...current,
        { id: crypto.randomUUID(), role: "assistant", text: response.text },
      ]);
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
        branch_id: "default",
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
          <h2>Quick Starts</h2>
          <div className="actions">
            {starterPrompts.map((prompt) => (
              <button className="chip" key={prompt} onClick={() => sendMessage(prompt)} type="button">
                {prompt}
              </button>
            ))}
          </div>
          <p className="hint">
            Session: {sessionId || "loading"}
            <br />
            The assistant uses backend tools for menu, cart, and order state.
          </p>
        </aside>
      </section>
    </main>
  );
}
