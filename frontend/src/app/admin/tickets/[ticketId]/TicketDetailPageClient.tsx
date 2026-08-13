"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AdminShell } from "@/app/admin/AdminShell";
import { MiniIcon } from "@/app/admin/orders/orderPresentation";
import {
  TicketActions,
  type TicketMutation,
  type TicketMutationFeedback,
  type TicketMutationKind,
  type TicketMutationOutcome,
} from "@/app/admin/tickets/[ticketId]/TicketActions";
import { TicketDetailSections } from "@/app/admin/tickets/[ticketId]/TicketDetailSections";
import { AdminApiError } from "@/lib/adminApi";
import {
  addAdminTicketNote,
  getAdminTicket,
  reopenAdminTicket,
  updateAdminTicketPriority,
  updateAdminTicketStatus,
} from "@/lib/adminTicketsApi";
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

type ConflictGuidanceState = {
  ticketId: string;
  message: string;
} | null;

const CONFLICT_MESSAGE = "This ticket changed while you were working. "
  + "The latest ticket has been loaded. Review the changes and submit again.";
const CONFLICT_LOADING_MESSAGE = "This ticket changed while you were working. "
  + "Loading the latest ticket.";
const CONFLICT_RECOVERY_FAILED_MESSAGE = "This ticket changed while you were working, "
  + "but the latest ticket could not be loaded. "
  + "Refresh the ticket before submitting another change.";
const AMBIGUOUS_MUTATION_MESSAGE = "The change could not be confirmed. "
  + "Refresh the ticket before trying again.";

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

function isVersionConflict(error: unknown): error is AdminApiError {
  return (
    error instanceof AdminApiError
    && error.status === 409
    && error.errorCode === "TICKET_VERSION_CONFLICT"
  );
}

function mutationSuccessMessage(kind: TicketMutationKind) {
  switch (kind) {
    case "status":
      return "Ticket status updated.";
    case "priority":
      return "Ticket priority updated.";
    case "note":
      return "Internal note added.";
    case "reopen":
      return "Ticket reopened.";
  }
}

