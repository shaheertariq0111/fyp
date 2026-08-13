"use client";

import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AdminShell } from "@/app/admin/AdminShell";
import { MiniIcon } from "@/app/admin/orders/orderPresentation";
import {
  TicketListSkeleton,
  TicketListTable,
  SupportTicketKpiStrip,
} from "@/app/admin/tickets/TicketListTable";
import { AdminApiError } from "@/lib/adminApi";
import { listAdminTickets } from "@/lib/adminTicketsApi";
import type {
  AdminTicketListItem,
  TicketPriority,
  TicketStatus,
  TicketType,
} from "@/lib/adminTicketTypes";

type TicketFilters = {
  status?: TicketStatus;
  ticket_type?: TicketType;
  priority?: TicketPriority;
};

type LoadKind = "initial" | "filter" | "pagination" | "refresh";
const TICKET_PAGE_SIZE = 25;

type RetryRequest = {
  filters: TicketFilters;
  cursor: string | null;
  pageIndex: number;
  kind: LoadKind;
};

type PageError = {
  error: AdminApiError;
  request: RetryRequest;
};

const statuses: TicketStatus[] = [
  "open",
  "in_review",
  "waiting_for_customer",
  "resolved",
  "closed",
];
const ticketTypes: TicketType[] = ["human_assistance", "order_complaint"];
const priorities: TicketPriority[] = ["normal", "high", "urgent"];

function validValue<T extends string>(value: string | null, values: readonly T[]): T | undefined {
  return value && values.includes(value as T) ? value as T : undefined;
}

function readFilters(searchParams: URLSearchParams): TicketFilters {
  return {
    status: validValue(searchParams.get("status"), statuses),
    ticket_type: validValue(searchParams.get("ticket_type"), ticketTypes),
    priority: validValue(searchParams.get("priority"), priorities),
  };
}

function filterQuery(filters: TicketFilters) {
  const params = new URLSearchParams();
  if (filters.status) {
    params.set("status", filters.status);
  }
  if (filters.ticket_type) {
    params.set("ticket_type", filters.ticket_type);
  }
  if (filters.priority) {
    params.set("priority", filters.priority);
  }
  return params.toString();
}

function pageUrl(filters: TicketFilters) {
  const query = filterQuery(filters);
  return query ? `/admin/tickets?${query}` : "/admin/tickets";
}

function normalizeError(error: unknown) {
  if (error instanceof AdminApiError) {
    return error;
  }
  return new AdminApiError(
    0,
    "ADMIN_NETWORK_ERROR",
    "The administrator service could not be reached. Please try again.",
  );
}

function isAbortError(error: unknown) {
  return typeof error === "object"
    && error !== null
    && "name" in error
    && error.name === "AbortError";
}

function TicketErrorPanel({
  pageError,
  onRetry,
  onReturnToFirst,
  disabled,
}: {
  pageError: PageError;
  onRetry: () => void;
  onReturnToFirst: () => void;
  disabled: boolean;
}) {
  const { error } = pageError;
  let title = "Support tickets unavailable";
  let message = "Support tickets could not be loaded. Please try again.";
  let retry = true;
  let returnToFirst = false;

  switch (error.errorCode) {
    case "INVALID_CURSOR":
      title = "Ticket page expired";
      message = "This ticket page has expired or is no longer valid.";
      retry = false;
      returnToFirst = true;
      break;
    case "TICKET_PAGINATION_STALLED":
      title = "Ticket pagination paused";
      message = error.userMessage;
      returnToFirst = true;
      break;
    case "TICKET_DATA_INVALID":
      title = "Ticket data unavailable";
      message = "The ticket list cannot be safely displayed.";
      retry = false;
      break;
    case "TICKET_BACKEND_UNAVAILABLE":
      message = "Support tickets are temporarily unavailable.";
      break;
    case "TICKET_INTERNAL_ERROR":
      message = "Support tickets could not be loaded safely.";
      break;
    case "ADMIN_NETWORK_ERROR":
      message = "The administrator service could not be reached.";
      break;
  }

  return (
    <section className="admin-error-panel admin-ticket-error" role="alert">
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
      <div className="admin-ticket-error-actions">
        {retry && (
          <button className="secondary" disabled={disabled} onClick={onRetry} type="button">
            {error.errorCode === "TICKET_PAGINATION_STALLED" ? "Retry current page" : "Retry"}
          </button>
        )}
        {returnToFirst && (
          <button className="secondary" disabled={disabled} onClick={onReturnToFirst} type="button">
            Return to first page
          </button>
        )}
      </div>
    </section>
  );
}

