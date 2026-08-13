import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminShell, formatEnvironmentLabel } from "@/app/admin/AdminShell";

let pathname = "/admin/tickets";

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
}));

vi.mock("@/lib/adminApi", () => ({
  adminPost: vi.fn(),
}));

beforeEach(() => {
  pathname = "/admin/tickets";
  window.localStorage.clear();
});

describe("AdminShell ticket navigation", () => {
  it("places Support Tickets immediately after Live Orders in desktop and mobile navigation", () => {
    render(<AdminShell title="Test">Content</AdminShell>);

    const navigations = screen.getAllByRole("navigation", { name: "Admin sections" });
    expect(navigations).toHaveLength(2);
    for (const navigation of navigations) {
      const links = within(navigation).getAllByRole("link");
      expect(links.map((link) => link.textContent)).toEqual([
        "Overview",
        "Live Orders",
        "Support Tickets",
        "Conversations",
        "Menu",
        "Customers",
        "Monitoring",
      ]);
      expect(within(navigation).getByRole("link", { name: "Support Tickets" }))
        .toHaveAttribute("href", "/admin/tickets");
      expect(within(navigation).getByRole("link", { name: "Conversations" }))
        .toHaveAttribute("href", "/admin/conversations");
    }
  });

  it.each([
    ["/admin/tickets", true],
    ["/admin/tickets/", true],
    ["/admin/tickets/TKT-20260724-ABC123", true],
    ["/admin/conversations", false],
    ["/admin/orders", false],
  ])("uses the existing active-route behavior for %s", (route, active) => {
    pathname = route;
    render(<AdminShell title="Test">Content</AdminShell>);

    for (const link of screen.getAllByRole("link", { name: "Support Tickets" })) {
      if (active) {
        expect(link).toHaveAttribute("aria-current", "page");
      } else {
        expect(link).not.toHaveAttribute("aria-current");
      }
    }
  });

  it.each([
    ["/admin/conversations", true],
    ["/admin/conversations/whatsapp-123", true],
    ["/admin/tickets", false],
  ])("marks Conversations active for %s", (route, active) => {
    pathname = route;
    render(<AdminShell title="Test">Content</AdminShell>);

    for (const link of screen.getAllByRole("link", { name: "Conversations" })) {
      if (active) {
        expect(link).toHaveAttribute("aria-current", "page");
      } else {
        expect(link).not.toHaveAttribute("aria-current");
      }
    }
  });

  it.each([
    [undefined, undefined, "Local development"],
    ["default", "http://localhost:8001", "Local development"],
    ["default", "https://abc123.execute-api.us-east-1.amazonaws.com", "AWS deployment"],
    ["main", "https://abc123.execute-api.us-east-1.amazonaws.com", "main"],
  ])("formats environment labels for branch %s and API %s", (branchId, apiBaseUrl, expected) => {
    expect(formatEnvironmentLabel(branchId, apiBaseUrl)).toBe(expected);
  });

  it("persists desktop sidebar collapse and expand actions", () => {
    const { container } = render(<AdminShell title="Test">Content</AdminShell>);

    const collapseButton = screen.getByRole("button", { name: "Collapse sidebar" });
    fireEvent.click(collapseButton);

    expect(container.firstElementChild).toHaveClass("is-sidebar-collapsed");
    expect(window.localStorage.getItem("admin-sidebar-collapsed")).toBe("true");

    const expandButton = screen.getByRole("button", { name: "Expand sidebar" });
    fireEvent.click(expandButton);

    expect(container.firstElementChild).not.toHaveClass("is-sidebar-collapsed");
    expect(window.localStorage.getItem("admin-sidebar-collapsed")).toBe("false");
  });

  it("restores a stored collapsed preference after client initialization", async () => {
    window.localStorage.setItem("admin-sidebar-collapsed", "true");
    const { container } = render(<AdminShell title="Test">Content</AdminShell>);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
    });
    expect(container.firstElementChild).toHaveClass("is-sidebar-collapsed", "is-sidebar-ready");

    const desktopNavigation = screen.getAllByRole("navigation", { name: "Admin sections" })[0];
    expect(within(desktopNavigation).getByRole("link", { name: "Overview" }))
      .toHaveAttribute("data-tooltip", "Overview");
    expect(screen.getAllByRole("button", { name: "Logout" })[0])
      .toHaveAttribute("data-tooltip", "Logout");
  });
});
