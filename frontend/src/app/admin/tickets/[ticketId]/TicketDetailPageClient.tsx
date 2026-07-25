"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AdminShell } from "@/app/admin/AdminShell";
import { TicketDetailSections } from "@/app/admin/tickets/[ticketId]/TicketDetailSections";
import { AdminApiError } from "@/lib/adminApi";
import { getAdminTicket } from "@/lib/adminTicketsApi";
import type { AdminTicketDetail } from "@/lib/adminTicketTypes";

type LoadingKind = "initial" | "refresh";

type LoadedTicketState = {
  ticketId: string;
  ticket: AdminTicketDetail;
} | null;

type TicketErrorState = {
  ticketId: string;
  error: AdminApiError;
} | null;

function isAbortError(error: unknown) {
  return (
    typeof error === "object"
    && error !== null
    && "name" in error
    && error.name === "AbortError"
  );
}

function safeRequestError(error: unknown) {
  if (error instanceof AdminApiError) {
    return error;
  }
  return new AdminApiError(
    0,
    "ADMIN_NETWORK_ERROR",
    "The administrator service could not be reached. Please try again.",
  );
}

function errorPresentation(error: AdminApiError) {
  if (error.status === 404 || error.errorCode === "TICKET_NOT_FOUND") {
    return {
      title: "Ticket not found",
      message: "This support ticket does not exist or is no longer available.",
      retryable: false,
    };
  }
  switch (error.errorCode) {
    case "TICKET_DATA_INVALID":
      return {
        title: "Ticket unavailable",
        message: "This ticket cannot be safely displayed.",
        retryable: true,
      };
    case "TICKET_BACKEND_UNAVAILABLE":
      return {
        title: "Ticket temporarily unavailable",
        message: "Support tickets are temporarily unavailable.",
        retryable: true,
      };
    case "TICKET_INTERNAL_ERROR":
      return {
        title: "Ticket unavailable",
        message: "This support ticket could not be loaded safely.",
        retryable: true,
      };
    case "ADMIN_NETWORK_ERROR":
      return {
        title: "Connection problem",
        message: "The administrator service could not be reached.",
        retryable: true,
      };
    default:
      return {
        title: "Ticket unavailable",
        message: "The support ticket could not be loaded.",
        retryable: true,
      };
  }
}

function TicketDetailError({
  error,
  onRetry,
}: {
  error: AdminApiError;
  onRetry: () => void;
}) {
  const presentation = errorPresentation(error);
  return (
    <section className="admin-error-panel admin-ticket-detail-error" role="alert">
      <div>
        <strong>{presentation.title}</strong>
        <p>{presentation.message}</p>
      </div>
      <div className="admin-ticket-error-actions">
        <Link className="admin-secondary-button" href="/admin/tickets">
          Back to Support Tickets
        </Link>
        {presentation.retryable && (
          <button className="admin-primary-button" onClick={onRetry} type="button">
            Retry
          </button>
        )}
      </div>
    </section>
  );
}

function TicketDetailSkeleton() {
  return (
    <section
      aria-hidden="true"
      className="admin-ticket-detail-skeleton"
    >
      {Array.from({ length: 6 }, (_, index) => (
        <div className="admin-ticket-detail-skeleton-panel" key={index}>
          <span className="admin-skeleton admin-skeleton-line" />
          <span className="admin-skeleton admin-skeleton-line" />
          <span className="admin-skeleton admin-skeleton-line" />
        </div>
      ))}
    </section>
  );
}

export function TicketDetailPageClient({ ticketId }: { ticketId: string }) {
  const [loadedTicket, setLoadedTicket] = useState<LoadedTicketState>(null);
  const [ticketError, setTicketError] = useState<TicketErrorState>(null);
  const [loadingKind, setLoadingKind] = useState<LoadingKind | null>("initial");
  const controllerRef = useRef<AbortController | null>(null);
  const requestSequenceRef = useRef(0);

  const loadTicket = useCallback(async (
    requestedTicketId: string,
    kind: LoadingKind,
  ) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const sequence = ++requestSequenceRef.current;

    if (kind === "initial") {
      setLoadedTicket(null);
    }
    setTicketError(null);
    setLoadingKind(kind);

    try {
      const response = await getAdminTicket(requestedTicketId, controller.signal);
      if (sequence !== requestSequenceRef.current || controller.signal.aborted) {
        return;
      }
      setLoadedTicket({
        ticketId: requestedTicketId,
        ticket: response.ticket,
      });
    } catch (requestError) {
      if (
        sequence !== requestSequenceRef.current
        || controller.signal.aborted
        || isAbortError(requestError)
      ) {
        return;
      }
      const safeError = safeRequestError(requestError);
      if (safeError.status !== 401) {
        setTicketError({
          ticketId: requestedTicketId,
          error: safeError,
        });
      }
    } finally {
      if (sequence === requestSequenceRef.current) {
        if (controllerRef.current === controller) {
          controllerRef.current = null;
        }
        setLoadingKind(null);
      }
    }
  }, []);

  useEffect(() => {
    void loadTicket(ticketId, "initial");
    return () => {
      requestSequenceRef.current += 1;
      controllerRef.current?.abort();
      controllerRef.current = null;
    };
  }, [loadTicket, ticketId]);

  const visibleTicket = loadedTicket?.ticketId === ticketId
    ? loadedTicket.ticket
    : null;
  const visibleError = ticketError?.ticketId === ticketId
    ? ticketError.error
    : null;
  const routeOwnershipChanged = (
    loadedTicket !== null && loadedTicket.ticketId !== ticketId
  ) || (
    ticketError !== null && ticketError.ticketId !== ticketId
  );
  const visibleLoadingKind = routeOwnershipChanged ? "initial" : loadingKind;

  const refresh = useCallback(() => {
    void loadTicket(ticketId, visibleTicket ? "refresh" : "initial");
  }, [loadTicket, ticketId, visibleTicket]);

  const isLoading = visibleLoadingKind !== null;
  const actions = (
    <button
      className="admin-primary-button"
      disabled={isLoading}
      onClick={refresh}
      type="button"
    >
      {visibleLoadingKind === "refresh" ? "Refreshing ticket..." : "Refresh"}
    </button>
  );

  return (
    <AdminShell
      actions={actions}
      subtitle="Read-only support ticket record"
      title="Support Ticket"
    >
      <div className="admin-ticket-detail-page">
        <div aria-live="polite" className="admin-visually-hidden" role="status">
          {visibleLoadingKind === "initial"
            ? "Loading support ticket"
            : visibleLoadingKind === "refresh"
              ? "Refreshing ticket"
              : ""}
        </div>
        {visibleError && <TicketDetailError error={visibleError} onRetry={refresh} />}
        {!visibleTicket
          && visibleLoadingKind === "initial"
          && <TicketDetailSkeleton />}
        {visibleTicket && <TicketDetailSections ticket={visibleTicket} />}
      </div>
    </AdminShell>
  );
}