export function TicketsPageClient() {
  const searchParams = useSearchParams();
  const { replace } = useRouter();
  const initialSearch = useRef(searchParams.toString());
  const [filters, setFilters] = useState<TicketFilters>(() => readFilters(searchParams));
  const initialFilters = useRef(filters);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [tickets, setTickets] = useState<AdminTicketListItem[]>([]);
  const [search, setSearch] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasData, setHasData] = useState(false);
  const [loadingKind, setLoadingKind] = useState<LoadKind | null>("initial");
  const [pageError, setPageError] = useState<PageError | null>(null);
  const requestController = useRef<AbortController | null>(null);
  const requestSequence = useRef(0);
  const paginationLocked = useRef(false);

  const loadTickets = useCallback(async (
    kind: LoadKind,
    requestedFilters: TicketFilters,
    cursor: string | null,
    requestedPageIndex: number,
  ) => {
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    const sequence = ++requestSequence.current;
    setLoadingKind(kind);
    setPageError(null);

    if (kind === "initial" || kind === "filter") {
      setHasData(false);
      setTickets([]);
      setNextCursor(null);
    }

    const retryRequest = {
      filters: requestedFilters,
      cursor,
      pageIndex: requestedPageIndex,
      kind,
    };

    try {
      const result = await listAdminTickets({
        ...requestedFilters,
        limit: TICKET_PAGE_SIZE,
        ...(cursor ? { cursor } : {}),
      }, controller.signal);
      if (sequence !== requestSequence.current) {
        return;
      }
      setTickets(result.tickets);
      setNextCursor(result.next_cursor);
      setPageIndex(requestedPageIndex);
      setHasData(true);
      setPageError(null);
    } catch (error) {
      if (sequence !== requestSequence.current || isAbortError(error)) {
        return;
      }
      const normalized = normalizeError(error);
      if (normalized.status !== 401) {
        setPageError({ error: normalized, request: retryRequest });
      }
    } finally {
      if (sequence === requestSequence.current) {
        requestController.current = null;
        paginationLocked.current = false;
        setLoadingKind(null);
      }
    }
  }, []);

  useEffect(() => {
    const normalizedQuery = filterQuery(initialFilters.current);
    if (initialSearch.current !== normalizedQuery) {
      replace(pageUrl(initialFilters.current), { scroll: false });
    }
    void loadTickets("initial", initialFilters.current, null, 0);
    return () => {
      requestController.current?.abort();
      requestSequence.current += 1;
    };
  }, [loadTickets, replace]);

  const isLoading = loadingKind !== null;
  const hasActiveFilters = Boolean(filters.status || filters.ticket_type || filters.priority);
  const visibleTickets = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return tickets;
    return tickets.filter((ticket) => [
      ticket.ticket_id,
      ticket.customer_name,
      ticket.customer_phone,
      ticket.ticket_type,
      ticket.category,
      ticket.order_id,
    ].some((value) => value?.toLowerCase().includes(query)));
  }, [search, tickets]);

  function applyFilters(nextFilters: TicketFilters) {
    setFilters(nextFilters);
    setCursorHistory([null]);
    setPageIndex(0);
    paginationLocked.current = false;
    replace(pageUrl(nextFilters), { scroll: false });
    void loadTickets("filter", nextFilters, null, 0);
  }

  function changeFilter(
    key: keyof TicketFilters,
    event: ChangeEvent<HTMLSelectElement>,
  ) {
    const value = event.target.value;
    applyFilters({
      ...filters,
      [key]: value || undefined,
    });
  }

  function clearFilters() {
    applyFilters({});
  }

  function nextPage() {
    if (paginationLocked.current || isLoading || !nextCursor) {
      return;
    }
    paginationLocked.current = true;
    const targetPageIndex = pageIndex + 1;
    setCursorHistory((history) => {
      const nextHistory = history.slice(0, targetPageIndex);
      nextHistory[targetPageIndex] = nextCursor;
      return nextHistory;
    });
    void loadTickets("pagination", filters, nextCursor, targetPageIndex);
  }

  function previousPage() {
    if (paginationLocked.current || isLoading || pageIndex === 0) {
      return;
    }
    paginationLocked.current = true;
    const targetPageIndex = pageIndex - 1;
    void loadTickets(
      "pagination",
      filters,
      cursorHistory[targetPageIndex] ?? null,
      targetPageIndex,
    );
  }

  function refresh() {
    if (isLoading) {
      return;
    }
    void loadTickets("refresh", filters, cursorHistory[pageIndex] ?? null, pageIndex);
  }

  function retry() {
    if (!pageError || isLoading) {
      return;
    }
    const { request } = pageError;
    void loadTickets(
      request.kind,
      request.filters,
      request.cursor,
      request.pageIndex,
    );
  }

  function returnToFirstPage() {
    setCursorHistory([null]);
    setPageIndex(0);
    void loadTickets(hasData ? "pagination" : "initial", filters, null, 0);
  }

  const actions = (
    <div className="admin-dashboard-actions">
      <button
        className="admin-refresh-button"
        disabled={isLoading}
        onClick={refresh}
        type="button"
      >
        <MiniIcon name="refresh" />
        {loadingKind === "refresh" ? "Refreshing..." : "Refresh"}
      </button>
    </div>
  );

  const showSkeleton = !hasData
    && (loadingKind === "initial" || loadingKind === "filter");
  const showEmpty = hasData && tickets.length === 0 && !pageError;
  const showSearchEmpty = hasData && tickets.length > 0 && visibleTickets.length === 0 && !pageError;

  return (
    <AdminShell
      actions={actions}
      subtitle="Review customer assistance requests and reported order problems"
      title="Support Tickets"
    >
      <div className="admin-ticket-page">
        <SupportTicketKpiStrip loading={showSkeleton} tickets={visibleTickets} />
        <section className="admin-ticket-toolbar" aria-label="Ticket filters">
          <label className="support-ticket-search">
            Search tickets
            <span>
              <MiniIcon name="search" />
              <input
                disabled={showSkeleton}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search by Ticket ID, customer, type, or order"
                value={search}
              />
            </span>
          </label>
          <label>
            Status
            <select value={filters.status ?? ""} onChange={(event) => changeFilter("status", event)}>
              <option value="">All statuses</option>
              <option value="open">Open</option>
              <option value="in_review">In Review</option>
              <option value="waiting_for_customer">Waiting for Customer</option>
              <option value="resolved">Resolved</option>
              <option value="closed">Closed</option>
            </select>
          </label>
          <label>
            Ticket type
            <select value={filters.ticket_type ?? ""} onChange={(event) => changeFilter("ticket_type", event)}>
              <option value="">All ticket types</option>
              <option value="human_assistance">Human Assistance</option>
              <option value="order_complaint">Order Complaint</option>
            </select>
          </label>
          <label>
            Priority
            <select value={filters.priority ?? ""} onChange={(event) => changeFilter("priority", event)}>
              <option value="">All priorities</option>
              <option value="normal">Normal</option>
              <option value="high">High</option>
              <option value="urgent">Urgent</option>
            </select>
          </label>
          {hasActiveFilters && !showEmpty && (
            <button className="secondary" disabled={isLoading} onClick={clearFilters} type="button">
              Clear filters
            </button>
          )}
          <p className="support-ticket-filter-help">Click a ticket to view full details</p>
        </section>
        <p className="support-ticket-scope-note">Search and summary metrics apply to the currently loaded cursor page.</p>

        <div className="admin-visually-hidden" aria-live="polite">
          {loadingKind === "pagination"
            ? "Loading ticket page"
            : loadingKind === "refresh"
              ? "Refreshing support tickets"
              : showSkeleton
                ? "Loading support tickets"
                : hasData
                  ? `Page ${pageIndex + 1} loaded`
                  : ""}
        </div>

        {showSkeleton && <TicketListSkeleton announce={false} />}

        {!hasData && pageError && (
          <TicketErrorPanel
            disabled={isLoading}
            onRetry={retry}
            onReturnToFirst={returnToFirstPage}
            pageError={pageError}
          />
        )}

        {showEmpty && (
          <section className="admin-empty-state">
            <strong>{hasActiveFilters ? "No tickets match these filters" : "No support tickets"}</strong>
            <p>
              {hasActiveFilters
                ? "Adjust or clear the filters to view other support tickets."
                : "Support tickets will appear here when customers request assistance or report an order problem."}
            </p>
            {hasActiveFilters && (
              <button className="secondary" onClick={clearFilters} type="button">Clear filters</button>
            )}
          </section>
        )}

        {showSearchEmpty && (
          <section className="admin-empty-state">
            <strong>No tickets on this loaded page match the search</strong>
            <p>Try another term, clear the search, or move to another cursor page.</p>
            <button className="secondary" onClick={() => setSearch("")} type="button">Clear search</button>
          </section>
        )}

        {hasData && tickets.length > 0 && (
          <section className="admin-ticket-results" aria-label="Ticket results">
            <TicketListTable rowOffset={pageIndex * TICKET_PAGE_SIZE} tickets={visibleTickets} />
            {pageError && (
              <TicketErrorPanel
                disabled={isLoading}
                onRetry={retry}
                onReturnToFirst={returnToFirstPage}
                pageError={pageError}
              />
            )}
            <div className="admin-ticket-pagination">
              <span className="support-ticket-loaded-count">
                Showing {visibleTickets.length} ticket{visibleTickets.length === 1 ? "" : "s"} on this page
              </span>
              <button
                className="secondary"
                disabled={isLoading || pageIndex === 0}
                onClick={previousPage}
                type="button"
              >
                Previous page
              </button>
              <span className="admin-ticket-page-label">Page {pageIndex + 1}</span>
              {loadingKind === "pagination" && (
                <span className="admin-ticket-loading-label">Loading...</span>
              )}
              <button
                className="secondary"
                disabled={isLoading || !nextCursor}
                onClick={nextPage}
                type="button"
              >
                Next page
              </button>
            </div>
          </section>
        )}
      </div>
    </AdminShell>
  );
}
