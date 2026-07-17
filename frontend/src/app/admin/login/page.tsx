"use client";

import Image from "next/image";
import { FormEvent, useRef, useState } from "react";
import { adminPost } from "@/lib/adminApi";

type LoginField = "username" | "password";
type FieldErrors = Partial<Record<LoginField, string>>;
type FieldTouched = Record<LoginField, boolean>;

const emptyTouched: FieldTouched = {
  username: false,
  password: false,
};

function loginErrors(username: string, password: string): FieldErrors {
  const errors: FieldErrors = {};
  if (!username.trim()) {
    errors.username = "Username is required.";
  }
  if (!password) {
    errors.password = "Password is required.";
  }
  return errors;
}

function visibleErrors(errors: FieldErrors, touched: FieldTouched, submitted: boolean): FieldErrors {
  return Object.fromEntries(
    Object.entries(errors).filter(([field]) => submitted || touched[field as LoginField]),
  ) as FieldErrors;
}

function hasErrors(errors: FieldErrors) {
  return Object.keys(errors).length > 0;
}

function classifyLoginError(error: Error) {
  const message = error.message.toLowerCase();
  if (message.includes("configured") || message.includes("failed to fetch") || message.includes("network")) {
    return "Sign-in service is currently unreachable. Try again when the service is available.";
  }
  if (message.includes("invalid") || message.includes("credential") || message.includes("401")) {
    return "Sign-in could not be completed. Check your credentials and try again.";
  }
  return "Sign-in could not be completed. Please try again.";
}

function LoginIcon({ name }: { name: "eye" | "eyeOff" | "lock" }) {
  const common = {
    width: 18,
    height: 18,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  switch (name) {
    case "eye":
      return (
        <svg {...common}>
          <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      );
    case "eyeOff":
      return (
        <svg {...common}>
          <path d="m3 3 18 18" />
          <path d="M10.6 10.6a3 3 0 0 0 4.2 4.2" />
          <path d="M9.9 4.3A10.6 10.6 0 0 1 12 4c6.5 0 10 8 10 8a18 18 0 0 1-2.1 3.2" />
          <path d="M6.6 6.6C3.5 8.7 2 12 2 12s3.5 8 10 8c1.8 0 3.4-.5 4.8-1.2" />
        </svg>
      );
    case "lock":
      return (
        <svg {...common}>
          <rect x="4" y="10" width="16" height="10" rx="2" />
          <path d="M8 10V7a4 4 0 0 1 8 0v3" />
        </svg>
      );
  }
}

export default function AdminLoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [touched, setTouched] = useState<FieldTouched>(emptyTouched);
  const [submitted, setSubmitted] = useState(false);
  const [serverError, setServerError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const requestInFlight = useRef(false);
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const warningLogged = useRef(false);

  const errors = loginErrors(username, password);
  const shownErrors = visibleErrors(errors, touched, submitted);

  const markTouched = (field: LoginField) => {
    setTouched((current) => ({ ...current, [field]: true }));
  };

  const focusFirstInvalid = (nextErrors: FieldErrors) => {
    if (nextErrors.username) {
      usernameRef.current?.focus();
      return;
    }
    if (nextErrors.password) {
      passwordRef.current?.focus();
    }
  };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextErrors = loginErrors(username, password);
    if (hasErrors(nextErrors)) {
      setSubmitted(true);
      focusFirstInvalid(nextErrors);
      return;
    }
    if (requestInFlight.current) {
      return;
    }
    requestInFlight.current = true;
    setIsSubmitting(true);
    setServerError("");
    try {
      await adminPost("/api/admin/login", { username: username.trim(), password });
      window.location.href = "/admin";
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      setServerError(classifyLoginError(exc));
      if (!warningLogged.current) {
        console.warn("Admin sign-in failed", { reason: "request_rejected_or_unavailable" });
        warningLogged.current = true;
      }
    } finally {
      requestInFlight.current = false;
      setIsSubmitting(false);
    }
  }

  return (
    <main className="admin-login">
      <section className="admin-login-brand" aria-label="Pizza Operations">
        <div className="admin-sidebar-brand">
          <span className="admin-brand-mark" aria-hidden="true">
            <span />
          </span>
          <span>
            <strong>Pizza Operations</strong>
            <small>Restaurant Control Center</small>
          </span>
        </div>
        <div className="admin-login-brand-content">
          <span className="admin-live-badge">
            <span aria-hidden="true" />
            Operations Console
          </span>
          <h1>Pizza Operations</h1>
          <p>Secure administrative access for restaurant order, menu, customer, and monitoring workflows.</p>
        </div>
        <p className="admin-login-note"><LoginIcon name="lock" /> Authorised staff access only.</p>
        <div className="admin-login-brand-overlay" aria-hidden="true" />
        <div className="admin-login-art" aria-hidden="true">
          <Image
            alt=""
            className="admin-login-brand-image"
            fill
            sizes="(max-width: 620px) 0px, (max-width: 1100px) 80vw, 55vw"
            src="/images/admin-login/pizza-hero.png"
          />
        </div>
      </section>

      <section className="admin-login-panel" aria-labelledby="admin-login-heading">
        <div>
          <h2 id="admin-login-heading">Welcome back</h2>
          <p>Sign in to your operations console</p>
        </div>
        {serverError && (
          <div className="admin-error-panel" role="alert" aria-live="polite">
            <strong>{serverError}</strong>
          </div>
        )}
        <form className="admin-login-form" onSubmit={submit}>
          <label>
            Username
            <input
              autoCapitalize="none"
              autoComplete="username"
              aria-describedby={shownErrors.username ? "admin-login-username-error" : undefined}
              aria-invalid={shownErrors.username ? true : undefined}
              disabled={isSubmitting}
              ref={usernameRef}
              spellCheck={false}
              type="text"
              value={username}
              onBlur={() => markTouched("username")}
              onChange={(event) => {
                setUsername(event.target.value);
                if (serverError) {
                  setServerError("");
                }
              }}
            />
            {shownErrors.username && <p className="admin-form-error" id="admin-login-username-error">{shownErrors.username}</p>}
          </label>
          <label>
            Password
            <span className="admin-password-control">
              <input
                autoComplete="current-password"
                aria-describedby={shownErrors.password ? "admin-login-password-error" : undefined}
                aria-invalid={shownErrors.password ? true : undefined}
                disabled={isSubmitting}
                ref={passwordRef}
                type={showPassword ? "text" : "password"}
                value={password}
                onBlur={() => markTouched("password")}
                onChange={(event) => {
                  setPassword(event.target.value);
                  if (serverError) {
                    setServerError("");
                  }
                }}
              />
              <button
                aria-label={showPassword ? "Hide password" : "Show password"}
                aria-pressed={showPassword}
                disabled={isSubmitting}
                onClick={() => setShowPassword((current) => !current)}
                type="button"
              >
                <LoginIcon name={showPassword ? "eyeOff" : "eye"} />
                <span>{showPassword ? "Hide" : "Show"}</span>
              </button>
            </span>
            {shownErrors.password && <p className="admin-form-error" id="admin-login-password-error">{shownErrors.password}</p>}
          </label>
          <button className="primary admin-login-submit" disabled={isSubmitting} type="submit">
            {isSubmitting ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}
