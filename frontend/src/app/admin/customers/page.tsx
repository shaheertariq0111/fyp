"use client";

import Link from "next/link";
import { FormEvent, useRef, useState } from "react";
import { AdminShell, money } from "@/app/admin/AdminShell";
import { FieldValue } from "@/app/admin/orders/orderPresentation";
import { StatusBadge, shortOrderId } from "@/app/admin/orders/orderPresentation";
import { CustomerIcon, addressLabel, customerName, shortReference } from "@/app/admin/customers/customerPresentation";
import { adminGet } from "@/lib/adminApi";

type Customer = {
  customer_id: string;
  display_name?: string | null;
  phone_e164?: string | null;
  phone_verified?: boolean;
  addresses?: Array<{ label?: string; address_text?: string; is_default?: boolean }>;
};

type Order = {
  order_id: string;
  status: string;
  total?: number;
  currency?: string;
};

type Profile = { customer: Customer; orders: Order[] };
type SearchState = "idle" | "searching" | "success" | "error";
type ProfileState = "idle" | "loading" | "success" | "error";

export default function AdminCustomersPage() {
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [searchState, setSearchState] = useState<SearchState>("idle");
  const [inputError, setInputError] = useState("");
  const [searchError, setSearchError] = useState("");
  const [selectedCustomerId, setSelectedCustomerId] = useState<string | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [profileState, setProfileState] = useState<ProfileState>("idle");
  const [profileMessage, setProfileMessage] = useState("");
  const searchInFlight = useRef(false);
  const profileInFlight = useRef<string | null>(null);
  const searchWarningLogged = useRef(false);
  const profileWarningLogged = useRef(false);

  const selectedCustomer = profile?.customer;
  const addresses = selectedCustomer?.addresses ?? [];
  const orders = profile?.orders ?? [];

  const actions = selectedCustomerId ? (
    <div className="admin-dashboard-actions">
      <button className="secondary admin-inline-action" onClick={clearSelection} type="button">
        <CustomerIcon name="clear" />
        Clear selection
      </button>
      <button
        className="admin-refresh-button"
        disabled={profileState === "loading"}
        onClick={() => void openCustomer(selectedCustomerId)}
        type="button"
      >
        <CustomerIcon name="refresh" />
        {profileState === "loading" ? "Refreshing..." : "Refresh profile"}
      </button>
    </div>
  ) : undefined;

  async function search(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      setInputError("Enter a customer name, phone number, or address.");
      return;
    }
    if (searchInFlight.current) {
      return;
    }
    searchInFlight.current = true;
    setSearchState("searching");
    setInputError("");
    setSearchError("");
    try {
      const result = await adminGet<{ customers: Customer[] }>(`/api/admin/customers?query=${encodeURIComponent(trimmed)}`);
      setCustomers(result.customers);
      setSubmittedQuery(trimmed);
      setSearchState("success");
      setSearchError("");
      searchWarningLogged.current = false;
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      setSearchState("error");
      setSearchError("Customer search could not be completed. Retry when the service is reachable.");
      if (!searchWarningLogged.current) {
        console.warn("Admin customer search failed", exc);
        searchWarningLogged.current = true;
      }
    } finally {
      searchInFlight.current = false;
    }
  }

  async function openCustomer(customerId: string) {
    if (profileInFlight.current === customerId) {
      return;
    }
    profileInFlight.current = customerId;
    setSelectedCustomerId(customerId);
    setProfileState("loading");
    setProfileMessage("");
    try {
      const result = await adminGet<Profile>(`/api/admin/customers/${customerId}`);
      setProfile(result);
      setProfileState("success");
      profileWarningLogged.current = false;
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      setProfileState("error");
      setProfileMessage("Customer profile could not be loaded. Retry when the service is reachable.");
      if (!profileWarningLogged.current) {
        console.warn("Admin customer profile failed", exc);
        profileWarningLogged.current = true;
      }
    } finally {
      profileInFlight.current = null;
    }
  }

  function clearSearch() {
    setQuery("");
    setSubmittedQuery("");
    setCustomers([]);
    setSearchState("idle");
    setInputError("");
    setSearchError("");
  }

  function clearSelection() {
    setSelectedCustomerId(null);
    setProfile(null);
    setProfileState("idle");
    setProfileMessage("");
  }

  return (
    <AdminShell
      actions={actions}
      subtitle="Search customer profiles, addresses, and order history"
      title="Customers"
    >
      <div className="admin-customers-page">
        <section className="admin-panel admin-customer-search-panel" aria-labelledby="customer-search-heading">
          <div className="admin-section-heading">
            <div>
              <h2 id="customer-search-heading">Customer search</h2>
              <p>Search for a customer by name, phone number, or address.</p>
            </div>
          </div>
          <form className="admin-customer-search-form" onSubmit={search}>
            <label className="admin-search-control">
              <span>Customer query</span>
              <span>
                <CustomerIcon name="search" />
                <input
                  aria-describedby={inputError ? "customer-search-validation" : undefined}
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    if (inputError) {
                      setInputError("");
                    }
                  }}
                  placeholder="Name, phone number, or address"
                />
              </span>
            </label>
            <button className="primary" disabled={searchState === "searching"} type="submit">
              {searchState === "searching" ? "Searching..." : "Search"}
            </button>
            <button className="secondary" disabled={searchState === "searching" && !query} onClick={clearSearch} type="button">
              Clear
            </button>
          </form>
          {inputError && <p className="admin-form-error" id="customer-search-validation">{inputError}</p>}
        </section>

        <div className="admin-customer-workspace">
          <section className="admin-panel admin-customer-results-panel" aria-labelledby="customer-results-heading">
            <div className="admin-section-heading">
              <div>
                <h2 id="customer-results-heading">Results</h2>
                <p>{submittedQuery ? `Search response for "${submittedQuery}".` : "Run a search to populate customer results."}</p>
              </div>
            </div>
            {searchState === "idle" && (
              <div className="admin-empty-state">
                <strong>Search for a customer by name, phone number, or address.</strong>
                <p>Results will appear here after a search completes.</p>
              </div>
            )}
            {searchState === "searching" && (
              <div className="admin-customer-result-list" aria-label="Loading customer results">
                {[0, 1, 2].map((row) => (
                  <div className="admin-customer-result-card" key={row}>
                    <span className="admin-skeleton admin-skeleton-line" />
                    <span className="admin-skeleton admin-skeleton-line" />
                    <span className="admin-skeleton admin-skeleton-pill" />
                  </div>
                ))}
              </div>
            )}
            {searchState === "error" && (
              <div className="admin-error-panel" role="alert">
                <div>
                  <strong>Customer search unavailable</strong>
                  <p>{searchError}</p>
                </div>
                <button className="secondary" onClick={() => void search()} type="button">Retry search</button>
              </div>
            )}
            {searchState === "success" && customers.length === 0 && (
              <div className="admin-empty-state">
                <strong>No customers matched your search.</strong>
                <p>Check spelling, phone format, or try another address fragment.</p>
                <button className="secondary" onClick={clearSearch} type="button">Clear search</button>
              </div>
            )}
            {(searchState === "success" || searchState === "error") && customers.length > 0 && (
              <div className="admin-customer-result-list" aria-label="Customer search results" aria-live="polite">
                {customers.map((customer) => {
                  const isSelected = selectedCustomerId === customer.customer_id;
                  const isLoading = profileState === "loading" && profileInFlight.current === customer.customer_id;
                  const addressCount = customer.addresses?.length ?? 0;
                  const defaultAddress = customer.addresses?.find((address) => address.is_default) ?? customer.addresses?.[0];
                  return (
                    <button
                      aria-current={isSelected ? "true" : undefined}
                      className={`admin-customer-result-card${isSelected ? " is-selected" : ""}`}
                      disabled={isLoading}
                      key={customer.customer_id}
                      onClick={() => void openCustomer(customer.customer_id)}
                      type="button"
                    >
                      <span>
                        <strong>{customerName(customer.display_name)}</strong>
                        <small title={customer.customer_id}>Customer {shortReference(customer.customer_id)}</small>
                      </span>
                      <span>{customer.phone_e164 || "No phone number returned"}</span>
                      <span>{defaultAddress?.address_text || "No default address returned"}</span>
                      <small>{addressCount} saved address{addressCount === 1 ? "" : "es"}{isLoading ? " · Loading profile..." : ""}</small>
                    </button>
                  );
                })}
              </div>
            )}
          </section>

          <section className="admin-panel admin-customer-profile-panel" aria-labelledby="customer-profile-heading">
            <div className="admin-section-heading">
              <div>
                <h2 id="customer-profile-heading">Profile</h2>
                <p>Returned profile details and order records for the selected customer.</p>
              </div>
            </div>
            {!selectedCustomerId && (
              <div className="admin-empty-state">
                <strong>Select a customer to view their profile and order history.</strong>
                <p>The profile panel will stay ready while you search.</p>
              </div>
            )}
            {selectedCustomerId && profileState === "loading" && (
              <div className="admin-customer-profile-skeleton" aria-label="Loading customer profile">
                <span className="admin-skeleton admin-skeleton-value" />
                <span className="admin-skeleton admin-skeleton-line" />
                <span className="admin-skeleton admin-skeleton-line" />
                <span className="admin-skeleton admin-skeleton-pill" />
              </div>
            )}
            {selectedCustomerId && profileState === "error" && (
              <div className="admin-error-panel" role="alert">
                <div>
                  <strong>Profile unavailable</strong>
                  <p>{profileMessage}</p>
                </div>
                <button className="secondary" onClick={() => void openCustomer(selectedCustomerId)} type="button">Retry</button>
              </div>
            )}
            {selectedCustomer && profileState === "success" && (
              <div className="admin-customer-profile">
                <div className="admin-customer-profile-header">
                  <div>
                    <span>Selected customer</span>
                    <h3>{customerName(selectedCustomer.display_name)}</h3>
                    <p title={selectedCustomer.customer_id}>Customer {shortReference(selectedCustomer.customer_id)}</p>
                  </div>
                  {selectedCustomer.phone_e164 ? (
                    <a className="admin-phone-link" href={`tel:${selectedCustomer.phone_e164}`}>
                      <CustomerIcon name="phone" />
                      {selectedCustomer.phone_e164}
                    </a>
                  ) : (
                    <span className="admin-muted-text">No phone number returned</span>
                  )}
                </div>
                <div className="admin-detail-grid">
                  <FieldValue label="Saved addresses">{addresses.length}</FieldValue>
                  <FieldValue label="Returned orders">{orders.length}</FieldValue>
                  <FieldValue label="Phone verified">{selectedCustomer.phone_verified ? "Yes" : "No"}</FieldValue>
                </div>

                <section className="admin-customer-subsection" aria-labelledby="customer-addresses-heading">
                  <h3 id="customer-addresses-heading">Addresses</h3>
                  {addresses.length === 0 ? (
                    <div className="admin-empty-state">
                      <strong>No saved addresses are available.</strong>
                    </div>
                  ) : (
                    <div className="admin-address-list">
                      {addresses.map((address) => (
                        <article className="admin-address-card" key={`${address.label ?? "address"}-${address.address_text ?? "blank"}`}>
                          <div>
                            <CustomerIcon name="location" />
                            <strong>{addressLabel(address.label)}</strong>
                            {address.is_default && <span className="admin-menu-badge is-active">Default</span>}
                          </div>
                          <p>{address.address_text || "Address text was not returned."}</p>
                        </article>
                      ))}
                    </div>
                  )}
                </section>

                <section className="admin-customer-subsection" aria-labelledby="customer-orders-heading">
                  <h3 id="customer-orders-heading">Returned orders</h3>
                  {orders.length === 0 ? (
                    <div className="admin-empty-state">
                      <strong>No orders were returned for this customer.</strong>
                    </div>
                  ) : (
                    <div className="admin-customer-order-list">
                      {orders.map((order) => (
                        <article className="admin-customer-order-card" key={order.order_id}>
                          <div>
                            <Link className="admin-order-id-link" href={`/admin/orders/${order.order_id}`} title={order.order_id}>
                              {shortOrderId(order.order_id)}
                            </Link>
                            <StatusBadge status={order.status} />
                          </div>
                          <strong>{money(order.total, order.currency)}</strong>
                          <Link className="secondary" href={`/admin/orders/${order.order_id}`}>View order</Link>
                        </article>
                      ))}
                    </div>
                  )}
                </section>
              </div>
            )}
          </section>
        </div>
      </div>
    </AdminShell>
  );
}
