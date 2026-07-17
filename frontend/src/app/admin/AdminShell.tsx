"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useEffect, useRef, useState } from "react";
import { adminPost } from "@/lib/adminApi";

type AdminShellProps = {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
};

type IconName = "overview" | "orders" | "menu" | "customers" | "monitoring" | "logout" | "menuToggle";

const navigationItems: Array<{ href: string; label: string; icon: IconName }> = [
  { href: "/admin", label: "Overview", icon: "overview" },
  { href: "/admin/orders", label: "Live Orders", icon: "orders" },
  { href: "/admin/menu", label: "Menu", icon: "menu" },
  { href: "/admin/customers", label: "Customers", icon: "customers" },
  { href: "/admin/monitoring", label: "Monitoring", icon: "monitoring" },
];

function AdminIcon({ name }: { name: IconName }) {
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
    case "overview":
      return (
        <svg {...common}>
          <path d="M4 13h6V4H4z" />
          <path d="M14 20h6v-9h-6z" />
          <path d="M4 20h6v-3H4z" />
          <path d="M14 7h6V4h-6z" />
        </svg>
      );
    case "orders":
      return (
        <svg {...common}>
          <path d="M7 4h10" />
          <path d="M6 8h12" />
          <path d="M8 12h8" />
          <path d="M5 20h14l-1-12H6z" />
        </svg>
      );
    case "menu":
      return (
        <svg {...common}>
          <path d="M4 6h16" />
          <path d="M4 12h16" />
          <path d="M4 18h10" />
        </svg>
      );
    case "customers":
      return (
        <svg {...common}>
          <path d="M16 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2" />
          <circle cx="9.5" cy="7" r="4" />
          <path d="M20 21v-2a3 3 0 0 0-2-2.83" />
          <path d="M16 3.13a4 4 0 0 1 0 7.75" />
        </svg>
      );
    case "monitoring":
      return (
        <svg {...common}>
          <path d="M4 19V5" />
          <path d="M4 19h16" />
          <path d="M8 16v-5" />
          <path d="M12 16V8" />
          <path d="M16 16v-3" />
        </svg>
      );
    case "logout":
      return (
        <svg {...common}>
          <path d="M10 17l5-5-5-5" />
          <path d="M15 12H3" />
          <path d="M21 19V5" />
        </svg>
      );
    case "menuToggle":
      return (
        <svg {...common}>
          <path d="M4 7h16" />
          <path d="M4 12h16" />
          <path d="M4 17h16" />
        </svg>
      );
  }
}

function isActiveRoute(pathname: string, href: string) {
  if (href === "/admin") {
    return pathname === href;
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function humanizeStatus(status: string) {
  return status
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatEnvironmentLabel(branchId: string | undefined) {
  const trimmed = branchId?.trim() ?? "";
  if (!trimmed || trimmed.toLowerCase() === "default") {
    return "Local development";
  }
  return trimmed;
}

export function AdminShell({ title, subtitle, actions, children }: AdminShellProps) {
  const pathname = usePathname();
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [isSigningOut, setIsSigningOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const mobileDrawerRef = useRef<HTMLDivElement>(null);
  const branchId = process.env.NEXT_PUBLIC_BRANCH_ID;
  const environmentLabel = formatEnvironmentLabel(branchId);

  useEffect(() => {
    if (!isDrawerOpen) {
      return;
    }
    const firstLink = mobileDrawerRef.current?.querySelector<HTMLAnchorElement | HTMLButtonElement>("a, button");
    firstLink?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsDrawerOpen(false);
        menuButtonRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isDrawerOpen]);

  function closeDrawer() {
    setIsDrawerOpen(false);
    menuButtonRef.current?.focus();
  }

  async function logout() {
    if (isSigningOut) {
      return;
    }
    setIsSigningOut(true);
    setLogoutError("");
    try {
      await adminPost("/api/admin/logout", {});
      window.location.href = "/admin/login";
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      setLogoutError("Sign out could not be completed. Please try again.");
      setIsSigningOut(false);
      console.warn("Admin sign-out failed", { reason: "request_rejected_or_unavailable" });
    }
  }

  const sidebar = (
    <aside className="admin-sidebar" aria-label="Admin navigation">
      <div className="admin-sidebar-brand">
        <span className="admin-brand-mark" aria-hidden="true">
          <span />
        </span>
        <span>
          <strong>Pizza Operations</strong>
          <small>Restaurant Control Center</small>
        </span>
      </div>
      <div className="admin-branch-indicator">
        <span>Environment</span>
        <strong>{environmentLabel}</strong>
      </div>
      <nav className="admin-sidebar-nav" aria-label="Admin sections">
        {navigationItems.map((item) => {
          const active = isActiveRoute(pathname, item.href);
          return (
            <Link
              aria-current={active ? "page" : undefined}
              className={`admin-nav-item${active ? " is-active" : ""}`}
              href={item.href}
              key={item.href}
              onClick={() => setIsDrawerOpen(false)}
            >
              <AdminIcon name={item.icon} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
      {logoutError && <p className="admin-sidebar-error" role="alert">{logoutError}</p>}
      <button className="admin-logout-button" disabled={isSigningOut} onClick={() => void logout()} type="button">
        <AdminIcon name="logout" />
        <span>{isSigningOut ? "Signing out..." : "Logout"}</span>
      </button>
    </aside>
  );

  return (
    <div className="admin-layout">
      <a className="admin-skip-link" href="#admin-main-content">Skip to main content</a>
      <div className="admin-desktop-sidebar">{sidebar}</div>
      {isDrawerOpen && (
        <button
          aria-label="Close admin navigation"
          className="admin-drawer-scrim"
          onClick={closeDrawer}
          type="button"
        />
      )}
      <div
        aria-label="Mobile admin navigation"
        className={`admin-mobile-drawer${isDrawerOpen ? " is-open" : ""}`}
        id="admin-mobile-navigation"
        ref={mobileDrawerRef}
      >
        {sidebar}
      </div>
      <div className="admin-main-shell">
        <header className="admin-topbar">
          <button
            aria-controls="admin-mobile-navigation"
            aria-expanded={isDrawerOpen}
            aria-label="Open admin navigation"
            className="admin-menu-button"
            onClick={() => setIsDrawerOpen(true)}
            ref={menuButtonRef}
            type="button"
          >
            <AdminIcon name="menuToggle" />
          </button>
          <div className="admin-topbar-title">
            <span className="admin-live-badge">
              <span aria-hidden="true" />
              Operations Console
            </span>
            <h1>{title}</h1>
            {subtitle && <p>{subtitle}</p>}
          </div>
          {actions && <div className="admin-topbar-actions">{actions}</div>}
        </header>
        <main className="admin-content" id="admin-main-content" tabIndex={-1}>{children}</main>
      </div>
    </div>
  );
}

export function money(value?: number, currency = "PKR") {
  const safeValue = value ?? 0;
  try {
    return new Intl.NumberFormat("en-PK", {
      style: "currency",
      currency,
      maximumFractionDigits: Number.isInteger(safeValue) ? 0 : 2,
    }).format(safeValue);
  } catch {
    return `${currency} ${safeValue}`;
  }
}
