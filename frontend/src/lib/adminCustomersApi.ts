import type {
  CustomerListResponse,
  CustomerProfile,
} from "@/app/admin/customers/customerTypes";
import { adminGet } from "@/lib/adminApi";

export type AdminCustomerListParams = {
  query?: string;
  limit?: number;
  cursor?: string;
};

function requestOptions(signal?: AbortSignal): RequestInit {
  return signal ? { signal } : {};
}

export function listAdminCustomers(
  params: AdminCustomerListParams = {},
  signal?: AbortSignal,
): Promise<CustomerListResponse> {
  const search = new URLSearchParams();
  if (params.query) {
    search.set("query", params.query);
  }
  if (params.limit !== undefined) {
    search.set("limit", String(params.limit));
  }
  if (params.cursor) {
    search.set("cursor", params.cursor);
  }
  const suffix = search.toString();
  return adminGet<CustomerListResponse>(
    `/api/admin/customers${suffix ? `?${suffix}` : ""}`,
    requestOptions(signal),
  );
}

export function getAdminCustomer(
  customerId: string,
  signal?: AbortSignal,
): Promise<CustomerProfile> {
  return adminGet<CustomerProfile>(
    `/api/admin/customers/${encodeURIComponent(customerId)}`,
    requestOptions(signal),
  );
}
