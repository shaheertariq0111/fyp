"use client";

import Link from "next/link";
import { money } from "@/app/admin/AdminShell";
import { CustomerIcon, addressLabel, customerName, shortReference } from "@/app/admin/customers/customerPresentation";
import type {
  Customer,
  CustomerProfile,
  CustomerProfileState,
  CustomerSearchState,
} from "@/app/admin/customers/customerTypes";
import { StatusBadge, shortOrderId } from "@/app/admin/orders/orderPresentation";

function initials(name?: string | null) {
  const parts = customerName(name).split(/\s+/).filter(Boolean);
  return parts.slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function ResultsEmptyState({ searched }: { searched: boolean }) {
  return (
    <div className="customer-results-empty">
      <span className="customer-results-empty-icon"><CustomerIcon name="search" /></span>
      <strong>{searched ? "No customers found" : "Search for a customer"}</strong>
      <p>{searched ? "Try another name, phone number, or address." : "Enter a name, phone number, or address to view matching customers."}</p>
    </div>
  );
}

export function CustomerResultsPanel({
  customers,
  onClear,
  onRetry,
  onSelect,
  profileState,
  searchError,
  searchState,
  selectedCustomerId,
}: {
  customers: Customer[];
  onClear: () => void;
  onRetry: () => void;
  onSelect: (customerId: string) => void;
  profileState: CustomerProfileState;
  searchError: string;
  searchState: CustomerSearchState;
  selectedCustomerId: string | null;
}) {
  return (
    <section className="admin-panel admin-customer-results-panel" aria-labelledby="customer-results-heading">
      <div className="customer-panel-heading"><h2 id="customer-results-heading">Results</h2></div>
      <div className="customer-results-scroll">
        {searchState === "idle" && <ResultsEmptyState searched={false} />}
        {searchState === "searching" && (
          <div className="customer-result-list" aria-label="Loading customer results">
            {[0, 1, 2].map((row) => <div className="customer-result-card customer-result-skeleton" key={row}><span className="admin-skeleton admin-skeleton-line" /><span className="admin-skeleton admin-skeleton-line" /><span className="admin-skeleton admin-skeleton-pill" /></div>)}
          </div>
        )}
        {searchState === "error" && (
          <div className="admin-error-panel" role="alert">
            <div><strong>Customer search unavailable</strong><p>{searchError}</p></div>
            <button className="secondary" onClick={onRetry} type="button">Retry search</button>
          </div>
        )}
        {searchState === "success" && customers.length === 0 && (
          <><ResultsEmptyState searched /><button className="secondary customer-empty-clear" onClick={onClear} type="button">Clear search</button></>
        )}
        {(searchState === "success" || searchState === "error") && customers.length > 0 && (
          <div className="customer-result-list" aria-label="Customer search results" aria-live="polite">
            {customers.map((customer) => {
              const selected = customer.customer_id === selectedCustomerId;
              const defaultAddress = customer.addresses?.find((address) => address.is_default) ?? customer.addresses?.[0];
              return (
                <button
                  aria-current={selected ? "true" : undefined}
                  className={`customer-result-card${selected ? " is-selected" : ""}`}
                  disabled={selected && profileState === "loading"}
                  key={customer.customer_id}
                  onClick={() => onSelect(customer.customer_id)}
                  type="button"
                >
                  <span className="customer-result-avatar">{initials(customer.display_name)}</span>
                  <span className="customer-result-content">
                    <strong>{customerName(customer.display_name)}</strong>
                    <span>{customer.phone_e164 || "Phone not provided"}</span>
                    <small title={customer.customer_id}>ID: {shortReference(customer.customer_id)}</small>
                    {defaultAddress?.address_text && <small className="customer-result-address">{defaultAddress.address_text}</small>}
                  </span>
                  <span className="customer-result-chevron" aria-hidden="true">›</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
      {customers.length > 0 && <p className="customer-results-footer">Showing returned search results</p>}
    </section>
  );
}

function CustomerIdentity({ customer }: { customer: Customer }) {
  return (
    <section className="customer-profile-identity" aria-label="Customer identity">
      <span className="customer-profile-avatar">{initials(customer.display_name)}</span>
      <div>
        <div className="customer-profile-name-row">
          <h2>{customerName(customer.display_name)}</h2>
          {customer.phone_verified && <span className="customer-verified-badge">Phone verified</span>}
        </div>
        <p><span>Customer ID:</span> <strong>{customer.customer_id}</strong></p>
        {customer.phone_e164 ? <a href={`tel:${customer.phone_e164}`}><CustomerIcon name="phone" />{customer.phone_e164}</a> : <p>Phone not provided</p>}
      </div>
    </section>
  );
}

function CustomerAddresses({ customer }: { customer: Customer }) {
  const addresses = customer.addresses ?? [];
  return (
    <section className="customer-profile-section" aria-labelledby="customer-addresses-heading">
      <h3 id="customer-addresses-heading"><CustomerIcon name="location" />Addresses</h3>
      {addresses.length === 0 ? <p className="customer-profile-empty">No saved addresses are available.</p> : (
        <div className="customer-address-grid">
          {addresses.map((address, index) => (
            <article className="customer-address-card" key={`${address.label ?? "address"}-${address.address_text ?? index}`}>
              <div><strong>{addressLabel(address.label)}</strong>{address.is_default && <span>Default</span>}</div>
              <p>{address.address_text || "Address text was not returned."}</p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function CustomerOrderSummary({ profile }: { profile: CustomerProfile }) {
  const orders = profile.orders;
  const completed = orders.filter((order) => ["completed", "delivered"].includes(order.status.toLowerCase())).length;
  const currencies = new Set(orders.map((order) => order.currency).filter(Boolean));
  const canSumSpend = orders.length > 0 && currencies.size === 1 && orders.every((order) => typeof order.total === "number");
  const currency = currencies.values().next().value as string | undefined;
  const spend = canSumSpend ? orders.reduce((sum, order) => sum + (order.total ?? 0), 0) : null;
  return (
    <section className="customer-order-summary" aria-labelledby="customer-order-summary-heading">
      <h3 id="customer-order-summary-heading">Returned Order Summary</h3>
      <div>
        <article><span><CustomerIcon name="orders" /></span><strong>{orders.length}</strong><small>Returned Orders</small></article>
        <article><span><CustomerIcon name="completed" /></span><strong>{completed}</strong><small>Completed / Delivered</small></article>
        {spend !== null && <article><span><CustomerIcon name="spend" /></span><strong>{money(spend, currency)}</strong><small>Spend in Returned Orders</small></article>}
      </div>
    </section>
  );
}

function CustomerOrderHistory({ profile }: { profile: CustomerProfile }) {
  return (
    <section className="customer-order-history" aria-labelledby="customer-order-history-heading">
      <h3 id="customer-order-history-heading">Returned Order History</h3>
      {profile.orders.length === 0 ? <p className="customer-profile-empty">No orders were returned for this customer.</p> : (
        <div className="customer-order-table-wrap">
          <table className="customer-order-table">
            <thead><tr><th>Order ID</th><th>Total</th><th>Status</th><th>Action</th></tr></thead>
            <tbody>
              {profile.orders.map((order) => (
                <tr key={order.order_id}>
                  <td><Link href={`/admin/orders/${encodeURIComponent(order.order_id)}`} title={order.order_id}>{shortOrderId(order.order_id)}</Link></td>
                  <td>{money(order.total, order.currency)}</td>
                  <td><StatusBadge status={order.status} /></td>
                  <td><Link className="customer-order-view" href={`/admin/orders/${encodeURIComponent(order.order_id)}`}>View</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {profile.orders.length > 0 && <p className="customer-order-scope">Showing returned order history</p>}
    </section>
  );
}

export function CustomerProfileWorkspace({
  profile,
  profileMessage,
  profileState,
  selectedCustomerId,
  onRetry,
}: {
  profile: CustomerProfile | null;
  profileMessage: string;
  profileState: CustomerProfileState;
  selectedCustomerId: string | null;
  onRetry: () => void;
}) {
  return (
    <section className="admin-panel admin-customer-profile-panel" aria-labelledby="customer-profile-heading">
      <div className="customer-panel-heading"><h2 id="customer-profile-heading">Customer Profile</h2></div>
      <div className="customer-profile-scroll">
        {!selectedCustomerId && <div className="customer-profile-placeholder"><span><CustomerIcon name="customer" /></span><strong>Select a customer</strong><p>Choose a customer from the results to view profile and order history.</p></div>}
        {selectedCustomerId && profileState === "loading" && <div className="admin-customer-profile-skeleton" aria-label="Loading customer profile"><span className="admin-skeleton admin-skeleton-value" /><span className="admin-skeleton admin-skeleton-line" /><span className="admin-skeleton admin-skeleton-line" /><span className="admin-skeleton admin-skeleton-pill" /></div>}
        {selectedCustomerId && profileState === "error" && <div className="admin-error-panel" role="alert"><div><strong>Profile unavailable</strong><p>{profileMessage}</p></div><button className="secondary" onClick={onRetry} type="button">Retry</button></div>}
        {profile?.customer && profileState === "success" && (
          <div className="customer-profile-workspace">
            <CustomerIdentity customer={profile.customer} />
            <CustomerAddresses customer={profile.customer} />
            <CustomerOrderSummary profile={profile} />
            <CustomerOrderHistory profile={profile} />
          </div>
        )}
      </div>
    </section>
  );
}
