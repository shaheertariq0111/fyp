"use client";

import { FormEvent, useRef, useState } from "react";
import { AdminShell } from "@/app/admin/AdminShell";
import { CustomerIcon } from "@/app/admin/customers/customerPresentation";
import { CustomerProfileWorkspace, CustomerResultsPanel } from "@/app/admin/customers/CustomerWorkspace";
import type {
  Customer,
  CustomerProfile,
  CustomerProfileState,
  CustomerSearchState,
} from "@/app/admin/customers/customerTypes";
import { adminGet } from "@/lib/adminApi";

export default function AdminCustomersPage() {
  const [query, setQuery] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [searchState, setSearchState] = useState<CustomerSearchState>("idle");
  const [inputError, setInputError] = useState("");
  const [searchError, setSearchError] = useState("");
  const [selectedCustomerId, setSelectedCustomerId] = useState<string | null>(null);
  const [profile, setProfile] = useState<CustomerProfile | null>(null);
  const [profileState, setProfileState] = useState<CustomerProfileState>("idle");
  const [profileMessage, setProfileMessage] = useState("");
  const searchInFlight = useRef(false);
  const profileInFlight = useRef<string | null>(null);
  const searchWarningLogged = useRef(false);
  const profileWarningLogged = useRef(false);

  async function search(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      setInputError("Enter a customer name, phone number, or address.");
      return;
    }
    if (searchInFlight.current) return;
    searchInFlight.current = true;
    setSearchState("searching");
    setInputError("");
    setSearchError("");
    try {
      const result = await adminGet<{ customers: Customer[] }>(`/api/admin/customers?query=${encodeURIComponent(trimmed)}`);
      setCustomers(result.customers);
      setSearchState("success");
      setSearchError("");
      searchWarningLogged.current = false;
    } catch (exc) {
      if (!(exc instanceof Error)) throw exc;
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
    if (profileInFlight.current === customerId) return;
    profileInFlight.current = customerId;
    setSelectedCustomerId(customerId);
    setProfileState("loading");
    setProfileMessage("");
    try {
      const result = await adminGet<CustomerProfile>(`/api/admin/customers/${customerId}`);
      setProfile(result);
      setProfileState("success");
      profileWarningLogged.current = false;
    } catch (exc) {
      if (!(exc instanceof Error)) throw exc;
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

  return (
    <AdminShell actions={actions} subtitle="Search customer profiles, addresses, and order history" title="Customers">
      <div className="admin-customers-page">
        <section className="admin-panel admin-customer-search-panel" aria-labelledby="customer-search-heading">
          <h2 id="customer-search-heading">Customer Query</h2>
          <form className="admin-customer-search-form" onSubmit={search}>
            <label className="admin-search-control">
              <span className="admin-visually-hidden">Customer query</span>
              <span>
                <CustomerIcon name="search" />
                <input
                  aria-describedby={inputError ? "customer-search-validation" : undefined}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    if (inputError) setInputError("");
                  }}
                  placeholder="Name, phone number, or address"
                  value={query}
                />
              </span>
            </label>
            <button className="primary customer-search-submit" disabled={searchState === "searching"} type="submit">
              <CustomerIcon name="search" />
              {searchState === "searching" ? "Searching..." : "Search"}
            </button>
            <button className="secondary customer-search-clear" disabled={searchState === "searching" && !query} onClick={clearSearch} type="button">
              <CustomerIcon name="clear" />
              Clear
            </button>
          </form>
          {inputError && <p className="admin-form-error" id="customer-search-validation">{inputError}</p>}
        </section>

        <div className="admin-customer-workspace">
          <CustomerResultsPanel
            customers={customers}
            onClear={clearSearch}
            onRetry={() => void search()}
            onSelect={(customerId) => void openCustomer(customerId)}
            profileState={profileState}
            searchError={searchError}
            searchState={searchState}
            selectedCustomerId={selectedCustomerId}
          />
          <CustomerProfileWorkspace
            onRetry={() => selectedCustomerId && void openCustomer(selectedCustomerId)}
            profile={profile}
            profileMessage={profileMessage}
            profileState={profileState}
            selectedCustomerId={selectedCustomerId}
          />
        </div>
      </div>
    </AdminShell>
  );
}
