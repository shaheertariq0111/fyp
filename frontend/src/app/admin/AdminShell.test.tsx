import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminShell } from "@/app/admin/AdminShell";

let pathname = "/admin/tickets";

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
}));

vi.mock("@/lib/adminApi", () => ({
  adminPost: vi.fn(),
}));

beforeEach(() => {
  pathname = "/admin/tickets";
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
        "Menu",
        "Customers",
        "Monitoring",
      ]);
      expect(within(navigation).getByRole("link", { name: "Support Tickets" }))
        .toHaveAttribute("href", "/admin/tickets");
    }
  });

  it.each([
    ["/admin/tickets", true],
    ["/admin/tickets/", true],
    ["/admin/tickets/TKT-20260724-ABC123", true],
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
});
