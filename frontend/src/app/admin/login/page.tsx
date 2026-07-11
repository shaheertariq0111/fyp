"use client";

import { FormEvent, useState } from "react";
import { adminPost } from "@/lib/adminApi";

export default function AdminLoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      await adminPost("/api/admin/login", { username, password });
      window.location.href = "/admin";
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Login failed.");
    }
  }

  return (
    <main className="admin-login">
      <form className="admin-login-panel" onSubmit={submit}>
        <h1>MVP Pizza Admin</h1>
        <label>
          Username
          <input value={username} onChange={(event) => setUsername(event.target.value)} />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
        </label>
        {error && <div className="admin-error">{error}</div>}
        <button className="primary" type="submit">Login</button>
      </form>
    </main>
  );
}
