import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AdminDashboardPage from "@/app/admin/page";
import type { Analytics, OrderDetail } from "@/app/admin/OperationsOverview";
import { adminGet } from "@/lib/adminApi";

vi.mock("@/lib/adminApi", () => ({ adminGet: vi.fn() }));

vi.mock("@/app/admin/AdminShell", () => ({
  AdminShell: ({ actions, children }: { actions?: React.ReactNode; children: React.ReactNode }) => <div>{actions}{children}</div>,
  money: (value?: number, currency = "PKR") => `${currency} ${value ?? 0}`,
}));

const mockedAdminGet = vi.mocked(adminGet);

function analyticsResponse(days: 7 | 30, itemName = "Fajita Pizza"): Analytics {
  const startDate = days === 7 ? "2026-08-08" : "2026-07-16";
  const windowOrderCount = days === 7 ? 3 : 8;
  const peakHour = days === 7 ? 19 : 0;
  return {
    today_orders: 3,
    active_orders: 2,
    revenue: 4210,
    failed_orders: 0,
    by_status: { accepted: 1, completed: 2 },
    recent_orders: [
      { order_id: "ORD-1", status: "accepted", total: 1200, currency: "PKR" },
      { order_id: "ORD-2", status: "completed", total: 3010, currency: "PKR" },
    ],
    chart_window: {
      start_at: `${startDate}T00:00:00+00:00`,
      end_at: "2026-08-15T00:00:00+00:00",
      timezone: "UTC",
      day_count: days,
    },
    orders_revenue_trend: [
      { date: startDate, order_count: 0, revenue_by_currency: {} },
      { date: "2026-08-14", order_count: windowOrderCount, revenue_by_currency: { PKR: 4200, USD: 10 } },
    ],
    orders_by_hour: Array.from({ length: 24 }, (_, hour) => ({
      hour,
      order_count: hour === peakHour ? windowOrderCount : 0,
    })),
    status_distribution: { accepted: 1, completed: 2 },
    top_selling_items: [{ item_id: "pizza-1", name: itemName, quantity: 5 }],
    by_fulfillment: { delivery: 2, takeaway: 1, unspecified: 0 },
  };
}

function orderDetail(orderId: string): { order: OrderDetail } {
  return {
    order: {
      order_id: orderId,
      status: orderId === "ORD-1" ? "accepted" : "completed",
      customer_name: orderId === "ORD-1" ? "Ava" : "Bilal",
      total: orderId === "ORD-1" ? 1200 : 3010,
      currency: "PKR",
      items: [{ name: "Fajita Pizza", quantity: 1, line_total: 1200 }],
      created_at: "2026-08-14T19:00:00+00:00",
    },
  };
}

function installSuccessfulRequests(responseForDays = analyticsResponse) {
  mockedAdminGet.mockImplementation((path) => {
    if (path.startsWith("/api/admin/orders/")) {
      return Promise.resolve(orderDetail(path.split("/").at(-1) ?? "")) as never;
    }
    const days = path.includes("window_days=30") ? 30 : 7;
    return Promise.resolve(responseForDays(days)) as never;
  });
}