function mutationErrorMessage(
  error: AdminApiError,
  kind: TicketMutationKind,
) {
  switch (error.errorCode) {
    case "INVALID_TICKET_STATUS":
      return "Select a valid ticket status.";
    case "INVALID_TICKET_PRIORITY":
      return "Select a valid ticket priority.";
    case "INVALID_TICKET_TRANSITION":
      return "This ticket transition is no longer available. Refresh the ticket and review it.";
    case "INVALID_REOPEN_TARGET":
      return "Select open or in review as the reopen status.";
    case "NOTE_REQUIRED":
      if (kind === "status") {
        return "A reason is required for this status change.";
      }
      if (kind === "note") {
        return "An internal note is required.";
      }
      return "Complete the required information before submitting.";
    case "NOTE_TOO_LONG":
      return "The internal note cannot exceed 2,000 characters.";
    case "REOPEN_REASON_REQUIRED":
      return "A reason is required to reopen this ticket.";
    case "TICKET_ITEM_TOO_LARGE":
      return "This ticket is too large to save this change.";
    case "TICKET_DATA_INVALID":
      return "This ticket cannot be safely updated.";
  }
  if (
    error.status === 0
    || error.status === 500
    || error.status === 503
    || error.errorCode === "NOTE_ID_GENERATION_FAILED"
    || error.errorCode === "TICKET_BACKEND_UNAVAILABLE"
    || error.errorCode === "TICKET_INTERNAL_ERROR"
    || error.errorCode === "ADMIN_NETWORK_ERROR"
  ) {
    return AMBIGUOUS_MUTATION_MESSAGE;
  }
  return "The ticket change could not be completed. Review the form and try again.";
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
  const [activeMutation, setActiveMutation] = useState<TicketMutationKind | null>(null);
  const [recoveringConflict, setRecoveringConflict] = useState(false);
  const [mutationFeedback, setMutationFeedback] = useState<TicketMutationFeedback>(null);
  const [conflictGuidance, setConflictGuidance] =
    useState<ConflictGuidanceState>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const requestSequenceRef = useRef(0);
  const mutationControllerRef = useRef<AbortController | null>(null);
  const mutationSequenceRef = useRef(0);
  const mutationBusyRef = useRef(false);
  const currentTicketIdRef = useRef(ticketId);
  currentTicketIdRef.current = ticketId;

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
      setConflictGuidance(null);
    }
    setMutationFeedback(null);
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
      setConflictGuidance(null);
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
    setActiveMutation(null);
    setRecoveringConflict(false);
    void loadTicket(ticketId, "initial");
    return () => {
      requestSequenceRef.current += 1;
      controllerRef.current?.abort();
      controllerRef.current = null;
      mutationSequenceRef.current += 1;
      mutationControllerRef.current?.abort();
      mutationControllerRef.current = null;
      mutationBusyRef.current = false;
    };
  }, [loadTicket, ticketId]);

  const visibleTicket = loadedTicket?.ticketId === ticketId
    ? loadedTicket.ticket
    : null;
  const visibleError = ticketError?.ticketId === ticketId
    ? ticketError.error
    : null;
  const visibleConflictGuidance = conflictGuidance?.ticketId === ticketId
    ? conflictGuidance.message
    : null;
  const routeOwnershipChanged = (
    loadedTicket !== null && loadedTicket.ticketId !== ticketId
  ) || (
    ticketError !== null && ticketError.ticketId !== ticketId
  );
  const visibleLoadingKind = routeOwnershipChanged ? "initial" : loadingKind;

  const mutateTicket = useCallback(async (
    mutation: TicketMutation,
  ): Promise<TicketMutationOutcome> => {
    if (mutationBusyRef.current || !visibleTicket) {
      return "error";
    }

    const requestedTicketId = ticketId;
    const expectedVersion = visibleTicket.version;
    const sequence = ++mutationSequenceRef.current;
    const controller = new AbortController();
    mutationControllerRef.current = controller;
    mutationBusyRef.current = true;
    setActiveMutation(mutation.kind);
    setRecoveringConflict(false);
    setMutationFeedback(null);
    setConflictGuidance(null);

    try {
      let response;
      switch (mutation.kind) {
        case "status":
          response = await updateAdminTicketStatus(
            requestedTicketId,
            {
              ...mutation.request,
              expected_version: expectedVersion,
            },
            controller.signal,
          );
          break;
        case "priority":
          response = await updateAdminTicketPriority(
            requestedTicketId,
            {
              ...mutation.request,
              expected_version: expectedVersion,
            },
            controller.signal,
          );
          break;
        case "note":
          response = await addAdminTicketNote(
            requestedTicketId,
            {
              ...mutation.request,
              expected_version: expectedVersion,
            },
            controller.signal,
          );
          break;
        case "reopen":
          response = await reopenAdminTicket(
            requestedTicketId,
            {
              ...mutation.request,
              expected_version: expectedVersion,
            },
            controller.signal,
          );
          break;
      }

      if (
        sequence !== mutationSequenceRef.current
        || controller.signal.aborted
        || requestedTicketId !== currentTicketIdRef.current
      ) {
        return "error";
      }
      setLoadedTicket({
        ticketId: requestedTicketId,
        ticket: response.ticket,
      });
      setMutationFeedback({
        kind: mutation.kind,
        tone: "success",
        message: mutationSuccessMessage(mutation.kind),
      });
      return "success";
    } catch (mutationError) {
      if (
        sequence !== mutationSequenceRef.current
        || controller.signal.aborted
        || requestedTicketId !== currentTicketIdRef.current
        || isAbortError(mutationError)
      ) {
        return "error";
      }
      const safeError = safeRequestError(mutationError);
      if (safeError.status === 401) {
        return "error";
      }
      if (
        safeError.status === 404
        || safeError.errorCode === "TICKET_NOT_FOUND"
      ) {
        setLoadedTicket(null);
        setMutationFeedback(null);
        setConflictGuidance(null);
        setTicketError({
          ticketId: requestedTicketId,
          error: new AdminApiError(
            404,
            "TICKET_NOT_FOUND",
            "This support ticket is unavailable.",
          ),
        });
        return "error";
      }
      if (isVersionConflict(mutationError)) {
        setActiveMutation(null);
        setRecoveringConflict(true);
        setConflictGuidance({
          ticketId: requestedTicketId,
          message: CONFLICT_LOADING_MESSAGE,
        });
        const recoveryController = new AbortController();
        mutationControllerRef.current = recoveryController;
        try {
          const latest = await getAdminTicket(
            requestedTicketId,
            recoveryController.signal,
          );
          if (
            sequence !== mutationSequenceRef.current
            || recoveryController.signal.aborted
            || requestedTicketId !== currentTicketIdRef.current
          ) {
            return "error";
          }
          setLoadedTicket({
            ticketId: requestedTicketId,
            ticket: latest.ticket,
          });
          setConflictGuidance({
            ticketId: requestedTicketId,
            message: CONFLICT_MESSAGE,
          });
        } catch (recoveryError) {
          if (
            sequence !== mutationSequenceRef.current
            || recoveryController.signal.aborted
            || requestedTicketId !== currentTicketIdRef.current
            || isAbortError(recoveryError)
          ) {
            return "error";
          }
          setConflictGuidance({
            ticketId: requestedTicketId,
            message: CONFLICT_RECOVERY_FAILED_MESSAGE,
          });
        }
        return "conflict";
      }
      setMutationFeedback({
        kind: mutation.kind,
        tone: "error",
        message: mutationErrorMessage(safeError, mutation.kind),
      });
      return "error";
    } finally {
      if (sequence === mutationSequenceRef.current) {
        mutationControllerRef.current = null;
        mutationBusyRef.current = false;
        setActiveMutation(null);
        setRecoveringConflict(false);
      }
    }
  }, [ticketId, visibleTicket]);

  const refresh = useCallback(() => {
    if (mutationBusyRef.current) {
      return;
    }
    void loadTicket(ticketId, visibleTicket ? "refresh" : "initial");
  }, [loadTicket, ticketId, visibleTicket]);

  const isLoading = visibleLoadingKind !== null;
  const mutationBusy = activeMutation !== null || recoveringConflict;
  const actions = (
    <div className="admin-dashboard-actions">
      <button
        className="admin-refresh-button"
        disabled={isLoading || mutationBusy}
        onClick={refresh}
        type="button"
      >
        <MiniIcon name="refresh" />
        {visibleLoadingKind === "refresh" ? "Refreshing ticket..." : "Refresh"}
      </button>
    </div>
  );

  return (
    <AdminShell
      actions={actions}
      subtitle="Review and manage this support ticket record"
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
        {visibleTicket && (
          <TicketDetailSections
            actions={(
              <TicketActions
                activeMutation={activeMutation}
                conflictGuidance={visibleConflictGuidance}
                disabled={mutationBusy || isLoading}
                feedback={mutationFeedback}
                key={ticketId}
                onMutate={mutateTicket}
                ticket={visibleTicket}
              />
            )}
            ticket={visibleTicket}
          />
        )}
      </div>
    </AdminShell>
  );
}
