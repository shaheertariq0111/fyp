import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  Customer,
  CustomerListResponse,
  CustomerProfile,
} from "@/app/admin/customers/customerTypes";
import AdminCustomersPage from "@/app/admin/customers/page";
import {
  getAdminCustomer,
  listAdminCustomers,
} from "@/lib/adminCustomersApi";

vi.mock("@/lib/adminCustomersApi", () => ({
  getAdminCustomer: vi.fn(),
  listAdminCustomers: vi.fn(),
}));

vi.mock("@/app/admin/AdminShell", () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  money: (value?: number, currency = "PKR") => `${currency} ${value ?? 0}`,
}));

const mockedListAdminCustomers = vi.mocked(listAdminCustomers);
const mockedGetAdminCustomer = vi.mocked(getAdminCustomer);

const ava: Customer = {
  customer_id: "cust-ava",
  display_name: "Ava Khan",
  phone_e164: "+923001234567",
  phone_verified: true,
  addresses: [{
    label: "Home",
    address_text: "42 Garden Avenue",
    is_default: true,
  }],
};

const john: Customer = {
  customer_id: "cust-john",
  display_name: "John Malik",
  phone_e164: "+923009876543",
  addresses: [],
};

function response(
  customers: Customer[] = [ava],
  nextCursor: string | null = null,
): CustomerListResponse {
  return { customers, next_cursor: nextCursor };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  mockedListAdminCustomers.mockReset();
  mockedGetAdminCustomer.mockReset();
  mockedListAdminCustomers.mockResolvedValue(response());
  mockedGetAdminCustomer.mockResolvedValue({ customer: ava, orders: [] });
});