function analyticsPaths() {
  return mockedAdminGet.mock.calls
    .map(([path]) => path)
    .filter((path) => path.startsWith("/api/admin/analytics"));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

beforeEach(() => {
  mockedAdminGet.mockReset();
  installSuccessfulRequests();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Admin Operations Overview analytics", () => {
  it("renders all real chart values, defaults to seven days, and keeps recent-order selection working", async () => {
    const user = userEvent.setup();
    render(<AdminDashboardPage />);

    expect(await screen.findByRole("heading", { name: "Analytics" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "7 Days" })).toHaveAttribute("aria-pressed", "true");
    expect(mockedAdminGet).toHaveBeenCalledWith(
      "/api/admin/analytics?window_days=7",
      { signal: expect.any(AbortSignal) },
    );
    expect(screen.getByRole("group", { name: /2026-08-14 3 orders PKR 4200 USD 10/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Order status distribution: Accepted 1, Completed 2/ })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: /Orders by hour:.*7 PM 3/ })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Top selling menu items: Fajita Pizza 5" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Delivery versus takeaway: Delivery 2, Takeaway 1" })).toBeInTheDocument();
    expect(screen.getByText("Peak Hour")).toBeInTheDocument();
    expect(screen.getAllByText(/7 PM.*8 PM/)).toHaveLength(2);
    const snapshot = screen.getByLabelText(/3 orders in 7 days, peak 7 PM.*8 PM, top item Fajita Pizza/);
    expect(within(snapshot).getByText("Current analytics snapshot")).toBeInTheDocument();
    expect(within(snapshot).getByText("3 orders")).toBeInTheDocument();
    expect(within(snapshot).getByText(/7 PM.*8 PM/)).toBeInTheDocument();
    expect(within(snapshot).getByText(/Fajita Pizza/)).toBeInTheDocument();
    expect(within(snapshot).getByRole("img", { name: /Daily orders:.*2026-08-14 3/ })).toBeInTheDocument();
    expect(screen.queryByText("Time-series data is not available")).not.toBeInTheDocument();

    await waitFor(() => expect(mockedAdminGet).toHaveBeenCalledWith(
      "/api/admin/orders/ORD-1",
      { signal: expect.any(AbortSignal) },
    ));
    const viewButtons = screen.getAllByRole("button", { name: "View Order" });
    await user.click(viewButtons[1]);
    expect(await screen.findByText("Bilal")).toBeInTheDocument();
    expect(mockedAdminGet).toHaveBeenCalledWith(
      "/api/admin/orders/ORD-2",
      { signal: expect.any(AbortSignal) },
    );
  });

  it("exposes exact focusable values and hover or focus tooltips for interactive charts", async () => {
    render(<AdminDashboardPage />);

    const trendChart = await screen.findByRole("group", { name: /Orders and revenue trend/ });
    const hourChart = screen.getByRole("group", { name: /Orders by hour/ });
    expect(trendChart.querySelector("title")).not.toBeInTheDocument();
    expect(hourChart.querySelector("title")).not.toBeInTheDocument();

    const orderBar = await screen.findByRole("img", { name: "Aug 14: 3 orders" });
    expect(orderBar).toHaveAttribute("tabindex", "0");
    fireEvent.mouseEnter(orderBar);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Aug 14");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Orders: 3");
    fireEvent.mouseLeave(orderBar);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    const pkrRevenue = screen.getByRole("img", { name: "Aug 14: revenue Rs 4,200" });
    const usdRevenue = screen.getByRole("img", { name: "Aug 14: revenue USD 10" });
    expect(pkrRevenue).toHaveAttribute("tabindex", "0");
    expect(usdRevenue).toHaveAttribute("tabindex", "0");
    fireEvent.focus(pkrRevenue);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Revenue: Rs 4,200");
    fireEvent.blur(pkrRevenue);

    const hourBar = screen.getByRole("img", { name: "7 PM to 8 PM: 3 orders (UTC)" });
    expect(hourBar).toHaveAttribute("tabindex", "0");
    fireEvent.focus(hourBar);
    expect(screen.getByRole("tooltip")).toHaveTextContent(/7 PM.*8 PM/);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Orders: 3");
    expect(screen.getByRole("tooltip")).toHaveTextContent("UTC");
    fireEvent.blur(hourBar);

    const topItem = screen.getByRole("img", { name: "Fajita Pizza: Quantity 5" });
    expect(topItem).toHaveAttribute("tabindex", "0");
    expect(topItem.querySelector("[title]")).not.toBeInTheDocument();
    fireEvent.focus(topItem);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Fajita Pizza");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Quantity: 5");
  });

  it("requests and displays the selected thirty-day analytics window", async () => {
    const user = userEvent.setup();
    render(<AdminDashboardPage />);
    await screen.findByRole("img", { name: "Fajita Pizza: Quantity 5" });

    await user.click(screen.getByRole("button", { name: "30 Days" }));

    await waitFor(() => expect(mockedAdminGet).toHaveBeenCalledWith(
      "/api/admin/analytics?window_days=30",
      { signal: expect.any(AbortSignal) },
    ));
    expect(screen.getByRole("button", { name: "30 Days" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText("30-day UTC view; revenue remains separated by currency.")).toBeInTheDocument();
  });

  it("updates the current snapshot from the selected analytics window response", async () => {
    const user = userEvent.setup();
    installSuccessfulRequests((days) => analyticsResponse(
      days,
      days === 30 ? "Choco Bread" : "Fajita Pizza",
    ));
    render(<AdminDashboardPage />);
    expect(await screen.findByLabelText(/3 orders in 7 days/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "30 Days" }));

    const snapshot = await screen.findByLabelText(/8 orders in 30 days, peak 12 AM.*1 AM, top item Choco Bread/);
    expect(within(snapshot).getByText("8 orders")).toBeInTheDocument();
    expect(within(snapshot).getByText(/12 AM.*1 AM/)).toBeInTheDocument();
    expect(within(snapshot).getByText(/Choco Bread/)).toBeInTheDocument();
  });

  it("shows intentional empty states for every chart", async () => {
    installSuccessfulRequests((days) => ({
      ...analyticsResponse(days),
      orders_revenue_trend: [{ date: "2026-08-14", order_count: 0, revenue_by_currency: {} }],
      orders_by_hour: Array.from({ length: 24 }, (_, hour) => ({ hour, order_count: 0 })),
      status_distribution: {},
      top_selling_items: [],
      by_fulfillment: { delivery: 0, takeaway: 0, unspecified: 0 },
    }));

    render(<AdminDashboardPage />);

    expect(await screen.findAllByText("No order activity in this period.")).toHaveLength(6);
    const snapshot = screen.getByLabelText("No order activity in this period");
    expect(within(snapshot).getByText("Current analytics snapshot")).toBeInTheDocument();
    expect(within(snapshot).getByText("No order activity in this period.")).toBeInTheDocument();
    expect(within(snapshot).queryByText(/Peak:/)).not.toBeInTheDocument();
    expect(within(snapshot).queryByText(/Top:/)).not.toBeInTheDocument();
  });

  it("uses matching, distinguishable semantic colors for status slices and legend markers", async () => {
    const statuses = [
      "accepted",
      "preparing",
      "ready_for_pickup",
      "out_for_delivery",
      "completed",
      "delivered",
      "failed",
    ];
    installSuccessfulRequests((days) => ({
      ...analyticsResponse(days),
      status_distribution: Object.fromEntries(statuses.map((status) => [status, 1])),
    }));
    render(<AdminDashboardPage />);

    const donut = await screen.findByRole("img", { name: /Order status distribution/ });
    const layout = donut.closest(".ops-donut-layout");
    expect(layout).not.toBeNull();
    const colors = statuses.map((status) => {
      const slice = donut.querySelector(`[data-series-key="${status}"]`);
      const marker = layout?.querySelector(`i[data-series-key="${status}"]`);
      expect(slice).not.toBeNull();
      expect(marker).not.toBeNull();
      expect(slice?.getAttribute("data-color")).toBe(marker?.getAttribute("data-color"));
      return slice?.getAttribute("data-color");
    });
    expect(new Set(colors).size).toBe(statuses.length);
    expect(donut).toHaveAccessibleName(/Accepted 1/);
    expect(donut).toHaveAccessibleName(/Preparing 1/);
    expect(donut).toHaveAccessibleName(/Completed 1/);
    expect(donut).toHaveAccessibleName(/Failed 1/);
  });

  it("preserves the selected window for manual and automatic refresh", async () => {
    vi.useFakeTimers();
    render(<AdminDashboardPage />);
    await act(async () => { await Promise.resolve(); });
    fireEvent.click(screen.getByRole("button", { name: "30 Days" }));
    await act(async () => { await Promise.resolve(); });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await act(async () => { await Promise.resolve(); });

    expect(analyticsPaths().at(-1)).toBe("/api/admin/analytics?window_days=30");
    const beforeAutoRefresh = analyticsPaths().length;
    await act(async () => {
      vi.advanceTimersByTime(30000);
      await Promise.resolve();
    });
    expect(analyticsPaths()).toHaveLength(beforeAutoRefresh + 1);
    expect(analyticsPaths().at(-1)).toBe("/api/admin/analytics?window_days=30");
  });

  it("does not let a stale seven-day response overwrite a newer thirty-day result", async () => {
    const sevenDay = deferred<Analytics>();
    const thirtyDay = deferred<Analytics>();
    mockedAdminGet.mockImplementation((path) => {
      if (path.startsWith("/api/admin/orders/")) {
        return Promise.resolve(orderDetail(path.split("/").at(-1) ?? "")) as never;
      }
      return (path.includes("window_days=30") ? thirtyDay.promise : sevenDay.promise) as never;
    });
    render(<AdminDashboardPage />);

    fireEvent.click(screen.getByRole("button", { name: "30 Days" }));
    await act(async () => { thirtyDay.resolve(analyticsResponse(30, "New Window Item")); });
    expect(await screen.findByRole("img", { name: "New Window Item: Quantity 5" })).toBeInTheDocument();

    await act(async () => { sevenDay.resolve(analyticsResponse(7, "Stale Window Item")); });
    await waitFor(() => expect(screen.queryByText("Stale Window Item")).not.toBeInTheDocument());
    expect(screen.getByRole("img", { name: "New Window Item: Quantity 5" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "30 Days" })).toHaveAttribute("aria-pressed", "true");
  });
});
