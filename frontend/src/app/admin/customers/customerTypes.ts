export type CustomerAddress = {
  label?: string;
  address_text?: string;
  is_default?: boolean;
};

export type Customer = {
  customer_id: string;
  display_name?: string | null;
  phone_e164?: string | null;
  phone_verified?: boolean;
  addresses?: CustomerAddress[];
};

export type CustomerOrder = {
  order_id: string;
  status: string;
  total?: number;
  currency?: string;
};

export type CustomerProfile = {
  customer: Customer;
  orders: CustomerOrder[];
};

export type CustomerSearchState = "idle" | "searching" | "success" | "error";
export type CustomerProfileState = "idle" | "loading" | "success" | "error";
