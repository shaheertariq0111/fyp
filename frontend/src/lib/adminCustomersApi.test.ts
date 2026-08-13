import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  CustomerListResponse,
  CustomerProfile,
} from "@/app/admin/customers/customerTypes";
import { adminGet } from "@/lib/adminApi";
import {
  getAdminCustomer,
  listAdminCustomers,
} from "@/lib/adminCustomersApi";

vi.mock("@/lib/adminApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/adminApi")>();
  return {
    ...actual,
    adminGet: vi.fn(),
  };
});

const mockedAdminGet = vi.mocked(adminGet);

const listResponse: CustomerListResponse = {
  customers: [],
  next_cursor: null,
};

const profileResponse: CustomerProfile = {
  customer: { customer_id: "cust/one" },
  orders: [],
};

beforeEach(() => {
  mockedAdminGet.mockReset();
});

describe("admin Customers API", () => {
  it("uses adminGet for bounded list and search request construction", async () => {
    mockedAdminGet.mockResolvedValue(listResponse);
    const controller = new AbortController();

    await listAdminCustomers({
      query: "Ava Khan",
      limit: 25,
      cursor: "opaque.cursor",
    }, controller.signal);

    expect(mockedAdminGet).toHaveBeenCalledWith(
      "/api/admin/customers?query=Ava+Khan&limit=25&cursor=opaque.cursor",
      { signal: controller.signal },
    );
  });

  it("uses adminGet and encodes customer IDs for profile requests", async () => {
    mockedAdminGet.mockResolvedValue(profileResponse);

    await getAdminCustomer("cust/one");

    expect(mockedAdminGet).toHaveBeenCalledWith(
      "/api/admin/customers/cust%2Fone",
      {},
    );
  });
});
