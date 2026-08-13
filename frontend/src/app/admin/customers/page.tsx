"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { AdminShell } from "@/app/admin/AdminShell";
import { CustomerIcon } from "@/app/admin/customers/customerPresentation";
import { CustomerProfileWorkspace, CustomerResultsPanel } from "@/app/admin/customers/CustomerWorkspace";
import type {
  Customer,
  CustomerProfile,
  CustomerProfileState,
  CustomerPaginationState,
  CustomerSearchState,
} from "@/app/admin/customers/customerTypes";
import { getAdminCustomer, listAdminCustomers } from "@/lib/adminCustomersApi";

const CUSTOMER_PAGE_SIZE = 25;

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

function appendUniqueCustomers(current: Customer[], additional: Customer[]) {
  const seen = new Set(current.map((customer) => customer.customer_id));
  return [
    ...current,
    ...additional.filter((customer) => {
      if (seen.has(customer.customer_id)) return false;
      seen.add(customer.customer_id);
      return true;
    }),
  ];
}

export default function AdminCustomersPage() {
  const [query, setQuery] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [searchState, setSearchState] = useState<CustomerSearchState>("searching");
  const [inputError, setInputError] = useState("");
  const [searchError, setSearchError] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [paginationState, setPaginationState] = useState<CustomerPaginationState>("idle");
  const [paginationError, setPaginationError] = useState("");
  const [selectedCustomerId, setSelectedCustomerId] = useState<string | null>(null);
  const [profile, setProfile] = useState<CustomerProfile | null>(null);
  const [profileState, setProfileState] = useState<CustomerProfileState>("idle");
  const [profileMessage, setProfileMessage] = useState("");
  const listRequestController = useRef<AbortController | null>(null);
  const listRequestSequence = useRef(0);
  const activeQuery = useRef("");
  const profileInFlight = useRef<string | null>(null);
  const searchWarningLogged = useRef(false);
  const profileWarningLogged = useRef(false);

  const loadCustomers = useCallback(async (
    requestedQuery: string,
    cursor: string | null,
    append: boolean,
  ) => {
    const sequence = ++listRequestSequence.current;
    listRequestController.current?.abort();
    const controller = new AbortController();
    listRequestController.current = controller;
    if (append) {
      setPaginationState("loading");
      setPaginationError("");
    } else {
      activeQuery.current = requestedQuery;
      setCustomers([]);
      setNextCursor(null);
      setSearchState("searching");
      setSearchError("");
      setPaginationState("idle");
      setPaginationError("");
    }
    try {
      const result = await listAdminCustomers({
        ...(requestedQuery ? { query: requestedQuery } : {}),
        limit: CUSTOMER_PAGE_SIZE,
        ...(cursor ? { cursor } : {}),
      }, controller.signal);
      if (sequence !== listRequestSequence.current) return;
      setCustomers((current) => (
        append ? appendUniqueCustomers(current, result.customers) : result.customers
      ));
      setNextCursor(result.next_cursor);
      setSearchState("success");
      setSearchError("");
      setPaginationState("idle");
      setPaginationError("");
      searchWarningLogged.current = false;
    } catch (exc) {
      if (sequence !== listRequestSequence.current || isAbortError(exc)) return;
      if (!(exc instanceof Error)) throw exc;
      if (append) {
        setPaginationState("error");
        setPaginationError("More customers could not be loaded. Please retry.");
      } else {
        setSearchState("error");
        setSearchError("Customer search could not be completed. Retry when the service is reachable.");
      }
      if (!searchWarningLogged.current) {
        console.warn("Admin customer list request failed", exc);
        searchWarningLogged.current = true;
      }
    } finally {
      if (sequence === listRequestSequence.current) {
        listRequestController.current = null;
      }
    }
  }, []);

  useEffect(() => {
    void loadCustomers("", null, false);
    return () => {
      listRequestSequence.current += 1;
      listRequestController.current?.abort();
      listRequestController.current = null;
    };
  }, [loadCustomers]);

  function search(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      setInputError("Enter a customer name, phone number, or address.");
      return;
    }
    setInputError("");
    void loadCustomers(trimmed, null, false);
  }

  async function openCustomer(customerId: string) {
    if (profileInFlight.current === customerId) return;
    profileInFlight.current = customerId;
    setSelectedCustomerId(customerId);
    setProfileState("loading");
    setProfileMessage("");
    try {
      const result = await getAdminCustomer(customerId);
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
    setInputError("");
    void loadCustomers("", null, false);
  }

  function loadMore() {
    if (!nextCursor || paginationState === "loading") return;
    void loadCustomers(activeQuery.current, nextCursor, true);
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
            <button className="primary customer-search-submit" type="submit">
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
            hasActiveQuery={Boolean(activeQuery.current)}
            nextCursor={nextCursor}
            onClear={clearSearch}
            onLoadMore={loadMore}
            onRetry={() => void loadCustomers(activeQuery.current, null, false)}
            onRetryPagination={loadMore}
            onSelect={(customerId) => void openCustomer(customerId)}
            paginationError={paginationError}
            paginationState={paginationState}
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