describe("Admin Customers page", () => {
  it("loads and renders the first real customer page on mount", async () => {
    render(<AdminCustomersPage />);

    expect(await screen.findByText("Ava Khan")).toBeInTheDocument();
    expect(mockedListAdminCustomers).toHaveBeenCalledWith(
      { limit: 25 },
      expect.any(AbortSignal),
    );
    expect(screen.queryByText("Search for a customer")).not.toBeInTheDocument();
  });

  it("renders a clean empty state when no persisted customers exist", async () => {
    mockedListAdminCustomers.mockResolvedValue(response([]));

    render(<AdminCustomersPage />);

    expect(await screen.findByText("No customers found")).toBeInTheDocument();
    expect(screen.getByText(/Persisted customers will appear here/)).toBeInTheDocument();
  });

  it("searches explicitly, resets pagination, and Clear reloads the default list", async () => {
    const user = userEvent.setup();
    mockedListAdminCustomers
      .mockResolvedValueOnce(response([ava], "initial.cursor"))
      .mockResolvedValueOnce(response([john], "search.cursor"))
      .mockResolvedValueOnce(response([ava]));
    render(<AdminCustomersPage />);
    await screen.findByText("Ava Khan");

    await user.type(screen.getByPlaceholderText("Name, phone number, or address"), "  John  ");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("John Malik")).toBeInTheDocument();
    expect(screen.queryByText("Ava Khan")).not.toBeInTheDocument();
    expect(mockedListAdminCustomers).toHaveBeenNthCalledWith(
      2,
      { query: "John", limit: 25 },
      expect.any(AbortSignal),
    );

    await user.click(screen.getByRole("button", { name: "Clear" }));

    expect(await screen.findByText("Ava Khan")).toBeInTheDocument();
    expect(mockedListAdminCustomers).toHaveBeenNthCalledWith(
      3,
      { limit: 25 },
      expect.any(AbortSignal),
    );
  });

  it("loads more with the opaque cursor, appends uniquely, and hides the control at the end", async () => {
    const user = userEvent.setup();
    mockedListAdminCustomers
      .mockResolvedValueOnce(response([ava], "opaque.cursor"))
      .mockResolvedValueOnce(response([ava, john]));
    render(<AdminCustomersPage />);
    await screen.findByText("Ava Khan");

    await user.click(screen.getByRole("button", { name: "Load More" }));

    expect(await screen.findByText("John Malik")).toBeInTheDocument();
    expect(screen.getAllByText("Ava Khan")).toHaveLength(1);
    expect(mockedListAdminCustomers).toHaveBeenNthCalledWith(
      2,
      { limit: 25, cursor: "opaque.cursor" },
      expect.any(AbortSignal),
    );
    expect(screen.queryByRole("button", { name: "Load More" })).not.toBeInTheDocument();
  });

  it("can continue a bounded search page that has no matches yet", async () => {
    const user = userEvent.setup();
    mockedListAdminCustomers
      .mockResolvedValueOnce(response([ava]))
      .mockResolvedValueOnce(response([], "search.cursor"))
      .mockResolvedValueOnce(response([john]));
    render(<AdminCustomersPage />);
    await screen.findByText("Ava Khan");
    const input = screen.getByPlaceholderText("Name, phone number, or address");
    await user.type(input, "John");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("No matches in this page")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load More" }));

    expect(await screen.findByText("John Malik")).toBeInTheDocument();
    expect(mockedListAdminCustomers).toHaveBeenNthCalledWith(
      3,
      { query: "John", limit: 25, cursor: "search.cursor" },
      expect.any(AbortSignal),
    );
  });

  it("keeps loaded customers when pagination fails and supports retry", async () => {
    const user = userEvent.setup();
    mockedListAdminCustomers
      .mockResolvedValueOnce(response([ava], "opaque.cursor"))
      .mockRejectedValueOnce(new Error("private failure"))
      .mockResolvedValueOnce(response([john]));
    render(<AdminCustomersPage />);
    await screen.findByText("Ava Khan");

    await user.click(screen.getByRole("button", { name: "Load More" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("More customers could not be loaded");
    expect(screen.getByText("Ava Khan")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry Load More" }));
    expect(await screen.findByText("John Malik")).toBeInTheDocument();
    expect(screen.getByText("Ava Khan")).toBeInTheDocument();
  });

  it("does not let an older search response overwrite a newer search", async () => {
    const user = userEvent.setup();
    const firstSearch = deferred<CustomerListResponse>();
    const secondSearch = deferred<CustomerListResponse>();
    mockedListAdminCustomers
      .mockResolvedValueOnce(response([ava]))
      .mockReturnValueOnce(firstSearch.promise)
      .mockReturnValueOnce(secondSearch.promise);
    render(<AdminCustomersPage />);
    await screen.findByText("Ava Khan");
    const input = screen.getByPlaceholderText("Name, phone number, or address");

    await user.clear(input);
    await user.type(input, "Alpha");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.clear(input);
    await user.type(input, "Beta");
    await user.click(screen.getByRole("button", { name: "Searching..." }));
    secondSearch.resolve(response([john]));
    expect(await screen.findByText("John Malik")).toBeInTheDocument();
    firstSearch.resolve(response([ava]));

    await waitFor(() => expect(screen.queryByText("Ava Khan")).not.toBeInTheDocument());
    expect(screen.getByText("John Malik")).toBeInTheDocument();
  });

  it("continues loading the selected customer profile and returned orders", async () => {
    const user = userEvent.setup();
    const profile: CustomerProfile = {
      customer: ava,
      orders: [{
        order_id: "ORD-1",
        status: "completed",
        total: 2500,
        currency: "PKR",
      }],
    };
    mockedGetAdminCustomer.mockResolvedValue(profile);
    render(<AdminCustomersPage />);

    await user.click(await screen.findByRole("button", { name: /Ava Khan/ }));

    expect(mockedGetAdminCustomer).toHaveBeenCalledWith("cust-ava");
    expect(await screen.findByText("ORD-1")).toBeInTheDocument();
    expect(screen.getByText("Phone verified")).toBeInTheDocument();
    expect(screen.getAllByText("42 Garden Avenue")).toHaveLength(2);
  });

  it("renders duplicate address content with distinct address IDs without a React key warning", async () => {
    const user = userEvent.setup();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    mockedGetAdminCustomer.mockResolvedValue({
      customer: {
        ...ava,
        addresses: [
          {
            address_id: "address-1",
            label: "Home",
            address_text: "42 Garden Avenue",
            is_default: true,
          },
          {
            address_id: "address-2",
            label: "Home",
            address_text: "42 Garden Avenue",
            is_default: false,
          },
        ],
      },
      orders: [],
    });

    try {
      render(<AdminCustomersPage />);
      await user.click(await screen.findByRole("button", { name: /Ava Khan/ }));

      expect(await screen.findAllByText("42 Garden Avenue")).toHaveLength(3);
      expect(screen.getByText("Default")).toBeInTheDocument();
      expect(consoleError.mock.calls.some((call) => (
        call.map(String).join(" ").includes("Encountered two children with the same key")
      ))).toBe(false);
    } finally {
      consoleError.mockRestore();
    }
  });
});
